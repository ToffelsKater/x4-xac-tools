"""Import and export X4: Foundations character models (.xac).

Import builds an armature from the file's node tree plus one skinned mesh object per mesh.
Export writes meshes back into a copy of an original .xac (the template), which supplies
the skeleton, materials and every mesh you do not export.
"""
import re

import bmesh
import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Quaternion, Vector

from . import panel, xac

SCALE = 0.01  # file centimetres -> Blender metres
TANGENT_SIGN = 1.0  # file tangent w = TANGENT_SIGN * Blender bitangent_sign


# X4 is left-handed +Y up with characters facing -Z. Swapping Y and Z gives Blender's +Z up
# with the character facing -Y. The swap is a mirror, so triangle winding flips as well.
def to_blender(v):
    return Vector((v[0], v[2], v[1]))


def to_file(v):
    return (v[0], v[2], v[1])


def strip_suffix(name):
    return re.sub(r'\.\d{3}$', '', name)


# ---------------------------------------------------------------------------- import

def load(context, filepath):
    with open(filepath, 'rb') as f:
        a = xac.parse(f.read())
    name = bpy.path.display_name_from_filepath(filepath)
    coll = bpy.data.collections.new(name)
    context.scene.collection.children.link(coll)
    arm_obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    arm_obj['xac_template'] = filepath
    arm_obj.show_in_front = True
    coll.objects.link(arm_obj)
    build_armature(context, arm_obj, a['nodes'])
    materials = {}
    for m in a['meshes']:
        build_mesh(a, m, arm_obj, coll, materials)
    notes = []
    if a['morphs']:
        notes.append('%d face targets imported as shape keys' % len(a['morphs']))
    if a['lods']:
        notes.append('%d LOD level(s) skipped' % a['lods'])
    return notes


def bind_heads(nodes):
    """Bind-pose joint positions in Blender space, one per node."""
    world = []
    for n in nodes:
        x, y, z, w = n['rot']
        local = Matrix.Translation(n['pos']) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
        world.append(local if n['parent'] < 0 else world[n['parent']] @ local)
    return [to_blender(m.translation) * SCALE for m in world]


def build_armature(context, arm_obj, nodes):
    heads = bind_heads(nodes)
    children = [[] for _ in nodes]
    for i, n in enumerate(nodes):
        if n['parent'] >= 0:
            children[n['parent']].append(i)

    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bones = [arm_obj.data.edit_bones.new(n['name']) for n in nodes]
    for i, (n, b) in enumerate(zip(nodes, bones)):
        b.head = heads[i]
        ends = [heads[c] for c in children[i] if (heads[c] - heads[i]).length > 0.005]
        if ends:
            b.tail = ends[0]
        else:  # leaf: continue the parent's direction (bone orientation is display only)
            d = heads[i] - heads[n['parent']] if n['parent'] >= 0 else Vector()
            b.tail = heads[i] + (d.normalized() if d.length > 1e-6 else Vector((0, 0, 1))) * 0.03
        if n['parent'] >= 0:
            b.parent = bones[n['parent']]
    bpy.ops.object.mode_set(mode='OBJECT')


def material(name, cache):
    if name not in cache:
        mat = next((m for m in bpy.data.materials if m.get('xac_name') == name), None)
        if mat is None:
            mat = bpy.data.materials.new(name)
            mat['xac_name'] = name  # full name; Blender may shorten or suffix the ID name
            mat['xac_game'] = True  # defined in the game's (or a mod's) library: build keeps it
        cache[name] = mat
    return cache[name]


def build_mesh(a, m, arm_obj, coll, materials):
    name = a['nodes'][m['node']]['name']
    org = m['org']
    verts = [(0.0, 0.0, 0.0)] * m['norg']
    for i, o in enumerate(org):  # split copies (uv seams, hard edges) share one original vertex
        verts[o] = m['pos'][i]
    faces, corners, face_slots, slots = [], [], [], []
    start = 0
    for s in m['subs']:
        if s['mat'] not in slots:
            slots.append(s['mat'])
        idx = s['idx']
        for t in range(0, len(idx), 3):
            tri = (start + idx[t], start + idx[t + 2], start + idx[t + 1])  # winding flip
            if len({org[i] for i in tri}) == 3:
                faces.append([org[i] for i in tri])
                corners += tri
                face_slots.append(slots.index(s['mat']))
        start += s['nvert']

    me = bpy.data.meshes.new(name)
    me.from_pydata([to_blender(v) * SCALE for v in verts], [], faces)
    me.uv_layers.new(name='UVMap').data.foreach_set(
        'uv', [x for i in corners for x in (m['uv'][i][0], 1.0 - m['uv'][i][1])])
    for s in slots:
        me.materials.append(material(a['materials'][s], materials))
    me.polygons.foreach_set('material_index', face_slots)
    me.shade_smooth()
    me.normals_split_custom_set([to_blender(m['normal'][i]) for i in corners])

    obj = bpy.data.objects.new(name, me)
    obj['xac_node'] = name
    coll.objects.link(obj)
    obj.parent = arm_obj
    obj.modifiers.new('Armature', 'ARMATURE').object = arm_obj
    shapes = [(t, d) for t in a['morphs'] for d in t['defs'] if d['node'] == m['node']]
    if shapes:
        obj.shape_key_add(name='Basis', from_mix=False)
        base = [0.0] * (3 * len(verts))
        me.vertices.foreach_get('co', base)
        for t, d in shapes:
            co = list(base)
            for vi, dp in zip(d['verts'], d['pos']):
                o = 3 * org[vi]
                co[o:o + 3] = [base[o] + dp[0] * SCALE, base[o + 1] + dp[2] * SCALE, base[o + 2] + dp[1] * SCALE]
            kb = obj.shape_key_add(name=t['name'], from_mix=False)
            kb.data.foreach_set('co', co)
            kb.value = 0.0  # new keys start at 1
            if t['range'][0] < t['range'][1]:
                kb.slider_min, kb.slider_max = max(-10.0, t['range'][0]), min(10.0, t['range'][1])
    groups = {}
    for v, ws in enumerate(a['skins'].get(m['node'], [])):
        for n, w in ws:
            if w == w:  # a few vanilla weights are NaN
                bone = a['nodes'][n]['name']
                if bone not in groups:
                    groups[bone] = obj.vertex_groups.new(name=bone)
                groups[bone].add([v], w, 'REPLACE')


# ---------------------------------------------------------------------------- export

def save(context, filepath, template, objects, drop_others, keep_lods, face_map=None):
    """face_map: {template face target: shape key name}; a shape key named like the target
    is used without one."""
    with open(template, 'rb') as f:
        tdata = f.read()
    a = xac.parse(tdata)
    bones = {n['name'] for n in a['nodes']}
    faces = {t['name']: (face_map or {}).get(t['name']) or t['name'] for t in a['morphs']}
    notes = []
    meshes = [mesh_data(context, obj, bones, notes, faces) for obj in objects]
    data, warn = xac.build(tdata, meshes, drop_others, keep_lods)
    with open(filepath, 'wb') as f:
        f.write(data)
    return notes + warn


def evaluated_rest_mesh(context, obj):
    """Copy of the mesh with modifiers applied, with its armature in rest pose. The shape key
    pin (show only the active key) is ignored, so a pinned preview never bakes into the mesh."""
    arm = obj.find_armature()
    pose, pin = arm and arm.data.pose_position, obj.show_only_shape_key
    if arm:
        arm.data.pose_position = 'REST'
    obj.show_only_shape_key = False
    try:
        dg = context.evaluated_depsgraph_get()
        return bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    finally:
        obj.show_only_shape_key = pin
        if arm:
            arm.data.pose_position = pose


def shape_deltas(obj, keys, tmat, nmat):
    """{key name: (position deltas in cm, normal deltas)} per vertex in file space, relative to
    each key's reference key. Normals are vertex normals of the mesh moved to each shape."""
    blocks = obj.data.shape_keys.key_blocks if obj.data.shape_keys else {}
    tmp = obj.data.copy()
    n = len(tmp.vertices)
    out, states = {}, {}
    try:
        def state(block):
            if block.name in states:
                return states[block.name]
            co = [0.0] * (3 * n)
            block.data.foreach_get('co', co)
            tmp.vertices.foreach_set('co', co)
            tmp.update()
            nrm = [0.0] * (3 * n)
            tmp.vertex_normals.foreach_get('vector', nrm)
            states[block.name] = ([Vector(co[i:i + 3]) for i in range(0, 3 * n, 3)],
                                  [(nmat @ Vector(nrm[i:i + 3])).normalized() for i in range(0, 3 * n, 3)])
            return states[block.name]
        for key in keys:
            if key not in blocks:
                continue
            (c0, n0), (c1, n1) = state(blocks[key].relative_key), state(blocks[key])
            out[key] = ([to_file(tmat @ (b - a) / SCALE) for a, b in zip(c0, c1)],
                        [to_file(b - a) for a, b in zip(n0, n1)])
    finally:
        bpy.data.meshes.remove(tmp)
    return out


def mesh_data(context, obj, bones, notes, faces=None):
    arm = obj.find_armature()
    matrix = (arm.matrix_world.inverted() if arm else Matrix()) @ obj.matrix_world
    nmat = matrix.to_3x3().inverted_safe().transposed()
    tmat = matrix.to_3x3()
    flip = matrix.determinant() < 0  # a negative scale already mirrored the mesh
    group_names = [g.name for g in obj.vertex_groups]
    me = evaluated_rest_mesh(context, obj)
    try:
        if not me.uv_layers:
            raise ValueError('%s has no UV map' % obj.name)
        ngons = [f for f in me.polygons if f.loop_total > 4]
        if ngons:  # tangents need tris/quads
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
            bm.to_mesh(me)
            bm.free()
        me.calc_tangents(uvmap=me.uv_layers.active.name)
        me.calc_loop_triangles()

        weights, ignored, trimmed = [], set(), 0
        for v in me.vertices:
            ws = []
            for g in v.groups:
                n = group_names[g.group] if g.group < len(group_names) else None
                if g.weight <= 0:
                    continue
                if n in bones:
                    ws.append((n, g.weight))
                else:
                    ignored.add(n)
            ws.sort(key=lambda x: -x[1])
            if len(ws) > xac.MAX_INFLUENCES:
                trimmed += 1
                ws = ws[:xac.MAX_INFLUENCES]
            total = sum(w for _, w in ws)
            weights.append([(n, w / total) for n, w in ws])

        uv = me.uv_layers.active.data
        subs, unweighted = {}, set()
        for tri in me.loop_triangles:
            s = subs.get(tri.material_index)
            if s is None:
                mat = me.materials[tri.material_index] if tri.material_index < len(me.materials) else None
                if mat is None:
                    raise ValueError('%s: material slot %d is empty' % (obj.name, tri.material_index + 1))
                s = subs[tri.material_index] = dict(
                    material=mat.get('xac_name') or strip_suffix(mat.name), verts=[], tris=[], keys={})
            ids = []
            for li in tri.loops:
                loop = me.loops[li]
                v = loop.vertex_index
                if not weights[v]:
                    unweighted.add(v)
                nrm = to_file((nmat @ me.corner_normals[li].vector).normalized())
                tan = to_file((tmat @ loop.tangent).normalized())
                sign = TANGENT_SIGN * loop.bitangent_sign
                u, w = uv[li].uv
                key = (v, round(u, 6), round(w, 6), tuple(round(x, 5) for x in nrm),
                       tuple(round(x, 4) for x in tan), sign)
                i = s['keys'].get(key)
                if i is None:
                    i = s['keys'][key] = len(s['verts'])
                    pos = to_file(matrix @ me.vertices[v].co / SCALE)
                    s['verts'].append((v, pos, nrm, (u, 1.0 - w), tan + (sign,)))
                ids.append(i)
            s['tris'].append(tuple(ids) if flip else (ids[0], ids[2], ids[1]))
        if unweighted:
            raise ValueError('%s: %d vertices have no weight on any template bone; weight paint them'
                             % (obj.name, len(unweighted)))
        if ignored:
            notes.append('%s: ignored vertex groups that are not template bones: %s'
                         % (obj.name, ', '.join(sorted(map(str, ignored)))))
        if trimmed:
            notes.append('%s: %d vertices had more than %d weights; kept the strongest'
                         % (obj.name, trimmed, xac.MAX_INFLUENCES))
        ordered = [v for _, s in sorted(subs.items()) for v in s['verts']]  # file vertex order
        morphs = {}
        if faces and obj.data.shape_keys:
            if len(me.vertices) != len(obj.data.vertices):
                notes.append('%s: shape keys skipped, its modifiers change the vertex count' % obj.name)
            else:
                deltas, flat = shape_deltas(obj, set(faces.values()), tmat, nmat), set()
                for target, key in faces.items():
                    if key not in deltas:
                        continue
                    dp, dn = deltas[key]
                    idx = [i for i, v in enumerate(ordered)
                           if max(map(abs, dp[v[0]])) > 1e-4 or max(map(abs, dn[v[0]])) > 0.004]
                    if not idx:
                        flat.add(key)
                    else:
                        morphs[target] = dict(verts=idx, pos=[dp[ordered[i][0]] for i in idx],
                                              normal=[dn[ordered[i][0]] for i in idx], tangent=[(0.0, 0.0, 0.0)] * len(idx))
                if flat:
                    notes.append('%s: these shape keys move nothing, so their face targets stay still: %s'
                                 % (obj.name, ', '.join(sorted(flat))))
        return dict(node=obj.get('xac_node') or strip_suffix(obj.name), norg=len(me.vertices), weights=weights,
                    subs=[dict(material=s['material'], verts=s['verts'], tris=s['tris'])
                          for _, s in sorted(subs.items())], morphs=morphs)
    finally:
        bpy.data.meshes.remove(me)


# ---------------------------------------------------------------------------- operators

class IMPORT_SCENE_OT_x4_xac(bpy.types.Operator, ImportHelper):
    """Import an X4: Foundations character model"""
    bl_idname = 'import_scene.x4_xac'
    bl_label = 'Import X4 Character'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.xac'
    filter_glob: StringProperty(default='*.xac', options={'HIDDEN'})

    def execute(self, context):
        try:
            notes = load(context, self.filepath)
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for n in notes:
            self.report({'INFO'}, n)
        return {'FINISHED'}


class EXPORT_SCENE_OT_x4_xac(bpy.types.Operator, ExportHelper):
    """Export meshes into an X4: Foundations character model, based on an original .xac"""
    bl_idname = 'export_scene.x4_xac'
    bl_label = 'Export X4 Character'
    filename_ext = '.xac'
    filter_glob: StringProperty(default='*.xac', options={'HIDDEN'})
    template: StringProperty(
        name='Template', subtype='FILE_PATH',
        description='Original .xac that supplies the skeleton, materials and the meshes you do not export')
    use_selection: BoolProperty(name='Selected Only', default=True)
    drop_others: BoolProperty(
        name='Remove Other Meshes', default=False,
        description='Leave out template meshes that are not exported (e.g. replace a whole outfit)')
    keep_lods: BoolProperty(
        name='Keep LODs', default=False,
        description='Keep the template LOD meshes; they still show the old geometry at a distance')

    def invoke(self, context, event):
        obj = context.active_object
        arm = obj if obj and obj.type == 'ARMATURE' else obj and obj.find_armature()
        if not self.template and arm:
            self.template = arm.get('xac_template', '')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        pool = context.selected_objects if self.use_selection else context.scene.objects
        objects = [o for o in pool if o.type == 'MESH']
        if not objects:
            self.report({'ERROR'}, 'No mesh objects to export')
            return {'CANCELLED'}
        if not self.template:
            self.report({'ERROR'}, 'Pick the original .xac as Template')
            return {'CANCELLED'}
        try:
            notes = save(context, self.filepath, bpy.path.abspath(self.template), objects,
                         self.drop_others, self.keep_lods)
        except (OSError, ValueError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for n in notes:
            self.report({'WARNING'}, n)
        return {'FINISHED'}


def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_x4_xac.bl_idname, text='X4 Character (.xac)')


def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_x4_xac.bl_idname, text='X4 Character (.xac)')


def register():
    bpy.utils.register_class(IMPORT_SCENE_OT_x4_xac)
    bpy.utils.register_class(EXPORT_SCENE_OT_x4_xac)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    panel.register()


def unregister():
    panel.unregister()
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.utils.unregister_class(EXPORT_SCENE_OT_x4_xac)
    bpy.utils.unregister_class(IMPORT_SCENE_OT_x4_xac)

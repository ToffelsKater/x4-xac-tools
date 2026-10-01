"""Character swapping: fit a humanoid avatar onto X4's skeleton and build it into a mod.

fit() poses the avatar so every recognised joint lands on the matching X4 joint (arms turned
from T-pose to X4's bind pose, limbs stretched to X4's lengths, head sized by a knob), bakes
that pose into its meshes, moves the weights onto X4 bones and parents the meshes to an X4
armature. build() exports those meshes into template .xac files, converts their textures and
writes an extension: a new character in a spawn group, or a replacement for one body file.
Rigs with one bone per limb segment are recognised (Unity/VRChat, VRM, Mixamo, Biped).
"""
import os
import re
import types

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import catalog, dds, library, xac

TEMPLATES = {  # skeleton -> (body template, head template), game paths
    'FEMALE': ('extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_f_crew_uniform_01.xac',
               'assets/characters/argon/heads/char_arg_f_dyn_blend_head.xac'),
    'MALE': ('extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_m_crew_uniform_01.xac',
             'assets/characters/argon/heads/char_arg_m_dyn_blend_head.xac'),
}
FINGERS = (('thumb',), ('index',), ('middle',), ('ring',), ('little', 'pinky'))
# X4 face targets the game animates -> (label, avatar shape key names to try, lower case).
# Blink and emotions play by target name; lip sync picks targets by their phoneme bits.
# Covers VRChat visemes, VRoid/VRM (Fcl_*), MMD (Japanese) and plain English names.
FACE = {
    'mt_blink': ('Blink', ['blink', 'vrc.blink', 'fcl_eye_close', 'まばたき', 'eyes_closed', 'eye_close']),
    'mt_smile': ('Smile', ['smile', 'fcl_all_joy', '笑い', '笑顔', 'にこり', 'joy', 'happy']),
    'mt_anger': ('Anger', ['angry', 'anger', 'fcl_all_angry', '怒り']),
    'mt_sadness': ('Sadness', ['sad', 'sadness', 'fcl_all_sorrow', 'sorrow', '悲し目', '困る']),
    'mt_fear': ('Fear', ['fear', 'surprised', 'fcl_all_surprised', 'びっくり', 'surprise']),
    'phon_aa': ('Lips: AA (father)', ['vrc.v_aa', 'fcl_mth_a', 'aa', 'あ', 'a', 'jawopen']),
    'phon_ih': ('Lips: IH (sit)', ['vrc.v_ih', 'fcl_mth_i', 'ih', 'い', 'i']),
    'phon_iy': ('Lips: EE (see)', ['vrc.v_e', 'fcl_mth_e', 'e', 'え', 'ee']),
    'phon_aw': ('Lips: AW (cow)', ['vrc.v_oh', 'fcl_mth_o', 'oh', 'お', 'o']),
    'phon_uw': ('Lips: OO (food)', ['vrc.v_ou', 'fcl_mth_u', 'ou', 'う', 'u']),
    'phon_w': ('Lips: W', ['vrc.v_ou', 'fcl_mth_u', 'ou', 'う', 'u']),
    'phon_m': ('Lips: M B P', ['vrc.v_pp', 'pp', 'm']),
    'phon_f': ('Lips: F V', ['vrc.v_ff', 'ff', 'f']),
    'phon_n': ('Lips: N D T S', ['vrc.v_nn', 'vrc.v_dd', 'nn', 'dd', 'n']),
    'phon_l': ('Lips: L', ['vrc.v_dd', 'vrc.v_nn', 'dd', 'l']),
    'phon_r': ('Lips: R', ['vrc.v_rr', 'rr', 'r']),
}
SETTINGS = ('mod_id', 'mod_name', 'mode', 'group', 'weight', 'target', 'normal_opengl')  # of a build
NOISE = {'j', 'bip', 'bip01', 'mixamorig', 'def', 'org', 'b', 'bone', 'valvebiped'}


# ---------------------------------------------------------------------------- rig recognition

def words(name):
    """'mixamorig:LeftUpLeg' -> ('L', ['up', 'leg'])."""
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name)
    toks = [t for t in re.findall(r'[a-z]+|\d+', name.lower()) if t not in NOISE and not t.isdigit()]
    side = 'L' if ('l' in toks or 'left' in toks) else 'R' if ('r' in toks or 'right' in toks) else None
    return side, [t for t in toks if t not in ('l', 'r', 'left', 'right')]


def _chain_up(bone, stop):
    """Ancestors of bone (nearest last) until one in `stop`."""
    out, b = [], bone.parent
    while b is not None and b not in stop:
        out.insert(0, b)
        b = b.parent
    return out, b


def recognise(arm):
    """Map avatar bones onto X4. Returns dict(fit, aim, scale_of, weights, spine, hips, neck,
    head, hands) or raises ValueError. fit[bone] is the X4 joint it is moved onto (None: placed
    by rule); aim[bone] is the bone (avatar or X4 name) it points at."""
    bones = arm.data.bones
    info = {b.name: words(b.name) for b in bones}

    def find(cores, side=None):
        hits = [b for b in bones if ''.join(info[b.name][1]) in cores and side in (None, info[b.name][0])]
        return min(hits, key=lambda b: len(b.parent_recursive)) if hits else None

    hips, head = find({'hips', 'hip', 'pelvis'}), find({'head'})
    if hips is None or head is None:
        raise ValueError('no hips or head bone found; is this a humanoid rig?')
    neck = find({'neck'}) or head.parent
    spine, top = _chain_up(neck, {hips})
    if top != hips:
        raise ValueError('the neck is not below the hips')
    chain = [hips] + spine + [neck, head]
    fit = {hips.name: 'Bip01 Pelvis', neck.name: 'Bip01 Neck', head.name: 'Bip01 Head'}
    fit.update({s.name: None for s in spine})  # placed along X4's spine by height
    aim = {a.name: b.name for a, b in zip(chain, chain[1:])}
    aim[head.name] = 'Bip01 HeadNub'
    scale_of, weights, hands = {}, {}, {}
    torso = set(chain)
    for side in 'LR':
        hand, foot = find({'hand', 'wrist'}, side), find({'foot', 'ankle'}, side)
        if hand is None or foot is None:
            raise ValueError('no %s hand or foot bone found' % ('left' if side == 'L' else 'right'))
        arm_chain, _ = _chain_up(hand, torso)
        if len(arm_chain) < 2:
            raise ValueError('the %s arm needs upper and lower arm bones' % side)
        upper, lower = arm_chain[-2:]
        if len(arm_chain) > 2:
            clav = arm_chain[0]
            fit[clav.name], aim[clav.name] = 'Bip01 %s Clavicle' % side, upper.name
            scale_of[clav.name] = spine[-1].name if spine else hips.name  # too short to size from
        fit[upper.name], aim[upper.name] = 'Bip01 %s UpperArm' % side, lower.name
        fit[lower.name], aim[lower.name] = 'Bip01 %s Forearm' % side, hand.name
        fit[hand.name] = 'Bip01 %s Hand' % side
        firsts = {}
        for n, names in enumerate(FINGERS):
            segs = sorted((d for d in hand.children_recursive if any(w in info[d.name][1] for w in names)),
                          key=lambda d: len(d.parent_recursive))[:3]
            for i, seg in enumerate(segs):
                fit[seg.name] = 'Bip01 %s Finger%d%s' % (side, n, ('', '1', '2')[i])
                aim[seg.name] = segs[i + 1].name if i + 1 < len(segs) else 'Bip01 %s Finger%dNub' % (side, n)
                scale_of[seg.name] = hand.name  # finger bones are too short to size from
            if segs:
                firsts[n] = segs[0].name
        aim[hand.name] = firsts.get(2) or firsts.get(1) or 'Bip01 %s Finger2' % side
        hands[hand.name] = firsts
        leg, _ = _chain_up(foot, torso)
        if len(leg) < 2:
            raise ValueError('the %s leg needs upper and lower leg bones' % side)
        fit[leg[-2].name], aim[leg[-2].name] = 'Bip01 %s Thigh' % side, leg[-1].name
        fit[leg[-1].name], aim[leg[-1].name] = 'Bip01 %s Calf' % side, foot.name
        fit[foot.name] = 'Bip01 %s Foot' % side
        for toe in foot.children:
            if 'toe' in ''.join(info[toe.name][1]):
                weights[toe.name] = {'Bip01 %s Toe0' % side: 1.0}  # follows the foot when fitting
    weights.update({b: {x4: 1.0} for b, x4 in fit.items() if x4})
    return dict(fit=fit, aim=aim, scale_of=scale_of, weights=weights, spine=[s.name for s in spine],
                hips=hips.name, neck=neck.name, head=head.name, hands=hands)


# ---------------------------------------------------------------------------- fitting

def x4_skeleton(path, load):
    """Import a template for its armature; drop its meshes. Returns (armature, {bone: head})."""
    before = set(bpy.data.objects)
    load(bpy.context, path)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    for o in new:
        if o.type == 'MESH':
            bpy.data.objects.remove(o)
    return arm, {b.name: arm.matrix_world @ b.head_local for b in arm.data.bones}


def pose_to_x4(av, x4, rig, head_scale):
    """Pose the avatar armature onto X4's joints. Returns {bone: scale}."""
    rest = {b.name: av.matrix_world @ b.head_local for b in av.data.bones}
    hips, neck = rest[rig['hips']], rest[rig['neck']]
    s_legs = x4['Bip01 Pelvis'].z / max(hips.z, 1e-6)
    target = {b: x4[x] for b, x in rig['fit'].items() if x}
    for b in rig['spine']:  # linear along X4's spine between pelvis and neck
        f = (rest[b].z - hips.z) / max(neck.z - hips.z, 1e-6)
        target[b] = x4['Bip01 Pelvis'].lerp(x4['Bip01 Neck'], f)
    feet = {b for b, x in rig['fit'].items() if x and x.endswith(' Foot')}
    for b in feet:  # soles stay on the floor: keep the ankle height, scaled like the legs
        target[b] = Vector((target[b].x, target[b].y, rest[b].z * s_legs))

    bpy.context.view_layer.objects.active = av
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in av.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode='POSE')
    inv = av.matrix_world.inverted()
    scales = {}
    for pb in av.pose.bones:  # parents come first
        name = pb.name
        if name not in target:
            continue
        head_rest, head_to = rest[name], target[name]
        if name in feet:
            rot, k = Matrix(), s_legs
        else:
            aim = rig['aim'][name]
            aim_rest = rest[aim] if aim in rest else av.matrix_world @ pb.bone.tail_local
            aim_to = target[aim] if aim in target else x4[aim]
            d_rest, d_to = aim_rest - head_rest, aim_to - head_to
            q = d_rest.rotation_difference(d_to)
            fingers = rig['hands'].get(name, {})
            if 1 in fingers and 4 in fingers:  # also turn the palm: index -> little across the knuckles
                a = q @ (rest[fingers[1]] - rest[fingers[4]])
                b = target[fingers[1]] - target[fingers[4]]
                axis = d_to.normalized()
                a, b = a - axis * a.dot(axis), b - axis * b.dot(axis)
                if a.length > 1e-6 and b.length > 1e-6:
                    q = a.rotation_difference(b) @ q
            rot = q.to_matrix().to_4x4()
            if name == rig['head']:
                k = scales[rig['hips']] * head_scale
            elif name in rig['scale_of']:
                k = scales.get(rig['scale_of'][name], s_legs)
            else:
                k = d_to.length / max(d_rest.length, 1e-6)
        scales[name] = k
        world = Matrix.Translation(head_to) @ rot @ Matrix.Scale(k, 4) @ Matrix.Translation(-head_rest)
        pb.matrix = inv @ world @ av.matrix_world @ pb.bone.matrix_local
        bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='OBJECT')
    return scales


def bake(obj):
    """Replace the mesh with its evaluated shape (shape key mix + posed armature), in world space.
    Shape keys survive: each is evaluated through the same pose. Returns a note or ''."""
    for md in list(obj.modifiers):
        if md.type == 'ARMATURE' and md.object is None:
            obj.modifiers.remove(md)
    # the shape key pin shows only the active key and ignores the sliders: every key would bake flat
    obj.show_only_shape_key = False
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    keys, note = [], ''
    blocks = obj.data.shape_keys.key_blocks if obj.data.shape_keys else []
    if len(blocks) > 1 and len(me.vertices) != len(obj.data.vertices):
        note = '%s: shape keys dropped, its modifiers change the vertex count' % obj.name
    elif len(blocks) > 1:
        values = [k.value for k in blocks]
        for i, kb in enumerate(blocks[1:], 1):
            for k, v in zip(blocks, values):
                k.value = v
            kb.value = 1.0
            bpy.context.view_layer.update()
            co = np.empty(3 * len(me.vertices), np.float32)
            obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices.foreach_get('co', co)
            keys.append((kb.name, co))
        for k, v in zip(blocks, values):
            k.value = v
    world = obj.matrix_world.copy()
    old = obj.data
    obj.modifiers.clear()
    obj.parent = None
    obj.data = me
    obj.matrix_world = Matrix()
    if keys:
        obj.shape_key_add(name='Basis', from_mix=False)
        for name, co in keys:
            kb = obj.shape_key_add(name=name, from_mix=False)
            kb.data.foreach_set('co', co)
            kb.value = 0.0
    me.transform(world, shape_keys=True)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return note


def remap_weights(obj, av, weights):
    """Vertex groups -> X4 bones; unmapped bones use their nearest mapped ancestor.
    Returns (vertices left without weight, share of all weight on the head)."""
    def resolve(group):
        b = av.data.bones.get(group)
        while b is not None and b.name not in weights:
            b = b.parent
        return weights[b.name] if b is not None else {}

    names = [g.name for g in obj.vertex_groups]
    cache, per_vertex = {}, []
    for v in obj.data.vertices:
        acc = {}
        for g in v.groups:
            n = names[g.group]
            if n not in cache:
                cache[n] = resolve(n)
            for x4, f in cache[n].items():
                acc[x4] = acc.get(x4, 0.0) + g.weight * f
        per_vertex.append(acc)
    obj.vertex_groups.clear()
    groups = {}
    for i, acc in enumerate(per_vertex):
        for x4, w in acc.items():
            if w > 0:
                if x4 not in groups:
                    groups[x4] = obj.vertex_groups.new(name=x4)
                groups[x4].add([i], w, 'REPLACE')
    head = sum(acc.get('Bip01 Head', 0.0) for acc in per_vertex)
    total = sum(sum(acc.values()) for acc in per_vertex) or 1.0
    return sum(1 for acc in per_vertex if not acc), head / total


def face_suffix(target):
    return target.rsplit('.', 1)[-1]


def flat_keys(meshes):
    """Names of shape keys that move no vertex (e.g. flattened by an earlier Fit)."""
    out = set()
    for o in meshes:
        for kb in o.data.shape_keys.key_blocks[1:] if o.data.shape_keys else []:
            a, b = np.empty(3 * len(kb.data), np.float32), np.empty(3 * len(kb.data), np.float32)
            kb.data.foreach_get('co', a)
            kb.relative_key.data.foreach_get('co', b)
            if not len(a) or np.abs(a - b).max() < 1e-6:
                out.add(kb.name)
    return out


def guess_face(x4arm, meshes):
    """Fill x4arm.x4_face: one row per animated face target of the head template, with the
    first shape key of the head meshes whose name matches FACE; rows whose key moves nothing
    are flagged. Returns a report line."""
    with open(x4arm['xac_head_template'], 'rb') as f:
        targets = [t['name'] for t in xac.parse(f.read())['morphs'] if face_suffix(t['name']) in FACE]
    keys = {}
    for o in meshes:
        if o.x4_part == 'HEAD' and o.data.shape_keys:
            for kb in o.data.shape_keys.key_blocks[1:]:
                keys.setdefault(kb.name.lower(), kb.name)
    flat = flat_keys([o for o in meshes if o.x4_part == 'HEAD'])
    x4arm.x4_face.clear()
    for t in sorted(targets, key=lambda t: list(FACE).index(face_suffix(t))):
        row = x4arm.x4_face.add()
        row.target = t
        row.key = next((keys[k] for k in FACE[face_suffix(t)][1] if k in keys), '')
        row.flat = row.key in flat
    found = [r for r in x4arm.x4_face if r.key]
    line = 'face: %d of %d expressions and lip shapes found in the shape keys' % (len(found), len(targets))
    if any(r.flat for r in found):
        line += '; WARNING: %d of them move nothing (re-import the avatar and fit it again)' % sum(r.flat for r in found)
    return line


def fit(context, av, skeleton, head_scale, game, cache, load):
    """Fit avatar armature `av` and its meshes to X4. Returns (X4 armature, report lines)."""
    rig = recognise(av)
    meshes = [o for o in context.scene.objects if o.type == 'MESH' and any(
        m.type == 'ARMATURE' and m.object == av for m in o.modifiers)]
    if not meshes:
        raise ValueError('no meshes are deformed by %s' % av.name)
    # expression keys left on (FBX imports can switch blink on) would bake into the neutral face
    # and leave nothing to animate; other active keys (fit fixes like anti-clipping) stay on
    face_keys = {n for _, names in FACE.values() for n in names}
    zeroed = []
    for obj in meshes:
        for kb in obj.data.shape_keys.key_blocks[1:] if obj.data.shape_keys else []:
            if kb.value and kb.name.lower() in face_keys:
                kb.value = 0.0
                zeroed.append(kb.name)
    body_path, head_path = TEMPLATES[skeleton]
    x4arm, x4 = x4_skeleton(catalog.extract(game, body_path, cache), load)
    x4arm.name = x4arm.data.name = av.name + '_x4'
    x4arm['xac_head_template'] = catalog.extract(game, head_path, cache)
    x4arm['x4_skeleton'] = skeleton
    scales = pose_to_x4(av, x4, rig, head_scale)
    weights = dict(rig['weights'])
    for b in rig['spine']:  # spine bones go to the X4 spine bone nearest to where they landed
        z = (av.matrix_world @ av.pose.bones[b].head).z
        weights[b] = {min(('Bip01 Spine', 'Bip01 Spine1', 'Bip01 Spine2'), key=lambda n: abs(x4[n].z - z)): 1.0}
    report = ['body scale %.2f, head scale %.2f, %d bones mapped' % (
        scales[rig['hips']], scales[rig['head']], sum(1 for x in rig['fit'].values() if x))]
    if zeroed:
        report.append('neutral face: switched off %s' % ', '.join(zeroed))
    for obj in meshes:
        note = bake(obj)
        if note:
            report.append(note)
        empty, head_share = remap_weights(obj, av, weights)
        obj.parent = x4arm
        obj.modifiers.new('Armature', 'ARMATURE').object = x4arm
        obj.x4_part = 'HEAD' if head_share > 0.8 else 'BODY'
        report.append('%s: %s%s' % (obj.name, obj.x4_part.lower(),
                                    ', %d vertices without weight' % empty if empty else ''))
    bpy.data.objects.remove(av)
    report.append(guess_face(x4arm, meshes))
    return x4arm, report


# ---------------------------------------------------------------------------- materials, textures

def _source(sock, depth=0):
    """Image texture node feeding a socket through a few nodes, or None."""
    if not sock.is_linked or depth > 4:
        return None
    node = sock.links[0].from_node
    if node.type == 'TEX_IMAGE' and node.image:
        return node
    for inp in node.inputs:
        found = _source(inp, depth + 1)
        if found:
            return found
    return None


def material_info(mat):
    """dict(diffuse=image, alpha=bool, normal=image or None, smooth=0..255) from its nodes.
    Roughness 0..1 maps to X4 smoothness 100..0 (vanilla cloth is ~80; more glares)."""
    info = dict(diffuse=None, alpha=False, normal=None, smooth=50)
    nodes = mat.node_tree.nodes if mat.node_tree else []
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        info['diffuse'] = next((n.image for n in nodes if n.type == 'TEX_IMAGE' and n.image), None)
    else:
        base = _source(bsdf.inputs['Base Color'])
        info['diffuse'] = base.image if base else None
        alpha = bsdf.inputs['Alpha']
        info['alpha'] = alpha.is_linked or alpha.default_value < 1.0
        src = _source(bsdf.inputs['Normal'])
        info['normal'] = src.image if src else None
        rough = bsdf.inputs['Roughness']
        if not rough.is_linked:
            info['smooth'] = int(round((1 - rough.default_value) * 100))
    if mat.x4_alpha != 'AUTO':
        info['alpha'] = mat.x4_alpha == 'CUTOUT'
    return info


def used_materials(objs):
    """Materials that faces of objs actually use, in first-use order."""
    out = []
    for o in objs:
        idx = np.zeros(len(o.data.polygons), np.int32)
        o.data.polygons.foreach_get('material_index', idx)
        for i in sorted(set(idx.tolist())):
            if i < len(o.material_slots):
                m = o.material_slots[i].material
                if m is not None and m not in out:
                    out.append(m)
    return out


def pixels(img):
    """Blender image -> HxWx4 uint8, top row first (DDS order)."""
    w, h = img.size
    buf = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(buf)
    return np.ascontiguousarray((np.clip(buf, 0, 1) * 255 + 0.5).astype(np.uint8).reshape(h, w, 4)[::-1])


def clean(name):
    return re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or 'x'


def join_copy(context, objs, name):
    """Join copies of objs into one object named after the template mesh node it replaces."""
    copies = []
    for o in sorted(objs, key=lambda o: o.data.shape_keys is None):  # join keeps the active one's keys
        c = o.copy()
        c.data = o.data.copy()
        context.scene.collection.objects.link(c)
        copies.append(c)
    with context.temp_override(active_object=copies[0], selected_editable_objects=copies, object=copies[0]):
        bpy.ops.object.join()
    joined = copies[0]
    joined.name = name
    joined['xac_node'] = name
    return joined


# ---------------------------------------------------------------------------- build

def check_skeleton(x4arm, template):
    """Refuse a target whose bind pose differs from the armature the meshes are weighted to:
    the game would deform them around joints in other places."""
    from . import bind_heads
    with open(template, 'rb') as f:
        nodes = xac.parse(f.read())['nodes']
    bones = x4arm.data.bones
    worst, name = max(((bones[n['name']].head_local - h).length, n['name'])
                      for n, h in zip(nodes, bind_heads(nodes))
                      if n['name'].startswith('Bip01') and n['name'] in bones)
    if worst > 0.01:
        raise ValueError('%s has a different skeleton (%s is %.0f cm off); fit to its skeleton or import '
                         'that body instead' % (os.path.basename(template), name, worst * 100))


def build(context, x4arm, s, game, cache, save, mod_dir=None, tex_cache=None):
    """Write the character as extension <game>/extensions/<mod id> (or mod_dir).
    `s`: its mod settings (mod_id, mod_name, mode, group, weight, target, normal_opengl).
    tex_cache: dict shared between builds of one batch, so textures are converted once."""
    mod_id = clean(s.mod_id)
    clash = [o.name for o in context.scene.objects if o is not x4arm and o.type == 'ARMATURE' and
             'xac_template' in o and getattr(o, 'x4mod', None) and clean(o.x4mod.mod_id) == mod_id]
    if clash:  # two characters writing one extension folder would overwrite each other
        raise ValueError('mod ID %s is also set on %s; give each character its own' % (mod_id, ', '.join(clash)))
    mod_dir = mod_dir or os.path.join(game, 'extensions', mod_id)
    assets = 'assets/characters/%s' % mod_id
    ref = 'extensions/%s/%s' % (mod_id, assets)
    tex_ref = ref.replace('/', '\\') + '\\textures\\'
    replace = s.mode == 'REPLACE'
    target = s.target or x4arm.get('x4_game_path', '')
    parts = {'HEAD': [], 'BODY': []}
    for o in context.scene.objects:
        if o.type == 'MESH' and o.find_armature() == x4arm and o.x4_part in parts:
            parts[o.x4_part].append(o)
    if not parts['BODY']:
        raise ValueError('no meshes marked Body')
    if replace and not target:
        raise ValueError('pick the body file to replace')
    group = None if replace else library.groups(game).get(s.group)
    if not replace and group is None:
        raise ValueError('pick a spawn group')
    if not replace and not (parts['HEAD'] and 'x4_skeleton' in x4arm):
        raise ValueError('a new character needs a fitted avatar with meshes marked Head; '
                         'use Replace Body to keep game heads')
    report = []
    if replace and parts['HEAD']:
        report.append('replace mode keeps the game heads; %d head meshes left out' % len(parts['HEAD']))
        parts['HEAD'] = []
    if replace:
        check_skeleton(x4arm, catalog.extract(game, target, cache))

    textures, materials = {}, []
    vanilla = library.material_textures(game, vanilla=True)

    tex_cache = {} if tex_cache is None else tex_cache

    def texture(key, fmt, make):
        if key not in textures:
            if key not in tex_cache:
                tex_cache[key] = dds.x4_texture(make(), fmt)
            textures[key] = tex_cache[key]
        return tex_ref + key

    def normal(img):
        data = pixels(img)
        if s.normal_opengl:
            data[..., 1] = 255 - data[..., 1]  # OpenGL green -> DirectX
        return data

    for mat in used_materials(parts['HEAD'] + parts['BODY']):
        if mat.get('xac_game') or mat.get('xac_name') in vanilla:
            continue  # a game material (imported with the model): the file keeps pointing at it
        info = material_info(mat)
        for img in (info['diffuse'], info['normal']):
            if img is not None and 0 in img.size:  # file missing or unreadable: Blender draws it pink
                raise ValueError('material %s: image %s cannot be loaded (%s); fix its path in the Image '
                                 'Editor or File > External Data > Find Missing Files'
                                 % (mat.name, img.name, bpy.path.abspath(img.filepath) or 'no file'))
        name = '%s.%s' % (mod_id, clean(mat.name))
        mat['xac_name'] = name
        props = {}
        if info['diffuse']:
            img, fmt = info['diffuse'], 'BC3' if info['alpha'] else 'BC1'
            props['diffuse_map'] = texture(clean(img.name) + ('_diffa' if info['alpha'] else '_diff'), fmt,
                                           lambda: pixels(img))
        if info['normal']:
            img = info['normal']
            props['normal_map'] = texture(clean(img.name) + '_normal', 'BC5', lambda: normal(img))
        else:
            props['normal_map'] = texture('flat_normal', 'BC5', lambda: np.full((64, 64, 4), 128, np.uint8))
        props['smooth_map'] = texture('smooth_%d' % info['smooth'], 'BC1',
                                      lambda: np.full((64, 64, 4), info['smooth'], np.uint8))
        props.update(diffuseStr=1.0, normalStr=1.0, Smoothness=1.0, Metallness=0.0)
        if info['alpha']:  # cut out, like vanilla hair
            materials.append((name, 'p1_hair', 'ALPHA1', props))
        else:
            props.update(AnisoX=0.5, AnisoY=0.5, specularStr=1.0, environmentStr=1.0)
            materials.append((name, 'p1_character', 'NONE', props))

    models = {}
    body_template = catalog.extract(game, target, cache) if replace else x4arm['xac_template']
    for part, template in (('BODY', body_template), ('HEAD', x4arm.get('xac_head_template'))):
        if not parts[part]:
            continue
        with open(template, 'rb') as f:
            a = xac.parse(f.read())
        joined = join_copy(context, parts[part], a['nodes'][a['meshes'][0]['node']]['name'])
        out = os.path.join(cache, '_build_%s_%s.xac' % (mod_id, part.lower()))
        faces = {r.target: r.key for r in x4arm.x4_face if r.key} if part == 'HEAD' else None
        try:  # the mod's material library defines the added materials, so skip that note
            report += [r for r in save(context, out, template, [joined], drop_others=True, keep_lods=False,
                                       face_map=faces) if not r.startswith('added materials')]
        finally:
            me = joined.data
            bpy.data.objects.remove(joined)
            bpy.data.meshes.remove(me)
        with open(out, 'rb') as f:
            models[part] = f.read()

    ext = [('%s/textures/%s.gz' % (assets, k), v[0]) for k, v in textures.items()]
    ext += [('%s/textures/%s-small.gz' % (assets, k), v[1]) for k, v in textures.items()]
    if materials:
        ext.append(('libraries/material_library.xml', library.material_patch(materials).encode()))
    subst = []
    if replace:
        subst.append((target, models['BODY']))
        if target.startswith('extensions/'):
            # ponytail: the in-game test shipped both DLC path forms and one of them worked; drop the
            # unused one once a test pins it down
            subst.append((target[len('extensions/'):], models['BODY']))
        deps = [library.owner(target)]
        description = 'Replaces %s.' % target.rsplit('/', 1)[-1]
    else:
        deps = [library.owner(TEMPLATES[x4arm['x4_skeleton']][0])]
        ext.append(('%s/%s_body.xac' % (assets, mod_id), models['BODY']))
        head = None
        if 'HEAD' in models:
            ext.append(('%s/%s_head.xac' % (assets, mod_id), models['HEAD']))
            head = '%s/%s_head' % (ref, mod_id)
        macro = 'character_%s_macro' % mod_id
        female = x4arm.get('x4_skeleton') == 'FEMALE'
        ext.append(('libraries/character_macros.xml', library.macro_patch(
            macro, female, group['race'], head, '%s/%s_body' % (ref, mod_id)).encode()))
        ext.append(('libraries/charactergroups.xml', library.group_patch(
            s.group, group['selects'], macro, s.weight).encode()))
        deps.append(group['extension'])
        description = 'Adds %s to the %s spawn group.' % (s.mod_name or mod_id, s.group)
        if group['female'] != female:
            report.append('note: %s is a %s group, the skeleton is %s' % (
                s.group, 'female' if group['female'] else 'male', x4arm.get('x4_skeleton', '').lower()))
    for name, entries in (('ext_01', ext), ('subst_01', subst)):
        for stale in (name + '.cat', name + '.dat'):
            if os.path.exists(os.path.join(mod_dir, stale)):
                os.remove(os.path.join(mod_dir, stale))
        if entries:
            catalog.write(mod_dir, name, entries)
    with open(os.path.join(mod_dir, 'content.xml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(library.content_xml(mod_id, s.mod_name or mod_id, description, deps))
    catalog.index(game, refresh=True)
    report.insert(0, '%s: %d materials, %d textures, written to %s' % (mod_id, len(materials), len(textures), mod_dir))
    return report


def group_suffix(group):
    """'terran.manager.female' -> 'terran_manager' (the sex is the skeleton's anyway)."""
    return clean('_'.join(p for p in group.split('.') if p not in ('female', 'male')))


def build_batch(context, x4arm, s, groups, game, cache, save, label=str, mod_root=None):
    """New Character into several spawn groups: one extension per group, Mod ID and name with
    the group as suffix. One group builds exactly like build(). label(group) names it for
    the extension list. mod_root: folder for the mods (default: the game's extensions)."""
    if len(groups) < 2:
        one = types.SimpleNamespace(**{k: getattr(s, k) for k in SETTINGS})
        one.group = groups[0] if groups else s.group
        return build(context, x4arm, one, game, cache, save,
                     mod_dir=mod_root and os.path.join(mod_root, clean(s.mod_id)))
    report, tex_cache = [], {}
    root = mod_root or os.path.join(game, 'extensions')
    if os.path.isdir(os.path.join(root, clean(s.mod_id))):
        report.append('note: %s (an earlier single build) is still installed and adds the character too; '
                      'delete it if the new mods replace it' % clean(s.mod_id))
    for g in groups:
        one = types.SimpleNamespace(**{k: getattr(s, k) for k in SETTINGS})
        one.group, one.mod_id = g, '%s_%s' % (clean(s.mod_id), group_suffix(g))
        one.mod_name = '%s (%s)' % (s.mod_name or clean(s.mod_id), label(g))
        report += build(context, x4arm, one, game, cache, save, mod_dir=os.path.join(root, one.mod_id),
                        tex_cache=tex_cache)
    return report


# ---------------------------------------------------------------------------- import with textures

def import_game(context, game, cache, path, load):
    """Import a game model with its textures; its armature remembers the game path, the
    default for Replace Body. Returns report lines."""
    before = set(context.scene.objects)
    notes = load(context, catalog.extract(game, path, cache))
    new = [o for o in context.scene.objects if o not in before]
    for o in new:
        if o.type == 'ARMATURE':
            o['x4_game_path'] = path
            context.view_layer.objects.active = o
    return notes + ['%d textures' % attach_textures(new, game, cache)]


def import_character(context, game, cache, macro, load):
    """Import a character macro: its torso as the editable model, head, hair and props on the
    same armature for reference (Part = Skip). Returns report lines."""
    models = library.macros(game)[macro]['models']
    idx = catalog.index(game)
    if models.get('torso') not in idx:
        raise ValueError('%s has no torso model in the game files' % macro)
    notes = import_game(context, game, cache, models['torso'], load)
    arm = context.view_layer.objects.active
    arm.name = macro.replace('character_', '').replace('_macro', '')
    missing = []
    for slot, path in sorted(models.items()):
        if slot == 'torso':
            continue
        if path not in idx:
            missing.append(path.rsplit('/', 1)[1])
            continue
        before = set(context.scene.objects)
        import_game(context, game, cache, path, load)
        new = set(context.scene.objects) - before
        for o in new:  # re-home the meshes onto the torso armature (same skeleton, same bone names)
            if o.type == 'MESH':
                o.parent = arm
                o.modifiers['Armature'].object = arm
                o.x4_part = 'SKIP'
                o.name = '%s: %s' % (slot, o.name)
                for c in list(o.users_collection):
                    c.objects.unlink(o)
                arm.users_collection[0].objects.link(o)
        for o in new:
            if o.type == 'ARMATURE':
                coll, data = o.users_collection[0], o.data
                bpy.data.objects.remove(o)
                bpy.data.armatures.remove(data)
                bpy.data.collections.remove(coll)
    context.view_layer.objects.active = arm
    return notes + ['not found: ' + ', '.join(missing)] if missing else notes


def attach_textures(objs, game, cache):
    """Give imported X4 materials their diffuse textures from the game's material libraries."""
    lib = library.material_textures(game)
    idx = catalog.index(game)
    found = 0
    for mat in {s.material for o in objs for s in o.material_slots if s.material}:
        path = lib.get(mat.get('xac_name', mat.name))
        if not path:
            continue
        key = path.replace('\\', '/').lower()
        gz = next((key + e for e in ('.gz', '.dds') if key + e in idx), None)
        if gz is None:
            continue
        local = os.path.join(cache, 'textures', clean(key) + '.dds')
        if not os.path.isfile(local):
            os.makedirs(os.path.dirname(local), exist_ok=True)
            data = catalog.read(game, gz)
            if gz.endswith('.gz'):
                import gzip
                data = gzip.decompress(data)
            with open(local, 'wb') as f:
                f.write(data)
        img = bpy.data.images.load(local, check_existing=True)
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            continue
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        tex.location = bsdf.location.x - 300, bsdf.location.y
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        found += 1
    return found

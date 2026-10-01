"""End-to-end check of the character tools without the UI.

usage: blender -b --factory-startup AVATAR.blend -P tools/character_test.py -- GAME_DIR OUT_DIR [DROP_MATERIAL ...]
Fits the avatar (female skeleton), builds a 'new character' mod into OUT_DIR (not the game),
re-reads everything it wrote, imports one vanilla body with textures, and renders
OUT_DIR/preview_fit.png and OUT_DIR/preview_import.png. Exits non-zero on failure.
"""
import gzip
import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import io_scene_x4_xac as addon  # noqa: E402
from io_scene_x4_xac import catalog, character, xac  # noqa: E402

args = sys.argv[sys.argv.index('--') + 1:]
game, out, drop = args[0], args[1], set(args[2:])
cache = os.path.join(out, 'cache')
os.makedirs(cache, exist_ok=True)
addon.register()


def render(objs, path, z=0.95, scale=2.3):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.color_type = 'TEXTURE'
    scene.render.resolution_x, scene.render.resolution_y = 500, 700
    for o in scene.objects:
        o.hide_render = o not in objs
    cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type, cam.data.ortho_scale = 'ORTHO', scale
    cam.location, cam.rotation_euler = (0, -5, z), (1.5708, 0, 0)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def read_cat(folder, name):
    entries, off = [], 0
    with open(os.path.join(folder, name + '.dat'), 'rb') as dat:
        for line in open(os.path.join(folder, name + '.cat'), encoding='utf-8'):
            p, size = line.rstrip('\n').rsplit(' ', 3)[:2]
            dat.seek(off)
            entries.append((p, dat.read(int(size))))
            off += int(size)
    return entries


# drop faces of materials X4 cannot use (e.g. expression overlays)
for o in bpy.data.objects:
    if o.type == 'MESH' and drop:
        idx = [i for i, m in enumerate(o.data.materials) if m and m.name in drop]
        if idx:
            bm = bmesh.new()
            bm.from_mesh(o.data)
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in idx], context='FACES')
            bm.to_mesh(o.data)
            bm.free()

for o in bpy.data.objects:  # an FBX import can leave blink on; fit must still get a neutral face
    if o.type == 'MESH' and o.data.shape_keys and 'まばたき' in o.data.shape_keys.key_blocks:
        o.data.shape_keys.key_blocks['まばたき'].value = 1.0
for o in bpy.data.objects:  # a pinned shape key preview (shows only the active key) must not flatten the keys
    if o.type == 'MESH' and o.data.shape_keys and 'vrc.v_aa' in o.data.shape_keys.key_blocks:
        o.show_only_shape_key = True
        o.active_shape_key_index = o.data.shape_keys.key_blocks.find('vrc.v_aa')
av = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
print('recognised:', {k: v for k, v in character.recognise(av)['fit'].items() if v and 'Finger' not in v})
arm, report = character.fit(bpy.context, av, 'FEMALE', 0.6, game, cache, addon.load)
print('\n'.join('fit: ' + r for r in report))
if 'line' in bpy.data.materials:  # exercise the cutout path (Haishima's line art)
    bpy.data.materials['line'].x4_alpha = 'CUTOUT'
s = arm.x4mod  # build settings live on each character
s.mod_id, s.mod_name, s.mode, s.group, s.weight = 'tooltest', 'Tool test', 'NEW', 'terran.service.female', 9
mod_dir = os.path.join(out, 'tooltest')
report = character.build(bpy.context, arm, s, game, cache, addon.save, mod_dir=mod_dir)
print('\n'.join('build: ' + r for r in report))

# a material named after another mod's material (e.g. from an earlier build) is converted, not reused
from io_scene_x4_xac import library  # noqa: E402
mod_only = sorted(set(library.material_textures(game)) - set(library.material_textures(game, vanilla=True)))
if mod_only and 'huku' in bpy.data.materials:
    bpy.data.materials['huku']['xac_name'] = mod_only[0]
    s.mod_id = 'tooltest_mat'
    character.build(bpy.context, arm, s, game, cache, addon.save, mod_dir=os.path.join(out, 'tooltest_mat'))
    body = xac.parse(dict(read_cat(os.path.join(out, 'tooltest_mat'), 'ext_01'))['assets/characters/tooltest_mat/tooltest_mat_body.xac'])
    used = sorted({body['materials'][x['mat']] for m in body['meshes'] for x in m['subs']})
    print('material check: %s -> %s' % (mod_only[0], used))
    mat_ok = all(u.startswith('tooltest_mat.') for u in used)
    s.mod_id = 'tooltest'
else:
    mat_ok = True

# batch: one mod per spawn group, id and name suffixed, textures converted once for all
from io_scene_x4_xac import dds, panel  # noqa: E402
encode, encoded = dds.x4_texture, []
dds.x4_texture = lambda img, fmt: encoded.append(fmt) or encode(img, fmt)
batch_groups = ['terran.service.female', 'terran.manager.female']
report = character.build_batch(bpy.context, arm, s, batch_groups, game, cache, addon.save,
                               label=panel.group_label, mod_root=os.path.join(out, 'batch'))
dds.x4_texture = encode
print('\n'.join('batch: ' + r for r in report))
batch_ok = len(encoded) == 7
for g in batch_groups:
    mid = 'tooltest_' + character.group_suffix(g)
    folder = os.path.join(out, 'batch', mid)
    content = open(os.path.join(folder, 'content.xml'), encoding='utf-8').read()
    ext = dict((p, d) for p, d in read_cat(folder, 'ext_01'))
    macros, groups_xml = ext['libraries/character_macros.xml'].decode(), ext['libraries/charactergroups.xml'].decode()
    batch_ok &= ('character_%s_macro' % mid in macros and "@name='%s'" % g in groups_xml and
                 'name="Tool test (%s)"' % panel.group_label(g) in content)
    print('batch: %s ok so far %s' % (mid, batch_ok))
print('batch: textures converted %d times for %d mods' % (len(encoded), len(batch_groups)))
render([o for o in bpy.context.scene.objects if o.type == 'MESH'], os.path.join(out, 'preview_fit.png'))
s.mode, s.target = 'REPLACE', 'extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_f_crew_uniform_01.xac'
report = character.build(bpy.context, arm, s, game, cache, addon.save, mod_dir=os.path.join(out, 'tooltest_replace'))
print('\n'.join('replace: ' + r for r in report))
with open(os.path.join(out, 'tooltest_replace', 'subst_01.cat'), encoding='utf-8') as f:
    print('replace: subst_01.cat ->', [line.split(' ')[0] for line in f])
s.target = 'extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_m_crew_uniform_01.xac'
try:
    character.build(bpy.context, arm, s, game, cache, addon.save, mod_dir=os.path.join(out, 'tooltest_bad'))
    print('FAIL: female fit accepted into a male body')
    failed_skeleton = True
except ValueError as e:
    print('skeleton check:', e)
    failed_skeleton = False

# re-read what was written
failed = failed_skeleton or not batch_ok or not mat_ok
entries = read_cat(mod_dir, 'ext_01')
for p, d in entries:
    if p.endswith('.xac'):
        a = xac.parse(d)
        print('model %s: %d meshes, materials %s' % (p, len(a['meshes']),
                                                     sorted({a['materials'][s['mat']] for m in a['meshes'] for s in m['subs']})))
        if p.endswith('_head.xac'):  # the avatar's blink, emotions and visemes became X4 face targets
            shaped = {t['name'].rsplit('.', 1)[1]: t['phonemes'] for t in a['morphs'] if t['defs']}
            print('face targets with shapes:', sorted(shaped))
            failed |= not {'mt_blink', 'mt_smile', 'phon_aa', 'phon_m'} <= set(shaped) or shaped.get('phon_aa') != 0x4
    elif p.endswith('.gz'):
        dd = gzip.decompress(d)
        failed |= dd[:4] != b'DDS '
    else:
        print('--- %s\n%s' % (p, d.decode()[:600]))
print('%d files in the mod' % len(entries))

# import a vanilla body with its textures
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
path = 'extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_f_crew_uniform_01.xac'
notes = character.import_game(bpy.context, game, cache, path, addon.load)
print('import:', notes)
failed |= notes[-1] == '0 textures'
render([o for o in bpy.context.scene.objects if o.type == 'MESH'], os.path.join(out, 'preview_import.png'))

# and back: edit the imported body, rebuild it over the file it came from
game_arm = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE')
body = max((o for o in bpy.context.scene.objects if o.type == 'MESH'), key=lambda o: len(o.data.vertices))
for v in body.data.vertices:  # a visible edit: 20% wider
    v.co.x *= 1.2
g = game_arm.x4mod
g.mod_id, g.mode = 'tooltest_back', 'REPLACE'
back = os.path.join(out, 'tooltest_back')
report = character.build(bpy.context, game_arm, g, game, cache, addon.save, mod_dir=back)
print('\n'.join('back: ' + r for r in report))
subst = read_cat(back, 'subst_01')
a = xac.parse(subst[0][1])
mats = sorted({a['materials'][x['mat']] for m in a['meshes'] for x in m['subs']})
print('back: subst', [p for p, _ in subst], 'materials', mats)
failed |= subst[0][0] != path or any(m.startswith('tooltest.') for m in mats)
failed |= os.path.exists(os.path.join(back, 'ext_01.cat'))  # nothing to add: vanilla materials only

# two characters with one mod id would overwrite each other's extension: refused
twin = game_arm.copy()
bpy.context.scene.collection.objects.link(twin)
twin.x4mod.mod_id = 'tooltest_back'
try:
    character.build(bpy.context, game_arm, g, game, cache, addon.save, mod_dir=os.path.join(out, 'tooltest_twin'))
    print('FAIL: duplicate mod id accepted')
    failed = True
except ValueError as e:
    print('mod id check:', e)
bpy.data.objects.remove(twin)

# a full character (head, uniform, hair on one armature) from a spawn group's macro
import re  # noqa: E402
from io_scene_x4_xac import library, panel  # noqa: E402
groups = panel.import_items(game)  # the grouped Import from Game menu
print('import menu:', {k: len(v) for k, v in groups.items()}, sorted(groups['CHARACTER'])[:6])
failed |= not all(groups.values()) or 'Terran Female' not in groups['CHARACTER'] or     max(len(v) for v in groups.values()) > panel.GROUP_SLOTS


class Layout:  # stands in for Blender's menu layout, which needs a UI to exist
    def __init__(self):
        self.menus, self.ops = [], []

    def menu(self, idname, text=''):
        self.menus.append((idname, text))

    def column_flow(self, columns=1):
        return self

    def operator(self, idname, text=''):
        self.ops.append(types.SimpleNamespace(text=text))
        return self.ops[-1]


import types  # noqa: E402
kind_menu, group_menu = types.SimpleNamespace(layout=Layout()), types.SimpleNamespace(layout=Layout())
panel.draw_kind(kind_menu, bpy.context, 'BODY')
panel.draw_group(group_menu, bpy.context, 0)
print('uniform menu:', kind_menu.layout.menus[:4], '->', [(o.text, o.path) for o in group_menu.layout.ops[:2]])
failed |= not group_menu.layout.ops or not all(o.path.endswith('.xac') and '/bodies/' in o.path for o in group_menu.layout.ops)
items = panel.group_items(g, bpy.context)
print('groups dropdown: %d entries, columns %s' % (len(items), [i[1] for i in items if not i[0]]))
failed |= 'terran.service.female' not in [i[0] for i in items]
values = [i[4] for i in items]  # Blender menus hold enum values in float32: must stay exact
failed |= len(set(values)) != len(values) or any(v >= 2 ** 24 for v in values)
failed |= not panel.group_items(g, None)  # Blender may call it without a context
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
macro = re.search(r'macro="([^"]+)"', library.groups(game)['terran.service.female']['selects'][0]).group(1)
notes = character.import_character(bpy.context, game, cache, macro, addon.load)
arm = bpy.context.view_layer.objects.active
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
print('character %s: %s, armatures %d, meshes %s' % (macro, notes, sum(o.type == 'ARMATURE' for o in bpy.data.objects),
                                                   [(o.name, o.x4_part) for o in meshes]))
failed |= any(o.find_armature() != arm for o in meshes) or not any(o.x4_part == 'SKIP' for o in meshes)
render(meshes, os.path.join(out, 'preview_character.png'))
sys.exit(1 if failed else 0)

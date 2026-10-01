"""Build the in-game test mod: re-export character bodies with a marker post on the back.

usage: blender -b --factory-startup -P tools/make_test_mod.py -- FILE.xac [...]
FILE is an extracted original under tools/../extracted, so its path below that directory is
the in-game path the mod overrides (see extract.py). The files are packed into subst_01.cat,
the catalog type every installed mod that replaces vanilla files uses (loose files and
ext_01.cat were tried first and did nothing). ext_01.cat carries a sentinel diff whose
"No matching node" error in debug.log proves the extension loaded. A body that looks
normal in game means the override did not load.
"""
import hashlib
import os
import sys
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
import io_scene_x4_xac as addon  # noqa: E402

SOURCE = os.path.join(ROOT, 'extracted')
MOD = r'D:\Steam\steamapps\common\X4 Foundations\extensions\xac_test'
BONE = 'Bip01 Spine2'
CONTENT = '''<?xml version="1.0" encoding="utf-8"?>
<content id="xac_test" version="100" name="XAC Test" description="Custom character model test: every body carries a marker post on the back." author="Damien" date="2026-09-30" save="0">
  <dependency id="ego_dlc_terran" optional="false" />
</content>
'''
SENTINEL = b'''<?xml version="1.0" encoding="utf-8"?>
<diff>
  <!-- xac_test sentinel: matches nothing on purpose, so debug.log proves the extension loaded -->
  <replace sel="/macros/macro[@name='xac_test_sentinel']/@class">npc</replace>
</diff>
'''


def add_marker(obj, arm):
    """A 12 x 12 x 90 cm post behind the upper spine (characters face -Y), rigid on BONE."""
    centre = arm.data.bones[BONE].head_local + Vector((0, 0.25, 0.3))
    group = (obj.vertex_groups.get(BONE) or obj.vertex_groups.new(name=BONE)).index
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    deform = bm.verts.layers.deform.verify()
    cube = bmesh.ops.create_cube(bm, size=1.0, calc_uvs=True,
                                 matrix=Matrix.Translation(centre) @ Matrix.Diagonal((0.12, 0.12, 0.9, 1.0)))
    for v in cube['verts']:
        v[deform][group] = 1.0
    bm.to_mesh(obj.data)
    bm.free()


def pack(name, entries):
    """Write NAME.cat/.dat. Index lines are 'virtual path, size, mtime, md5'."""
    index, blobs = [], []
    for path, data in entries:
        index.append('%s %d %d %s' % (path, len(data), int(time.time()), hashlib.md5(data).hexdigest()))
        blobs.append(data)
    with open(os.path.join(MOD, name + '.cat'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(index) + '\n')
    with open(os.path.join(MOD, name + '.dat'), 'wb') as f:
        f.write(b''.join(blobs))


addon.register()
os.makedirs(MOD, exist_ok=True)
with open(os.path.join(MOD, 'content.xml'), 'w', encoding='utf-8') as f:
    f.write(CONTENT)
entries, failed = [], []
for path in sys.argv[sys.argv.index('--') + 1:]:
    virtual = os.path.relpath(os.path.abspath(path), SOURCE).replace('\\', '/')
    out = os.path.join(MOD, 'staging.xac')
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon.load(bpy.context, path)
        objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        # the biggest mesh is a torso or boot piece: always drawn, and far below the 34-bone
        # limit, so the marker's extra bone cannot push a submesh over it
        target = max(objects, key=lambda o: len(o.data.vertices))
        add_marker(target, target.find_armature())
        notes = addon.save(bpy.context, out, path, objects, drop_others=False, keep_lods=False)
        with open(out, 'rb') as f:
            entries.append((virtual, f.read()))
        print('==', virtual, '| marker on', target.name, '|', '; '.join(notes))
    except Exception as e:  # a body we cannot rebuild should not stop the test mod
        failed.append('%s: %s' % (virtual, e))
        print('!!', virtual, '|', e)
if os.path.exists(os.path.join(MOD, 'staging.xac')):
    os.remove(os.path.join(MOD, 'staging.xac'))
# Replacing an existing file takes a subst catalog; ext catalogs only add files. For DLC files
# the right path form is not proven yet, so ship both the root-relative one and the
# extensions/-less one terran_kitbashed_capitals uses.
alt = [(p[len('extensions/'):], d) for p, d in entries if p.startswith('extensions/')]
pack('subst_01', entries + alt)
pack('ext_01', [('libraries/character_macros.xml', SENTINEL)])
print('packed %d files into %s\\subst_01.cat; %d failed' % (len(entries), MOD, len(failed)))
for f in failed:
    print('  failed:', f)

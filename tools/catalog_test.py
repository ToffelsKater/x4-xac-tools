"""The catalog index follows mods being added, rewritten and deleted while Blender runs.

usage: python tools/catalog_test.py   (no Blender needed; builds a tiny fake game folder)
"""
import os
import shutil
import sys
import tempfile
import time
import types

pkg = types.ModuleType('x4pkg')  # load the add-on modules without bpy
pkg.__path__ = [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'io_scene_x4_xac')]
sys.modules['x4pkg'] = pkg
from x4pkg import catalog  # noqa: E402

game = tempfile.mkdtemp(prefix='x4cat_')
try:
    catalog.write(game, '01', [('libraries/base.xml', b'<base/>')])
    mod = os.path.join(game, 'extensions', 'lina')
    catalog.write(mod, 'ext_01', [('libraries/material_library.xml', b'<old/>')])
    open(os.path.join(mod, 'content.xml'), 'w').write('<content id="lina_test_mod"/>')
    key = 'extensions/lina/libraries/material_library.xml'
    assert catalog.read(game, key) == b'<old/>'

    time.sleep(1.1)  # past the once-a-second disk check
    catalog.write(mod, 'ext_01', [('libraries/material_library.xml', b'<new version/>')])
    assert catalog.read(game, key) == b'<new version/>', 'rewritten mod not picked up'

    time.sleep(1.1)
    shutil.rmtree(mod)  # the user deletes the mod folder
    assert key not in catalog.index(game), 'deleted mod still indexed'
    assert 'libraries/base.xml' in catalog.index(game)
    print('catalog index follows added, rewritten and deleted mods: OK')
finally:
    shutil.rmtree(game, ignore_errors=True)

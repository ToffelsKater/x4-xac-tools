"""Import each .xac with the add-on, export it unchanged, and check the geometry survived.

usage: blender -b --factory-startup -P tools/roundtrip_test.py -- FILE.xac [...]
Exits non-zero on a mismatch. Exported files go to %TEMP%/xac_roundtrip/.
"""
import os
import sys
import tempfile

import bpy

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
import io_scene_x4_xac as addon  # noqa: E402
from io_scene_x4_xac import xac  # noqa: E402


def triangles(a):
    """Per mesh node name: {(material, original vertex ids from the lowest): [corner attributes]}.
    Blender keeps original vertex numbers, so they identify a triangle on both sides."""
    names = [n['name'] for n in a['nodes']]
    out = {}
    for m in a['meshes']:
        tris, start = {}, 0
        skin = a['skins'].get(m['node'], [])
        for s in m['subs']:
            for t in range(0, len(s['idx']), 3):
                ids = [start + i for i in s['idx'][t:t + 3]]
                org = [m['org'][i] for i in ids]
                if len(set(org)) < 3:
                    continue  # degenerate; the importer drops these
                r = org.index(min(org))  # rotate to a canonical start, keep winding
                ids = ids[r:] + ids[:r]
                tris[(a['materials'][s['mat']],) + tuple(m['org'][i] for i in ids)] = [
                    (m['pos'][i], m['uv'][i], m['normal'][i], m['tangent'][i],
                     sorted((names[n], round(w, 3)) for n, w in (skin[m['org'][i]] if skin else []) if w == w))
                    for i in ids]
            start += s['nvert']
        out[names[m['node']]] = tris
    return out


def shapes(a):
    """{(target, mesh node name, original vertex): (position delta, normal delta)}."""
    names = [n['name'] for n in a['nodes']]
    org = {m['node']: m['org'] for m in a['meshes']}
    out = {}
    for t in a['morphs']:
        for d in t['defs']:
            for v, p, n in zip(d['verts'], d['pos'], d['normal']):
                if max(map(abs, p)) > 1e-3:
                    out[(t['name'], names[d['node']], org[d['node']][v])] = (p, n)
    return out


def close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


addon.register()
out_dir = os.path.join(tempfile.gettempdir(), 'xac_roundtrip')
os.makedirs(out_dir, exist_ok=True)
failed = False
for path in sys.argv[sys.argv.index('--') + 1:]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon.load(bpy.context, path)
    objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    out = os.path.join(out_dir, os.path.basename(path))
    notes = addon.save(bpy.context, out, path, objects, drop_others=False, keep_lods=False)
    with open(path, 'rb') as f:
        a1 = xac.parse(f.read())
    with open(out, 'rb') as f:
        a2 = xac.parse(f.read())
    before, after = triangles(a1), triangles(a2)
    print('==', os.path.basename(path), '|', '; '.join(notes))
    if a1['morphs']:  # face targets: same names and phoneme bits, same moving vertices and deltas
        s1, s2 = shapes(a1), shapes(a2)
        # deltas right at the 1e-3 cutoff may land on either side of it after re-quantising
        pos = sum(close(v[0], s2.get(k, ((0, 0, 0),))[0], 0.01) for k, v in s1.items())
        nrm = sum(k in s2 and close(v[1], s2[k][1], 0.1) for k, v in s1.items())
        meta = [(t['name'], t['phonemes'], t['range']) for t in a1['morphs']] == \
            [(t['name'], t['phonemes'], t['range']) for t in a2['morphs']]
        ok = meta and pos == len(s1) and len(s2) <= len(s1) * 1.02
        failed |= not ok
        print('  %-28s %s targets %d, moving vertices %d/%d, positions %d, normals within 0.1: %d' % (
            'face targets', 'OK ' if ok else 'BAD', len(a1['morphs']), len(s2), len(s1), pos, nrm))
    for node, tris in before.items():
        tris2 = after[node]
        shared = [(c, c2) for k in tris if k in tris2 for c, c2 in zip(tris[k], tris2[k])]
        good = dict(
            pos=sum(close(c[0], c2[0], 1e-3) for c, c2 in shared),
            uv=sum(close(c[1], c2[1], 1e-5) for c, c2 in shared),
            normal=sum(dot(c[2], c2[2]) > 0.999 for c, c2 in shared),
            # vanilla weights can sum to 1.001; the exporter normalizes them
            weights=sum([n for n, _ in c[4]] == [n for n, _ in c2[4]] and
                        close([w for _, w in c[4]], [w for _, w in c2[4]], 0.0025) for c, c2 in shared))
        tangent = sum(dot(c[3][:3], c2[3][:3]) > 0.9 for c, c2 in shared)
        sign = sum(c[3][3] == c2[3][3] for c, c2 in shared)
        ok = tris.keys() == tris2.keys() and all(v == len(shared) for v in good.values())
        failed |= not ok
        print('  %-28s %s tris %d/%d  corners %d  %s  tangent dir %d sign %d' % (
            node, 'OK ' if ok else 'BAD', len(tris.keys() & tris2.keys()), len(tris), len(shared),
            ' '.join('%s %d' % kv for kv in good.items()), tangent, sign))
sys.exit(1 if failed else 0)

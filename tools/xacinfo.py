"""Print what an .xac contains; optionally render its meshes front and side (needs Pillow).

usage: python xacinfo.py FILE.xac [--png OUT.png]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'io_scene_x4_xac'))
import xac  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('file')
p.add_argument('--png')
args = p.parse_args()
with open(args.file, 'rb') as f:
    a = xac.parse(f.read())
names = [n['name'] for n in a['nodes']]
print('source   ', a['source'], '|', a['file'])
print('nodes    ', len(names), '(%d Bip01 bones)' % sum(n.startswith('Bip01') for n in names))
print('materials', len(a['materials']))
for m in a['meshes']:
    skin = a['skins'].get(m['node'], [])
    print('mesh %-28s verts %5d (org %5d)  tris %5d  bones %2d  max weights/vert %d' % (
        names[m['node']], len(m['pos']), m['norg'], sum(len(s['idx']) for s in m['subs']) // 3,
        len({n for ws in skin for n, _ in ws}), max(map(len, skin), default=0)))
    for s in m['subs']:
        print('     material %s' % a['materials'][s['mat']])
print('LODs     ', a['lods'], '| morph targets', a['morph'])

if args.png:
    from PIL import Image, ImageDraw
    tris = []  # (mesh number, 3 positions)
    for k, m in enumerate(a['meshes']):
        start = 0
        for s in m['subs']:
            tris += [(k, [m['pos'][start + j] for j in s['idx'][i:i + 3]]) for i in range(0, len(s['idx']), 3)]
            start += s['nvert']
    pts = [v for _, t in tris for v in t]
    lo = [min(v[i] for v in pts) for i in range(3)]
    hi = [max(v[i] for v in pts) for i in range(3)]
    size = 800
    scale = (size - 40) / max(hi[i] - lo[i] for i in range(3))
    img = Image.new('RGB', (size * 2, size), 'white')
    draw = ImageDraw.Draw(img)
    colors = [(74, 111, 165), (107, 143, 113), (201, 162, 126), (90, 90, 90), (180, 80, 80), (120, 60, 160)]
    # front view faces the character (its left hand on the right), side view shows its left side
    for x_axis, depth, ox in ((0, 2, size // 2), (2, 0, size + size // 2)):
        for k, t in sorted(tris, key=lambda kt: sum(v[depth] for v in kt[1]) * (1 if depth == 0 else -1)):
            draw.polygon([(ox + (v[x_axis] - (lo[x_axis] + hi[x_axis]) / 2) * scale,
                           size - 20 - (v[1] - lo[1]) * scale) for v in t],
                         fill=colors[k % len(colors)], outline=(30, 30, 30))
    img.save(args.png)
    print('wrote', args.png)

"""Extract files from X4's .cat/.dat archives.

usage: python extract.py REGEX [--game DIR] [--out DIR] [--list]
  python extract.py "terran/bodies/.*\\.xac$" --out D:/X4Extract

REGEX is matched (case-insensitive) against the in-game path, e.g.
extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_m_crew_uniform_01.xac.
Files land at that path under --out. Later archives override earlier ones, like in game.
"""
import argparse
import glob
import os
import re

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('pattern')
p.add_argument('--game', default=r'D:\Steam\steamapps\common\X4 Foundations')
p.add_argument('--out', default='extracted')
p.add_argument('--list', action='store_true', help='only print matching paths')
args = p.parse_args()
rx = re.compile(args.pattern, re.I)

cats = [(c, '') for c in sorted(glob.glob(os.path.join(args.game, '*.cat')))]
cats += [(c, 'extensions/%s/' % os.path.basename(os.path.dirname(c)))
         for c in sorted(glob.glob(os.path.join(args.game, 'extensions', '*', '*.cat')))]
count = 0
for cat, prefix in cats:
    if cat.endswith('_sig.cat'):
        continue
    offset = 0
    with open(cat, encoding='utf-8', errors='replace') as index, open(cat[:-4] + '.dat', 'rb') as dat:
        for line in index:
            path, size = line.rstrip('\n').rsplit(' ', 3)[:2]
            size = int(size)
            if rx.search(prefix + path):
                count += 1
                if args.list:
                    print(prefix + path, size)
                else:
                    dst = os.path.join(args.out, prefix + path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    dat.seek(offset)
                    with open(dst, 'wb') as f:
                        f.write(dat.read(size))
            offset += size
print(count, 'files' + ('' if args.list else ' extracted to ' + os.path.abspath(args.out)))

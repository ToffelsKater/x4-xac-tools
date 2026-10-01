"""Find collision shapes (.jcs) that crash X4 9.00.

usage: python jcscheck.py [--game DIR]

X4 stores collision as Jolt Physics shapes. 9.00 changed the StaticCompound layout: older
files carry a u64 node count right after the 28-byte sub-shapes, 9.00 files start the node
array there. The 9.00 loader desyncs on an old file, reads a garbage shape sub-type and
crashes (access violation at X4.exe+0x13C1645 in the 9.00 build). Egosoft re-exported its own
old files for 9.00 (see ego_dlc_ventures/ext_03_diff_v900.cat); mods built for 8.x or earlier
were not. The check flags a compound whose word after the sub-shapes is a small count; no
9.00 game or DLC file trips it.
"""
import argparse
import glob
import os
import struct
from collections import defaultdict


def old_layout(d):
    """Reason string if d is an old-layout StaticCompound, else None."""
    if len(d) < 70 or d[4] != 7:  # u32 shape id, u8 sub-type (7 = StaticCompound)
        return None
    n, = struct.unpack_from('<Q', d, 53)  # after u64 user data and 10 floats
    o = 61 + n * 28
    if n > 100000 or o + 8 > len(d):
        return 'implausible sub-shape count %d' % n
    k, = struct.unpack_from('<Q', d, o)
    return 'u64 node count %d after %d sub-shapes' % (k, n) if 0 < k <= 4 * n else None


def demo():
    good = struct.pack('<IBQ10fQ', 0, 7, 0, *[0.0] * 10, 2) + b'\0' * 56 + b'\x00\x3c' * 8
    bad = struct.pack('<IBQ10fQ', 0, 7, 0, *[0.0] * 10, 2) + b'\0' * 56 + struct.pack('<Q', 1) + b'\0' * 8
    assert old_layout(good) is None and old_layout(bad)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--game', default=r'D:\Steam\steamapps\common\X4 Foundations')
    args = p.parse_args()
    cats = [(c, 'base game') for c in sorted(glob.glob(os.path.join(args.game, '*.cat')))]
    cats += [(c, os.path.basename(os.path.dirname(c)))
             for c in sorted(glob.glob(os.path.join(args.game, 'extensions', '*', '*.cat')))]
    found = defaultdict(dict)  # extension -> path -> reason; later catalogs replace earlier ones
    for cat, ext in cats:
        if cat.endswith('_sig.cat'):
            continue
        offset = 0
        with open(cat, encoding='utf-8', errors='replace') as index, open(cat[:-4] + '.dat', 'rb') as dat:
            for line in index:
                path, size = line.rstrip('\n').rsplit(' ', 3)[:2]
                size = int(size)
                if path.endswith('.jcs'):
                    dat.seek(offset)
                    found[ext][path] = old_layout(dat.read(size))
                offset += size
    bad_total = 0
    for ext in sorted(found):
        bad = {p: r for p, r in found[ext].items() if r}
        bad_total += len(bad)
        if bad:
            print('%s: %d of %d shapes use the old layout' % (ext, len(bad), len(found[ext])))
            for path, reason in sorted(bad.items())[:3]:
                print('    %s  (%s)' % (path, reason))
    print('%d crash-prone shapes' % bad_total)


if __name__ == '__main__':
    demo()
    main()

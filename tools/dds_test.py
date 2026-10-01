"""Check io_scene_x4_xac/dds.py: encode images, decode them with Pillow, compare.

usage: python tools/dds_test.py IMAGE [...]   (needs Pillow)
Fails if a level does not decode or the top level's PSNR is below 30 dB.
"""
import gzip
import importlib.util
import io
import os
import sys
import time

import numpy as np
from PIL import Image

spec = importlib.util.spec_from_file_location(
    'dds', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'io_scene_x4_xac', 'dds.py'))
dds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dds)


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


failed = False
for path in sys.argv[1:]:
    src = np.asarray(Image.open(path).convert('RGBA'))
    for fmt, channels in (('BC1', slice(0, 3)), ('BC3', slice(0, 4)), ('BC5', slice(0, 2))):
        t = time.time()
        big, small = dds.x4_texture(src, fmt)
        took = time.time() - t
        im = Image.open(io.BytesIO(gzip.decompress(big)))
        im.load()
        got = np.asarray(im.convert('RGBA'))
        Image.open(io.BytesIO(gzip.decompress(small))).load()
        q = psnr(src[..., channels], got[..., channels])
        failed |= q < 30
        print('%-24s %s  %.1f dB  %.1fs  %d KB' % (os.path.basename(path), fmt, q, took, len(big) // 1024))
sys.exit(1 if failed else 0)

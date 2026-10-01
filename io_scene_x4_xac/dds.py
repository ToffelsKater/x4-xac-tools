"""Write X4 textures: DDS with a full mip chain, BC1/BC3/BC5 blocks encoded with numpy.

X4 ships textures as gzipped DDS (`name.gz`) plus a 1/8-size `name-small.gz`. The header must
look like vanilla's: X4's loader (gli) divides by the DX10 array size, so a 0 there crashes the
game. No bpy, so it runs (and is tested) outside Blender.
"""
import gzip
import struct

import numpy as np

FOURCC = {'BC1': b'DXT1', 'BC3': b'DXT5', 'BC5': b'DX10'}
BATCH = 32768  # blocks per numpy pass; bounds memory at ~100 MB


def _blocks(img):
    """HxWxC array -> (N, 16, C) float32 of 4x4 blocks, row-major, edges padded."""
    h, w, c = img.shape
    img = np.pad(img, ((0, -h % 4), (0, -w % 4), (0, 0)), mode='edge')
    bh, bw = img.shape[0] // 4, img.shape[1] // 4
    return img.reshape(bh, 4, bw, 4, c).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, c).astype(np.float32)


def _to565(c):
    c = np.clip(np.rint(c * np.array([31, 63, 31]) / 255), 0, [31, 63, 31]).astype(np.uint64)
    return c[:, 0] << 11 | c[:, 1] << 5 | c[:, 2]


def _from565(v):
    return np.stack([(v >> 11 & 31) * 255 / 31, (v >> 5 & 63) * 255 / 63, (v & 31) * 255 / 31], 1).astype(np.float32)


def _color(px):
    """(N,16,3) -> (N,) uint64 BC1 blocks, endpoints on each block's principal axis."""
    mean = px.mean(1, keepdims=True)
    d = px - mean
    cov = np.einsum('nki,nkj->nij', d, d)
    axis = np.ones((len(px), 3), np.float32)
    for _ in range(6):  # power iteration
        axis = np.einsum('nij,nj->ni', cov, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-12
    t = np.einsum('nki,ni->nk', d, axis)
    c0 = _to565(mean[:, 0] + axis * t.max(1, keepdims=True))
    c1 = _to565(mean[:, 0] + axis * t.min(1, keepdims=True))
    c0, c1 = np.maximum(c0, c1), np.minimum(c0, c1)  # c0 > c1 selects the 4-colour mode
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    idx = ((px[:, :, None] - pal[:, None]) ** 2).sum(-1).argmin(-1).astype(np.uint64)
    idx[c0 == c1] = 0
    bits = (idx << (2 * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    return c0 | c1 << 16 | bits << 32


def _single(a):
    """(N,16) -> (N,) uint64 BC4 blocks (8-value mode)."""
    a0, a1 = np.rint(a.max(1)), np.rint(a.min(1))
    f = np.array([7, 0, 6, 5, 4, 3, 2, 1], np.float32) / 7  # weight of a0 per index
    pal = a0[:, None] * f + a1[:, None] * (1 - f)
    idx = np.abs(a[:, :, None] - pal[:, None]).argmin(-1).astype(np.uint64)
    idx[a0 == a1] = 0
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    return a0.astype(np.uint64) | a1.astype(np.uint64) << 8 | bits << 16


def encode(img, fmt):
    """One mip level. img: HxWx4 uint8, top row first. fmt: BC1, BC3 or BC5 (normal in R, G)."""
    b = _blocks(img)
    out = []
    for s in range(0, len(b), BATCH):
        px = b[s:s + BATCH]
        if fmt == 'BC1':
            out.append(_color(px[..., :3])[:, None])
        elif fmt == 'BC3':
            out.append(np.stack([_single(px[..., 3]), _color(px[..., :3])], 1))
        else:
            out.append(np.stack([_single(px[..., 0]), _single(px[..., 1])], 1))
    return np.concatenate(out).astype('<u8').tobytes()


def half(img):
    """2x box downsample (odd sizes repeat their last row/column)."""
    h, w = img.shape[:2]
    f = np.pad(img.astype(np.float32), ((0, h % 2), (0, w % 2), (0, 0)), mode='edge')
    f = (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]) / 4
    return np.rint(f).astype(np.uint8)


def dds(img, fmt):
    """DDS file bytes with every mip level down to 1x1."""
    levels, level = [], img
    while True:
        levels.append(encode(level, fmt))
        if level.shape[0] == 1 and level.shape[1] == 1:
            break
        level = half(level)
    h, w = img.shape[:2]
    header = struct.pack('<4s7I44x', b'DDS ', 124, 0xA1007, h, w, len(levels[0]), 0, len(levels))
    header += struct.pack('<2I4s5I', 32, 0x4, FOURCC[fmt], 0, 0, 0, 0, 0)  # pixel format: FOURCC only
    header += struct.pack('<5I', 0x401008, 0, 0, 0, 0)  # TEXTURE | MIPMAP | COMPLEX
    if fmt == 'BC5':
        header += struct.pack('<5I', 83, 3, 0, 1, 0)  # BC5_UNORM, TEXTURE2D, no flags, array size 1
    return header + b''.join(levels)


def x4_texture(img, fmt):
    """(name.gz bytes, name-small.gz bytes) the way X4 ships a texture."""
    small = img
    for _ in range(3):
        if min(small.shape[:2]) > 4:
            small = half(small)
    return gzip.compress(dds(img, fmt), 6), gzip.compress(dds(small, fmt), 6)

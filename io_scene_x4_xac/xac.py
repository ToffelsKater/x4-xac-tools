"""Read and write X4: Foundations character models (.xac).

The files are EMotion FX 3 actors ("XAC " v1.0, little endian) exported by Egosoft from
3ds Max. Layouts were reverse-engineered from the game files and cross-checked against
Lumberyard's EMotion FX importer. Pure Python (no bpy) so the command-line tools share it.

Coordinates are left-handed: +Y up, characters face -Z, their left hand is at +X, units cm.
"""
import struct

INFO, NODES, MATINFO, MATERIAL, MESH, SKIN, LOD, MORPH = 7, 11, 13, 3, 1, 2, 8, 12
# Vertex layers in the order X4 writes them: (type id, bytes per vertex, deform flag).
# 5 = original vertex number, 0 = position, 1 = normal, 3 = uv, 2 = tangent (xyz + sign).
LAYERS = ((5, 4, 0), (0, 12, 1), (1, 12, 1), (3, 8, 0), (2, 16, 1))
LAYER_KEYS = {0: 'pos', 1: 'normal', 3: 'uv', 2: 'tangent'}
NODE_FIXED = 156    # node record bytes before its name
MAT_FIXED = 84      # std material bytes before its name
MAX_BONES = 57      # most bones per submesh seen in vanilla files (Argon bodies); the engine limit is unknown
MAX_INFLUENCES = 4  # most weights per vertex seen in vanilla files


class Reader:
    def __init__(self, b, o=0):
        self.b, self.o = b, o

    def get(self, fmt):
        v = struct.unpack_from('<' + fmt, self.b, self.o)
        self.o += struct.calcsize('<' + fmt)
        return v

    def string(self):
        n = self.get('I')[0]
        self.o += n
        return self.b[self.o - n:self.o].decode('latin1')


def parse(b):
    """Parse an .xac file into plain dicts. Unknown chunks are only recorded in 'chunks'."""
    if b[:4] != b'XAC ' or b[4:6] != b'\x01\x00':
        raise ValueError('not an X4 .xac file (expected "XAC " v1.0)')
    a = dict(chunks=[], nodes=[], materials=[], meshes=[], skins={}, lods=0, morph=False, morphs=[], source='')
    r = Reader(b, 8)
    while r.o + 12 <= len(b):
        start = r.o
        cid, size, ver = r.get('III')
        end = r.o + size
        if cid == INFO and ver == 2:
            r.o += 16
            a['source'], a['file'] = r.string(), r.string()
        elif cid == NODES and ver == 1:
            count = r.get('II')[0]
            for _ in range(count):
                rec = r.o
                r.o = rec + NODE_FIXED
                a['nodes'].append(dict(
                    name=r.string(), record=rec,
                    rot=struct.unpack_from('<4f', b, rec),  # x, y, z, w
                    pos=struct.unpack_from('<3f', b, rec + 32),
                    parent=struct.unpack_from('<i', b, rec + 76)[0]))
        elif cid == MATERIAL and ver == 2:
            r.o += MAT_FIXED
            a['materials'].append(r.string())
            # quirk: the texture layers trail the chunk and are not counted in its size
            r.o = end
            for _ in range(b[start + 12 + MAT_FIXED - 1]):
                r.o += 28
                r.string()
            end = r.o
        elif cid == MESH and ver == 1:
            node, norg, nvert, _, nsub, nlay = r.get('6I4x')
            m = dict(node=node, norg=norg, subs=[])
            for _ in range(nlay):
                t, sz = r.get('IIxxxx')
                if t == 5:
                    m['org'] = r.get('%dI' % nvert)
                elif t in LAYER_KEYS:
                    w = sz // 4
                    flat = r.get('%df' % (nvert * w))
                    m[LAYER_KEYS[t]] = [flat[i:i + w] for i in range(0, len(flat), w)]
                else:
                    r.o += sz * nvert
            for _ in range(nsub):
                ni, nv, mat, nb = r.get('4I')
                m['subs'].append(dict(mat=mat, nvert=nv, idx=r.get('%dI' % ni), bones=r.get('%dI' % nb)))
            a['meshes'].append(m)
        elif cid == SKIN and ver == 3:
            node, _, ninf = r.get('III4x')
            inf = [r.get('fHxx') for _ in range(ninf)]
            table = [r.get('II') for _ in range((end - r.o) // 8)]
            a['skins'][node] = [[(inf[i][1], inf[i][0]) for i in range(s, s + n)] for s, n in table]
        elif cid == LOD:
            a['lods'] += 1
        elif cid == MORPH and ver == 1:
            a['morph'] = True
            a['morph_lod'], a['morphs'] = parse_morphs(r)
        a['chunks'].append(dict(id=cid, start=start, end=end))
        r.o = end
    if r.o != len(b):
        raise ValueError('chunk walk ended at byte %d of %d' % (r.o, len(b)))
    return a


def parse_morphs(r):
    """Chunk 12 v1 (EMotion FX Actor_MorphTargets): u32 count, u32 lod, then per target
    f32 range min/max, u32 lod, u32 deform count, u32 transform count, u32 phoneme set bits
    (lip sync picks targets by these), name; per deform: u32 mesh node, f32 min/max of the
    position deltas, u32 vertex count, u16x3 positions, u8x3 normals and tangents (-1..1),
    u32 vertex numbers (split vertices, as in the mesh layers); transforms are 60 bytes.
    Returns (lod, [target dicts]) with deltas decoded to float triples (cm)."""
    count, lod = r.get('II')
    out = []
    for _ in range(count):
        lo, hi, tlod, ndef, ntrans, phon = r.get('ffIIII')
        t = dict(name=r.string(), range=(lo, hi), lod=tlod, phonemes=phon, defs=[])
        for _ in range(ndef):
            node, vmin, vmax, nv = r.get('IffI')
            k = (vmax - vmin) / 65535
            pos, nrm, tan = r.get('%dH' % (3 * nv)), r.get('%dB' % (3 * nv)), r.get('%dB' % (3 * nv))
            t['defs'].append(dict(
                node=node, verts=r.get('%dI' % nv),
                pos=[(pos[i] * k + vmin, pos[i + 1] * k + vmin, pos[i + 2] * k + vmin) for i in range(0, 3 * nv, 3)],
                normal=[tuple(x / 127.5 - 1 for x in nrm[i:i + 3]) for i in range(0, 3 * nv, 3)],
                tangent=[tuple(x / 127.5 - 1 for x in tan[i:i + 3]) for i in range(0, 3 * nv, 3)]))
        t['transforms'] = r.b[r.o:r.o + 60 * ntrans]
        r.o += 60 * ntrans
        out.append(t)
    return lod, out


def morph_chunk(lod, targets):
    """Inverse of parse_morphs; targets as it returns them."""
    q8 = lambda v: min(255, max(0, int(round((v + 1) * 127.5))))  # noqa: E731
    out = [struct.pack('<II', len(targets), lod)]
    for t in targets:
        out.append(struct.pack('<ffIIII', t['range'][0], t['range'][1], t['lod'], len(t['defs']),
                               len(t['transforms']) // 60, t['phonemes']) + string(t['name']))
        for d in t['defs']:
            flat = [x for p in d['pos'] for x in p]
            vmin, vmax = (min(flat), max(flat)) if flat else (0.0, 0.0)
            k = 65535 / (vmax - vmin) if vmax > vmin else 0.0
            nv = len(d['verts'])
            out.append(struct.pack('<IffI', d['node'], vmin, vmax, nv))
            out.append(struct.pack('<%dH' % len(flat), *(int(round((x - vmin) * k)) for x in flat)))
            out.append(bytes(q8(x) for p in d['normal'] for x in p))
            out.append(bytes(q8(x) for p in d['tangent'] for x in p))
            out.append(struct.pack('<%dI' % nv, *d['verts']))
        out.append(t['transforms'])
    return chunk(MORPH, 1, b''.join(out))


def chunk(cid, ver, data):
    return struct.pack('<III', cid, len(data), ver) + data


def string(s):
    s = s.encode('latin1')
    return struct.pack('<I', len(s)) + s


def mesh_chunk(node, norg, subs):
    """subs: dicts with mat (index), bones (node indices), verts [(org, pos, normal, uv, tangent4)], tris."""
    verts = [v for s in subs for v in s['verts']]
    nidx = sum(3 * len(s['tris']) for s in subs)
    out = [struct.pack('<6I4x', node, norg, len(verts), nidx, len(subs), len(LAYERS))]
    for t, size, deform in LAYERS:
        out.append(struct.pack('<IIBBxx', t, size, deform, 0))
        if t == 5:
            out.append(struct.pack('<%dI' % len(verts), *(v[0] for v in verts)))
        else:
            k = {0: 1, 1: 2, 3: 3, 2: 4}[t]
            out.append(struct.pack('<%df' % (len(verts) * size // 4), *(x for v in verts for x in v[k])))
    for s in subs:
        idx = [i for tri in s['tris'] for i in tri]
        out.append(struct.pack('<4I%dI%dI' % (len(idx), len(s['bones'])),
                               len(idx), len(s['verts']), s['mat'], len(s['bones']), *idx, *s['bones']))
    return chunk(MESH, 1, b''.join(out))


def skin_chunk(node, weights):
    """weights: one list of (node index, weight) per original vertex."""
    inf, table = [], []
    for ws in weights:
        table.append((len(inf), len(ws)))
        inf.extend(ws)
    out = [struct.pack('<III4x', node, len({n for n, _ in inf}), len(inf))]
    out += [struct.pack('<fHxx', w, n) for n, w in inf]
    out += [struct.pack('<II', *e) for e in table]
    return chunk(SKIN, 3, b''.join(out))


def build(template, meshes, drop_others=False, keep_lods=False):
    """Write a new .xac from a template file with some meshes replaced or added.

    meshes: dicts with node (name), norg, weights (one [(bone name, weight)] list per original
    vertex) and subs (dicts with material (name), verts, tris as in mesh_chunk); optional
    morphs {target name: dict(verts (split vertex numbers), pos, normal, tangent)}. Skeleton,
    materials and meshes that are not replaced are copied from the template.
    Returns (bytes, warnings).
    """
    a = parse(template)
    warn = []
    if len({m['node'] for m in meshes}) != len(meshes):
        raise ValueError('two exported objects use the same mesh node name')
    index = {n['name']: i for i, n in enumerate(a['nodes'])}
    mats = {name: i for i, name in enumerate(a['materials'])}
    new_nodes, new_mats = [], []
    for m in meshes:
        if m['node'] not in index:
            index[m['node']] = len(a['nodes']) + len(new_nodes)
            new_nodes.append(m['node'])
        for s in m['subs']:
            if s['material'] not in mats:
                mats[s['material']] = len(a['materials']) + len(new_mats)
                new_mats.append(s['material'])
    if new_nodes:
        warn.append('added mesh nodes: ' + ', '.join(new_nodes))
    if new_mats:
        warn.append('added materials (define them in your material library): ' + ', '.join(new_mats))

    new_chunks = {}
    for m in meshes:
        node = index[m['node']]
        weights = []
        for ws in m['weights']:
            for n, _ in ws:
                if n not in index:
                    raise ValueError('bone %r is not in the template skeleton' % n)
            weights.append([(index[n], w) for n, w in ws])
        subs = []
        for s in m['subs']:
            bones = sorted({n for v in s['verts'] for n, _ in weights[v[0]]})
            if len(bones) > MAX_BONES:
                warn.append('%s / %s uses %d bones (vanilla max %d); split it by material if it breaks in game'
                            % (m['node'], s['material'], len(bones), MAX_BONES))
            subs.append(dict(s, mat=mats[s['material']], bones=bones))
        new_chunks[node] = (mesh_chunk(node, m['norg'], subs), skin_chunk(node, weights))

    node_of = {c['start']: struct.unpack_from('<I', template, c['start'] + 12)[0]
               for c in a['chunks'] if c['id'] in (MESH, SKIN)}
    template_nodes = set(node_of.values())
    changed = bool(new_chunks) or (drop_others and bool(template_nodes - set(new_chunks)))
    last_mat = max((c['start'] for c in a['chunks'] if c['id'] == MATERIAL), default=None)

    # morph targets: keep the template's names, ranges and phoneme bits (animations and lip
    # sync find targets by those); deltas of replaced or dropped meshes go, exported ones come in
    kept = lambda node: node not in new_chunks and not (drop_others and node in template_nodes)  # noqa: E731
    targets = [dict(t, defs=[d for d in t['defs'] if kept(d['node'])]) for t in a['morphs']]
    by_name = {t['name']: t for t in targets}
    unknown, shaped = set(), set()
    for m in meshes:
        for name, d in m.get('morphs', {}).items():
            if name not in by_name:
                unknown.add(name)
                continue
            by_name[name]['defs'].append(dict(d, node=index[m['node']]))
            shaped.add(name)
    if unknown:
        warn.append('shape keys with no matching target in the template were left out: ' + ', '.join(sorted(unknown)))
    if a['morph'] and changed:
        warn.append('face targets with shapes: %d of %d%s' % (
            sum(1 for t in targets if t['defs']), len(targets), '' if shaped else
            ' (none from the exported meshes: no lip sync or expressions on them)'))

    head, meshes_out, skins_out, tail = [template[:8]], [], [], []
    for c in a['chunks']:
        cid, raw = c['id'], template[c['start']:c['end']]
        if cid == NODES and new_nodes:
            count, roots = struct.unpack_from('<II', template, c['start'] + 12)
            proto = bytearray(template[a['nodes'][0]['record']:a['nodes'][0]['record'] + NODE_FIXED])
            # identity rotation, scale rotation, position and scale; root node without children
            struct.pack_into('<4f4f3f3f', proto, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1)
            struct.pack_into('<iI', proto, 76, -1, 0)
            body = template[c['start'] + 20:c['end']] + b''.join(bytes(proto) + string(n) for n in new_nodes)
            head.append(chunk(NODES, 1, struct.pack('<II', count + len(new_nodes), roots + len(new_nodes)) + body))
        elif cid == MATINFO and new_mats:
            total, std, fx = struct.unpack_from('<3I', template, c['start'] + 12)
            head.append(chunk(MATINFO, 1, struct.pack('<3I', total + len(new_mats), std + len(new_mats), fx)))
        elif cid == MATERIAL:
            head.append(raw)
            if c['start'] == last_mat:
                fixed = bytearray(template[c['start'] + 12:c['start'] + 12 + MAT_FIXED])
                fixed[-1] = 0  # no texture layers; X4 finds textures through the material library
                head += [chunk(MATERIAL, 2, bytes(fixed) + string(n)) for n in new_mats]
        elif cid in (MESH, SKIN):
            node = node_of[c['start']]
            if node in new_chunks:
                raw = new_chunks[node][cid == SKIN]
            if node in new_chunks or not drop_others:
                (meshes_out if cid == MESH else skins_out).append(raw)
        elif cid == LOD and changed and not keep_lods:
            pass
        elif cid == MORPH and changed:
            (tail if meshes_out else head).append(morph_chunk(a['morph_lod'], targets))
        else:
            (tail if meshes_out else head).append(raw)
    for node, (mesh, skin) in new_chunks.items():
        if node not in template_nodes:
            meshes_out.append(mesh)
            skins_out.append(skin)
    if changed and a['lods'] and not keep_lods:
        warn.append('LOD meshes removed; the full mesh is used at every distance')
    data = b''.join(head + meshes_out + skins_out + tail)
    parse(data)  # refuse to hand back a file we cannot read ourselves
    return data, warn

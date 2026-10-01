"""Read X4's character libraries (materials, character macros, spawn groups) and write the
patches a character mod needs. No bpy.

Libraries are merged from the base game and every extension's `libraries/*.xml`; extensions
ship them as <diff> patches, which are read here as plain XML (enough to list what exists).
"""
import re
from xml.sax.saxutils import quoteattr

from . import catalog

HUMAN = ('character_argon_female_01', 'character_argon_male_01')  # the skeleton the fitter targets


def _files(game, name, vanilla=False):
    """Texts of every libraries/<name> in load order, base game first; vanilla: game and DLCs only."""
    idx = catalog.index(game)
    paths = sorted(p for p in idx if p.endswith('libraries/' + name) and
                   (p == 'libraries/' + name or p.startswith('extensions/ego_dlc_' if vanilla else 'extensions/')))
    paths.sort(key=lambda p: p != 'libraries/' + name)
    return [(p, catalog.read(game, p).decode('utf-8', 'replace')) for p in paths]


def owner(path):
    """Extension folder a library path belongs to ('' = base game)."""
    return path.split('/')[1] if path.startswith('extensions/') else ''


def material_textures(game, vanilla=False):
    """{'collection.material': diffuse texture path or None} over all material libraries
    (vanilla: the base game's and DLCs' only)."""
    out = {}
    for _, text in _files(game, 'material_library.xml', vanilla):
        for coll, body in re.findall(r'<collection name="([^"]+)"(.*?)</collection>', text, re.S):
            for mat, props in re.findall(r'<material name="([^"]+)"(.*?)</material>', body, re.S):
                m = re.search(r'name="diffuse_map" value="([^"]+)"', props)
                out['%s.%s' % (coll, mat)] = m and m.group(1)
    return out


def _models(body):
    """{slot: model path} of a macro body; a random choice takes its most likely model."""
    out = {}
    for slot, attrs, inner in re.findall(r'<model type="([^"]+)"([^>]*?)(?:/>|>(.*?)</model>)', body, re.S):
        ref = re.search(r'\bref="([^"]+)"', attrs)
        if ref is None and inner:
            picks = []
            for tag in re.findall(r'<select\b[^>]*>', inner):
                w, r = re.search(r'\bweight="([^"]*)"', tag), re.search(r'\bref="([^"]+)"', tag)
                if r:
                    picks.append((float(w.group(1)) if w else 1.0, r.group(1)))
            ref = picks and max(picks, key=lambda p: p[0])[1]
        else:
            ref = ref and ref.group(1)
        if ref and ref.lower() != 'none':  # 'none' = the random choice picks nothing
            out[slot] = ref.replace('\\', '/').lower() + '.xac'
    return out


def macros(game):
    """{macro name: dict(component, race, female, models)} for character macros, inheritance
    resolved. models: {slot (head, torso, props...): game path}."""
    raw = {}
    for _, text in _files(game, 'character_macros.xml'):
        for name, attrs, body in re.findall(r'<macro name="([^"]+)"([^>]*)>(.*?)</macro>', text, re.S):
            ref = re.search(r'\bref="([^"]+)"', attrs)
            comp = re.search(r'<component ref="([^"]+)"', body)
            ident = re.search(r'<identification[^>]*>', body)
            race = re.search(r'race="([^"]+)"', ident.group(0)) if ident else None
            female = re.search(r'female="([^"]+)"', ident.group(0)) if ident else None
            raw[name] = dict(ref=ref and ref.group(1), component=comp and comp.group(1),
                             race=race and race.group(1), female=female and female.group(1) == 'true',
                             models=_models(body))
    out = {}
    for name in raw:
        m, seen, merged = raw[name], set(), dict(models={})
        while m and id(m) not in seen:
            seen.add(id(m))
            for k in ('component', 'race', 'female'):
                if merged.get(k) is None and m[k] is not None:
                    merged[k] = m[k]
            for slot, path in m['models'].items():
                merged['models'].setdefault(slot, path)
            m = raw.get(m['ref'])
        out[name] = merged
    return out


def groups(game):
    """{group name: dict(selects=[attribute text], extension, female, race)} for the game's
    and DLCs' groups that pick macros, and only those whose macros all use the human skeleton."""
    known = macros(game)
    out = {}
    for path, text in _files(game, 'charactergroups.xml', vanilla=True):
        for name, body in re.findall(r'<character name="([^"]+)">((?:(?!</character>).)*)</character>', text, re.S):
            selects = re.findall(r'<select\s+([^>]*?)\s*/>', body)
            picked = [re.search(r'macro="([^"]+)"', s) for s in selects]
            if not selects or not all(picked):
                continue
            comps = {known.get(p.group(1), {}).get('component') for p in picked}
            if comps <= set(HUMAN):
                first = known.get(picked[0].group(1), {})
                out[name] = dict(selects=selects, extension=owner(path),
                                 female=first.get('female', False), race=first.get('race') or 'argon')
    return out


def material_patch(materials):
    """materials: [(name, shader, blend mode, {property: value})] -> material_library diff.
    Values that are str are BitMap paths, numbers are Float."""
    out = []
    for name, shader, blend, props in materials:
        lines = []
        for k, v in props.items():
            kind = 'BitMap' if isinstance(v, str) else 'Float'
            lines.append('          <property type="%s" name="%s" value=%s />' % (kind, k, quoteattr(str(v))))
        out.append('      <material name="%s" shader="%s" blendmode="%s" preview="none">\n        <properties>\n%s\n'
                   '        </properties>\n      </material>' % (name.split('.', 1)[1], shader, blend, '\n'.join(lines)))
    coll = materials[0][0].split('.', 1)[0]
    return ('<?xml version="1.0" encoding="utf-8"?>\n<diff>\n  <add sel="/materiallibrary">\n'
            '    <collection name="%s">\n%s\n    </collection>\n  </add>\n</diff>\n' % (coll, '\n'.join(out)))


def macro_patch(name, female, race, head, torso):
    head_line = '          <model type="head" ref="%s" />\n' % head if head else ''
    return '''<?xml version="1.0" encoding="utf-8"?>
<diff>
  <add sel="/macros">
    <macro name="{name}" class="npc">
      <component ref="{comp}" />
      <properties>
        <identification name="@random" race="{race}" female="{female}" />
        <bonemods>
          <bonemod type="Legs" exact="1.0">
            <bones>
              <bone name="Bip01 Pelvis"/>
            </bones>
          </bonemod>
        </bonemods>
        <models>
{head}          <model type="torso" ref="{torso}" />
        </models>
      </properties>
    </macro>
  </add>
</diff>
'''.format(name=name, comp=HUMAN[0] if female else HUMAN[1], race=race, female='true' if female else 'false',
           head=head_line, torso=torso)


def group_patch(group, selects, macro, weight):
    """Add our macro to a spawn group. Vanilla selects without a weight get weight 1, so
    `weight` is relative to one vanilla entry. Only adds, so other mods' additions survive."""
    sel = "//character[@name='%s']" % group
    lines = ['  <add sel="%s/select[@macro=\'%s\']" type="@weight">1</add>' % (sel, m)
             for s in selects if 'weight=' not in s for m in re.findall(r'macro="([^"]+)"', s)]
    lines.append('  <add sel="%s">\n    <select macro="%s" weight="%d" />\n  </add>' % (sel, macro, weight))
    return '<?xml version="1.0" encoding="utf-8"?>\n<diff>\n%s\n</diff>\n' % '\n'.join(lines)


def content_xml(mod_id, name, description, dependencies):
    deps = ''.join('  <dependency id="%s" optional="false" />\n' % d for d in sorted(set(dependencies)) if d)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<content id=%s version="100" name=%s description=%s '
            'author="x4_xac" save="0">\n%s</content>\n' % (quoteattr(mod_id), quoteattr(name), quoteattr(description), deps))

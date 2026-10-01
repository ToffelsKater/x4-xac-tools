"""X4's .cat/.dat archives: look files up the way the game layers them, and write new ones.

A catalog is a text index (`path size mtime md5` per line) plus a .dat holding the files back
to back. Base-game catalogs and `subst_*` catalogs use game paths; an extension's `ext_*`
catalogs use paths inside the extension, which the game addresses as `extensions/<folder>/...`.
Only `subst_*` can replace an existing file; `ext_*` and loose files only add. No bpy.
"""
import glob
import hashlib
import os
import re
import time

_cache = {}


def game_dir_from_steam():
    """X4's folder from the Steam libraries, or '' if it cannot be found."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            steam = winreg.QueryValueEx(k, 'SteamPath')[0]
        with open(os.path.join(steam, 'steamapps', 'libraryfolders.vdf'), encoding='utf-8') as f:
            libraries = [line.split('"')[3] for line in f if line.strip().startswith('"path"')]
    except (OSError, ImportError, IndexError):
        return ''
    for lib in libraries:
        path = os.path.join(lib.replace('\\\\', '\\'), 'steamapps', 'common', 'X4 Foundations')
        if os.path.isfile(os.path.join(path, '01.cat')):
            return path
    return ''


def disabled_extensions(game):
    """Extension folders switched off in the newest X4 profile (Documents/Egosoft/X4/<id>)."""
    profiles = glob.glob(os.path.join(os.path.expanduser('~'), 'Documents', 'Egosoft', 'X4', '*', 'content.xml'))
    if not profiles:
        return set()
    with open(max(profiles, key=os.path.getmtime), encoding='utf-8', errors='replace') as f:
        off = set(re.findall(r'<extension id="([^"]+)" enabled="(?:false|0)"', f.read()))
    out = set()
    for content in glob.glob(os.path.join(game, 'extensions', '*', 'content.xml')):
        with open(content, encoding='utf-8', errors='replace') as f:
            m = re.search(r'<content\b[^>]*?\bid="([^"]+)"', f.read())
        if m and m.group(1) in off:
            out.add(os.path.basename(os.path.dirname(content)).lower())
    return out


def catalogs(game):
    """(catalog, path prefix) in load order: base game, then each enabled extension's ext_ and subst_."""
    out = [(c, '') for c in sorted(glob.glob(os.path.join(game, '*.cat')))]
    off = disabled_extensions(game)
    for ext in sorted(glob.glob(os.path.join(game, 'extensions', '*', ''))):
        folder = os.path.basename(os.path.dirname(ext)).lower()
        if folder in off:
            continue
        out += [(c, 'extensions/%s/' % folder) for c in sorted(glob.glob(os.path.join(ext, 'ext_*.cat')))]
        out += [(c, '') for c in sorted(glob.glob(os.path.join(ext, 'subst_*.cat')))]
    return [(c, p) for c, p in out if not c.endswith('_sig.cat')]


def index(game, refresh=False):
    """{game path (lower case, forward slashes): (dat file, offset, size)}; later catalogs win.
    Rebuilt whenever a catalog appears, disappears or changes (mods get added, deleted and
    switched on or off while Blender runs), so a returned dict is never stale."""
    now = time.time()
    if not refresh and game in _cache and now - _cache[game][2] < 1.0:
        return _cache[game][1]  # panel redraws call this a lot; look at the disk at most once a second
    cats = catalogs(game)
    stamp = [(c, os.path.getmtime(c), os.path.getsize(c)) for c, _ in cats]
    if refresh or _cache.get(game, (None,))[0] != stamp:
        files = {}
        for cat, prefix in cats:
            offset = 0
            with open(cat, encoding='utf-8', errors='replace') as f:
                for line in f:
                    parts = line.rstrip('\n').rsplit(' ', 3)
                    if len(parts) < 4:
                        continue
                    size = int(parts[1])
                    files[(prefix + parts[0]).replace('\\', '/').lower()] = (cat[:-4] + '.dat', offset, size)
                    offset += size
        _cache[game] = [stamp, files, now]
    _cache[game][2] = now
    return _cache[game][1]


def read(game, path):
    dat, offset, size = index(game)[path.replace('\\', '/').lower()]
    with open(dat, 'rb') as f:
        f.seek(offset)
        return f.read(size)


def extract(game, path, cache_dir):
    """Copy a game file into cache_dir (at its game path) and return the local path."""
    local = os.path.join(cache_dir, *path.replace('\\', '/').lower().split('/'))
    entry = index(game)[path.replace('\\', '/').lower()]
    stamp = '%s:%d:%d' % entry
    marker = local + '.src'
    if not (os.path.isfile(local) and os.path.isfile(marker) and open(marker).read() == stamp):
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, 'wb') as f:
            f.write(read(game, path))
        with open(marker, 'w') as f:
            f.write(stamp)
    return local


def write(folder, name, entries):
    """Write folder/name.cat + .dat from [(path, bytes)]."""
    os.makedirs(folder, exist_ok=True)
    now = int(time.time())
    with open(os.path.join(folder, name + '.cat'), 'w', encoding='utf-8', newline='\n') as f:
        f.writelines('%s %d %d %s\n' % (p, len(d), now, hashlib.md5(d).hexdigest()) for p, d in entries)
    with open(os.path.join(folder, name + '.dat'), 'wb') as f:
        f.writelines(d for _, d in entries)

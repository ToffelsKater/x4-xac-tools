"""The X4 sidebar tab: import game characters, fit an avatar, build a character mod."""
import math
import os
import zlib

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import catalog, character, library

SKELETONS = [('FEMALE', 'Female', 'Terran female crew skeleton'), ('MALE', 'Male', 'Terran male crew skeleton')]
PARTS = [('BODY', 'Body', 'Exported into the body (torso) model'),
         ('HEAD', 'Head', 'Exported into the head model (new characters only)'),
         ('SKIP', 'Skip', 'Not exported')]
ALPHA = [('AUTO', 'Auto', 'Cut out if the material uses its alpha'),
         ('OPAQUE', 'Opaque', 'Solid (X4 p1_character shader)'),
         ('CUTOUT', 'Cutout', 'Alpha-tested like vanilla hair (X4 p1_hair shader)')]
KINDS = [('CHARACTER', 'Full Character', 'Head, uniform and hair of a game character, on one skeleton'),
         ('BODY', 'Uniform', 'Body models (clothing); what Replace Body swaps'),
         ('HEAD', 'Head', 'Head models'),
         ('PROPS', 'Hair & Props', 'Hair, beards, helmets and other attached props'),
         ('OTHER', 'Other', 'Every other character model')]
FACTIONS = {'terran': 'Terran Protectorate', 'pioneers': 'Segaris Pioneers', 'yaki': 'Yaki', 'argon': 'Argon',
            'antigone': 'Antigone Republic', 'hatikvah': 'Hatikvah Free League', 'split': 'Zyarth Patriarchy',
            'freesplit': 'Free Families', 'court': 'Court of Curbs', 'loanshark': 'Vigor Syndicate',
            'scavenger': 'Riptide Rakers', 'kaori': 'Kaori (Timelines)', 'timelineshub': 'Timelines Hub'}
ROLES = {'service': 'Service Crew', 'marine': 'Marines', 'pilot': 'Pilots', 'manager': 'Managers',
         'civilian': 'Civilians', 'commander': 'Commanders'}
GROUP_SLOTS = 24  # submenus per import kind; the game has at most 11 groups per kind
_search = []   # items of the open search popup; Blender needs them kept alive
_groups = {}   # (game, female) -> spawn group dropdown items, same reason
_imports = {}  # game -> {kind: {group: [(label, id)]}}
_slots = {}    # import submenu slot -> (kind, group), set by the kind menu that opens it


def prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def game_dir(context):
    p = prefs(context)
    game = (p and p.game_dir) or catalog.game_dir_from_steam()
    if not os.path.isfile(os.path.join(game, '01.cat')):
        raise ValueError('set the X4 folder in the add-on preferences')
    return game


def cache_dir(context):
    p = prefs(context)
    path = (p and p.cache_dir) or bpy.utils.extension_path_user(__package__, path='cache', create=True)
    os.makedirs(path, exist_ok=True)
    return path


def is_character(obj):
    return obj is not None and obj.type == 'ARMATURE' and 'xac_template' in obj


def x4_armature(context):
    """The character the panel works on: the one picked in Character, else the active object's."""
    arm = context.scene.x4char.character
    if is_character(arm) and context.scene.objects.get(arm.name) == arm:
        return arm
    obj = context.active_object
    arm = obj if obj and obj.type == 'ARMATURE' else obj and obj.find_armature()
    return arm if is_character(arm) else None


def adopt(context, arm, name):
    """Make a new armature the panel's character, with a mod id of its own."""
    arm.x4mod.mod_id = character.clean(name)
    context.scene.x4char.character = arm


def enum_value(name):
    """Stable dropdown value for a name. Blender's menu buttons hold the value in a float32,
    so it must stay below 2**24; the offset keeps clear of the column headings' 1..n."""
    return zlib.crc32(name.encode()) % 0xFFF000 + 0x1000


def group_items(self, context):
    """Spawn groups for the character's skeleton, one dropdown column per faction plus diplomats.
    Values are hashes of the names, so a choice survives the list changing. Blender passes
    context=None on some UI paths (e.g. storing the choice), so fall back to bpy.context."""
    context = context or bpy.context
    try:
        game = game_dir(context)
    except ValueError:
        return []
    female = (self.id_data.get('x4_skeleton') or context.scene.x4char.skeleton) == 'FEMALE'
    if (game, female) not in _groups:
        columns = {}
        for name, g in library.groups(game).items():
            if g['female'] == female:
                faction, role = name.split('.')[:2]
                if role == 'factiondiplomat':
                    col, label = 'Diplomats', FACTIONS.get(faction, faction.title())
                else:
                    col, label = FACTIONS.get(faction, faction.title()), ROLES.get(role, role.title())
                columns.setdefault(col, []).append(
                    (name, label, '%s (%s)' % (name, g['extension'] or 'base game'), 'NONE', enum_value(name)))
        items = []
        for n, col in enumerate(sorted(columns, key=lambda c: (c == 'Diplomats', c))):
            items.append(('', col, '', 'NONE', n + 1))
            items += sorted(columns[col], key=lambda i: i[1])
        _groups[(game, female)] = items
    return _groups[(game, female)]


def group_label(name):
    """'terran.manager.female' -> 'Terran Protectorate Managers'."""
    faction, role = (name.split('.') + [''])[:2]
    return '%s %s' % (FACTIONS.get(faction, faction.title()),
                      'Diplomat' if role == 'factiondiplomat' else ROLES.get(role, role.title()))


def model_kind(path):
    folder = path.rsplit('/', 2)[-2]
    return {'bodies': 'BODY', 'heads': 'HEAD', 'props': 'PROPS'}.get(folder, 'OTHER')


def import_items(game):
    """{kind: {group: [(label, id)]}} for the import menu. Models are grouped by their race
    folder, characters by race and sex; id is a game path, or a macro name for a character."""
    idx = catalog.index(game)
    if _imports.get(game, (None,))[0] is not idx:  # the index is rebuilt when mods change
        out = {k: {} for k, _, _ in KINDS}
        for p in idx:
            if p.endswith('.xac') and '/characters/' in p:
                rest = p.split('/characters/', 1)[1]
                group = rest.split('/')[0].replace('_', ' ').title() if '/' in rest else 'Other'
                owner = library.owner(p)
                label = rest.rsplit('/', 1)[-1][:-4]
                if owner and not owner.startswith('ego_dlc'):
                    label += '  [%s]' % owner
                out[model_kind(p)].setdefault(group, []).append((label, p))
        for name, m in library.macros(game).items():
            if m['models'].get('torso') in idx:
                group = '%s %s' % ((m.get('race') or 'other').title(), 'Female' if m.get('female') else 'Male')
                out['CHARACTER'].setdefault(group, []).append(
                    (name.replace('character_', '').replace('_macro', ''), name))
        for groups in out.values():
            for items in groups.values():
                items.sort()
        _imports[game] = (idx, out)
    return _imports[game][1]


def draw_kind(self, context, kind):
    try:
        groups = import_items(game_dir(context))[kind]
    except ValueError as e:
        self.layout.label(text=str(e), icon='ERROR')
        return
    for i, g in enumerate(sorted(groups)[:GROUP_SLOTS]):
        _slots[i] = (kind, g)
        self.layout.menu('X4CHAR_MT_import_%d' % i, text='%s (%d)' % (g, len(groups[g])))


def draw_group(self, context, slot):
    kind, group = _slots.get(slot, (None, None))
    items = import_items(game_dir(context))[kind].get(group, []) if kind else []
    flow = self.layout.column_flow(columns=max(1, math.ceil(len(items) / 30)))
    for label, ident in items:
        flow.operator('x4char.import_game', text=label).path = ident


class X4CHAR_MT_import(bpy.types.Menu):
    bl_idname = 'X4CHAR_MT_import'
    bl_label = 'Import from Game'

    def draw(self, context):
        self.layout.operator('x4char.import_game', text='Search...', icon='VIEWZOOM')
        self.layout.separator()
        for kind, label, _ in KINDS:
            self.layout.menu('X4CHAR_MT_import_%s' % kind.lower(), text=label)


def _menu(idname, label, draw, arg):
    """A menu class whose draw calls draw(self, context, arg); Blender wants exactly 2 args."""
    return type(idname, (bpy.types.Menu,), dict(bl_idname=idname, bl_label=label,
                                                  draw=lambda self, context: draw(self, context, arg)))


KIND_MENUS = [_menu('X4CHAR_MT_import_%s' % k.lower(), label, draw_kind, k) for k, label, _ in KINDS]
GROUP_MENUS = [_menu('X4CHAR_MT_import_%d' % i, 'Models', draw_group, i) for i in range(GROUP_SLOTS)]


class X4Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    game_dir: StringProperty(name='X4 folder', subtype='DIR_PATH',
                             description='X4: Foundations install folder (found through Steam if empty)')
    cache_dir: StringProperty(name='Cache folder', subtype='DIR_PATH',
                              description='Where extracted game files go (the add-on user folder if empty)')

    def draw(self, context):
        self.layout.prop(self, 'game_dir')
        if not self.game_dir:
            self.layout.label(text='Found: ' + (catalog.game_dir_from_steam() or 'nothing, set it'))
        self.layout.prop(self, 'cache_dir')


class X4CharSettings(bpy.types.PropertyGroup):
    """Per scene: fitting, and which character the panel works on."""
    skeleton: EnumProperty(name='Skeleton', items=SKELETONS)
    head_scale: FloatProperty(name='Head Size', default=1.0, min=0.2, max=3.0,
                              description='Head scale relative to the body (1 = scaled like the body)')
    character: PointerProperty(type=bpy.types.Object, name='Character', poll=lambda self, o: is_character(o),
                               description='X4 character (fitted or imported armature) that Parts, Face and Build use')


class X4GroupRow(bpy.types.PropertyGroup):
    name: StringProperty(name='Spawn Group')


class X4ModSettings(bpy.types.PropertyGroup):
    """Per character armature: the mod it builds into."""
    mod_id: StringProperty(name='Mod ID', default='my_character',
                           description='Extension folder and id; lower case, digits and _')
    mod_name: StringProperty(name='Name', description='Name shown in the extension list')
    mode: EnumProperty(name='Mode', items=[
        ('NEW', 'New Character', 'Add a character with its own head to a spawn group'),
        ('REPLACE', 'Replace Body', 'Replace a vanilla body model everywhere it is used (keeps game heads)')])
    group: EnumProperty(name='Spawn Group', items=group_items,
                        description='Game spawn group the new character joins (groups for its skeleton)')
    weight: IntProperty(name='Weight', default=3, min=1, max=1000,
                        description='Spawn weight against 1 for each vanilla character in the group')
    groups: CollectionProperty(type=X4GroupRow, description='Spawn groups to build for; two or more build one mod each')
    target: StringProperty(name='Body')
    normal_opengl: BoolProperty(name='OpenGL Normal Maps', default=True,
                                description='Normal maps made for Blender/Unity (green up); flipped for X4')


class X4FaceRow(bpy.types.PropertyGroup):
    target: StringProperty(name='X4 Target')
    key: StringProperty(name='Shape Key', description='Avatar shape key exported as this X4 face target')
    flat: BoolProperty(description='The shape key moves nothing, so this target stays still')


def show_report(context, title, lines):
    """All report lines in a popup; the status bar only keeps the last one."""
    def draw(menu, _context):
        for line in lines:
            warn = 'WARNING' in line or 'move nothing' in line or line.startswith('note')
            menu.layout.label(text=line, icon='ERROR' if warn else 'DOT')
    context.window_manager.popup_menu(draw, title=title, icon='INFO')


class X4CHAR_OT_guess_face(bpy.types.Operator):
    """Match the head's shape keys to X4's expressions and lip shapes by name again"""
    bl_idname = 'x4char.guess_face'
    bl_label = 'Guess Face Mapping'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = x4_armature(context)
        return arm is not None and 'xac_head_template' in arm

    def execute(self, context):
        arm = x4_armature(context)
        meshes = [o for o in context.scene.objects if o.type == 'MESH' and o.find_armature() == arm]
        self.report({'INFO'}, character.guess_face(arm, meshes))
        return {'FINISHED'}


class X4CHAR_OT_import_game(bpy.types.Operator):
    """Import a character or model from the game or an installed mod, with its textures"""
    bl_idname = 'x4char.import_game'
    bl_label = 'Import from Game'
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = 'item'
    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    item: EnumProperty(items=lambda self, context: _search)

    def invoke(self, context, event):
        if self.path:  # picked from the import menu
            return self.execute(context)
        try:
            groups = import_items(game_dir(context))
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        names = {k: label for k, label, _ in KINDS}
        _search[:] = [(ident, '%s  [%s, %s]' % (label, group, names[kind]), '')
                      for kind, gs in groups.items() for group, items in gs.items() for label, ident in items]
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import load
        ident = self.path or self.item
        try:
            game, cache = game_dir(context), cache_dir(context)
            if ident.endswith('.xac'):
                notes = character.import_game(context, game, cache, ident, load)
                name = ident.rsplit('/', 1)[1][:-4]
            else:
                notes = character.import_character(context, game, cache, ident, load)
                name = ident.replace('character_', '').replace('_macro', '')
        except (OSError, ValueError, KeyError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        adopt(context, context.view_layer.objects.active, name)
        self.report({'INFO'}, '; '.join(notes))
        return {'FINISHED'}


class X4CHAR_OT_fit(bpy.types.Operator):
    """Fit the selected avatar armature and its meshes to X4's skeleton (bakes the pose)"""
    bl_idname = 'x4char.fit'
    bl_label = 'Fit Avatar to X4'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE' and \
            'xac_template' not in context.active_object

    def execute(self, context):
        from . import load
        s, name = context.scene.x4char, context.active_object.name
        try:
            arm, report = character.fit(context, context.active_object, s.skeleton, s.head_scale,
                                        game_dir(context), cache_dir(context), load)
        except (OSError, ValueError, KeyError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        context.view_layer.objects.active = arm
        adopt(context, arm, name)
        for line in report:
            self.report({'INFO'}, line)
        show_report(context, 'Fit Avatar', report)
        return {'FINISHED'}


class X4CHAR_OT_pick(bpy.types.Operator):
    """Pick the game's body model (uniform) to replace"""
    bl_idname = 'x4char.pick'
    bl_label = 'Pick'
    bl_property = 'item'
    item: EnumProperty(items=lambda self, context: _search)

    @classmethod
    def poll(cls, context):
        return x4_armature(context) is not None

    def invoke(self, context, event):
        try:
            game = game_dir(context)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _search[:] = [(p, '%s  [%s]' % (p.rsplit('/', 1)[1], library.owner(p) or 'base'), p)
                      for p in sorted(catalog.index(game)) if p.endswith('.xac') and model_kind(p) == 'BODY']
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        x4_armature(context).x4mod.target = self.item
        return {'FINISHED'}


class X4CHAR_OT_group_add(bpy.types.Operator):
    """Add the chosen spawn group to the ones this character is built for"""
    bl_idname = 'x4char.group_add'
    bl_label = 'Add Spawn Group'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = x4_armature(context)
        return arm is not None and arm.x4mod.group not in ('', *(r.name for r in arm.x4mod.groups))

    def execute(self, context):
        m = x4_armature(context).x4mod
        m.groups.add().name = m.group
        return {'FINISHED'}


class X4CHAR_OT_group_remove(bpy.types.Operator):
    """Remove this spawn group"""
    bl_idname = 'x4char.group_remove'
    bl_label = 'Remove Spawn Group'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty()

    def execute(self, context):
        x4_armature(context).x4mod.groups.remove(self.index)
        return {'FINISHED'}


class X4CHAR_OT_build(bpy.types.Operator):
    """Export the character, convert its textures and write the mod into the game's extensions folder"""
    bl_idname = 'x4char.build'
    bl_label = 'Build Mod'

    @classmethod
    def poll(cls, context):
        return x4_armature(context) is not None

    def execute(self, context):
        from . import save
        arm = x4_armature(context)
        m = arm.x4mod
        try:
            if m.mode == 'NEW':  # one mod, or one per group with suffixed id and name
                report = character.build_batch(context, arm, m, [r.name for r in m.groups] or [m.group],
                                               game_dir(context), cache_dir(context), save, label=group_label)
            else:
                report = character.build(context, arm, m, game_dir(context), cache_dir(context), save)
        except (OSError, ValueError, KeyError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _imports.clear()  # the new mod's models and character show up in the import menu
        for line in report:
            self.report({'INFO'}, line)
        show_report(context, 'Build Mod', report)
        return {'FINISHED'}


class X4CHAR_PT_main(bpy.types.Panel):
    bl_label = 'X4 Characters'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'X4'

    def draw(self, context):
        s, layout = context.scene.x4char, self.layout
        layout.menu('X4CHAR_MT_import', icon='IMPORT')

        box = layout.box()
        box.label(text='1. Fit Avatar', icon='ARMATURE_DATA')
        box.prop(s, 'skeleton', expand=True)
        box.prop(s, 'head_scale')
        box.operator('x4char.fit')

        arm = x4_armature(context)
        box = layout.box()
        box.label(text='2. Parts', icon='MESH_DATA')
        box.prop(s, 'character', text='', icon='OUTLINER_OB_ARMATURE')
        if arm and s.character != arm:
            box.label(text='Using the selected ' + arm.name, icon='INFO')
        meshes = [o for o in context.scene.objects if arm and o.type == 'MESH' and o.find_armature() == arm]
        if not meshes:
            box.label(text='Pick a character, or fit or import one')
        for o in meshes:
            row = box.row()
            row.label(text=o.name)
            row.prop(o, 'x4_part', text='')
        mats = character.used_materials([o for o in meshes if o.x4_part != 'SKIP'])
        if mats:
            box.label(text='Materials (Roughness sets X4 gloss)')
        for m in mats:
            row = box.row()
            row.label(text=m.name, icon='MATERIAL')
            row.prop(m, 'x4_alpha', text='')

        if arm and 'xac_head_template' in arm:
            box = layout.box()
            row = box.row()
            row.label(text='Face (expressions, lip sync)', icon='MONKEY')
            row.operator('x4char.guess_face', text='', icon='FILE_REFRESH')
            owner = next((o.data.shape_keys for o in meshes if o.x4_part == 'HEAD' and o.data.shape_keys), None)
            if owner is None:
                box.label(text='Head meshes have no shape keys: the face stays still')
            else:
                col = box.column(align=True)
                for r in arm.x4_face:
                    split = col.split(factor=0.45)
                    split.label(text=character.FACE[character.face_suffix(r.target)][0],
                                icon='ERROR' if r.flat and r.key else 'NONE')
                    split.prop_search(r, 'key', owner, 'key_blocks', text='')
                if any(r.flat and r.key for r in arm.x4_face):
                    box.label(text='Marked keys move nothing: re-import the avatar and fit again', icon='ERROR')

        box = layout.box()
        box.label(text='3. Build Mod' + (': ' + arm.name if arm else ''), icon='PACKAGE')
        if arm is None:
            box.label(text='No character')
            return
        m = arm.x4mod
        box.prop(m, 'mod_id')
        box.prop(m, 'mod_name')
        box.prop(m, 'mode', expand=True)
        if m.mode == 'NEW':
            row = box.row(align=True)
            row.prop(m, 'group')
            row.operator('x4char.group_add', text='', icon='ADD')
            for i, r in enumerate(m.groups):
                row = box.row(align=True)
                row.label(text=group_label(r.name), icon='COMMUNITY')
                row.operator('x4char.group_remove', text='', icon='X').index = i
            if len(m.groups) > 1:
                box.label(text='Builds %d mods: %s_<group>' % (len(m.groups), character.clean(m.mod_id)), icon='INFO')
            elif not m.groups:
                box.label(text='+ adds groups; two or more build one mod each', icon='INFO')
            box.prop(m, 'weight')
        else:
            row = box.row(align=True)
            row.prop(m, 'target')
            row.operator('x4char.pick', text='', icon='VIEWZOOM')
            if not m.target and arm.get('x4_game_path'):
                box.label(text='Empty: ' + arm['x4_game_path'].rsplit('/', 1)[1], icon='INFO')
        box.prop(m, 'normal_opengl')
        box.operator('x4char.build', icon='EXPORT')


CLASSES = (X4Preferences, X4CharSettings, X4GroupRow, X4ModSettings, X4FaceRow, X4CHAR_OT_guess_face,
           X4CHAR_OT_import_game, X4CHAR_OT_fit, X4CHAR_OT_pick, X4CHAR_OT_group_add, X4CHAR_OT_group_remove,
           X4CHAR_OT_build, X4CHAR_MT_import, *KIND_MENUS, *GROUP_MENUS,
           X4CHAR_PT_main)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.x4char = PointerProperty(type=X4CharSettings)
    bpy.types.Object.x4mod = PointerProperty(type=X4ModSettings)
    bpy.types.Object.x4_part = EnumProperty(name='Part', items=PARTS, default='BODY')
    bpy.types.Object.x4_face = CollectionProperty(type=X4FaceRow)
    bpy.types.Material.x4_alpha = EnumProperty(name='Alpha', items=ALPHA, default='AUTO')


def unregister():
    del bpy.types.Material.x4_alpha
    del bpy.types.Object.x4_face
    del bpy.types.Object.x4_part
    del bpy.types.Object.x4mod
    del bpy.types.Scene.x4char
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

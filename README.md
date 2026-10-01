# X4 XAC Tools

Tools for X4: Foundations character models (`.xac`, the EMotion FX 3 actors used for NPC
bodies, heads, hair and props): a Blender add-on for import/export plus command-line helpers.

Status: every Terran body, a hair prop and a head survive import → export with identical
triangles, UVs, normals and weights (`tools/roundtrip_test.py`). **Works in game (9.00):** a
re-exported Terran body with added geometry loads and renders skinned, shipped through
`subst_01.cat`.

## Blender add-on

Works in Blender 5.1 (also tested on 4.2 LTS). Download `x4_xac-<version>.zip` from
[Releases](https://github.com/ToffelsKater/x4-xac-tools/releases) and install it through
`Edit > Preferences > Get Extensions > ⌄ > Install from Disk...`. The X4 folder is found
through Steam; set it in the add-on preferences if that fails.

## Character swapping (sidebar `N > X4`)

<img src="docs/panel.png" align="right" width="250" alt="The X4 sidebar panel">

The X4 tab does the whole trip from the game to Blender and back, without extracting,
packing or writing XML by hand. **Build Mod** writes the extension straight into
`X4 Foundations/extensions/<Mod ID>`; enable it in the game's extension list. Delete the
folder to undo.

Several characters can share a scene. The **Character** field (top of Parts) picks the one
that Parts, Face and Build Mod work on; importing or fitting selects the new one. Mod
settings (Mod ID, name, mode, group...) belong to each character, and each gets a Mod ID
from its name. Two characters with the same Mod ID are refused, because they would
overwrite each other's extension.

**Edit a game character:**
1. **Import from Game** opens a menu: *Search...* (everything, type to filter) or a type,
   then a group, then the model:
   - *Full Character*: a character as the game assembles it (character macro), grouped by
     race and sex: uniform, head and hair on one armature. Head and hair are marked Skip, so
     only the uniform is built. Where the game picks at random, the most likely model is taken.
   - *Uniform* (body models, what Replace Body swaps), *Head*, *Hair & Props*, *Other*: single
     model files, grouped by race. Files from mods show the mod in brackets.

   Models come with their diffuse textures, and the armature remembers the uniform's path.

   ![Import from Game menu](docs/import.png)
2. Edit the meshes (sculpt, add parts, weight paint). Keep the game materials or assign new
   ones.
3. **Build Mod** in *Replace Body* mode, with *Body* left empty: the file goes back over
   the one it came from. Game materials stay as they are; new materials get converted
   textures and a material library entry.

**Put your own avatar in:**
1. Import the avatar (FBX, VRM, .blend...). Select its armature, pick the X4 skeleton
   (Female/Male) and a Head Size, then **Fit Avatar to X4**. It recognises Unity/VRChat,
   VRM, Mixamo and Biped bone names. The pose is baked: joints land on X4's, and the arms turn
   from T-pose to X4's rest pose. Weights move onto X4 bones.
   Shape keys survive the fit: each is baked through the same pose.
2. **Parts**: every mesh is marked Head, Body or Skip (guessed from its weights). Per
   material, *Alpha* picks opaque or cutout (like vanilla hair), and Roughness sets the gloss.
   **Face**: the 16 face targets X4 animates (blink, smile, anger, sadness, fear and 11 lip
   shapes), each with the head shape key exported for it. Fit fills this in from the key
   names: VRChat visemes (`vrc.v_aa`...), VRoid/VRM (`Fcl_*`), MMD (まばたき, 笑い...) and
   plain English. ⟳ guesses again. Unassigned targets keep still.
3. **Build Mod**:
   - *New Character*: your head and body as a new NPC in a spawn group, at *Weight* against 1
     for each vanilla entry. Nothing vanilla is replaced. The *Spawn Group* dropdown lists
     the groups for the chosen skeleton, one column per faction (roles: service crew,
     marines, pilots, managers, civilians) plus a Diplomats column. **+** adds the chosen
     group to the character's list (× removes one). With one group, Build Mod writes one mod
     as before. With two or more, it writes one mod per group: Mod ID `<id>_<group>` (e.g.
     `c_lina_terran_manager`) and the name with the group appended ("Lina (Terran
     Protectorate Managers)"), so each can be switched on or off in the game. Textures are
     converted once for the whole batch.

     ![Spawn Group dropdown](docs/spawn-group.png)
   - *Replace Body*: your body replaces one game body file everywhere it is used, under the
     game heads. Head meshes are left out. The target must have the skeleton you fitted to;
     a mismatch is refused (female and male crew differ by up to 8 cm).

Textures are converted to X4's gzipped DDS (BC1/BC3/BC5 with mips and `-small` copies).
Normal maps are expected in OpenGL convention (Blender/Unity); untick *OpenGL Normal Maps* for
DirectX ones. `tools/character_test.py` runs this whole path without the UI.

Rebuild the zip after changing the add-on:

```powershell
& "D:\Blender5.1\blender.exe" --factory-startup --command extension build --source-dir io_scene_x4_xac --output-dir dist
```

## Workflow

1. **Extract** the models you want to change from the game archives:
   ```powershell
   python tools\extract.py "terran/bodies/.*\.xac$" --out D:\X4Extract
   ```
   Files land at their in-game path, e.g.
   `extensions/ego_dlc_terran/assets/characters/terran/bodies/char_ter_m_crew_uniform_01.xac`.
2. **Import** with `File > Import > X4 Character (.xac)`. You get an armature (the Biped
   `Bip01 …` skeleton plus helper bones) and one skinned object per mesh. Materials carry the
   X4 material library name (e.g. `terrancharacters.ter_p1_…`).
3. **Edit or replace** meshes:
   - An object replaces the template mesh with the same name (or the name stored in its
     `xac_node` custom property). A new name adds a new mesh.
   - Parent it to the armature and weight paint it to template bones. Every vertex needs a
     weight; at most 4 per vertex are kept.
   - Material slots become submeshes. A material with a new name is added to the file; you
     then have to define it in the mod's material library.
4. **Export** with `File > Export > X4 Character (.xac)` and the meshes selected. The template
   (the original `.xac`) is filled in from the imported armature. Options:
   - *Remove Other Meshes*: drop template meshes you did not export (replace a whole outfit).
   - *Keep LODs*: keep the template's LOD meshes. Off by default, because they would show the
     old geometry at a distance.
5. **Ship** the file in a mod extension, packed into a **`subst_01.cat`/`subst_01.dat`**
   pair at the original path (verified in game on 9.00). Loose files and `ext_01.cat` do not
   work: they only add files, they never replace one. A catalog's index line is
   `path size mtime md5`, separated by spaces, LF endings; the `.dat` is the files
   concatenated in index order. `tools/make_test_mod.py` does the packing. Base-game files use
   `assets/...`; for DLC files the test mod ships both `extensions/ego_dlc_terran/assets/...`
   and `ego_dlc_terran/assets/...`, and which of the two the game uses is not pinned down yet.

## Limits

- Face targets (morph chunk 12) import as shape keys and export from them. X4 plays blink and
  emotions by target name (`Ter_f_cau.mt_blink`...) and lip sync by the phoneme bits
  (EMotion FX phoneme sets), so the export keeps the template's names and bits. The ~50
  face-shape targets the game uses to vary NPC faces stay empty on a custom head. Eyes do not
  follow a look-at target on a custom head (they move with the head bone). Not yet checked in
  game.
- Keep each material section at or below 57 bones; that is the vanilla maximum (Argon bodies
  reach it, Terran ones stay near 34), and the engine limit is unknown.
- The skeleton comes from the template and is never changed. Moving bones in Blender does
  nothing in game.

## Command-line tools

| Script | Use |
| --- | --- |
| `tools/extract.py REGEX` | Extract matching files from the `.cat/.dat` archives (`--list` to preview). |
| `tools/xacinfo.py FILE [--png OUT]` | Print meshes, materials and bones; optionally render front and side views (needs Pillow). |
| `tools/roundtrip_test.py` | `blender -b --factory-startup -P tools/roundtrip_test.py -- FILE.xac ...` imports, re-exports and compares. |
| `tools/character_test.py` | `blender -b --factory-startup AVATAR.blend -P tools/character_test.py -- GAME_DIR OUT_DIR` fits the avatar, builds a new-character and a replace mod into OUT_DIR, checks the skeleton guard, then imports a game body, edits it and builds it back. Exits non-zero on failure. |
| `tools/dds_test.py` | Checks the DDS encoder outside Blender. |
| `tools/catalog_test.py` | Checks that the file index follows mods being added, rewritten and deleted while Blender runs. |
| `tools/jcscheck.py` | Lists collision shapes (`.jcs`) in the game and every installed mod that use the pre-9.00 Jolt compound layout. X4 9.00 crashes (`X4.exe+0x13C1645`) when it loads one. Run it after installing a ship mod. |
| `tools/make_test_mod.py` | `blender -b --factory-startup -P tools/make_test_mod.py -- extracted/**/*.xac` builds the in-game test mod `extensions/xac_test`: each body re-exported with a marker post on its back, packed into `subst_01.cat`, plus a sentinel diff in `ext_01.cat` that proves in `debug.log` that the extension loaded. Each file's path below `extracted/` is the in-game path it overrides. |

## Format notes

`io_scene_x4_xac/xac.py` documents the chunk layouts in code. In short: the file space is
left-handed, +Y up, characters face -Z, and units are centimetres. The add-on swaps Y and Z
and scales by 0.01. Because that swap is a mirror, triangle winding flips.

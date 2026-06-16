# C4D Texture Linker

Cinema 4D **S24** Python scripts that auto-connect texture maps to the correct
material channels — built for the "import an OBJ + MTL group, then wire up all
the PBR maps from another folder" workflow, without doing it by hand for every
material.

Two entry points:

- **`texture_linker.py`** — link textures to the materials already in your
  scene.
- **`import_and_link.py`** — one shot: import a whole folder of objects, *then*
  link textures from a second folder. (Imports its matching logic from
  `texture_linker.py`, so keep both files in the same folder.)

## What it does

1. You pick a folder of texture maps.
2. For every **classic material** in the scene it looks for textures whose
   file name contains the material's name.
3. It detects each map's channel from its file-name suffix
   (`BaseColor`, `Normal`, `Roughness`, `Bump`, `Displacement`, `Opacity`, …).
4. It creates a bitmap shader and plugs it into the right slot, setting the
   colour profile (sRGB for colour maps, linear for data maps).

## How to run

- Cinema 4D → **Script Manager** (`Shift+F11`) → open `texture_linker.py` →
  **Execute**.
- Or place it in your scripts folder so it appears under
  **Extensions → User Scripts**.

A folder picker appears; choose your texture folder. A summary dialog reports
what was assigned, and the full log is printed to the Python console.

## Import + link in one go

Run `import_and_link.py` from the Script Manager. It asks for:

1. an **objects folder** — every `.obj`/`.fbx`/`.3ds`/`.dae`/`.abc`/`.gltf`/…
   file in it is merged into the current scene;
2. a **texture folder** — textures are then matched to the resulting
   materials exactly as `texture_linker.py` does.

Both steps happen inside a single undo block, so one `Ctrl+Z` reverts the
whole operation. Key settings at the top of the file:

| Setting | Purpose |
| --- | --- |
| `OBJECTS_FOLDER` / `TEXTURE_FOLDER` | Hard-code folders to skip the pickers. |
| `RECURSIVE_OBJECTS` | Search sub-folders for objects to import. |
| `SPREAD_OBJECTS` / `SPREAD_SPACING` | Offset each imported file along X so they don't overlap. |

## Configuration

Edit the `CONFIG` block at the top of `texture_linker.py`:

| Setting | Purpose |
| --- | --- |
| `TEXTURE_FOLDER` | Hard-code a folder to skip the picker (`None` = always prompt). |
| `RECURSIVE` | Search sub-folders too. |
| `DRY_RUN` | Log what *would* happen without changing anything. |
| `OVERWRITE_EXISTING` | Replace a channel that already has a shader. |
| `MATCH_MODE` | `"name_in_filename"` (many materials share a folder) or `"all"` (one material per folder). |
| `ALLOW_SINGLE_LETTER_SUFFIX` | Honour suffixes like `wood_D`, `wood_N`, `wood_R`. |

The `CHANNEL_KEYWORDS` table maps file-name tokens to channels — extend it to
match your studio's naming.

## Supported channels

Color · Luminance (emissive) · Specular colour · Transparency · Alpha
(opacity) · Bump · Normal · Displacement, plus the Reflection-colour map on a
reflectance layer.

## Notes & limitations

- Targets the **classic material** (`c4d.Mmaterial`) — what the OBJ/MTL
  importer creates. Redshift / node materials are skipped with a message.
- Roughness / glossiness / metalness have no plain bitmap slot in the classic
  Reflectance UI, so they are **detected and reported for manual hookup**
  rather than guessed at. Reflection-colour maps are wired automatically.
- Every run is wrapped in a single undo step, so `Ctrl+Z` reverts it.

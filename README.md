# C4D Texture Tool

A single, self-contained Cinema 4D **S24** Python script that:

1. *(optional)* imports a whole folder of 3D objects (OBJ/FBX/3DS/ABC/glTF/…)
   into the current scene, then
2. auto-connects textures from a folder to the correct **material channels**
   (BaseColor / Normal / Roughness / Bump / Displacement / Opacity / …).

Built for the "import a big OBJ + MTL group, then wire up all the PBR maps from
another folder" workflow — without doing it by hand for every material.

It opens a small dialog so you can **see it working**: folder pickers, options,
a **live progress bar**, and a results log that fills in as it runs (errors show
up in that log too, so it never silently does nothing). The work runs
incrementally, so **Cinema stays responsive** — objects appear in the viewport
as they import, and a **Cancel** button stops a long job cleanly.

## How to run

Cinema 4D → **Script Manager** (`Shift+F11`) → open `texture_tool.py` →
**Execute**. One file, no dependencies — nothing to import, nothing to break.

## Using the dialog

| Control | What it does |
| --- | --- |
| **Import objects** | When ticked, every importable file in the *Objects folder* is merged into the scene first. Untick to only link textures to existing materials. |
| **Objects / Texture folder** | Pick with **Browse…** |
| **Group each file's parts under a Null** | Parent each file's imported objects under a Null named after the file (its exact base name), so the scene is organised. Object positions/scale are preserved exactly. |
| **Match mode** | How files are matched to materials (see below). |
| **Dry run** | Preview only — logs what *would* be wired up without changing anything. Run this first. |
| **Overwrite existing** | Replace a channel that already has a shader. |
| **Recurse textures / objects** | Search sub-folders. |
| **Spread apart (moves objects)** | *Off by default.* Offsets each group/file along X. Leave off to keep the OBJ's original positions. |
| **Preview groups** | Show the proposed grouping without importing anything. |
| **Run** | Do it. The progress bar and log update live; C4D stays usable. |
| **Cancel** | Stop a running job cleanly (the partial result is still undoable). |

### Grouping

When **Group each file's parts under a Null** is on, each file's imported
objects are parented under a Null named after the file's **exact base name**.
Numbers are kept, so `tree007` and `tree008` are separate Nulls (they're
different models) — names are never stripped or merged. The tool shows the
planned Nulls and asks for confirmation before importing. **Positions and scale
from the OBJ are preserved** — grouping only re-parents (the world transform is
restored after re-parenting); it never moves or rescales anything. Use
**Preview groups** to see the plan first.

## How files are matched to materials

A texture is detected by its **file-name suffix** (`*_BaseColor`, `*_Normal`,
`*_Roughness`, `*_Bump`, `*_Disp`, `*_Opacity`, `wood_N`, …) and matched to a
material by one of:

- **Auto** *(default)* — match by material name; but if the scene has exactly
  **one** material, that material gets every map (no name needed).
- **By material name only** — a texture is linked when the material's name
  appears in the texture's path relative to the chosen folder. So all of these
  match material `WoodFloor`:
  - `WoodFloor_BaseColor.png`
  - `WoodFloor/basecolor.png` (a sub-folder named after the material)
  - `wood_floor_color.png` (material named `Wood Floor`)
- **All files → every material** — for one-material-per-folder setups.

If nothing matches, the log lists your material names and a few example
filenames, plus how to fix it.

## Supported channels

Color · Luminance (emissive) · Specular colour · Transparency · Alpha
(opacity) · Bump · Normal · Displacement, plus the Reflection-colour map on a
reflectance layer.

## Notes & limitations

- Targets the **classic material** (`c4d.Mmaterial`) — what the OBJ/MTL
  importer creates. Octane / Redshift / node materials are skipped with a note.
- Roughness / glossiness / metalness have no plain bitmap slot in the classic
  Reflectance UI, so they're **detected and reported for manual hookup** rather
  than guessed at. Reflection-colour maps are wired automatically.
- The whole run (import + linking) is one undo step — `Ctrl+Z` reverts it.
- Edit the `CHANNEL_KEYWORDS` table near the top to match your studio's naming.

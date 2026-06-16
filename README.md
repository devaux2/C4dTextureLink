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
objects are parented under a Null named after the file. The name is cleaned up:

- **Common prefix stripped.** A shared prefix like `dota_d_` is removed. Leave
  the **Strip prefix** field blank to auto-detect it, or type your own.
- **Split parts merged.** `_<number>` suffixes that are split parts of one model
  collapse onto their base: `campfire_rocks001`, `campfire_rocks001_1 … _4` all
  go under **`campfire_rocks001`**. This only happens when the base is a real
  sibling, so a model whose name genuinely ends in a number (e.g.
  `bones_tintable_002`) is kept — and its part `bones_tintable_002_1` joins it.
- **Attached numbers kept.** `tree007` and `tree008` are different models, so
  they stay separate Nulls.

The tool shows the planned Nulls and asks for confirmation before importing;
**Preview groups** prints the full list without importing. **Positions and scale
from the OBJ are preserved** — grouping only re-parents (the world transform is
restored after re-parenting); it never moves or rescales anything.

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

---

# Companion tools

## Octane Universal linking

`texture_tool.py` targets standard C4D materials. For an **Octane** scene use:

- **`octane_inspector.py`** — run once to dump your Octane build's parameter
  IDs (the linker was built against Octane 2025; re-run if yours differs).
- **`octane_texture_linker.py`** — converts the scene's standard materials to
  **Octane Universal** materials and wires the matching textures.

Workflow:

1. Create one Octane **Universal** material in the scene as a template (the
   linker clones it, so all Universal/BSDF/node-space defaults are correct for
   your version — no guessing).
2. Run `octane_texture_linker.py`, pick the texture folder, optionally **Dry
   run** to preview, then **Run**. For each standard material it builds an
   Octane Universal material, wires matching maps (Diffuse, Specular,
   Roughness, Metallic, Reflection, Bump, Normal, Displacement, Opacity,
   Transmission, Emission), copies the base colour, and swaps it onto the
   objects that used the standard material. One undo step.

Notes:

- The template clone's placeholder textures are stripped before wiring, so the
  **Diffuse map always loads** (it won't be skipped as "already connected").
- Conversion runs **one scene group (Null) at a time**, refreshing Octane only
  at each group boundary (and every few materials), so it streams textures in
  chunks instead of compiling everything at once — which can crash Octane.

## Deduplicate into instances

`mesh_instancer.py` finds objects that are the **same shape** and replaces the
duplicates with **Render Instances** — a big RAM/render win when the same prop
is imported many times.

Crucially it matches meshes **up to a transform**, not just byte-identical
geometry. Decompiled game maps usually bake each placement's
position/rotation/scale into the vertices, so copies of the same model have
different vertex numbers. This tool buckets by topology (point/poly count +
polygon connectivity), then solves and verifies the affine transform that maps
one mesh onto another, and instances the copies with their recovered transforms
— so positions/rotations/scale are preserved exactly.

**Analyze** reports the duplicate groups and how many objects would be removed;
**Convert** does it (one undo step).

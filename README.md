# C4D Dota → Octane pipeline

A set of **single-purpose Cinema 4D scripts**, one per stage, for turning a
folder of game-map assets into an organised, instanced, Octane-textured scene.
Run each from the **Script Manager** (`Shift+F11` → open → Execute). Each script
is self-contained (no cross-imports) and shows a dialog with a live progress
bar, a results log, and a Cancel button; every run is a single undo step.

**Core pipeline**

| Stage | Script | Does |
| --- | --- | --- |
| 1 | `1_import_objects.py` | Import a folder of objects, organising each file's parts under a named Null. |
| 2 | `2_mesh_instancer.py` | Replace duplicated meshes with instances (shape-matched, transform-aware). |
| 3 | `3_octane_inspector.py` | One-off: read your Octane build's parameter IDs (only if your version differs from Octane 2025). |
| 4 | `4_octane_texture_linker.py` | Build Octane Universal materials and wire the matching textures. |

**Organise the scene**

| Script | Does |
| --- | --- |
| `group_objects.py` | Group objects **already in the scene** under Nulls by name (after merging one big FBX). Source-2-name aware; nested mode. |
| `organize_scene.py` | Sort the whole top level under ~10 theme Nulls (Water/Trees/Rocks/Structures/Props/Dire/Radiant/Overlays/…). |
| `sort_instances.py` | Inside each Null, keep real meshes on top and move instances into an `Instances` sub-Null. |
| `sort_by_tris.py` | Reorder top-level objects/Nulls by triangle count (heaviest first), with a ranking. |

**Optimise**

| Script | Does |
| --- | --- |
| `decimate.py` | In-place polygon reduction of selected meshes (instances follow their master). |
| `instances_to_scatter.py` | Convert C4D instances into **Octane Scatter** (CSV mode) — one GPU mesh per type, exact placement. |

**Octane material fixes** (on materials already built)

| Script | Does |
| --- | --- |
| `add_opacity.py` | Add the colour-alpha → Opacity cutout to Octane materials **already textured** (no re-import). |
| `fix_albedo.py` | Find Octane materials missing an Albedo, suggest matching colour files, and assign the best. |

**Diagnostics** (read-only / safe)

| Script | Does |
| --- | --- |
| `dump_names.py` | Export every object name + type to a chosen `.txt` (for diagnosing naming). |
| `octane_probe.py` | Report how one Octane material is wired (channel links + image node settings). |
| `object_probe.py` | Report a selected object's plugin id + all parameters (used to drive e.g. Octane Scatter). |
| `instance_check.py` | Explain why two "same model" meshes did/didn't instance. |

## Getting assets in (format matters)

**Use FBX or glTF, not OBJ.** OBJ/MTL cannot represent PBR maps, the
material↔texture links, cutout opacity, multiple UVs, or instancing — so a
glTF→OBJ round-trip is what leaves materials bare and leaves rendering as solid
cards.

- **glTF / GLB** import is native in Cinema 4D **2024+** (File → Merge Object…).
  Prefer the self-contained **`.glb`**; a `.gltf` needs its `.bin` + textures
  alongside it.
- If glTF import misbehaves, **convert glTF → FBX** (e.g. via Blender) and import
  the FBX — FBX preserves materials, texture references, UVs and hierarchy.

## Stage 1 — Import (`1_import_objects.py`)

Pick the objects folder and **Import**. Options:

- **Group under Nulls** — parent each file's objects under a Null named after the
  file. Names are cleaned: a common **prefix** (e.g. `dota_d_`) is stripped
  (blank = auto-detect, or type your own), and `_<number>` split parts collapse
  onto their base (`rocks001`, `rocks001_1…` → `rocks001`) while attached
  numbers stay distinct (`tree007` ≠ `tree008`).
- **Spread apart** — *off by default*; on, it offsets files along X. Off keeps
  the source positions/scale exactly.
- **Preview groups** shows the planned Nulls without importing.

### Grouping objects already in the scene (`group_objects.py`)

If you merged one big FBX/glTF, everything lands flat with names like
`dire_tower002.dire_tower002.010`. This groups top-level objects under Nulls by
name — by default the base is everything before the first dot
(`dire_tower002.dire_tower002.010` → `dire_tower002`), so all copies of a model
go under one Null. For Source 2 / Dota mesh names like
`n0_lr0_c0_s_cb_nomerge84_water_flow.meshset_0`, tick **Strip Source 2 mesh
boilerplate** to peel off the `n0_/lr0_/c0_/s_/cb_/nomergeNNN_/…/.meshset_0`
wrapper down to the real asset (`water_flow`). If the asset itself ends in an
*instance index* (e.g. `mesh_overlay117`, `mesh_overlay332` — same asset,
different copy), tick **Strip trailing numbers** to merge them all into one
`mesh_overlay` group (aggressive — it also merges `tree007`/`tree008`, so use
it only when the trailing number is an instance index). Better for most cases
is **Nested**: builds `category > model > instances`
(`tree > tree008 > tree008_1/2/3`, `mesh_overlay > mesh_overlay117`), giving a
tidy ~N top-level categories while keeping every specific name underneath —
without merging different models. Other options: strip a common prefix
(auto-detect), merge `_N` split parts, and "only group 2+ objects" (leave
unique objects loose). Positions and scale are preserved (it only re-parents).
**Preview groups** shows the resulting top-level count.

## Stage 2 — Instancing (`2_mesh_instancer.py`)

**Analyze** reports groups of identical-shape meshes and how many objects would
collapse into instances; **Convert** replaces the duplicates with Render
Instances at their exact transforms. It matches meshes **up to a transform**
(so baked-in world-space duplicates are still found) by bucketing on
topology and solving/verifying the affine transform between candidates.

## Stage 3 — Octane inspector (`3_octane_inspector.py`)

Only needed if your Octane version differs from the one this was built against
(Octane 2025). Select an Octane Universal material (with an Image Texture on a
channel) and run it; it dumps the parameter IDs to a window and a text file.

## Stage 4 — Octane texture linking (`4_octane_texture_linker.py`)

Keep one Octane **Universal** material in the scene as a template. Pick the
texture folder, optionally **Dry run** to preview, then **Start** and step
through **group by group** (avoids the all-at-once compile that crashes Octane).

For each standard material it clones the template, wires the matching maps, and
swaps it onto the objects that used the material. Highlights:

- **Matching uses the material's embedded texture reference** (from the
  imported MTL/FBX/glTF), reduced to an asset stem — so it works even when the
  material name and texture name differ. Falls back to the material name. The
  dry-run log shows the resolved `[key:...]`.
- **Source 2 / Dota naming understood:** `*_metalnessmask_*` → metalness,
  `*_specmask_*` / `*_vmat_g_tspecular*` → specular, `*_selfillum_*` → emission,
  `*_refl_*` → reflection, `*_trans_*` / `*_cutout_*` → **Opacity**. Packed
  `*_orm_*` maps are skipped; a file with no channel word is treated as colour.
- The template's placeholder textures are stripped per clone, so the **Diffuse
  map always loads** and every present map (incl. Roughness/Opacity) is wired.

Channels wired: Diffuse, Specular, Metalness, Roughness, Reflection, Bump,
Normal, Displacement, Opacity, Transmission, Emission.

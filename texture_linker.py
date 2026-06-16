"""
texture_linker.py
=================

Auto-connect texture maps to the correct material channels in Cinema 4D S24.

Workflow this is built for
--------------------------
1. Import a large OBJ + MTL group (Cinema 4D creates classic materials for it).
2. Run this script and point it at a folder full of texture maps.
3. For every material in the scene the script looks for textures whose file
   name contains the material's name, figures out which channel each map
   belongs to from its file-name suffix (BaseColor / Normal / Roughness / ...),
   creates a bitmap shader, and plugs it into the right slot.

It targets the **classic material** (``c4d.Mmaterial``) which is what the OBJ/MTL
importer produces. Redshift / Standard (node) materials are skipped with a note.

How to run
----------
- Cinema 4D  ->  Script Manager (Shift+F11)  ->  open this file  ->  Execute.
- Or drop it in your scripts folder so it shows up under Extensions > User Scripts.

Tweak the CONFIG block and the CHANNEL_KEYWORDS table below to match your
studio's texture naming.
"""

import os
import re
import c4d
from c4d import gui, storage


# ---------------------------------------------------------------------------
# CONFIG  -- edit these to taste
# ---------------------------------------------------------------------------

# If set to a path string, that folder is used and no dialog is shown.
# Leave as None to be prompted with a folder picker each run.
TEXTURE_FOLDER = None

# Search sub-folders too.
RECURSIVE = True

# Preview only: log what *would* happen without touching any material.
DRY_RUN = False

# Overwrite a channel even if it already has a shader in it.
OVERWRITE_EXISTING = False

# How a texture file is matched to a material:
#   "name_in_filename" -> the material name must appear in the file name
#                         (best when many materials share one folder).
#   "all"              -> every file in the folder is considered a candidate
#                         for every material (best for one-material-per-folder).
MATCH_MODE = "name_in_filename"

# Allow single-letter suffixes like  wood_D / wood_N / wood_R.
# Handy, but can mis-fire on odd names -- turn off if you get false hits.
ALLOW_SINGLE_LETTER_SUFFIX = True

# Image extensions to consider.
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp",
    ".exr", ".hdr", ".psd", ".iff", ".pict", ".dds", ".webp",
}


# ---------------------------------------------------------------------------
# CHANNEL MAPPING
# ---------------------------------------------------------------------------
# Each "channel" is an internal key. CHANNEL_KEYWORDS maps a key to the
# file-name tokens that identify it. Longer/more specific keywords should be
# listed; matching is done token-by-token (split on _ - . and spaces).
#
# "color" data is treated as sRGB; everything else as linear.

CHANNEL_KEYWORDS = {
    "color":        ["basecolor", "base_color", "albedo", "diffuse", "diff",
                     "color", "col", "base", "d"],
    "luminance":    ["emissive", "emission", "emit", "glow", "luminance",
                     "lum", "e"],
    "specular":     ["specular", "spec", "s"],
    "reflection":   ["reflection", "reflect", "refl"],
    "roughness":    ["roughness", "rough", "rgh", "r"],
    "glossiness":   ["glossiness", "glossy", "gloss", "gls"],
    "metalness":    ["metalness", "metallic", "metal", "mtl", "m"],
    "normal":       ["normal", "nrml", "nrm", "norm", "n"],
    "bump":         ["bump", "bmp", "b"],
    "displacement": ["displacement", "displace", "disp", "height", "heightmap",
                     "h"],
    "alpha":        ["opacity", "alpha", "mask", "o"],
    "transparency": ["transparency", "transmission", "transmit", "refraction",
                     "trans"],
    "ao":           ["ambientocclusion", "occlusion", "ao"],
}

# Single-letter tokens are only honoured when ALLOW_SINGLE_LETTER_SUFFIX is on.
_SINGLE_LETTER = {kw for kws in CHANNEL_KEYWORDS.values() for kw in kws
                  if len(kw) == 1}


# ---------------------------------------------------------------------------
# Channel -> classic material assignment
# ---------------------------------------------------------------------------
# Each entry: (USE flag id, shader-link channel id, is_color_data)
# Channels not present in the classic material (roughness/metalness/reflection)
# are handled separately via the reflectance layer in _assign_reflectance().

def _build_channel_table():
    return {
        "color":        (c4d.MATERIAL_USE_COLOR,
                         c4d.MATERIAL_COLOR_SHADER, True),
        "luminance":    (c4d.MATERIAL_USE_LUMINANCE,
                         c4d.MATERIAL_LUMINANCE_SHADER, True),
        "specular":     (c4d.MATERIAL_USE_SPECULARCOLOR,
                         c4d.MATERIAL_SPECULAR_COLOR_SHADER, True),
        "transparency": (c4d.MATERIAL_USE_TRANSPARENCY,
                         c4d.MATERIAL_TRANSPARENCY_SHADER, True),
        "alpha":        (c4d.MATERIAL_USE_ALPHA,
                         c4d.MATERIAL_ALPHA_SHADER, False),
        "bump":         (c4d.MATERIAL_USE_BUMP,
                         c4d.MATERIAL_BUMP_SHADER, False),
        "normal":       (c4d.MATERIAL_USE_NORMAL,
                         c4d.MATERIAL_NORMAL_SHADER, False),
        "displacement": (c4d.MATERIAL_USE_DISPLACEMENT,
                         c4d.MATERIAL_DISPLACEMENT_SHADER, False),
    }


# Reflectance-based maps that the classic material exposes through its
# Reflectance channel rather than a top-level slot.
REFLECTANCE_CHANNELS = {"reflection", "roughness", "glossiness", "metalness"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text):
    """Lower-case and strip everything but letters/digits, for name matching."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tokenize(name):
    """Split a base file name into lower-case tokens."""
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def gather_textures(folder, recursive):
    """Return a list of (full_path, file_name) for every image in folder."""
    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    found.append((os.path.join(root, f), f))
    else:
        for f in os.listdir(folder):
            full = os.path.join(folder, f)
            if (os.path.isfile(full)
                    and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS):
                found.append((full, f))
    return found


def detect_channel(file_name):
    """Work out which channel a texture belongs to from its name.

    Scans tokens from the end of the name (the suffix usually carries the
    map type) and returns the first channel key that matches, or None.
    """
    base = os.path.splitext(file_name)[0]
    tokens = _tokenize(base)

    # Build token -> channel lookup (first keyword wins on ties).
    token_to_channel = {}
    for channel, keywords in CHANNEL_KEYWORDS.items():
        for kw in keywords:
            token_to_channel.setdefault(kw, channel)

    for token in reversed(tokens):
        if len(token) == 1 and not ALLOW_SINGLE_LETTER_SUFFIX:
            continue
        if token in _SINGLE_LETTER and not ALLOW_SINGLE_LETTER_SUFFIX:
            continue
        if token in token_to_channel:
            return token_to_channel[token]
    return None


def file_matches_material(file_name, material_name):
    """True if this file should be considered for this material."""
    if MATCH_MODE == "all":
        return True
    return _normalize(material_name) in _normalize(file_name)


def make_bitmap_shader(mat, path, is_color):
    """Create and insert an Xbitmap shader loaded with `path`."""
    shader = c4d.BaseList2D(c4d.Xbitmap)
    shader[c4d.BITMAPSHADER_FILENAME] = path

    # Colour profile: sRGB for colour-bearing maps, linear for data maps.
    try:
        shader[c4d.BITMAPSHADER_COLORPROFILE] = (
            c4d.BITMAPSHADER_COLORPROFILE_SRGB if is_color
            else c4d.BITMAPSHADER_COLORPROFILE_LINEAR)
    except AttributeError:
        pass  # constant not available in this build; leave default

    mat.InsertShader(shader)
    return shader


def _assign_reflectance(mat, channel, path, log):
    """Plug roughness / metalness / reflection maps into a reflectance layer.

    The classic material exposes these through the Reflectance channel rather
    than a dedicated top-level slot. We make sure a GGX layer exists and route
    the reflection-colour map onto it. Roughness / metalness do not have a
    plain texture slot in the classic reflectance UI, so they are reported for
    manual hookup instead of being silently dropped.
    """
    mat[c4d.MATERIAL_USE_REFLECTION] = True

    if mat.GetReflectionLayerCount() == 0:
        mat.AddReflectionLayer()

    layer = mat.GetReflectionLayerIndex(0)
    if layer is None:
        log.append("      ! could not access a reflectance layer; skipped")
        return False

    base = layer.GetDataID()

    if channel == "reflection":
        shader = make_bitmap_shader(mat, path, False)
        mat[base + c4d.REFLECTION_LAYER_COLOR_TEXTURE] = shader
        return True

    # roughness / glossiness / metalness: no direct bitmap slot on the classic
    # reflectance layer -- flag it rather than guess at internal IDs.
    log.append("      ~ %s map detected -- assign manually on the "
               "Reflectance layer: %s" % (channel, os.path.basename(path)))
    return False


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def process_material(mat, textures, channel_table, log):
    """Assign matching textures to one material. Returns count assigned."""
    mat_name = mat.GetName()
    log.append("Material: %s" % mat_name)

    # Collect candidate files and the channel each one maps to.
    # Keep only the first file found per channel (avoid double assignment).
    chosen = {}
    for path, fname in textures:
        if not file_matches_material(fname, mat_name):
            continue
        channel = detect_channel(fname)
        if channel is None:
            continue
        if channel == "ao":
            continue  # no dedicated classic slot; usually baked into color
        chosen.setdefault(channel, path)

    if not chosen:
        log.append("   (no matching textures found)")
        return 0

    assigned = 0
    for channel, path in sorted(chosen.items()):
        short = os.path.basename(path)

        if channel in REFLECTANCE_CHANNELS:
            if DRY_RUN:
                log.append("   [dry] %-12s -> Reflectance  (%s)"
                           % (channel, short))
                continue
            if _assign_reflectance(mat, channel, path, log):
                log.append("   [ok]  %-12s -> Reflectance  (%s)"
                           % (channel, short))
                assigned += 1
            continue

        if channel not in channel_table:
            continue

        use_id, shader_id, is_color = channel_table[channel]

        if not OVERWRITE_EXISTING and mat[shader_id] is not None:
            log.append("   [skip] %-12s already has a shader" % channel)
            continue

        if DRY_RUN:
            log.append("   [dry] %-12s -> %s" % (channel, short))
            continue

        shader = make_bitmap_shader(mat, path, is_color)
        mat[use_id] = True
        mat[shader_id] = shader
        log.append("   [ok]  %-12s -> %s" % (channel, short))
        assigned += 1

    if not DRY_RUN:
        mat.Update(True, True)
        mat.Message(c4d.MSG_UPDATE)

    return assigned


def main():
    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return

    # 1. Resolve the texture folder.
    folder = TEXTURE_FOLDER
    if not folder:
        folder = storage.LoadDialog(
            title="Select the texture folder",
            flags=c4d.FILESELECT_DIRECTORY)
    if not folder or not os.path.isdir(folder):
        gui.MessageDialog("No valid texture folder selected.")
        return

    # 2. Index the textures.
    textures = gather_textures(folder, RECURSIVE)
    if not textures:
        gui.MessageDialog("No image files found in:\n%s" % folder)
        return

    materials = doc.GetMaterials()
    if not materials:
        gui.MessageDialog("The scene has no materials.")
        return

    channel_table = _build_channel_table()
    log = ["Texture folder: %s" % folder,
           "Images found:   %d" % len(textures),
           "Materials:      %d" % len(materials),
           "Match mode:     %s" % MATCH_MODE,
           "Dry run:        %s" % DRY_RUN,
           "-" * 60]

    doc.StartUndo()
    total = 0
    try:
        for mat in materials:
            if mat.GetType() != c4d.Mmaterial:
                log.append("Material: %s  (not a classic material -- skipped)"
                           % mat.GetName())
                continue
            if not DRY_RUN:
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)
            total += process_material(mat, textures, channel_table, log)
            log.append("")
    finally:
        doc.EndUndo()

    log.append("-" * 60)
    log.append("Done. %d texture(s) %s."
               % (total, "would be assigned" if DRY_RUN else "assigned"))

    c4d.EventAdd()

    report = "\n".join(log)
    print(report)                 # full log to the Python console
    gui.MessageDialog(report)     # summary dialog


if __name__ == "__main__":
    main()

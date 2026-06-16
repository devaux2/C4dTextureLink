"""
texture_tool.py
===============

Cinema 4D S24 -- one self-contained tool that:

    1. (optional) imports a whole folder of 3D objects (OBJ/FBX/...) into the
       current scene, then
    2. auto-connects textures from a folder to the correct material channels
       (BaseColor / Normal / Roughness / Bump / Displacement / Opacity / ...).

It opens a small dialog so you can SEE what is happening:
    - pick the folders with Browse buttons,
    - toggle dry-run / match mode / etc,
    - a progress bar shows in C4D's status bar while it runs,
    - a results log fills the window (and any error is shown there too).

This file has NO external dependencies -- everything lives here, so there is
nothing to import and nothing to break.

How to run
----------
Cinema 4D -> Script Manager (Shift+F11) -> open this file -> Execute.
"""

import os
import re
import traceback
import c4d
from c4d import gui, storage, documents


# ===========================================================================
# CONFIG you may want to tweak (the dialog overrides most of these at runtime)
# ===========================================================================

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp",
    ".exr", ".hdr", ".psd", ".iff", ".pict", ".dds", ".webp",
}

OBJECT_EXTENSIONS = {
    ".obj", ".fbx", ".3ds", ".dae", ".stl", ".ply", ".abc",
    ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz", ".c4d",
}

# How an imported file is offset so a folder of objects doesn't pile up.
SPREAD_SPACING = 200.0

# File-name tokens that identify each channel (edit for your naming).
CHANNEL_KEYWORDS = {
    "color":        ["basecolor", "albedo", "diffuse", "diff", "color", "col",
                     "base", "d"],
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

_SINGLE_LETTER = {kw for kws in CHANNEL_KEYWORDS.values() for kw in kws
                  if len(kw) == 1}

REFLECTANCE_CHANNELS = {"reflection", "roughness", "glossiness", "metalness"}


# ===========================================================================
# Options bundle (filled from the dialog)
# ===========================================================================

class Options(object):
    def __init__(self):
        self.do_import = True
        self.objects_folder = ""
        self.textures_folder = ""
        self.recursive_objects = False
        self.recursive_textures = True
        self.spread = True
        self.match_mode = "auto"        # "auto" | "name" | "all"
        self.allow_single_letter = True
        self.dry_run = False
        self.overwrite = False


# ===========================================================================
# Pure helpers (no UI) -- these are unit-tested off-DCC
# ===========================================================================

def _normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tokenize(name):
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def gather_files(folder, extensions, recursive):
    """Return (full_path, rel_path) for matching files at/under folder."""
    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in extensions:
                    full = os.path.join(root, f)
                    found.append((full, os.path.relpath(full, folder)))
    else:
        for f in sorted(os.listdir(folder)):
            full = os.path.join(folder, f)
            if (os.path.isfile(full)
                    and os.path.splitext(f)[1].lower() in extensions):
                found.append((full, f))
    return found


def detect_channel(file_path, allow_single_letter):
    """Identify a texture's channel from its file-name suffix, or None."""
    base = os.path.splitext(os.path.basename(file_path))[0]
    tokens = _tokenize(base)

    token_to_channel = {}
    for channel, keywords in CHANNEL_KEYWORDS.items():
        for kw in keywords:
            token_to_channel.setdefault(kw, channel)

    for token in reversed(tokens):
        if token in _SINGLE_LETTER and not allow_single_letter:
            continue
        if token in token_to_channel:
            return token_to_channel[token]
    return None


def file_matches_material(rel_path, material_name, match_all):
    """True if this texture should be considered for this material."""
    if match_all:
        return True
    mat_n = _normalize(material_name)
    path_n = _normalize(rel_path)
    if not mat_n:
        return False
    if mat_n in path_n:
        return True
    tokens = [t for t in _tokenize(material_name) if len(t) >= 3]
    if tokens and all(_normalize(t) in path_n for t in tokens):
        return True
    return False


# ===========================================================================
# Material assignment
# ===========================================================================

def _channel_table():
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


def make_bitmap_shader(mat, path, is_color):
    shader = c4d.BaseList2D(c4d.Xbitmap)
    shader[c4d.BITMAPSHADER_FILENAME] = path
    try:
        shader[c4d.BITMAPSHADER_COLORPROFILE] = (
            c4d.BITMAPSHADER_COLORPROFILE_SRGB if is_color
            else c4d.BITMAPSHADER_COLORPROFILE_LINEAR)
    except AttributeError:
        pass
    mat.InsertShader(shader)
    return shader


def _assign_reflectance(mat, channel, path, dry_run, log):
    if dry_run:
        log("   [dry] %-12s -> Reflectance  (%s)"
            % (channel, os.path.basename(path)))
        return False
    mat[c4d.MATERIAL_USE_REFLECTION] = True
    if mat.GetReflectionLayerCount() == 0:
        mat.AddReflectionLayer()
    layer = mat.GetReflectionLayerIndex(0)
    if layer is None:
        log("   !     could not access a reflectance layer; skipped")
        return False
    base = layer.GetDataID()
    if channel == "reflection":
        shader = make_bitmap_shader(mat, path, False)
        mat[base + c4d.REFLECTION_LAYER_COLOR_TEXTURE] = shader
        log("   [ok]  %-12s -> Reflectance  (%s)"
            % (channel, os.path.basename(path)))
        return True
    log("   ~     %s map detected -- assign manually on the Reflectance "
        "layer (%s)" % (channel, os.path.basename(path)))
    return False


def process_material(mat, textures, channel_table, match_all, opts, log):
    """Assign matching textures to one material. Returns count assigned."""
    log("Material: %s" % mat.GetName())

    chosen = {}
    for path, rel in textures:
        if not file_matches_material(rel, mat.GetName(), match_all):
            continue
        channel = detect_channel(rel, opts.allow_single_letter)
        if channel is None or channel == "ao":
            continue
        chosen.setdefault(channel, path)

    if not chosen:
        log("   (no matching textures found)")
        return 0

    assigned = 0
    for channel, path in sorted(chosen.items()):
        short = os.path.basename(path)

        if channel in REFLECTANCE_CHANNELS:
            if _assign_reflectance(mat, channel, path, opts.dry_run, log):
                assigned += 1
            continue

        if channel not in channel_table:
            continue
        use_id, shader_id, is_color = channel_table[channel]

        if not opts.overwrite and mat[shader_id] is not None:
            log("   [skip] %-12s already has a shader" % channel)
            continue

        if opts.dry_run:
            log("   [dry] %-12s -> %s" % (channel, short))
            continue

        shader = make_bitmap_shader(mat, path, is_color)
        mat[use_id] = True
        mat[shader_id] = shader
        log("   [ok]  %-12s -> %s" % (channel, short))
        assigned += 1

    if not opts.dry_run:
        mat.Update(True, True)
        mat.Message(c4d.MSG_UPDATE)
    return assigned


def link_all(doc, opts, log, progress=None):
    """Link every classic material in doc to textures. Returns count."""
    folder = opts.textures_folder
    textures = gather_files(folder, IMAGE_EXTENSIONS, opts.recursive_textures)
    if not textures:
        log("No image files found in: %s" % folder)
        return 0

    materials = doc.GetMaterials()
    if not materials:
        log("The scene has no materials.")
        return 0

    classic = [m for m in materials if m.GetType() == c4d.Mmaterial]
    match_all = decide_match_all(opts, len(classic))

    log("Images found: %d   Materials: %d (%d classic)   Match: %s"
        % (len(textures), len(materials), len(classic),
           "all" if match_all else "by name"))
    log("-" * 58)

    channel_table = _channel_table()
    total = 0
    for i, mat in enumerate(materials):
        if progress:
            progress(i, len(materials), "Linking " + mat.GetName())
        if mat.GetType() != c4d.Mmaterial:
            log("Material: %s  (not a classic material -- skipped)"
                % mat.GetName())
            log("")
            continue
        if not opts.dry_run:
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)
        total += process_material(mat, textures, channel_table,
                                  match_all, opts, log)
        log("")

    if total == 0 and not match_all and classic:
        log("Nothing matched -- material names probably don't appear in the "
            "texture file names.")
        log("Materials: %s" % ", ".join(m.GetName() for m in classic[:12]))
        log("Examples:  %s"
            % ", ".join(os.path.basename(p) for p, _ in textures[:6]))
        log("Fix: rename files to include the material name, use per-material "
            "sub-folders, or set Match mode = All files.")
    return total


def decide_match_all(opts, classic_count):
    """Resolve the effective match mode to a simple boolean."""
    if opts.match_mode == "all":
        return True
    if opts.match_mode == "name":
        return False
    return classic_count == 1  # auto


def merge_one(doc, full, opts, offset_index, log):
    """Merge a single 3D file into doc. Returns (ok, new_offset_index)."""
    flags = (c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS
             | c4d.SCENEFILTER_MERGESCENE)

    # c4d.BaseObject is unhashable, so diff by id(). Keep references in
    # `before_objs` so the wrappers (and thus their ids) stay valid.
    before_objs = []
    before_ids = set()
    obj = doc.GetFirstObject()
    while obj:
        before_objs.append(obj)
        before_ids.add(id(obj))
        obj = obj.GetNext()

    if not documents.MergeDocument(doc, full, flags):
        log("   ! failed to import: %s" % os.path.basename(full))
        return False, offset_index

    new_roots = []
    obj = doc.GetFirstObject()
    while obj:
        if id(obj) not in before_ids:
            new_roots.append(obj)
        obj = obj.GetNext()

    if opts.spread and new_roots:
        for root in new_roots:
            doc.AddUndo(c4d.UNDOTYPE_CHANGE, root)
            pos = root.GetRelPos()
            pos.x += offset_index * SPREAD_SPACING
            root.SetRelPos(pos)
        offset_index += 1

    log("   [ok] %s  (%d object(s))"
        % (os.path.basename(full), len(new_roots)))
    return True, offset_index


def import_objects(doc, opts, log, progress=None):
    """Merge every importable file in the objects folder. Returns count."""
    paths = gather_files(opts.objects_folder, OBJECT_EXTENSIONS,
                         opts.recursive_objects)
    if not paths:
        log("No importable 3D files found in: %s" % opts.objects_folder)
        return 0

    imported = 0
    offset_index = 0
    for i, (full, _rel) in enumerate(paths):
        if progress:
            progress(i, len(paths), "Importing " + os.path.basename(full))
        ok, offset_index = merge_one(doc, full, opts, offset_index, log)
        if ok:
            imported += 1
    return imported


def run_pipeline(opts, log, progress=None):
    """Top-level: optional import then link. Returns (imported, assigned)."""
    doc = documents.GetActiveDocument()
    if doc is None:
        log("No active document.")
        return 0, 0

    imported = 0
    doc.StartUndo()
    try:
        if opts.do_import:
            log("=" * 58)
            log("IMPORT  (%s)" % opts.objects_folder)
            imported = import_objects(doc, opts, log, progress)
            log("Imported %d file(s)." % imported)
            c4d.EventAdd()  # realise imported objects/materials before linking

        log("")
        log("=" * 58)
        log("LINK TEXTURES  (%s)%s"
            % (opts.textures_folder, "  [DRY RUN]" if opts.dry_run else ""))
        assigned = link_all(doc, opts, log, progress)
    finally:
        doc.EndUndo()

    c4d.EventAdd()
    log("")
    log("=" * 58)
    log("Done. Imported %d file(s); %d texture(s) %s."
        % (imported, assigned,
           "would be assigned" if opts.dry_run else "assigned"))
    return imported, assigned


# ===========================================================================
# Dialog
# ===========================================================================

G_IMPORT = 1001
G_OBJ_FOLDER = 1002
G_OBJ_BROWSE = 1003
G_TEX_FOLDER = 1004
G_TEX_BROWSE = 1005
G_MATCH = 1006
G_DRYRUN = 1007
G_REC_TEX = 1008
G_OVERWRITE = 1009
G_SPREAD = 1010
G_REC_OBJ = 1011
G_RUN = 1012
G_CANCEL = 1013
G_CLOSE = 1014
G_LOG = 1015
G_PROG = 1016

MATCH_AUTO = 0
MATCH_NAME = 1
MATCH_ALL = 2

# Milliseconds of work per Timer tick before yielding back to the UI.
TICK_BUDGET_MS = 50

_dialog = None  # keep the async dialog alive


class ProgressArea(gui.GeUserArea):
    """A simple horizontal progress bar drawn in the dialog."""

    def __init__(self):
        super(ProgressArea, self).__init__()
        self.percent = 0.0
        self.label = ""

    def GetMinSize(self):
        return (220, 18)

    def set(self, percent, label):
        self.percent = max(0.0, min(1.0, percent))
        self.label = label
        self.Redraw()

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        w = x2 - x1 + 1
        # track
        self.DrawSetPen(c4d.Vector(0.16, 0.16, 0.16))
        self.DrawRectangle(x1, y1, x2, y2)
        # fill
        fill = int(w * self.percent)
        if fill > 0:
            self.DrawSetPen(c4d.Vector(0.26, 0.55, 0.9))
            self.DrawRectangle(x1, y1, x1 + fill, y2)
        # text
        self.DrawSetTextCol(c4d.Vector(1.0), c4d.COLOR_TRANS)
        self.DrawText("%d%%  %s" % (int(self.percent * 100), self.label),
                      x1 + 5, y1 + 1)


class TextureToolDialog(gui.GeDialog):

    def __init__(self):
        super(TextureToolDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur_label = ""

    # --- layout -----------------------------------------------------------
    def CreateLayout(self):
        self.SetTitle("Import & Texture Linker")

        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        # Import row
        self.AddCheckbox(G_IMPORT, c4d.BFH_LEFT, 0, 0,
                         "Import objects from a folder first")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Objects folder", 0)
        self.AddEditText(G_OBJ_FOLDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_OBJ_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        # Texture row
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Texture folder", 0)
        self.AddEditText(G_TEX_FOLDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_TEX_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.AddSeparatorH(0)

        # Options
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Match mode", 0)
        self.AddComboBox(G_MATCH, c4d.BFH_LEFT, 220, 0)
        self.AddChild(G_MATCH, MATCH_AUTO,
                      "Auto (by name; all if 1 material)")
        self.AddChild(G_MATCH, MATCH_NAME, "By material name only")
        self.AddChild(G_MATCH, MATCH_ALL, "All files -> every material")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddCheckbox(G_DRYRUN, c4d.BFH_LEFT, 0, 0,
                         "Dry run (link preview)")
        self.AddCheckbox(G_OVERWRITE, c4d.BFH_LEFT, 0, 0,
                         "Overwrite existing")
        self.AddCheckbox(G_REC_TEX, c4d.BFH_LEFT, 0, 0, "Recurse textures")
        self.AddCheckbox(G_REC_OBJ, c4d.BFH_LEFT, 0, 0, "Recurse objects")
        self.AddCheckbox(G_SPREAD, c4d.BFH_LEFT, 0, 0, "Spread imports")
        self.GroupEnd()

        self.AddSeparatorH(0)

        # Progress bar
        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        # Log
        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 200,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        # Buttons
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Run")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 90, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_IMPORT, True)
        self.SetBool(G_DRYRUN, False)
        self.SetBool(G_OVERWRITE, False)
        self.SetBool(G_REC_TEX, True)
        self.SetBool(G_REC_OBJ, False)
        self.SetBool(G_SPREAD, True)
        self.SetInt32(G_MATCH, MATCH_AUTO)
        self._enable_import_fields()
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Pick your folder(s), then press Run.")
        return True

    # --- helpers ----------------------------------------------------------
    def _enable_import_fields(self):
        on = self.GetBool(G_IMPORT)
        self.Enable(G_OBJ_FOLDER, on)
        self.Enable(G_OBJ_BROWSE, on)
        self.Enable(G_REC_OBJ, on)
        self.Enable(G_SPREAD, on)

    def _log(self, line):
        self._loglines.append(line)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _read_options(self):
        opts = Options()
        opts.do_import = self.GetBool(G_IMPORT)
        opts.objects_folder = self.GetString(G_OBJ_FOLDER).strip()
        opts.textures_folder = self.GetString(G_TEX_FOLDER).strip()
        opts.recursive_objects = self.GetBool(G_REC_OBJ)
        opts.recursive_textures = self.GetBool(G_REC_TEX)
        opts.spread = self.GetBool(G_SPREAD)
        opts.dry_run = self.GetBool(G_DRYRUN)
        opts.overwrite = self.GetBool(G_OVERWRITE)
        sel = self.GetInt32(G_MATCH)
        opts.match_mode = {MATCH_AUTO: "auto", MATCH_NAME: "name",
                           MATCH_ALL: "all"}.get(sel, "auto")
        return opts

    def _update_progress(self):
        i, n = 0, 0
        if self._phase == "import":
            i, n = self._idx, len(self._import_paths)
        elif self._phase == "link":
            i, n = self._idx, len(self._materials)
        frac = (float(i) / n) if n else (1.0 if self._phase == "finish"
                                         else 0.0)
        self._prog.set(frac, self._cur_label)
        c4d.StatusSetText(self._cur_label)
        c4d.StatusSetBar(int(frac * 100))

    # --- events -----------------------------------------------------------
    def Command(self, cid, msg):
        if cid == G_IMPORT:
            self._enable_import_fields()
        elif cid == G_OBJ_BROWSE:
            path = storage.LoadDialog(title="Select the OBJECTS folder",
                                      flags=c4d.FILESELECT_DIRECTORY)
            if path:
                self.SetString(G_OBJ_FOLDER, path)
        elif cid == G_TEX_BROWSE:
            path = storage.LoadDialog(title="Select the TEXTURE folder",
                                      flags=c4d.FILESELECT_DIRECTORY)
            if path:
                self.SetString(G_TEX_FOLDER, path)
        elif cid == G_RUN:
            self._start()
        elif cid == G_CANCEL:
            if self._running:
                self._cancel = True
        elif cid == G_CLOSE:
            if self._running:
                self._cancel = True  # finish cleanly, then it can be closed
            else:
                self.Close()
        return True

    def AskClose(self):
        # Block closing mid-run so the undo block stays balanced.
        if self._running:
            self._cancel = True
            return True  # abort the close for now
        return False

    # --- run pipeline incrementally on the timer --------------------------
    def _start(self):
        opts = self._read_options()
        if not opts.textures_folder or not os.path.isdir(opts.textures_folder):
            self.SetString(G_LOG, "Please choose a valid TEXTURE folder.")
            return
        if opts.do_import and (not opts.objects_folder
                               or not os.path.isdir(opts.objects_folder)):
            self.SetString(G_LOG, "Please choose a valid OBJECTS folder "
                                  "(or turn off 'Import objects').")
            return

        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return

        self._opts = opts
        self._loglines = []
        self._cancel = False
        self._idx = 0
        self._offset_index = 0
        self._imported_count = 0
        self._assigned_total = 0
        self._cur_label = "Starting..."

        self._doc.StartUndo()
        if opts.do_import:
            self._import_paths = gather_files(opts.objects_folder,
                                              OBJECT_EXTENSIONS,
                                              opts.recursive_objects)
            self._log("=" * 58)
            self._log("IMPORT  (%s)" % opts.objects_folder)
            if not self._import_paths:
                self._log("No importable 3D files found.")
                self._phase = "linkprep"
            else:
                self._log("%d file(s) to import." % len(self._import_paths))
                self._phase = "import"
        else:
            self._import_paths = []
            self._phase = "linkprep"

        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        """Do one unit of work; return True when everything is finished."""
        ph = self._phase

        if ph == "import":
            if self._idx < len(self._import_paths):
                full, _rel = self._import_paths[self._idx]
                self._cur_label = "Importing %d/%d  %s" % (
                    self._idx + 1, len(self._import_paths),
                    os.path.basename(full))
                ok, self._offset_index = merge_one(
                    self._doc, full, self._opts, self._offset_index, self._log)
                if ok:
                    self._imported_count += 1
                self._idx += 1
            else:
                self._log("Imported %d file(s)." % self._imported_count)
                self._phase = "linkprep"
            return False

        if ph == "linkprep":
            self._log("")
            self._log("=" * 58)
            self._log("LINK TEXTURES  (%s)%s" % (
                self._opts.textures_folder,
                "  [DRY RUN]" if self._opts.dry_run else ""))
            self._textures = gather_files(self._opts.textures_folder,
                                          IMAGE_EXTENSIONS,
                                          self._opts.recursive_textures)
            self._materials = self._doc.GetMaterials()
            self._classic = [m for m in self._materials
                             if m.GetType() == c4d.Mmaterial]
            self._match_all = decide_match_all(self._opts, len(self._classic))
            self._channel_table = _channel_table()
            self._log("Images: %d   Materials: %d (%d classic)   Match: %s"
                      % (len(self._textures), len(self._materials),
                         len(self._classic),
                         "all" if self._match_all else "by name"))
            self._log("-" * 58)
            self._idx = 0
            if not self._textures:
                self._log("No image files found in the texture folder.")
                self._phase = "finish"
            elif not self._materials:
                self._log("The scene has no materials.")
                self._phase = "finish"
            else:
                self._phase = "link"
            return False

        if ph == "link":
            if self._idx < len(self._materials):
                mat = self._materials[self._idx]
                self._cur_label = "Linking %d/%d  %s" % (
                    self._idx + 1, len(self._materials), mat.GetName())
                if mat.GetType() != c4d.Mmaterial:
                    self._log("Material: %s  (not a classic material -- "
                              "skipped)" % mat.GetName())
                    self._log("")
                else:
                    if not self._opts.dry_run:
                        self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)
                    self._assigned_total += process_material(
                        mat, self._textures, self._channel_table,
                        self._match_all, self._opts, self._log)
                    self._log("")
                self._idx += 1
            else:
                if (self._assigned_total == 0 and not self._match_all
                        and self._classic):
                    self._log("Nothing matched -- material names probably "
                              "don't appear in the texture file names.")
                    self._log("Materials: %s" % ", ".join(
                        m.GetName() for m in self._classic[:12]))
                    self._log("Examples:  %s" % ", ".join(
                        os.path.basename(p) for p, _ in self._textures[:6]))
                    self._log("Fix: rename files to include the material "
                              "name, use per-material sub-folders, or set "
                              "Match mode = All files.")
                self._phase = "finish"
            return False

        return True  # finish

    def _finish(self):
        if not self._running:
            return
        self._running = False
        self.SetTimer(0)
        try:
            self._doc.EndUndo()
        except Exception:
            pass
        c4d.StatusClear()
        c4d.EventAdd()
        self._log("")
        self._log("=" * 58)
        state = "Cancelled" if self._cancel else "Done"
        self._log("%s. Imported %d file(s); %d texture(s) %s." % (
            state, self._imported_count, self._assigned_total,
            "would be assigned" if self._opts.dry_run else "assigned"))
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_RUN, True)
        self.Enable(G_CANCEL, False)
        print("\n".join(self._loglines))

    def Timer(self, msg):
        if not self._running:
            return
        try:
            if self._cancel:
                self._log("Cancelling...")
                self._finish()
                return
            start = c4d.GeGetMilliseconds()
            done = False
            while (c4d.GeGetMilliseconds() - start) < TICK_BUDGET_MS:
                if self._step():
                    done = True
                    break
            self._update_progress()
            c4d.EventAdd()  # show imported objects / material changes live
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- the run stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = TextureToolDialog()
    # Async so C4D's viewport/status keep updating while it runs.
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=600)


if __name__ == "__main__":
    main()

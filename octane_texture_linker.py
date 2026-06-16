"""
octane_texture_linker.py
========================

Dedicated Octane texture linker for Cinema 4D (S24+ / Octane 2025).

For every standard material the OBJ/MTL import created, this builds an Octane
**Universal** material, wires the matching textures from a folder into the
correct Octane channels, and swaps it onto the objects that used the standard
material. Result: a fully Octane-textured scene, automatically.

Parameter IDs (read from this exact Octane build via octane_inspector.py):
    Octane material plugin .... 1029501
    Image Texture node ........ 1029508   (File param 1100)
    Channel 'Texture' links ... see OCT_LINK below
    Diffuse colour value ...... 2515

How Universal materials are made
--------------------------------
Octane's Universal material has version-specific type/BSDF/node-space defaults,
so instead of recreating them this script CLONES a template you already made:
keep one Octane **Universal** material in the scene (e.g. 'OctUniversal1'); it
is cloned for every converted material. Select it as the active material, or
just leave it in the scene and the script finds the first Octane material.

Run it
------
Script Manager (Shift+F11) -> open this file -> Execute.
Pick the texture folder, then Run. Use **Dry run** first to preview matches.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, storage, documents


# ---------------------------------------------------------------------------
# Octane IDs (from the inspector report)
# ---------------------------------------------------------------------------

OCT_MAT_ID = 1029501          # Octane Material plugin
OCT_IMG_ID = 1029508          # Octane Image Texture node
OCT_IMG_FILE = 1100           # Image Texture > File (filename)
OCT_DIFFUSE_COLOR = 2515      # Universal > Diffuse > Color (vector)

# channel key -> Universal "Texture" link parameter id
OCT_LINK = {
    "color":        2517,     # Diffuse
    "specular":     2524,     # Specular
    "metalness":    2587,     # Specular map (metallic)
    "roughness":    2533,     # Roughness
    "glossiness":   2533,     # (mapped to roughness)
    "reflection":   2528,     # Reflection
    "bump":         2539,     # Bump
    "normal":       2542,     # Normal
    "displacement": 2580,     # Displacement
    "alpha":        2545,     # Opacity
    "transmission": 2555,     # Transmission
    "emission":     2557,     # Emission
}

# Maps that carry actual colour (sRGB); the rest are data maps.
COLOR_CHANNELS = {"color", "specular", "reflection", "emission"}


# ---------------------------------------------------------------------------
# Texture matching (self-contained)
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp",
    ".exr", ".hdr", ".psd", ".iff", ".pict", ".dds", ".webp",
}

# Channel detection, priority-ordered. Tokens >= 4 chars match as substrings
# (so 'metalnessmask', 'tspecular', 'selfillum' are recognised); shorter tokens
# (and single letters) must match exactly to avoid false hits on hashes.
# Tuned for Source 2 / Dota exports like '<name>_metalnessmask_psd_<hash>'.
CHANNEL_RULES = [
    ("metalness",    ["metalnessmask", "metalness", "metallic", "metalmask",
                      "metal", "mtl"]),
    ("specular",     ["specularmask", "specmask", "tspecular", "specular",
                      "spec"]),
    ("roughness",    ["roughness", "rough", "rgh"]),
    ("glossiness",   ["glossiness", "glossy", "gloss", "gls"]),
    ("reflection",   ["reflection", "reflect", "refl"]),
    ("emission",     ["selfillum", "emissive", "emission", "emit", "glow",
                      "luminance", "illum", "lum"]),
    ("normal",       ["normalmap", "normal", "nrml", "nrm", "norm"]),
    ("bump",         ["bump", "bmp", "heightmap", "height"]),
    ("displacement", ["displacement", "displace", "disp"]),
    ("transmission", ["transmission", "transmit", "refraction",
                      "transparency", "trans"]),
    ("alpha",        ["opacitymask", "opacity", "alpha", "mask"]),
    ("color",        ["basecolor", "albedo", "diffuse", "diff", "color",
                      "col", "base"]),
]
# Tokens that mean "skip this file entirely" (packed/auxiliary maps).
SKIP_TOKENS = {"orm", "ao", "ambientocclusion", "occlusion", "mra", "rma",
               "spcmask"}
# Single-letter suffixes (only used when allow_single is on).
SINGLE = {"d": "color", "e": "emission", "s": "specular", "r": "roughness",
          "m": "metalness", "n": "normal", "b": "bump", "h": "displacement",
          "o": "alpha"}


def _normalize(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _tokenize(name):
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def gather_files(folder, recursive):
    found = []
    if recursive:
        for root, _d, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    full = os.path.join(root, f)
                    found.append((full, os.path.relpath(full, folder)))
    else:
        for f in sorted(os.listdir(folder)):
            full = os.path.join(folder, f)
            if (os.path.isfile(full)
                    and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS):
                found.append((full, f))
    return found


def detect_channel(file_path, allow_single):
    """Return a channel key, "skip" (ignore this file), or None (no channel
    word -- caller may treat it as the colour/diffuse map)."""
    base = os.path.splitext(os.path.basename(file_path))[0]
    # Scan tokens from the end (the map type is usually the last real word
    # before the _psd_/_tga_/<hash> tail).
    for tok in reversed(_tokenize(base)):
        if tok in SKIP_TOKENS:
            return "skip"
        for channel, kws in CHANNEL_RULES:
            for kw in kws:
                if len(kw) >= 4:
                    if kw in tok:           # substring (metalnessmask, ...)
                        return channel
                elif tok == kw:             # short tokens: exact only
                    return channel
        if allow_single and len(tok) == 1 and tok in SINGLE:
            return SINGLE[tok]
    return None


FORMAT_TOKENS = {"psd", "tga", "png", "jpg", "jpeg", "tif", "tiff", "exr",
                 "hdr", "bmp", "dds", "vmat", "g", "tex", "mat", "vtex"}


def _is_hashish(tok):
    """True for content-hash-looking tokens (long hex / long digit runs)."""
    if len(tok) >= 6 and all(c in "0123456789abcdef" for c in tok):
        return True
    if len(tok) >= 8 and tok.isdigit():
        return True
    return False


def _token_is_channel(tok):
    if tok in SKIP_TOKENS:
        return True
    for _ch, kws in CHANNEL_RULES:
        for kw in kws:
            if len(kw) >= 4:
                if kw in tok:
                    return True
            elif tok == kw:
                return True
    return False


def asset_key_from_filename(fn):
    """Reduce a texture filename to its asset stem (drop channel words, file
    formats and content hashes). 'leaves_white000_color_psd_2e440edb' ->
    'leaveswhite000'."""
    base = os.path.splitext(os.path.basename(str(fn)))[0]
    keep = []
    for t in _tokenize(base):
        if t in FORMAT_TOKENS or _is_hashish(t) or _token_is_channel(t):
            continue
        keep.append(t)
    return _normalize("".join(keep))


def material_asset_key(mat):
    """The asset stem of the texture the imported (MTL) material already
    references -- the most reliable key for finding its real maps. None if the
    material has no embedded bitmap."""
    if mat.GetType() != c4d.Mmaterial:
        return None
    for chan in (c4d.MATERIAL_COLOR_SHADER, c4d.MATERIAL_LUMINANCE_SHADER,
                 c4d.MATERIAL_NORMAL_SHADER, c4d.MATERIAL_BUMP_SHADER,
                 c4d.MATERIAL_ALPHA_SHADER):
        try:
            sh = mat[chan]
        except Exception:
            sh = None
        if sh is not None and sh.GetType() == c4d.Xbitmap:
            fn = sh[c4d.BITMAPSHADER_FILENAME]
            if fn:
                key = asset_key_from_filename(fn)
                if len(key) >= 3:
                    return key
    return None


def file_matches_material(rel_path, mat_name, asset_key, match_all):
    """Match by the material's embedded texture stem (asset_key) when known,
    falling back to the material name."""
    if match_all:
        return True
    pn = _normalize(rel_path)
    if asset_key and asset_key in pn:
        return True
    mn = _normalize(mat_name)
    if mn and mn in pn:
        return True
    toks = [t for t in _tokenize(mat_name) if len(t) >= 3]
    return bool(toks) and all(_normalize(t) in pn for t in toks)


# ---------------------------------------------------------------------------
# Octane material building
# ---------------------------------------------------------------------------

def find_template(doc):
    act = doc.GetActiveMaterial()
    if act is not None and act.GetType() == OCT_MAT_ID:
        return act
    for m in doc.GetMaterials():
        if m.GetType() == OCT_MAT_ID:
            return m
    return None


def make_image_node(mat, path, channel):
    img = c4d.BaseList2D(OCT_IMG_ID)
    img.SetName("%s_%s" % (channel, os.path.basename(path)))
    img[OCT_IMG_FILE] = path
    mat.InsertShader(img)
    return img


def clear_textures(mat):
    """Remove any placeholder texture nodes copied from the template clone and
    null every channel link, so freshly matched maps always go in (the
    template's Diffuse texture would otherwise read as 'already connected')."""
    for lid in set(OCT_LINK.values()):
        try:
            mat[lid] = None
        except Exception:
            pass
    sh = mat.GetFirstShader()
    while sh:
        nxt = sh.GetNext()
        sh.Remove()
        sh = nxt


def tags_under(root):
    """All texture tags on `root` and its descendants."""
    out = []

    def rec(o):
        while o:
            for t in o.GetTags():
                if t.GetType() == c4d.Ttexture:
                    out.append(t)
            rec(o.GetDown())
            o = o.GetNext()

    rec(root)
    return out


def wire_textures(new_mat, chosen, log):
    """chosen = {channel: path}. Returns number of channels wired.
    The clone is cleared first, so every matched map is linked (diffuse
    included)."""
    n = 0
    for channel, path in sorted(chosen.items()):
        link = OCT_LINK.get(channel)
        if link is None:
            continue
        img = make_image_node(new_mat, path, channel)
        new_mat[link] = img
        log("      [ok]  %-12s -> %s" % (channel, os.path.basename(path)))
        n += 1
    return n


def collect_matches(mat_name, asset_key, textures, match_all, allow_single):
    """Pick one texture per channel for a material. Files with no channel word
    (e.g. 'bones_tintable_002_psd_<hash>') are treated as the colour map if no
    explicit colour texture is found."""
    chosen = {}
    fallback_color = None
    for path, rel in textures:
        if not file_matches_material(rel, mat_name, asset_key, match_all):
            continue
        ch = detect_channel(rel, allow_single)
        if ch == "skip":
            continue
        if ch is None:
            if fallback_color is None:
                fallback_color = path
            continue
        chosen.setdefault(ch, path)
    if "color" not in chosen and fallback_color is not None:
        chosen["color"] = fallback_color
    return chosen


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

class ProgressArea(gui.GeUserArea):
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
        self.DrawSetPen(c4d.Vector(0.16, 0.16, 0.16))
        self.DrawRectangle(x1, y1, x2, y2)
        fill = int(w * self.percent)
        if fill > 0:
            self.DrawSetPen(c4d.Vector(0.26, 0.55, 0.9))
            self.DrawRectangle(x1, y1, x1 + fill, y2)
        self.DrawSetTextCol(c4d.Vector(1.0), c4d.COLOR_TRANS)
        self.DrawText("%d%%  %s" % (int(self.percent * 100), self.label),
                      x1 + 5, y1 + 1)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

TICK_BUDGET_MS = 50

G_TEX = 4001
G_BROWSE = 4002
G_MATCH = 4003
G_DRYRUN = 4004
G_OVERWRITE = 4005
G_RECURSE = 4006
G_REMOVE = 4007
G_COLOR = 4008
G_RUN = 4009
G_CANCEL = 4010
G_CLOSE = 4011
G_LOG = 4012
G_PROG = 4013
G_NEXT = 4014

MATCH_AUTO, MATCH_NAME, MATCH_ALL = 0, 1, 2

_dialog = None


class OctaneLinkerDialog(gui.GeDialog):
    def __init__(self):
        super(OctaneLinkerDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False        # a timer-driven step is in progress
        self._session = False        # a group-by-group conversion is underway
        self._mode = None            # "dry" | "group"
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Octane Texture Linker")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Texture folder", 0)
        self.AddEditText(G_TEX, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Match mode", 0)
        self.AddComboBox(G_MATCH, c4d.BFH_LEFT, 220, 0)
        self.AddChild(G_MATCH, MATCH_AUTO, "Auto (by name; all if 1 material)")
        self.AddChild(G_MATCH, MATCH_NAME, "By material name only")
        self.AddChild(G_MATCH, MATCH_ALL, "All files -> every material")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddCheckbox(G_DRYRUN, c4d.BFH_LEFT, 0, 0, "Dry run (preview)")
        self.AddCheckbox(G_RECURSE, c4d.BFH_LEFT, 0, 0, "Recurse textures")
        self.AddCheckbox(G_REMOVE, c4d.BFH_LEFT, 0, 0,
                         "Remove standard materials")
        self.AddCheckbox(G_COLOR, c4d.BFH_LEFT, 0, 0,
                         "Copy base colour")
        self.GroupEnd()

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 220,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Start")
        self.AddButton(G_NEXT, c4d.BFH_LEFT, 110, 0, "Next group")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_DRYRUN, False)
        self.SetBool(G_RECURSE, True)
        self.SetBool(G_REMOVE, True)
        self.SetBool(G_COLOR, True)
        self.SetInt32(G_MATCH, MATCH_AUTO)
        self.Enable(G_NEXT, False)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Keep one Octane Universal material in the scene "
                              "as a template, pick the texture folder, then "
                              "Start.\nConversion runs one group at a time: "
                              "after each group, check Octane is OK, then click "
                              "'Next group'.")
        return True

    def _set_buttons(self, run, nxt, cancel):
        self.Enable(G_RUN, run)
        self.Enable(G_NEXT, nxt)
        self.Enable(G_CANCEL, cancel)

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _progress(self, frac, label):
        self._prog.set(frac, label)
        c4d.StatusSetText(label)
        c4d.StatusSetBar(int(frac * 100))

    def Command(self, cid, msg):
        if cid == G_BROWSE:
            p = storage.LoadDialog(title="Select the TEXTURE folder",
                                   flags=c4d.FILESELECT_DIRECTORY)
            if p:
                self.SetString(G_TEX, p)
        elif cid == G_RUN:
            self._begin()
        elif cid == G_NEXT:
            self._advance()
        elif cid == G_CANCEL:
            if self._running:
                self._cancel = True       # stop after the current step
            elif self._session:
                self._abort()
        elif cid == G_CLOSE:
            if self._running:
                self._cancel = True
            elif self._session:
                self._abort()
            else:
                self.Close()
        return True

    def AskClose(self):
        if self._running:
            self._cancel = True
            return True
        if self._session:
            self._abort()
            return True
        return False

    def _begin(self):
        if self._running or self._session:
            return
        folder = self.GetString(G_TEX).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Please choose a valid texture folder.")
            return
        doc = documents.GetActiveDocument()
        if doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        template = find_template(doc)
        if template is None:
            self.SetString(G_LOG, "No Octane material found to use as a "
                                  "template.\nCreate one Octane Universal "
                                  "material in the scene first, then Start.")
            return

        self._doc = doc
        self._template = template
        self._dry = self.GetBool(G_DRYRUN)
        self._remove = self.GetBool(G_REMOVE)
        self._copy_color = self.GetBool(G_COLOR)
        sel = self.GetInt32(G_MATCH)
        mode = {MATCH_AUTO: "auto", MATCH_NAME: "name",
                MATCH_ALL: "all"}.get(sel, "auto")

        self._textures = gather_files(folder, self.GetBool(G_RECURSE))
        self._mats = [m for m in doc.GetMaterials()
                      if m.GetType() == c4d.Mmaterial]
        if mode == "all":
            self._match_all = True
        elif mode == "name":
            self._match_all = False
        else:
            self._match_all = (len(self._mats) == 1)

        self._loglines = []
        self._log("=" * 58)
        self._log("OCTANE TEXTURE LINK%s" % ("  [DRY RUN]" if self._dry else ""))
        self._log("Template: %s" % template.GetName())
        self._log("Textures: %d   Standard materials: %d   Match: %s"
                  % (len(self._textures), len(self._mats),
                     "all" if self._match_all else "by name"))
        self._log("-" * 58)
        if not self._textures:
            self._log("No image files found.")
            return
        if not self._mats:
            self._log("No standard materials to convert.")
            return

        self._cancel = False
        self._converted = 0
        self._wired = 0
        self._namemap = {}     # old name (lower) -> new octane material
        self._old = []         # originals to remove
        self._cur = "Starting..."

        if self._dry:
            self._mode = "dry"
            self._idx = 0
            self._dry_list = self._mats
            self._session = False
            self._running = True
            self._set_buttons(False, False, True)
            self.SetTimer(20)
            return

        # Group-by-group: build the groups, then process the first one and
        # WAIT for the user to confirm Octane is stable before continuing.
        self._mode = "group"
        self._groups = []
        o = self._doc.GetFirstObject()
        while o:
            self._groups.append((o.GetName(), tags_under(o)))
            o = o.GetNext()
        self._total_tags = sum(len(t) for _, t in self._groups) or 1
        self._done_tags = 0
        self._gi = 0
        self._session = True
        self._doc.StartUndo()
        self._log("%d group(s). One group is processed per click; check Octane "
                  "between groups." % len(self._groups))
        self._process_group()

    def _process_group(self):
        # Skip groups with no texture tags.
        while self._gi < len(self._groups) and not self._groups[self._gi][1]:
            self._gi += 1
        if self._gi >= len(self._groups):
            self._finalize()
            return
        self._ti = 0
        gname = self._groups[self._gi][0]
        self._cur = "Group %d/%d '%s'" % (self._gi + 1, len(self._groups),
                                          gname)
        self._running = True
        self._set_buttons(False, False, True)     # only Cancel while working
        self.SetTimer(20)

    def _group_done(self):
        self.SetTimer(0)
        self._running = False
        c4d.EventAdd()                            # load THIS group into Octane
        gname = self._groups[self._gi][0]
        self._gi += 1
        self._log("Group %d/%d '%s' done.  Converted %d, wired %d so far."
                  % (self._gi, len(self._groups), gname,
                     self._converted, self._wired))
        if self._gi >= len(self._groups):
            self._log(">> All groups processed. Click 'Next group' to finish "
                      "(remove originals + final refresh).")
        else:
            self._log(">> Check Octane is stable, then click 'Next group'.")
        self._update_progress()
        self._set_buttons(False, True, True)      # Next group + Cancel

    def _advance(self):
        if self._running or not self._session:
            return
        self._process_group()

    def _finalize(self):
        if self._remove:
            for orig in self._old:
                self._doc.AddUndo(c4d.UNDOTYPE_DELETE, orig)
                orig.Remove()
        try:
            self._doc.EndUndo()
        except Exception:
            pass
        c4d.EventAdd()
        c4d.StatusClear()
        self._session = False
        self._log("-" * 58)
        self._log("Done. Converted %d material(s); wired %d texture(s)."
                  % (self._converted, self._wired))
        self._prog.set(1.0, "Finished")
        self._set_buttons(True, False, False)
        print("\n".join(self._loglines))

    def _abort(self):
        self.SetTimer(0)
        self._running = False
        try:
            self._doc.EndUndo()
        except Exception:
            pass
        c4d.EventAdd()
        c4d.StatusClear()
        self._session = False
        self._log("-" * 58)
        self._log("Cancelled. Converted %d material(s) so far (one undo step)."
                  % self._converted)
        self._set_buttons(True, False, False)
        print("\n".join(self._loglines))

    def _convert_material(self, orig):
        """Build the Octane Universal material for `orig` and return it."""
        name = orig.GetName()
        key = material_asset_key(orig)
        chosen = collect_matches(name, key, self._textures,
                                 self._match_all, True)
        new = self._template.GetClone()
        new.SetName(name)
        clear_textures(new)        # so diffuse (and all maps) always load
        if self._copy_color and orig[c4d.MATERIAL_USE_COLOR] \
                and "color" not in chosen:
            try:
                new[OCT_DIFFUSE_COLOR] = orig[c4d.MATERIAL_COLOR_COLOR]
            except Exception:
                pass
        self._log("Material: %s%s  (%d map(s))"
                  % (name, "  [key:%s]" % key if key else "", len(chosen)))
        self._wired += wire_textures(new, chosen, self._log)
        self._doc.InsertMaterial(new)
        self._doc.AddUndo(c4d.UNDOTYPE_NEW, new)
        self._namemap[name.lower()] = new
        self._old.append(orig)
        self._converted += 1
        return new

    def _step(self):
        """Process one unit of work; return True when the current phase ends
        (the whole dry list, or the current group's tags)."""
        if self._mode == "dry":
            if self._idx < len(self._dry_list):
                orig = self._dry_list[self._idx]
                self._cur = "Preview %d/%d" % (self._idx + 1,
                                               len(self._dry_list))
                key = material_asset_key(orig)
                chosen = collect_matches(orig.GetName(), key, self._textures,
                                         self._match_all, True)
                self._log("Material: %s%s  (%d map(s))"
                          % (orig.GetName(),
                             "  [key:%s]" % key if key else "", len(chosen)))
                for ch, path in sorted(chosen.items()):
                    self._log("   [dry] %-12s -> %s"
                              % (ch, os.path.basename(path)))
                self._idx += 1
                return False
            return True

        # group mode: one texture tag of the current group
        gname, tags = self._groups[self._gi]
        if self._ti < len(tags):
            self._cur = "Group %d/%d '%s'  (%d/%d)" % (
                self._gi + 1, len(self._groups), gname,
                self._ti + 1, len(tags))
            tag = tags[self._ti]
            m = tag[c4d.TEXTURETAG_MATERIAL]
            if m is not None and m.GetType() == c4d.Mmaterial:
                new = self._namemap.get(m.GetName().lower())
                if new is None:
                    new = self._convert_material(m)
                self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
                tag[c4d.TEXTURETAG_MATERIAL] = new
            self._ti += 1
            self._done_tags += 1
            return False
        return True

    def _update_progress(self):
        if self._mode == "dry":
            frac = self._idx / float(len(self._dry_list) or 1)
        else:
            frac = self._done_tags / float(self._total_tags or 1)
        self._progress(frac, self._cur)

    def _finish_dry(self):
        self.SetTimer(0)
        self._running = False
        c4d.StatusClear()
        self._log("-" * 58)
        self._log("Cancelled." if self._cancel
                  else "Dry run done. %d material(s) previewed."
                  % len(self._mats))
        self._prog.set(1.0, "Finished")
        self._set_buttons(True, False, False)
        print("\n".join(self._loglines))

    def Timer(self, msg):
        if not self._running:
            return
        try:
            if self._cancel:
                if self._mode == "dry":
                    self._finish_dry()
                else:
                    self._abort()
                return
            start = time.time()
            done = False
            while (time.time() - start) * 1000.0 < TICK_BUDGET_MS:
                if self._step():
                    done = True
                    break
            self._update_progress()
            # No EventAdd mid-work: Octane is refreshed once per group (in
            # _group_done) so it loads one group at a time.
            if done:
                if self._mode == "dry":
                    self._finish_dry()
                else:
                    self._group_done()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            if self._mode == "group" and self._session:
                self._abort()
            else:
                self._finish_dry()


def main():
    global _dialog
    _dialog = OctaneLinkerDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=560)


if __name__ == "__main__":
    main()

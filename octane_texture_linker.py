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

CHANNEL_KEYWORDS = {
    "color":        ["basecolor", "albedo", "diffuse", "diff", "color", "col",
                     "base", "d"],
    "emission":     ["emissive", "emission", "emit", "glow", "luminance",
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
    "transmission": ["transmission", "transmit", "refraction", "transparency",
                     "trans"],
    "ao":           ["ambientocclusion", "occlusion", "ao"],
}
_SINGLE = {kw for kws in CHANNEL_KEYWORDS.values() for kw in kws if len(kw) == 1}


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
    base = os.path.splitext(os.path.basename(file_path))[0]
    lookup = {}
    for ch, kws in CHANNEL_KEYWORDS.items():
        for kw in kws:
            lookup.setdefault(kw, ch)
    for tok in reversed(_tokenize(base)):
        if tok in _SINGLE and not allow_single:
            continue
        if tok in lookup:
            return lookup[tok]
    return None


def file_matches_material(rel_path, mat_name, match_all):
    if match_all:
        return True
    mn = _normalize(mat_name)
    pn = _normalize(rel_path)
    if not mn:
        return False
    if mn in pn:
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


def collect_matches(mat_name, textures, match_all, allow_single):
    chosen = {}
    for path, rel in textures:
        if not file_matches_material(rel, mat_name, match_all):
            continue
        ch = detect_channel(rel, allow_single)
        if ch is None or ch == "ao":
            continue
        chosen.setdefault(ch, path)
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
# Refresh Octane after at most this many new materials (and at every group
# boundary), so it streams textures in chunks instead of all at once.
EVENT_BATCH = 8

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

MATCH_AUTO, MATCH_NAME, MATCH_ALL = 0, 1, 2

_dialog = None


class OctaneLinkerDialog(gui.GeDialog):
    def __init__(self):
        super(OctaneLinkerDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
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

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Run")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 90, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_DRYRUN, False)
        self.SetBool(G_RECURSE, True)
        self.SetBool(G_REMOVE, True)
        self.SetBool(G_COLOR, True)
        self.SetInt32(G_MATCH, MATCH_AUTO)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Keep one Octane Universal material in the scene "
                              "as a template, pick the texture folder, Run.")
        return True

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
            self._start()
        elif cid == G_CANCEL:
            if self._running:
                self._cancel = True
        elif cid == G_CLOSE:
            if self._running:
                self._cancel = True
            else:
                self.Close()
        return True

    def AskClose(self):
        if self._running:
            self._cancel = True
            return True
        return False

    def _start(self):
        if self._running:
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
                                  "material in the scene first, then Run.")
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
        self._idx = 0
        self._converted = 0
        self._wired = 0
        self._namemap = {}     # old name (lower) -> new octane material
        self._old = []         # originals to remove
        self._since_event = 0
        self._cur = "Starting..."

        if self._dry:
            self._dry_list = self._mats
            self._phase = "dry"
        else:
            # Process one scene group (top-level object) at a time, so Octane
            # isn't asked to compile every material/load every texture at once.
            self._groups = []
            o = self._doc.GetFirstObject()
            while o:
                self._groups.append((o.GetName(), tags_under(o)))
                o = o.GetNext()
            self._total_tags = sum(len(t) for _, t in self._groups) or 1
            self._done_tags = 0
            self._gi = 0
            self._ti = 0
            self._phase = "process"

        if not self._dry:
            self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _convert_material(self, orig):
        """Build the Octane Universal material for `orig` and return it."""
        name = orig.GetName()
        chosen = collect_matches(name, self._textures, self._match_all, True)
        new = self._template.GetClone()
        new.SetName(name)
        clear_textures(new)        # so diffuse (and all maps) always load
        if self._copy_color and orig[c4d.MATERIAL_USE_COLOR] \
                and "color" not in chosen:
            try:
                new[OCT_DIFFUSE_COLOR] = orig[c4d.MATERIAL_COLOR_COLOR]
            except Exception:
                pass
        self._log("Material: %s  (%d map(s))" % (name, len(chosen)))
        self._wired += wire_textures(new, chosen, self._log)
        self._doc.InsertMaterial(new)
        self._doc.AddUndo(c4d.UNDOTYPE_NEW, new)
        self._namemap[name.lower()] = new
        self._old.append(orig)
        self._converted += 1
        return new

    def _step(self):
        ph = self._phase

        if ph == "dry":
            if self._idx < len(self._dry_list):
                orig = self._dry_list[self._idx]
                self._cur = "Preview %d/%d" % (self._idx + 1,
                                               len(self._dry_list))
                chosen = collect_matches(orig.GetName(), self._textures,
                                         self._match_all, True)
                self._log("Material: %s  (%d map(s))"
                          % (orig.GetName(), len(chosen)))
                for ch, path in sorted(chosen.items()):
                    self._log("   [dry] %-12s -> %s"
                              % (ch, os.path.basename(path)))
                self._idx += 1
            else:
                self._phase = "finish"
            return False

        if ph == "process":
            if self._gi < len(self._groups):
                gname, tags = self._groups[self._gi]
                if self._ti < len(tags):
                    self._cur = "Group '%s'  %d/%d" % (
                        gname, self._done_tags + 1, self._total_tags)
                    tag = tags[self._ti]
                    m = tag[c4d.TEXTURETAG_MATERIAL]
                    if m is not None and m.GetType() == c4d.Mmaterial:
                        new = self._namemap.get(m.GetName().lower())
                        if new is None:
                            new = self._convert_material(m)
                            self._since_event += 1
                        self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
                        tag[c4d.TEXTURETAG_MATERIAL] = new
                    self._ti += 1
                    self._done_tags += 1
                    # Safety valve: refresh Octane every few new materials.
                    if self._since_event >= EVENT_BATCH:
                        self._since_event = 0
                        c4d.EventAdd()
                else:
                    # Group done -> let Octane digest this group's textures.
                    if self._since_event > 0:
                        self._since_event = 0
                        c4d.EventAdd()
                    self._gi += 1
                    self._ti = 0
            else:
                self._idx = 0
                self._phase = "cleanup" if self._remove else "finish"
            return False

        if ph == "cleanup":
            if self._idx < len(self._old):
                self._cur = "Removing old %d/%d" % (self._idx + 1,
                                                    len(self._old))
                orig = self._old[self._idx]
                self._doc.AddUndo(c4d.UNDOTYPE_DELETE, orig)
                orig.Remove()
                self._idx += 1
            else:
                self._phase = "finish"
            return False

        return True

    def _update_progress(self):
        ph = self._phase
        if ph == "dry":
            frac = self._idx / float(len(self._dry_list) or 1)
        elif ph == "process":
            frac = self._done_tags / float(self._total_tags or 1)
        elif ph == "cleanup":
            frac = self._idx / float(len(self._old) or 1)
        elif ph == "finish":
            frac = 1.0
        else:
            frac = 0.0
        self._progress(frac, self._cur)

    def _finish(self):
        if not self._running:
            return
        self._running = False
        self.SetTimer(0)
        if not self._dry:
            try:
                self._doc.EndUndo()
            except Exception:
                pass
        c4d.StatusClear()
        c4d.EventAdd()
        self._log("-" * 58)
        if self._cancel:
            self._log("Cancelled.")
        elif self._dry:
            self._log("Dry run done. %d material(s) would be converted."
                      % len(self._mats))
        else:
            self._log("Done. Converted %d material(s); wired %d texture(s)."
                      % (self._converted, self._wired))
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
                self._finish()
                return
            start = time.time()
            done = False
            while (time.time() - start) * 1000.0 < TICK_BUDGET_MS:
                if self._step():
                    done = True
                    break
            self._update_progress()
            # NB: no EventAdd here -- Octane is refreshed per group/batch in
            # _step so it isn't hit with every material at once (which crashes
            # it). The progress bar updates without a scene event.
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = OctaneLinkerDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=560)


if __name__ == "__main__":
    main()

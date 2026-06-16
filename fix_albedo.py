"""
fix_albedo.py  --  find & fix Octane materials missing an Albedo texture
========================================================================

Some materials end up with a Normal/Reflection/etc. wired but no Albedo (the
colour file didn't match during linking). This scans the scene for Octane
materials with an empty Albedo, works out each one's asset (from the maps it
DOES have, or its name), suggests matching colour files from a folder, and can
assign the best one (optionally also wiring the colour alpha into Opacity).

Run: Script Manager (Shift+F11) -> open -> Execute.
  1. Pick the texture folder, Scan  -> see each missing-Albedo material + the
     suggested colour file(s).
  2. Apply best suggestions          -> assigns the top candidate. One undo step.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, storage, documents

OCT_MAT_ID = 1029501
OCT_IMG_ID = 1029508
OCT_IMG_FILE = 1100
OCT_IMG_TYPE = 1105
OCT_IMG_TYPE_ALPHA = 2
OCT_IMG_INVERT = 1117
OCT_ALBEDO = 2517
OCT_OPACITY = 2545
# Channels to read an existing texture from, to infer the asset name.
OCT_OTHER_LINKS = [2542, 2528, 2533, 2524, 2587, 2539, 2580, 2555, 2557]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".bmp",
                    ".exr", ".hdr", ".psd", ".dds", ".webp"}

# Channel detection (same rules as the Octane linker).
CHANNEL_RULES = [
    ("metalness",    ["metalnessmask", "metalness", "metallic", "metal", "mtl"]),
    ("specular",     ["specmask", "tspecular", "specular", "spec"]),
    ("roughness",    ["roughness", "rough", "rgh"]),
    ("reflection",   ["reflection", "reflect", "refl"]),
    ("emission",     ["selfillum", "emissive", "emission", "emit", "glow",
                      "illum"]),
    ("normal",       ["normalmap", "normal", "nrml", "nrm", "norm"]),
    ("bump",         ["bump", "bmp", "heightmap", "height"]),
    ("displacement", ["displacement", "displace", "disp"]),
    ("transmission", ["transmission", "transmit", "refraction", "transparency",
                      "trans"]),
    ("alpha",        ["opacitymask", "opacity", "alpha", "mask"]),
    ("color",        ["basecolor", "albedo", "diffuse", "diff", "color", "col",
                      "base"]),
]
SKIP_TOKENS = {"orm", "ao", "occlusion", "mra", "rma"}
FORMAT_TOKENS = {"psd", "tga", "png", "jpg", "jpeg", "tif", "tiff", "exr",
                 "hdr", "bmp", "dds", "vmat", "g", "tex"}


def _normalize(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _tokenize(name):
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def detect_channel(file_path):
    base = os.path.splitext(os.path.basename(file_path))[0]
    for tok in reversed(_tokenize(base)):
        if tok in SKIP_TOKENS:
            return "skip"
        for channel, kws in CHANNEL_RULES:
            for kw in kws:
                if (len(kw) >= 4 and kw in tok) or tok == kw:
                    return channel
    return None


def _is_hashish(tok):
    if len(tok) >= 6 and all(c in "0123456789abcdef" for c in tok):
        return True
    return len(tok) >= 8 and tok.isdigit()


def _token_is_channel(tok):
    if tok in SKIP_TOKENS:
        return True
    for _ch, kws in CHANNEL_RULES:
        for kw in kws:
            if (len(kw) >= 4 and kw in tok) or tok == kw:
                return True
    return False


def asset_key_from_filename(fn):
    base = os.path.splitext(os.path.basename(str(fn)))[0]
    keep = [t for t in _tokenize(base)
            if t not in FORMAT_TOKENS and not _is_hashish(t)
            and not _token_is_channel(t)]
    return _normalize("".join(keep))


def material_keys(mat):
    """Asset keys to match a colour file by: from the maps the material already
    has, plus its name."""
    keys = []
    for link in OCT_OTHER_LINKS:
        try:
            node = mat[link]
        except Exception:
            node = None
        if node is not None and node.GetType() == OCT_IMG_ID:
            k = asset_key_from_filename(node[OCT_IMG_FILE])
            if len(k) >= 3 and k not in keys:
                keys.append(k)
    nm = _normalize(mat.GetName())
    if len(nm) >= 3 and nm not in keys:
        keys.append(nm)
    return keys


def gather_color_files(folder, recursive):
    """List of (path, norm_name, is_color_word) for colour-candidate images."""
    out = []
    walker = os.walk(folder) if recursive else [
        (folder, [], [f for f in os.listdir(folder)])]
    for root, _d, files in walker:
        for f in files:
            if os.path.splitext(f)[1].lower() not in IMAGE_EXTENSIONS:
                continue
            ch = detect_channel(f)
            if ch == "skip":
                continue
            if ch is not None and ch != "color":
                continue            # a data map, not an albedo candidate
            out.append((os.path.join(root, f), _normalize(f), ch == "color"))
    return out


def suggest_albedo(mat, color_files):
    """Return ranked list of candidate colour file paths for a material."""
    keys = material_keys(mat)
    if not keys:
        return []
    scored = []
    for path, norm, is_color_word in color_files:
        for ki, key in enumerate(keys):
            if key and key in norm:
                # earlier key (from existing maps) is more reliable; prefer an
                # explicit *_color* file and a shorter (closer) name.
                score = (ki, 0 if is_color_word else 1, len(norm))
                scored.append((score, path))
                break
    scored.sort(key=lambda sp: sp[0])
    seen, out = set(), []
    for _s, p in scored:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def make_image_node(mat, path):
    img = c4d.BaseList2D(OCT_IMG_ID)
    img.SetName("color_%s" % os.path.basename(path))
    img[OCT_IMG_FILE] = path
    mat.InsertShader(img)
    return img


def octane_materials(doc, selected_only):
    mats = (doc.GetActiveMaterials() if selected_only else doc.GetMaterials())
    return [m for m in (mats or []) if m.GetType() == OCT_MAT_ID]


def missing_albedo(mat):
    a = mat[OCT_ALBEDO]
    return a is None or a.GetType() != OCT_IMG_ID


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

G_TEX = 10001
G_BROWSE = 10002
G_RECURSE = 10003
G_SEL = 10004
G_OPACITY = 10005
G_SCAN = 10006
G_APPLY = 10007
G_CANCEL = 10008
G_CLOSE = 10009
G_LOG = 10010
G_PROG = 10011

_dialog = None


class FixAlbedoDialog(gui.GeDialog):
    def __init__(self):
        super(FixAlbedoDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""
        self._plan = []      # [(mat, [candidate paths])]

    def CreateLayout(self):
        self.SetTitle("Fix Missing Albedo")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Texture folder", 0)
        self.AddEditText(G_TEX, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddCheckbox(G_RECURSE, c4d.BFH_LEFT, 0, 0, "Recurse")
        self.AddCheckbox(G_SEL, c4d.BFH_LEFT, 0, 0, "Only selected materials")
        self.AddCheckbox(G_OPACITY, c4d.BFH_LEFT, 0, 0,
                         "Also colour alpha -> Opacity")
        self.GroupEnd()

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 300,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_SCAN, c4d.BFH_LEFT, 90, 0, "Scan")
        self.AddButton(G_APPLY, c4d.BFH_LEFT, 150, 0, "Apply best suggestions")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 70, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 70, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_RECURSE, True)
        self.SetBool(G_SEL, False)
        self.SetBool(G_OPACITY, True)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Pick the texture folder and Scan to list Octane "
                              "materials missing an Albedo, with suggested "
                              "colour files. Then Apply best suggestions.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 6000:
            self._loglines = self._loglines[-6000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _scan(self):
        doc = documents.GetActiveDocument()
        if doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        folder = self.GetString(G_TEX).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Please choose a valid texture folder.")
            return
        c4d.StatusSetText("Scanning textures...")
        color_files = gather_color_files(folder, self.GetBool(G_RECURSE))
        mats = octane_materials(doc, self.GetBool(G_SEL))
        missing = [m for m in mats if missing_albedo(m)]
        c4d.StatusClear()

        self._plan = []
        self._loglines = ["=" * 58,
                          "Octane materials: %d   Missing Albedo: %d   "
                          "Colour files: %d"
                          % (len(mats), len(missing), len(color_files)),
                          "-" * 58]
        with_sug = 0
        for m in missing:
            cands = suggest_albedo(m, color_files)
            self._plan.append((m, cands))
            if cands:
                with_sug += 1
                self._log("%s" % m.GetName())
                for p in cands[:3]:
                    self._log("    -> %s" % os.path.basename(p))
            else:
                self._log("%s  (no candidate found)" % m.GetName())
        self._log("-" * 58)
        self._log("%d of %d have a suggestion. 'Apply best suggestions' wires "
                  "the first one for each." % (with_sug, len(missing)))
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _start_apply(self):
        if self._running:
            return
        if not self._plan:
            self.SetString(G_LOG, "Scan first.")
            return
        self._doc = documents.GetActiveDocument()
        self._opacity = self.GetBool(G_OPACITY)
        self._tasks = [(m, c[0]) for m, c in self._plan if c]
        if not self._tasks:
            self.SetString(G_LOG, "No suggestions to apply.")
            return
        self._loglines = []
        self._log("=" * 58)
        self._log("APPLY  (%d material(s))" % len(self._tasks))
        self._cancel = False
        self._idx = 0
        self._applied = 0
        self._cur = "Starting..."
        self._doc.StartUndo()
        self._running = True
        self.Enable(G_SCAN, False)
        self.Enable(G_APPLY, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._tasks):
            return True
        mat, path = self._tasks[self._idx]
        self._cur = "Applying %d/%d" % (self._idx + 1, len(self._tasks))
        self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)
        img = make_image_node(mat, path)
        mat[OCT_ALBEDO] = img
        if self._opacity and mat[OCT_OPACITY] is None:
            op = make_image_node(mat, path)
            try:
                op[OCT_IMG_TYPE] = OCT_IMG_TYPE_ALPHA
                op[OCT_IMG_INVERT] = 1
            except Exception:
                pass
            mat[OCT_OPACITY] = op
        mat.Update(True, True)
        mat.Message(c4d.MSG_UPDATE)
        self._log("[ok] %s -> %s" % (mat.GetName(), os.path.basename(path)))
        self._applied += 1
        self._idx += 1
        return False

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
        self._log("-" * 58)
        self._log(("Cancelled. " if self._cancel else "Done. ")
                  + "Assigned Albedo to %d material(s)." % self._applied)
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_SCAN, True)
        self.Enable(G_APPLY, True)
        self.Enable(G_CANCEL, False)
        print("\n".join(self._loglines))

    def Command(self, cid, msg):
        if cid == G_BROWSE:
            p = storage.LoadDialog(title="Select the TEXTURE folder",
                                   flags=c4d.FILESELECT_DIRECTORY)
            if p:
                self.SetString(G_TEX, p)
        elif cid == G_SCAN:
            self._scan()
        elif cid == G_APPLY:
            self._start_apply()
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
            self._prog.set(self._idx / float(len(self._tasks) or 1), self._cur)
            c4d.StatusSetText(self._cur)
            c4d.StatusSetBar(int(100.0 * self._idx / (len(self._tasks) or 1)))
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = FixAlbedoDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=560)


if __name__ == "__main__":
    main()

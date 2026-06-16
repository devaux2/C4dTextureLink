"""
add_opacity.py  --  add colour-alpha cutout Opacity to existing Octane materials
================================================================================

For Octane materials that are ALREADY textured, this adds the cutout: it reads
each material's existing Albedo image, creates a second Image Texture of the
SAME file with Type = Alpha, and wires it into Opacity. No folders, no
re-matching, no re-import -- it just patches materials already in the scene.

(That's the manual step you did on tree_pine_frond: same colour file plugged in
again as Alpha -> Opacity. Octane image Type 1105 = 2 is "Alpha".)

Options:
  - Only selected materials   (otherwise every Octane material)
  - Overwrite existing Opacity (otherwise skip ones already wired)

Run: Script Manager (Shift+F11) -> open -> Execute. One undo step.
"""

import os
import time
import traceback
import c4d
from c4d import gui, documents

OCT_MAT_ID = 1029501
OCT_IMG_ID = 1029508
OCT_IMG_FILE = 1100
OCT_IMG_TYPE = 1105
OCT_IMG_TYPE_ALPHA = 2
OCT_IMG_INVERT = 1117
OCT_ALBEDO = 2517
OCT_OPACITY = 2545


def add_alpha_opacity(mat, overwrite, log):
    """Returns 'added' / 'skip-noalbedo' / 'skip-haveopacity'."""
    albedo = mat[OCT_ALBEDO]
    if albedo is None or albedo.GetType() != OCT_IMG_ID:
        log("   - %s : no Albedo image, skipped" % mat.GetName())
        return "skip-noalbedo"
    if (not overwrite) and mat[OCT_OPACITY] is not None:
        log("   - %s : already has Opacity, skipped" % mat.GetName())
        return "skip-haveopacity"

    path = albedo[OCT_IMG_FILE]
    img = c4d.BaseList2D(OCT_IMG_ID)
    img.SetName("opacity_%s" % os.path.basename(str(path)))
    img[OCT_IMG_FILE] = path
    try:
        img[OCT_IMG_TYPE] = OCT_IMG_TYPE_ALPHA   # output the alpha channel
        img[OCT_IMG_INVERT] = 1
    except Exception:
        pass
    mat.InsertShader(img)
    mat[OCT_OPACITY] = img
    mat.Update(True, True)
    mat.Message(c4d.MSG_UPDATE)
    log("   [ok] %s : alpha -> Opacity" % mat.GetName())
    return "added"


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

G_SEL = 8001
G_OVERWRITE = 8002
G_RUN = 8003
G_CANCEL = 8004
G_CLOSE = 8005
G_LOG = 8006
G_PROG = 8007

_dialog = None


class OpacityDialog(gui.GeDialog):
    def __init__(self):
        super(OpacityDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Add Cutout Opacity to Octane Materials")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddCheckbox(G_SEL, c4d.BFH_LEFT, 0, 0,
                         "Only selected materials")
        self.AddCheckbox(G_OVERWRITE, c4d.BFH_LEFT, 0, 0,
                         "Overwrite existing Opacity")
        self.GroupEnd()

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 260,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Add Opacity")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_SEL, False)
        self.SetBool(G_OVERWRITE, False)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Adds the colour image's alpha channel into "
                              "Opacity on existing Octane materials (for leaf/"
                              "fence/grate cutouts). One undo step.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def Command(self, cid, msg):
        if cid == G_RUN:
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
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return

        self._overwrite = self.GetBool(G_OVERWRITE)
        if self.GetBool(G_SEL):
            mats = self._doc.GetActiveMaterials() or []
        else:
            mats = self._doc.GetMaterials()
        self._mats = [m for m in mats if m.GetType() == OCT_MAT_ID]

        if not self._mats:
            self.SetString(G_LOG, "No Octane materials found%s."
                           % (" selected" if self.GetBool(G_SEL) else ""))
            return

        self._loglines = []
        self._log("=" * 56)
        self._log("ADD OPACITY  (%d Octane material(s))" % len(self._mats))
        self._cancel = False
        self._idx = 0
        self._added = 0
        self._cur = "Starting..."

        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._mats):
            return True
        mat = self._mats[self._idx]
        self._cur = "Material %d/%d" % (self._idx + 1, len(self._mats))
        self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, mat)
        if add_alpha_opacity(mat, self._overwrite, self._log) == "added":
            self._added += 1
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
        self._log("-" * 56)
        self._log(("Cancelled. " if self._cancel else "Done. ")
                  + "Added Opacity to %d material(s)." % self._added)
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
            self._prog.set(self._idx / float(len(self._mats) or 1), self._cur)
            c4d.StatusSetText(self._cur)
            c4d.StatusSetBar(int(100.0 * self._idx / (len(self._mats) or 1)))
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = OpacityDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=600, defaulth=520)


if __name__ == "__main__":
    main()

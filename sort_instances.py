"""
sort_instances.py  --  separate masters from instances inside each Null
=======================================================================

Inside every Null that holds a mix of real meshes and instances, this leaves
the real mesh objects at the top and moves all the instance objects into an
"Instances" sub-Null. Makes it easy to find/select the master meshes (e.g. to
decimate them) without wading through thousands of instances.

Positions are preserved (it only re-parents). One undo step.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import time
import traceback
import c4d
from c4d import gui, documents

SUBNULL_NAME = "Instances"


def all_nulls(doc):
    out = []

    def rec(o):
        while o:
            if o.GetType() == c4d.Onull:
                out.append(o)
            rec(o.GetDown())
            o = o.GetNext()

    rec(doc.GetFirstObject())
    return out


def child_split(null):
    """Return (polygon children, instance children) directly under `null`."""
    polys, insts = [], []
    c = null.GetDown()
    while c:
        t = c.GetType()
        if t == c4d.Opolygon:
            polys.append(c)
        elif t == c4d.Oinstance:
            insts.append(c)
        c = c.GetNext()
    return polys, insts


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

G_REQUIRE = 12001
G_RUN = 12002
G_CANCEL = 12003
G_CLOSE = 12004
G_LOG = 12005
G_PROG = 12006

_dialog = None


class SortInstDialog(gui.GeDialog):
    def __init__(self):
        super(SortInstDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Masters up top, Instances in sub-Null")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.AddCheckbox(G_REQUIRE, c4d.BFH_LEFT, 0, 0,
                         "Only Nulls that also contain a real mesh")

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 280,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Sort")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_REQUIRE, False)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Moves each Null's instance children into an "
                              "'Instances' sub-Null, leaving the real meshes on "
                              "top. One undo step.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _start(self):
        if self._running:
            return
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        require_mesh = self.GetBool(G_REQUIRE)

        # Snapshot nulls that have instance children (and a mesh, if required).
        self._jobs = []
        for n in all_nulls(self._doc):
            if n.GetName() == SUBNULL_NAME:
                continue                      # don't process our own sub-nulls
            polys, insts = child_split(n)
            if not insts:
                continue
            if require_mesh and not polys:
                continue
            self._jobs.append(n)

        if not self._jobs:
            self.SetString(G_LOG, "No Nulls with instance children found.")
            return

        self._loglines = []
        self._log("=" * 56)
        self._log("SORT  (%d Null(s) with instances)" % len(self._jobs))
        self._cancel = False
        self._idx = 0
        self._moved = 0
        self._cur = "Starting..."
        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._jobs):
            return True
        null = self._jobs[self._idx]
        self._cur = "Null %d/%d" % (self._idx + 1, len(self._jobs))
        _polys, insts = child_split(null)
        if insts:
            sub = c4d.BaseObject(c4d.Onull)
            sub.SetName(SUBNULL_NAME)
            sub.InsertUnderLast(null)          # below the real meshes
            self._doc.AddUndo(c4d.UNDOTYPE_NEW, sub)
            for inst in insts:
                mg = inst.GetMg()
                self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, inst)
                inst.Remove()
                inst.InsertUnderLast(sub)
                inst.SetMg(mg)                 # preserve placement
                self._moved += 1
            self._log("   %s : %d instance(s) -> '%s'"
                      % (null.GetName(), len(insts), SUBNULL_NAME))
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
                  + "Moved %d instance(s) under '%s' sub-Nulls in %d Null(s)."
                  % (self._moved, SUBNULL_NAME, self._idx))
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_RUN, True)
        self.Enable(G_CANCEL, False)
        print("\n".join(self._loglines))

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
            self._prog.set(self._idx / float(len(self._jobs) or 1), self._cur)
            c4d.StatusSetText(self._cur)
            c4d.StatusSetBar(int(100.0 * self._idx / (len(self._jobs) or 1)))
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = SortInstDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=600, defaulth=500)


if __name__ == "__main__":
    main()

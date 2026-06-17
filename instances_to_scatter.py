"""
instances_to_scatter.py  --  convert C4D instances into Octane Scatter (CSV)
============================================================================

Takes the Render Instances the mesh instancer made and replaces each
master + its instances with a single **Octane Scatter** in CSV mode -- one mesh
on the GPU, placed by a per-instance transform list. Much lighter on the GPU
than thousands of C4D instances, with the exact same placement (position,
rotation, scale).

For every master that has instances it:
  - writes a CSV of the master's + each instance's transform (3x4 matrix rows),
  - creates an Octane Scatter (Type = Use Csv File) with a copy of the master
    as its source object,
  - deletes the original master and its instances.

You choose a folder for the CSV files (the Scatter references them by path, so
keep them next to your project).

Run: Script Manager (Shift+F11) -> open -> Execute. One undo step.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, storage, documents

# Octane Scatter (from object_probe).
OCT_SCATTER_ID = 1035961
SC_TYPE = 10002          # Distribution > Type (cycle)
SC_TYPE_CSV = 3          # ... "Use Csv File" (Vertex=0/Surface=1/Poisson=2)
SC_FILE = 10042          # Csv > File
SC_IMPORT_SCALE = 10048  # Csv > Import Scale

INST_LINK = c4d.INSTANCEOBJECT_LINK


def mg_to_row(mg):
    """C4D global matrix -> Octane Scatter CSV row (12 numbers, 3x4)."""
    o, a, b, c = mg.off, mg.v1, mg.v2, mg.v3
    vals = (a.x, b.x, c.x, o.x,
            a.y, b.y, c.y, o.y,
            a.z, b.z, c.z, o.z)
    return " ".join(repr(v) for v in vals)


def all_objects(doc):
    out = []

    def rec(o):
        while o:
            out.append(o)
            rec(o.GetDown())
            o = o.GetNext()

    rec(doc.GetFirstObject())
    return out


def group_instances(objs, selected_only, doc):
    """Group instance objects by their linked master.
    Returns ordered [(master, [instances]), ...] (masters keyed by name)."""
    if selected_only:
        active = set(id(o) for o in
                     doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_CHILDREN))
    order, groups = [], {}
    for o in objs:
        if o.GetType() != c4d.Oinstance:
            continue
        if selected_only and id(o) not in active:
            continue
        m = o[INST_LINK]
        if m is None or m.GetType() != c4d.Opolygon:
            continue
        key = m.GetName()              # masters are distinctly named
        if key not in groups:
            groups[key] = [m, []]
            order.append(key)
        groups[key][1].append(o)
    return [(groups[k][0], groups[k][1]) for k in order]


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


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

G_FOLDER = 13001
G_BROWSE = 13002
G_SEL = 13003
G_RUN = 13004
G_CANCEL = 13005
G_CLOSE = 13006
G_LOG = 13007
G_PROG = 13008

_dialog = None


class ScatterDialog(gui.GeDialog):
    def __init__(self):
        super(ScatterDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Instances -> Octane Scatter (CSV)")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 90, 0, "CSV folder", 0)
        self.AddEditText(G_FOLDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.AddCheckbox(G_SEL, c4d.BFH_LEFT, 0, 0,
                         "Only selected instances")

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 280,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 150, 0, "Convert to Scatter")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        doc = documents.GetActiveDocument()
        dpath = doc.GetDocumentPath() if doc else ""
        self.SetString(G_FOLDER, dpath or "")
        self.SetBool(G_SEL, False)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Converts C4D instances into Octane Scatters "
                              "(CSV mode). Pick a folder to hold the .csv files "
                              "(keep them with your project). One undo step.")
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
        folder = self.GetString(G_FOLDER).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Please choose a valid folder for the CSVs.")
            return
        if c4d.BaseObject(OCT_SCATTER_ID) is None:
            self.SetString(G_LOG, "Octane Scatter (id %s) is not available -- "
                                  "is the Octane plugin loaded?" % OCT_SCATTER_ID)
            return

        self._folder = folder
        self._groups = group_instances(all_objects(self._doc),
                                        self.GetBool(G_SEL), self._doc)
        if not self._groups:
            self.SetString(G_LOG, "No C4D instances found%s."
                           % (" in the selection" if self.GetBool(G_SEL)
                              else ""))
            return

        self._loglines = []
        self._log("=" * 56)
        self._log("INSTANCES -> SCATTER  (%d master(s))" % len(self._groups))
        self._cancel = False
        self._idx = 0
        self._made = 0
        self._cur = "Starting..."
        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _convert(self, master, insts):
        name = master.GetName()
        # 1. CSV of master + every instance transform.
        rows = [mg_to_row(master.GetMg())]
        rows += [mg_to_row(i.GetMg()) for i in insts]
        csv_path = os.path.join(self._folder, _safe_name(name) + ".csv")
        with open(csv_path, "w") as f:
            f.write("\n".join(rows))

        # 2. Scatter on a copy of the master, in CSV mode, at world identity.
        scatter = c4d.BaseObject(OCT_SCATTER_ID)
        scatter.SetName(name + "_scatter")
        parent = master.GetUp()
        if parent is not None:
            scatter.InsertUnder(parent)
        else:
            self._doc.InsertObject(scatter)
        scatter.SetMg(c4d.Matrix())            # CSV is read in world space
        scatter[SC_TYPE] = SC_TYPE_CSV
        scatter[SC_FILE] = c4d.Filename(csv_path)   # dtype 131 needs a Filename
        scatter[SC_IMPORT_SCALE] = 1.0
        scatter.Message(c4d.MSG_UPDATE)
        self._doc.AddUndo(c4d.UNDOTYPE_NEW, scatter)

        # Verify the params actually stuck (asymmetric custom datatypes).
        got_type = scatter[SC_TYPE]
        got_file = scatter[SC_FILE]
        if got_type != SC_TYPE_CSV or not got_file:
            self._log("   ! WARN %s: Type=%s File=%s -- params didn't stick"
                      % (name, got_type, got_file))

        src = master.GetClone()
        src.SetMl(c4d.Matrix())                # template geometry at origin
        src.InsertUnder(scatter)

        # 3. Remove the originals (and the now-empty 'Instances' null if any).
        empties = set()
        for i in insts:
            up = i.GetUp()
            self._doc.AddUndo(c4d.UNDOTYPE_DELETE, i)
            i.Remove()
            if (up is not None and up.GetType() == c4d.Onull
                    and up.GetName() == "Instances"):
                empties.add(up)
        self._doc.AddUndo(c4d.UNDOTYPE_DELETE, master)
        master.Remove()
        for n in empties:
            if n.GetDown() is None:            # only if truly empty now
                self._doc.AddUndo(c4d.UNDOTYPE_DELETE, n)
                n.Remove()

        self._made += 1
        self._log("   [ok] %-28s %d instance(s) -> Scatter (%s)"
                  % (name, len(insts), os.path.basename(csv_path)))

    def _step(self):
        if self._idx >= len(self._groups):
            return True
        master, insts = self._groups[self._idx]
        self._cur = "Scatter %d/%d" % (self._idx + 1, len(self._groups))
        try:
            self._convert(master, insts)
        except Exception:
            self._log("   ! %s : %s" % (master.GetName(),
                      traceback.format_exc().splitlines()[-1]))
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
                  + "Made %d Octane Scatter(s)." % self._made)
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_RUN, True)
        self.Enable(G_CANCEL, False)
        print("\n".join(self._loglines))

    def Command(self, cid, msg):
        if cid == G_BROWSE:
            p = storage.LoadDialog(title="Folder for the CSV files",
                                   flags=c4d.FILESELECT_DIRECTORY)
            if p:
                self.SetString(G_FOLDER, p)
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
            self._prog.set(self._idx / float(len(self._groups) or 1), self._cur)
            c4d.StatusSetText(self._cur)
            c4d.StatusSetBar(int(100.0 * self._idx / (len(self._groups) or 1)))
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = ScatterDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=540)


if __name__ == "__main__":
    main()

"""
fix_scatter_orientation.py  --  correct Octane Scatter CSV transforms in place
==============================================================================

If instances_to_scatter produced scatters that are mis-oriented (e.g. upside
down), it's a matrix-convention mismatch in the CSVs. This rewrites the CSV
files in the chosen folder -- transposing each row's 3x3 rotation (and
optionally negating a translation axis) -- then reloads every Octane Scatter so
the change shows immediately. No undo / re-run needed.

Running it twice toggles the transpose, so you can try a setting, look, and
revert if it's worse.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, storage, documents

SCATTER_ID = 1035961
SC_RELOAD = 12010


def fix_row(nums, transpose, neg):
    """nums = 12 floats (m00..m23, 3x4). Returns the adjusted 12 floats.
    neg = (nx, ny, nz) booleans to negate translation axes."""
    m = nums[:12]
    if transpose:
        # transpose the 3x3, keep translation (idx 3,7,11) in the last column
        m = [m[0], m[4], m[8], m[3],
             m[1], m[5], m[9], m[7],
             m[2], m[6], m[10], m[11]]
    if neg[0]:
        m[3] = -m[3]
    if neg[1]:
        m[7] = -m[7]
    if neg[2]:
        m[11] = -m[11]
    return m


def rewrite_csv(path, transpose, neg):
    with open(path, "r") as f:
        raw = f.read()
    out_lines = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = re.split(r"[,\s]+", line.strip())
        try:
            nums = [float(p) for p in parts[:12]]
        except ValueError:
            out_lines.append(line)             # leave non-numeric lines as-is
            continue
        if len(nums) < 12:
            out_lines.append(line)
            continue
        m = fix_row(nums, transpose, neg)
        tail = parts[12:]                       # keep any trailing ID column
        out_lines.append(" ".join(repr(v) for v in m)
                         + (" " + " ".join(tail) if tail else ""))
    with open(path, "w") as f:
        f.write("\n".join(out_lines))


def reload_scatters(doc):
    count = [0]

    def walk(o):
        while o:
            if o.GetType() == SCATTER_ID:
                try:
                    c4d.CallButton(o, SC_RELOAD)
                    o.Message(c4d.MSG_UPDATE)
                    count[0] += 1
                except Exception:
                    pass
            walk(o.GetDown())
            o = o.GetNext()

    walk(doc.GetFirstObject())
    return count[0]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

G_FOLDER = 14001
G_BROWSE = 14002
G_TRANSPOSE = 14003
G_NX = 14004
G_NY = 14005
G_NZ = 14006
G_RELOAD = 14007
G_RUN = 14008
G_CLOSE = 14009
G_LOG = 14010

_dialog = None


class FixDialog(gui.GeDialog):
    def __init__(self):
        super(FixDialog, self).__init__()
        self._loglines = []

    def CreateLayout(self):
        self.SetTitle("Fix Scatter Orientation (rewrite CSVs)")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 80, 0, "CSV folder", 0)
        self.AddEditText(G_FOLDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 5, 0, "")
        self.AddCheckbox(G_TRANSPOSE, c4d.BFH_LEFT, 0, 0, "Transpose 3x3")
        self.AddCheckbox(G_NX, c4d.BFH_LEFT, 0, 0, "Negate Tx")
        self.AddCheckbox(G_NY, c4d.BFH_LEFT, 0, 0, "Negate Ty")
        self.AddCheckbox(G_NZ, c4d.BFH_LEFT, 0, 0, "Negate Tz")
        self.AddCheckbox(G_RELOAD, c4d.BFH_LEFT, 0, 0, "Reload scatters")
        self.GroupEnd()

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 220,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 140, 0, "Fix & reload")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        doc = documents.GetActiveDocument()
        self.SetString(G_FOLDER, doc.GetDocumentPath() if doc else "")
        self.SetBool(G_TRANSPOSE, True)
        self.SetBool(G_NX, False)
        self.SetBool(G_NY, False)
        self.SetBool(G_NZ, False)
        self.SetBool(G_RELOAD, True)
        self.SetString(G_LOG, "Rewrites the scatter CSVs (transpose fixes "
                              "upside-down) and reloads the scatters. Run twice "
                              "to toggle a setting back.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        self.SetString(G_LOG, "\n".join(self._loglines))

    def Command(self, cid, msg):
        if cid == G_BROWSE:
            p = storage.LoadDialog(title="Folder with the scatter CSVs",
                                   flags=c4d.FILESELECT_DIRECTORY)
            if p:
                self.SetString(G_FOLDER, p)
        elif cid == G_RUN:
            self._run()
        elif cid == G_CLOSE:
            self.Close()
        return True

    def _run(self):
        folder = self.GetString(G_FOLDER).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Choose the folder containing the CSVs.")
            return
        transpose = self.GetBool(G_TRANSPOSE)
        neg = (self.GetBool(G_NX), self.GetBool(G_NY), self.GetBool(G_NZ))
        csvs = [f for f in os.listdir(folder) if f.lower().endswith(".csv")]
        if not csvs:
            self.SetString(G_LOG, "No .csv files in that folder.")
            return

        self._loglines = []
        self._log("=" * 52)
        self._log("Rewriting %d CSV(s): transpose=%s negate=%s"
                  % (len(csvs), transpose, neg))
        done = 0
        try:
            for i, f in enumerate(csvs):
                c4d.StatusSetBar(int(100.0 * i / len(csvs)))
                try:
                    rewrite_csv(os.path.join(folder, f), transpose, neg)
                    done += 1
                except Exception as e:
                    self._log("   ! %s : %s" % (f, e))
            c4d.StatusClear()
            self._log("Rewrote %d CSV(s)." % done)
            if self.GetBool(G_RELOAD):
                doc = documents.GetActiveDocument()
                n = reload_scatters(doc)
                c4d.EventAdd()
                self._log("Reloaded %d Octane Scatter(s)." % n)
            self._log("Done. Check the viewport; run again to revert if worse.")
        except Exception:
            c4d.StatusClear()
            self._log("ERROR:")
            self._log(traceback.format_exc())
        print("\n".join(self._loglines))


def main():
    global _dialog
    _dialog = FixDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=600, defaulth=440)


if __name__ == "__main__":
    main()

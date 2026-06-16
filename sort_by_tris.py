"""
sort_by_tris.py  --  sort top-level objects/Nulls by triangle count
===================================================================

Reorders the top-level objects in the Object Manager by how many triangles
each one contains (summed over its whole hierarchy), so the heaviest groups
sit at the top (or bottom). Handy for spotting what's eating your poly budget.

Counts polygons, which equals triangles for triangulated meshes (anything from
glTF/FBX). Render/instances reference their master, so an instance counts as ~0
geometry of its own.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import time
import traceback
import c4d
from c4d import gui, documents


def count_tris(root):
    """Sum polygon counts over root and all its descendants."""
    total = 0
    stack = [root]
    while stack:
        o = stack.pop()
        if o.GetType() == c4d.Opolygon:
            total += o.GetPolygonCount()
        d = o.GetDown()
        while d:
            stack.append(d)
            d = d.GetNext()
    return total


def top_level_objects(doc):
    out = []
    o = doc.GetFirstObject()
    while o:
        out.append(o)
        o = o.GetNext()
    return out


def _fmt(n):
    return "{:,}".format(n)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

G_DESC = 7001
G_RUN = 7002
G_CLOSE = 7003
G_LOG = 7004


class SortDialog(gui.GeDialog):
    def __init__(self):
        super(SortDialog, self).__init__()
        self._loglines = []

    def CreateLayout(self):
        self.SetTitle("Sort objects by triangle count")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.AddCheckbox(G_DESC, c4d.BFH_LEFT, 0, 0,
                         "Largest first (untick for smallest first)")

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 320,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 120, 0, "Sort")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_DESC, True)
        self.SetString(G_LOG, "Sort reorders the top-level objects by triangle "
                              "count and lists the ranking. One undo step.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        self.SetString(G_LOG, "\n".join(self._loglines))

    def Command(self, cid, msg):
        if cid == G_RUN:
            self._run()
        elif cid == G_CLOSE:
            self.Close()
        return True

    def _run(self):
        doc = documents.GetActiveDocument()
        if doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        objs = top_level_objects(doc)
        if not objs:
            self.SetString(G_LOG, "No top-level objects.")
            return

        self._loglines = []
        descending = self.GetBool(G_DESC)
        try:
            c4d.StatusSetText("Counting triangles...")
            counted = []
            n = len(objs)
            for i, o in enumerate(objs):
                c4d.StatusSetBar(int(100.0 * i / n))
                counted.append((o, count_tris(o)))
            c4d.StatusClear()

            counted.sort(key=lambda oc: oc[1], reverse=descending)

            # Reorder the document's top level to match.
            doc.StartUndo()
            prev = None
            for o, _t in counted:
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, o)
                o.Remove()
                if prev is None:
                    doc.InsertObject(o)
                else:
                    o.InsertAfter(prev)
                prev = o
            doc.EndUndo()
            c4d.EventAdd()

            total = sum(t for _o, t in counted)
            self._log("=" * 52)
            self._log("Sorted %d top-level object(s) by triangles (%s)."
                      % (len(counted), "largest first" if descending
                         else "smallest first"))
            self._log("Scene total: %s tris" % _fmt(total))
            self._log("-" * 52)
            for o, t in counted[:200]:
                pct = (100.0 * t / total) if total else 0.0
                self._log("%12s  %5.1f%%   %s" % (_fmt(t), pct, o.GetName()))
            if len(counted) > 200:
                self._log("... and %d more" % (len(counted) - 200))
            print("\n".join(self._loglines))
        except Exception:
            c4d.StatusClear()
            self._log("")
            self._log("ERROR:")
            self._log(traceback.format_exc())


_dialog = None


def main():
    global _dialog
    _dialog = SortDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=560, defaulth=560)


if __name__ == "__main__":
    main()

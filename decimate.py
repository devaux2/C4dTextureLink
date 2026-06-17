"""
decimate.py  --  in-place polygon reduction (instances follow)
==============================================================

Reduces the triangle count of the polygon objects you select, IN PLACE -- the
object keeps its identity, so any Render Instances pointing at it automatically
use the reduced mesh. So to cut your 3.2M tree tris, just select the Trees null
(or the tree master meshes) and run this; the thousands of instances follow.

UVs and normals are carried over so textures still map.

Use it
------
1. Select the objects to reduce (a Null selects its whole hierarchy; instances
   are skipped automatically -- only real meshes are reduced).
2. Script Manager (Shift+F11) -> open -> Execute.
3. Set "Keep %" (e.g. 25 = keep a quarter of the polys), Reduce.

Test on ONE tree first (select it, Keep 25, Reduce) to dial in the quality.
One undo step. Requires C4D R21+ (Polygon Reduction generator).
"""

import time
import traceback
import c4d
from c4d import gui, documents

# Don't bother reducing meshes already lighter than this (polys).
MIN_POLYS = 500

# Polygon Reduction generator. The named symbol isn't exposed in every build,
# so fall back to the plugin ID (1037576) and the raw "reduction strength"
# parameter id (1000).
OPOLYREDUX = getattr(c4d, "Opolyreduxgen", 1037576)
REDUX_STRENGTH = getattr(c4d, "POLYREDUCTIONGEN_REDUCTIONSTRENGTH", 1000)
_BUILDFLAGS = getattr(c4d, "BUILDFLAGS_NONE", getattr(c4d, "BUILDFLAGS_0", 0))


def find_polys_in_selection(doc):
    """Polygon objects within the active selection (recurse into Nulls);
    instances are not polygon objects, so they're skipped."""
    sel = doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_CHILDREN)
    out, seen = [], set()

    def rec(o):
        while o:
            if o.GetType() == c4d.Opolygon and id(o) not in seen:
                out.append(o)
                seen.add(id(o))
            rec(o.GetDown())
            o = o.GetNext()

    for s in sel:
        if s.GetType() == c4d.Opolygon and id(s) not in seen:
            out.append(s)
            seen.add(id(s))
        rec(s.GetDown())
    return out


def _first_polygon(obj):
    """Find the first polygon object in a (possibly nested) result."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if o is None:
            continue
        if o.GetType() == c4d.Opolygon:
            return o
        stack.append(o.GetNext())
        stack.append(o.GetDown())
    return None


def reduce_mesh(src, strength):
    """Return a reduced clone's polygon object, or None. `strength` is the
    fraction removed (0.75 = keep 25%)."""
    gen = c4d.BaseObject(OPOLYREDUX)
    if gen is None:
        raise RuntimeError("Polygon Reduction generator unavailable (id %s)"
                           % OPOLYREDUX)
    gen[REDUX_STRENGTH] = strength       # fraction removed (0.75 = keep 25%)
    clone = src.GetClone()
    clone.SetMl(c4d.Matrix())            # bake in local space
    clone.InsertUnder(gen)

    tmp = documents.BaseDocument()
    tmp.InsertObject(gen)
    tmp.ExecutePasses(None, True, True, True, _BUILDFLAGS)   # build the cache
    res = c4d.utils.SendModelingCommand(
        c4d.MCOMMAND_CURRENTSTATETOOBJECT, [gen], doc=tmp)
    if not res:
        return None
    baked = res[0] if isinstance(res, list) else res
    return _first_polygon(baked)


def apply_in_place(obj, reduced):
    """Replace obj's geometry with `reduced`, keeping obj's identity + tags."""
    pts = reduced.GetAllPoints()
    polys = reduced.GetAllPolygons()
    obj.ResizeObject(len(pts), len(polys))
    obj.SetAllPoints(pts)
    for i, p in enumerate(polys):
        obj.SetPolygon(i, p)
    # Replace UVW / normal tags with the reduced mesh's.
    for t in list(obj.GetTags()):
        if t.GetType() in (c4d.Tuvw, c4d.Tnormal):
            t.Remove()
    for t in reduced.GetTags():
        if t.GetType() in (c4d.Tuvw, c4d.Tnormal):
            obj.InsertTag(t.GetClone())
    obj.Message(c4d.MSG_UPDATE)


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

TICK_BUDGET_MS = 60     # reduction is heavy; one mesh can exceed this

G_KEEP = 11001
G_RUN = 11002
G_CANCEL = 11003
G_CLOSE = 11004
G_LOG = 11005
G_PROG = 11006

_dialog = None


class DecimateDialog(gui.GeDialog):
    def __init__(self):
        super(DecimateDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Decimate (in-place, instances follow)")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 120, 0, "Keep % of polys", 0)
        self.AddEditSlider(G_KEEP, c4d.BFH_SCALEFIT, 0, 0)
        self.GroupEnd()

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 280,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 110, 0, "Reduce")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetInt32(G_KEEP, 25, 1, 100)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Select the meshes (or a Null) to reduce, set "
                              "Keep %, then Reduce. Instances follow their "
                              "master automatically. Test on one tree first.")
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
        objs = find_polys_in_selection(self._doc)
        if not objs:
            self.SetString(G_LOG, "Select one or more meshes (or a Null "
                                  "containing them) first.")
            return
        keep = max(1, min(100, self.GetInt32(G_KEEP)))
        self._strength = 1.0 - keep / 100.0
        self._objs = objs
        self._loglines = []
        self._log("=" * 56)
        self._log("DECIMATE  keep %d%%  (%d mesh(es))" % (keep, len(objs)))
        self._cancel = False
        self._idx = 0
        self._before = 0
        self._after = 0
        self._cur = "Starting..."
        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._objs):
            return True
        obj = self._objs[self._idx]
        self._cur = "Reducing %d/%d" % (self._idx + 1, len(self._objs))
        n = obj.GetPolygonCount()
        if n < MIN_POLYS:
            self._log("   - %s : %d polys, skipped (already light)"
                      % (obj.GetName(), n))
            self._idx += 1
            return False
        try:
            reduced = reduce_mesh(obj, self._strength)
            if reduced is None or reduced.GetPolygonCount() == 0:
                self._log("   ! %s : reduction produced nothing, skipped"
                          % obj.GetName())
            elif reduced.GetPolygonCount() >= n:
                self._log("   ! %s : no reduction (%d -> %d) -- strength "
                          "param id likely wrong" % (obj.GetName(), n,
                                                     reduced.GetPolygonCount()))
            else:
                self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
                self._before += n
                apply_in_place(obj, reduced)
                m = obj.GetPolygonCount()
                self._after += m
                self._log("   [ok] %-28s %d -> %d" % (obj.GetName(), n, m))
        except Exception:
            self._log("   ! %s : %s" % (obj.GetName(),
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
        saved = self._before - self._after
        self._log(("Cancelled. " if self._cancel else "Done. ")
                  + "%s -> %s polys (saved %s)."
                  % ("{:,}".format(self._before), "{:,}".format(self._after),
                     "{:,}".format(saved)))
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
            self._prog.set(self._idx / float(len(self._objs) or 1), self._cur)
            c4d.StatusSetText(self._cur)
            c4d.StatusSetBar(int(100.0 * self._idx / (len(self._objs) or 1)))
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = DecimateDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=620, defaulth=520)


if __name__ == "__main__":
    main()

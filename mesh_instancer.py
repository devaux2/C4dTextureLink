"""
mesh_instancer.py
=================

Cinema 4D S24+  --  turn repeated meshes into instances, even when each copy
has its transform baked into the vertex positions (as decompiled game maps
usually do).

Why this is needed
------------------
Game engines store a model once and place it many times via per-instance
transforms (GPU instancing). When a map is decompiled to OBJ, each placement
is usually exported with its position/rotation/scale BAKED into the vertices,
so two copies of the same tree have different vertex numbers and look like
unrelated meshes. This tool matches meshes by SHAPE (topology + geometry up to
a transform), keeps one master, recovers each copy's transform, and replaces
the copies with instances at the right place.

How it works
------------
1. Bucket objects by point + polygon count (cheap).
2. Within contended buckets, bucket again by polygon connectivity (same model
   == same topology).
3. Within a topology group, solve the affine transform that maps one mesh's
   vertices onto another (vertex order is preserved, so it's exact) and verify
   it. Matches are grouped.
4. Keep one master; replace the rest with Instance objects whose matrix places
   them exactly where the copy was.

Use it
------
Script Manager (Shift+F11) -> open this file -> Execute.
- **Analyze**: report duplicate groups + how many objects would be removed.
- **Convert**: replace duplicates with instances (one undo step).
"""

import hashlib
import struct
import time
import traceback
import c4d
from c4d import gui, documents


TICK_BUDGET_MS = 50


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def all_objects(doc):
    out = []

    def rec(o):
        while o:
            out.append(o)
            rec(o.GetDown())
            o = o.GetNext()

    rec(doc.GetFirstObject())
    return out


def leaf_polygon_objects(doc):
    return [o for o in all_objects(doc)
            if o.GetType() == c4d.Opolygon and o.GetDown() is None]


def poly_hash(obj):
    """Hash of polygon connectivity (point indices)."""
    h = hashlib.md5()
    buf = bytearray()
    for p in obj.GetAllPolygons():
        buf += struct.pack("<iiii", p.a, p.b, p.c, p.d)
    h.update(buf)
    return h.hexdigest()


def bbox_diag(pts):
    if not pts:
        return 1.0
    mnx = mxx = pts[0].x
    mny = mxy = pts[0].y
    mnz = mxz = pts[0].z
    for v in pts:
        if v.x < mnx:
            mnx = v.x
        elif v.x > mxx:
            mxx = v.x
        if v.y < mny:
            mny = v.y
        elif v.y > mxy:
            mxy = v.y
        if v.z < mnz:
            mnz = v.z
        elif v.z > mxz:
            mxz = v.z
    d = ((mxx - mnx) ** 2 + (mxy - mny) ** 2 + (mxz - mnz) ** 2) ** 0.5
    return d or 1.0


def independent_triple(R, tol):
    """Find 3 reference points spanning a non-degenerate basis. None if the
    mesh is planar/linear."""
    n = len(R)
    p0 = R[0]
    ia = a = None
    for j in range(1, n):
        d = R[j] - p0
        if d.GetLength() > tol:
            ia, a = j, d
            break
    if ia is None:
        return None
    al = a.GetLength()
    ib = b = None
    for j in range(1, n):
        if j == ia:
            continue
        d = R[j] - p0
        if a.Cross(d).GetLength() > tol * al:
            ib, b = j, d
            break
    if ib is None:
        return None
    nrm = a.Cross(b)
    nl = nrm.GetLength()
    if nl <= 0:
        return None
    for j in range(1, n):
        if j in (ia, ib):
            continue
        d = R[j] - p0
        if abs(nrm.Dot(d)) / nl > tol:
            return (ia, ib, j)
    return None


def verify(M, R, C, tol):
    n = len(R)
    step = max(1, n // 300)
    for i in range(0, n, step):
        if (M * R[i] - C[i]).GetLength() > tol:
            return False
    return True


def solve_transform(R, rdiag, C):
    """Return the c4d.Matrix mapping master-local points R onto copy-local
    points C, or None if they aren't the same shape under a transform."""
    n = len(R)
    if n == 0 or n != len(C):
        return None
    tol_len = max(1e-4, rdiag * 1e-5)
    pos_tol = max(1e-3, rdiag * 1e-4)
    p0R, p0C = R[0], C[0]

    trip = independent_triple(R, tol_len)
    if trip is None:
        # Planar/linear: only handle a pure translation.
        M = c4d.Matrix()
        M.off = p0C - p0R
        return M if verify(M, R, C, pos_tol) else None

    ia, ib, ic = trip
    Rb = c4d.Matrix(c4d.Vector(0), R[ia] - p0R, R[ib] - p0R, R[ic] - p0R)
    Cb = c4d.Matrix(c4d.Vector(0), C[ia] - p0C, C[ib] - p0C, C[ic] - p0C)
    try:
        A = Cb * (~Rb)               # linear part (off == 0)
    except Exception:
        return None
    M = c4d.Matrix(p0C - (A * p0R), A.v1, A.v2, A.v3)
    return M if verify(M, R, C, pos_tol) else None


def set_render_instance(inst, on):
    if not on:
        return
    try:
        inst[c4d.INSTANCEOBJECT_RENDERINSTANCE_MODE] = 1
        return
    except Exception:
        pass
    try:
        inst[c4d.INSTANCEOBJECT_RENDERINSTANCE] = True
    except Exception:
        pass


def make_instance(doc, master, copy, m_at, render_inst):
    """Replace `copy` with an instance of `master` at the copy's location."""
    inst = c4d.BaseObject(c4d.Oinstance)
    inst.SetName(copy.GetName())
    inst[c4d.INSTANCEOBJECT_LINK] = master
    set_render_instance(inst, render_inst)
    inst.InsertAfter(copy)
    inst.SetMg(copy.GetMg() * m_at)     # master-local -> copy world
    doc.AddUndo(c4d.UNDOTYPE_NEW, inst)
    doc.AddUndo(c4d.UNDOTYPE_DELETE, copy)
    copy.Remove()


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

G_RENDER = 3001
G_ANALYZE = 3002
G_CONVERT = 3003
G_CANCEL = 3004
G_CLOSE = 3005
G_LOG = 3006
G_PROG = 3007

_dialog = None


class InstancerDialog(gui.GeDialog):
    def __init__(self):
        super(InstancerDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Mesh -> Instances")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.AddCheckbox(G_RENDER, c4d.BFH_LEFT, 0, 0,
                         "Use Render Instances (lightest for rendering)")

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 240,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_ANALYZE, c4d.BFH_LEFT, 110, 0, "Analyze")
        self.AddButton(G_CONVERT, c4d.BFH_LEFT, 130, 0, "Convert to instances")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_RENDER, True)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Analyze first to see duplicate groups, then "
                              "Convert.")
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
        if cid == G_ANALYZE:
            self._start("analyze")
        elif cid == G_CONVERT:
            self._start("convert")
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

    # --- run --------------------------------------------------------------
    def _start(self, mode):
        if self._running:
            return
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return

        self._mode = mode
        self._render_inst = self.GetBool(G_RENDER)
        self._loglines = []
        self._cancel = False
        self._cur = "Scanning..."

        objs = leaf_polygon_objects(self._doc)
        self._log("=" * 58)
        self._log("ANALYZE" if mode == "analyze" else "CONVERT")
        self._log("Polygon objects found: %d" % len(objs))
        if not objs:
            self._log("Nothing to do.")
            return

        # Bucket by (point count, poly count); only contended buckets matter.
        size_buckets = {}
        for o in objs:
            size_buckets.setdefault((o.GetPointCount(), o.GetPolygonCount()),
                                    []).append(o)
        self._to_hash = [o for lst in size_buckets.values() if len(lst) >= 2
                         for o in lst]
        self._log("Candidates (shared point/poly count): %d"
                  % len(self._to_hash))

        # State
        self._topo = {}            # (pcnt,vcnt,polyhash) -> [objs]
        self._size_buckets = size_buckets
        self._topo_groups = []
        self._result = []          # [(master, [(copy, M)])]
        self._tasks = []
        self._idx = 0
        self._converted = 0
        # cluster state
        self._g = 0
        self._m = 0
        self._refs = []
        self._clusters = []
        self._cluster_total = 0
        self._cluster_done = 0

        self._phase = "topo" if self._to_hash else "group"

        if mode == "convert":
            self._doc.StartUndo()

        self._running = True
        self.Enable(G_ANALYZE, False)
        self.Enable(G_CONVERT, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        ph = self._phase

        # 2. topology bucketing
        if ph == "topo":
            if self._idx < len(self._to_hash):
                o = self._to_hash[self._idx]
                self._cur = "Topology %d/%d" % (self._idx + 1,
                                                len(self._to_hash))
                try:
                    key = (o.GetPointCount(), o.GetPolygonCount(), poly_hash(o))
                    self._topo.setdefault(key, []).append(o)
                except Exception:
                    pass
                self._idx += 1
            else:
                self._phase = "group"
            return False

        # build topo groups (cheap, do once)
        if ph == "group":
            self._topo_groups = [lst for lst in self._topo.values()
                                 if len(lst) >= 2]
            self._cluster_total = sum(len(g) for g in self._topo_groups)
            self._g = self._m = self._cluster_done = 0
            self._refs, self._clusters = [], []
            self._phase = "cluster" if self._topo_groups else "report"
            return False

        # 3. cluster by transform within each topology group
        if ph == "cluster":
            if self._g < len(self._topo_groups):
                grp = self._topo_groups[self._g]
                if self._m < len(grp):
                    obj = grp[self._m]
                    self._cur = "Matching %d/%d" % (self._cluster_done + 1,
                                                    self._cluster_total)
                    pts = obj.GetAllPoints()
                    matched = False
                    for ci, (robj, rpts, rdiag) in enumerate(self._refs):
                        m_at = solve_transform(rpts, rdiag, pts)
                        if m_at is not None:
                            self._clusters[ci].append((obj, m_at))
                            matched = True
                            break
                    if not matched:
                        self._refs.append((obj, pts, bbox_diag(pts)))
                        self._clusters.append([])
                    self._m += 1
                    self._cluster_done += 1
                else:
                    for ci, cl in enumerate(self._clusters):
                        if cl:
                            self._result.append((self._refs[ci][0], cl))
                    self._refs, self._clusters = [], []
                    self._m = 0
                    self._g += 1
            else:
                self._phase = "report"
            return False

        # report groups
        if ph == "report":
            copies = sum(len(cl) for _, cl in self._result)
            self._log("Instance groups: %d   Objects that become instances: %d"
                      % (len(self._result), copies))
            self._result.sort(key=lambda mc: len(mc[1]), reverse=True)
            for master, cl in self._result[:25]:
                self._log("   %-28s x%d" % (master.GetName(), len(cl) + 1))
            if len(self._result) > 25:
                self._log("   ... and %d more group(s)"
                          % (len(self._result) - 25))
            if self._mode == "analyze":
                self._phase = "finish"
            else:
                self._tasks = [(master, copy, m) for master, cl in self._result
                               for (copy, m) in cl]
                self._idx = 0
                self._phase = "convert"
            return False

        # 4. convert
        if ph == "convert":
            if self._idx < len(self._tasks):
                master, copy, m_at = self._tasks[self._idx]
                self._cur = "Instancing %d/%d" % (self._idx + 1,
                                                  len(self._tasks))
                try:
                    make_instance(self._doc, master, copy, m_at,
                                  self._render_inst)
                    self._converted += 1
                except Exception:
                    self._log("   ! failed on %s" % copy.GetName())
                self._idx += 1
            else:
                self._log("Converted %d object(s) to instances."
                          % self._converted)
                self._phase = "finish"
            return False

        return True

    def _update_progress(self):
        ph = self._phase
        if ph == "topo":
            frac = self._idx / float(len(self._to_hash) or 1)
        elif ph == "cluster":
            frac = self._cluster_done / float(self._cluster_total or 1)
        elif ph == "convert":
            frac = self._idx / float(len(self._tasks) or 1)
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
        if self._mode == "convert":
            try:
                self._doc.EndUndo()
            except Exception:
                pass
        c4d.StatusClear()
        c4d.EventAdd()
        self._log("=" * 58)
        self._log("Cancelled." if self._cancel else "Done.")
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_ANALYZE, True)
        self.Enable(G_CONVERT, True)
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
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = InstancerDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=620, defaulth=560)


if __name__ == "__main__":
    main()

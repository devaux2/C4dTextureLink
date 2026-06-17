"""
instance_check.py  --  why didn't these instance?
=================================================

Select two or more objects you believe are the SAME model, then run this. It
reports, for each (vs the first as reference), whether they:
  - share point + polygon counts,
  - share topology (polygon connectivity hash),
  - match up to a transform (the test the instancer uses),
so we can see why the mesh instancer didn't group them.

Read-only. Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import hashlib
import struct
import os
import c4d
from c4d import gui, storage, documents


def poly_hash(o):
    h = hashlib.md5()
    buf = bytearray()
    for p in o.GetAllPolygons():
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
        mnx, mxx = min(mnx, v.x), max(mxx, v.x)
        mny, mxy = min(mny, v.y), max(mxy, v.y)
        mnz, mxz = min(mnz, v.z), max(mxz, v.z)
    return (((mxx - mnx) ** 2 + (mxy - mny) ** 2 + (mxz - mnz) ** 2) ** 0.5
            or 1.0)


def independent_triple(R, tol):
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
    ib = b = None
    al = a.GetLength()
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
        if abs(nrm.Dot(R[j] - p0)) / nl > tol:
            return (ia, ib, j)
    return None


def matches_up_to_transform(R, C):
    """True if C == some affine * R (index correspondence), like the instancer."""
    n = len(R)
    if n == 0 or n != len(C):
        return False
    diag = bbox_diag(R)
    tol_len = max(1e-4, diag * 1e-5)
    pos_tol = max(1e-3, diag * 1e-4)
    p0R, p0C = R[0], C[0]
    trip = independent_triple(R, tol_len)
    if trip is None:
        M = c4d.Matrix()
        M.off = p0C - p0R
    else:
        ia, ib, ic = trip
        Rb = c4d.Matrix(c4d.Vector(0), R[ia] - p0R, R[ib] - p0R, R[ic] - p0R)
        Cb = c4d.Matrix(c4d.Vector(0), C[ia] - p0C, C[ib] - p0C, C[ic] - p0C)
        try:
            A = Cb * (~Rb)
        except Exception:
            return False
        M = c4d.Matrix(p0C - (A * p0R), A.v1, A.v2, A.v3)
    step = max(1, n // 300)
    for i in range(0, n, step):
        if (M * R[i] - C[i]).GetLength() > pos_tol:
            return False
    return True


def selected_polys(doc):
    sel = doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_CHILDREN)
    out = []

    def rec(o):
        while o:
            if o.GetType() == c4d.Opolygon:
                out.append(o)
            rec(o.GetDown())
            o = o.GetNext()

    for s in sel:
        if s.GetType() == c4d.Opolygon:
            out.append(s)
        rec(s.GetDown())
    return out


def main():
    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return
    objs = selected_polys(doc)
    if len(objs) < 2:
        gui.MessageDialog("Select at least two polygon objects you think are "
                          "the same model.")
        return

    out = ["INSTANCE CHECK  (%d objects)" % len(objs), "=" * 50]
    ref = objs[0]
    rpts = ref.GetAllPoints()
    rpc, rvc = ref.GetPointCount(), ref.GetPolygonCount()
    rhash = poly_hash(ref)
    out.append("Reference: %s" % ref.GetName())
    out.append("  points=%d polys=%d topo=%s" % (rpc, rvc, rhash[:8]))
    out.append("-" * 50)

    same_count = same_topo = xform = 0
    for o in objs[1:]:
        pc, vc = o.GetPointCount(), o.GetPolygonCount()
        cnt_ok = (pc == rpc and vc == rvc)
        topo_ok = cnt_ok and poly_hash(o) == rhash
        xf_ok = cnt_ok and matches_up_to_transform(rpts, o.GetAllPoints())
        same_count += cnt_ok
        same_topo += topo_ok
        xform += xf_ok
        verdict = ("INSTANCE-able (matches up to transform)" if xf_ok else
                   "same count+topology" if topo_ok else
                   "same count, DIFFERENT topology (re-triangulated/reordered)"
                   if cnt_ok else
                   "DIFFERENT point/poly count")
        out.append("%s" % o.GetName())
        out.append("  points=%d polys=%d topo=%s  -> %s"
                   % (pc, vc, poly_hash(o)[:8] if True else "", verdict))

    out.append("-" * 50)
    out.append("vs reference: same count %d/%d, same topology %d/%d, "
               "instance-able %d/%d"
               % (same_count, len(objs) - 1, same_topo, len(objs) - 1,
                  xform, len(objs) - 1))
    if xform == len(objs) - 1:
        out.append(">> These SHOULD instance. If they didn't, they weren't in "
                   "the instancer's selection/run.")
    elif same_count and not same_topo:
        out.append(">> Same geometry but vertex order/triangulation differs per "
                   "copy -> the transform solver can't match them. Needs an "
                   "order-independent or name-based instancer.")
    elif not same_count:
        out.append(">> Point/polygon counts differ -> they are NOT identical "
                   "meshes (edited/merged/different LOD). Can't instance.")
    report = "\n".join(out)
    print(report)
    show_report(report)


# --- copyable, scrollable report window with Save ---------------------------

_dialog = None
_RG_TEXT = 1
_RG_SAVE = 2
_RG_CLOSE = 3


class ReportDialog(gui.GeDialog):
    def __init__(self, text):
        super(ReportDialog, self).__init__()
        self._text = text

    def CreateLayout(self):
        self.SetTitle("Instance Check")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)
        self.AddMultiLineEditText(
            _RG_TEXT, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 420,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddButton(_RG_SAVE, c4d.BFH_LEFT, 120, 0, "Save to file...")
        self.AddButton(_RG_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetString(_RG_TEXT, self._text)
        return True

    def Command(self, cid, msg):
        if cid == _RG_SAVE:
            path = storage.SaveDialog(title="Save report", force_suffix="txt",
                                      def_file="instance_check.txt")
            if path:
                if not path.lower().endswith(".txt"):
                    path += ".txt"
                try:
                    with open(path, "w") as f:
                        f.write(self._text)
                    gui.MessageDialog("Saved to:\n%s" % path)
                except Exception as e:
                    gui.MessageDialog("Could not save: %s" % e)
        elif cid == _RG_CLOSE:
            self.Close()
        return True


def show_report(text):
    global _dialog
    _dialog = ReportDialog(text)
    _dialog.Open(c4d.DLG_TYPE_MODAL_RESIZEABLE, defaultw=620, defaulth=520)


if __name__ == "__main__":
    main()

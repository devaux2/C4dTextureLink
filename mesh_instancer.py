"""
mesh_instancer.py
=================

Cinema 4D S24+  --  turn repeated meshes into instances.

A scene built by importing hundreds of OBJs often contains the same mesh
(a tree, rock, prop) loaded many times over -- huge RAM and render cost. This
tool finds objects whose geometry is identical, keeps one as the master, and
replaces the rest with Instance objects that reference it, preserving each
copy's position / rotation / scale.

How it works
------------
- Objects are bucketed by point + polygon count (cheap), then the geometry of
  objects in contended buckets is hashed (local point positions + polygon
  indices). Only exact matches are grouped, so different meshes are never
  merged.
- For each group of duplicates, the first is kept; the others become Instance
  objects (Render Instances by default) at the same world transform.

Use it
------
Script Manager (Shift+F11) -> open this file -> Execute.
- **Analyze**: scan and report duplicate groups + how many objects would be
  removed. Changes nothing.
- **Convert**: do the replacement (one undo step; Ctrl+Z reverts).
Progress shows live; **Cancel** stops cleanly.
"""

import hashlib
import struct
import time
import traceback
import c4d
from c4d import gui, documents


TICK_BUDGET_MS = 50
# Geometry quantisation for the hash (units of 1/QUANT). Identical instances
# have bit-identical local points, so this only guards against float noise.
QUANT = 10000.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def all_objects(doc):
    """Every object in the document, depth-first."""
    out = []

    def rec(o):
        while o:
            out.append(o)
            rec(o.GetDown())
            o = o.GetNext()

    rec(doc.GetFirstObject())
    return out


def leaf_polygon_objects(doc):
    """Polygon objects with no children (safe to replace with an instance)."""
    res = []
    for o in all_objects(doc):
        if o.GetType() == c4d.Opolygon and o.GetDown() is None:
            res.append(o)
    return res


def mesh_hash(obj):
    """Hash of local point positions + polygon indices."""
    h = hashlib.md5()
    buf = bytearray()
    for v in obj.GetAllPoints():
        buf += struct.pack("<qqq", int(round(v.x * QUANT)),
                            int(round(v.y * QUANT)), int(round(v.z * QUANT)))
    h.update(buf)
    buf = bytearray()
    for p in obj.GetAllPolygons():
        buf += struct.pack("<iiii", p.a, p.b, p.c, p.d)
    h.update(buf)
    return h.hexdigest()


def set_render_instance(inst, on):
    """Best-effort enable of Render Instance mode across C4D versions."""
    if not on:
        return
    try:
        inst[c4d.INSTANCEOBJECT_RENDERINSTANCE_MODE] = 1  # render instance
        return
    except Exception:
        pass
    try:
        inst[c4d.INSTANCEOBJECT_RENDERINSTANCE] = True
    except Exception:
        pass


def make_instance(doc, master, original, render_inst):
    """Replace `original` with an Instance of `master` at the same transform."""
    mg = original.GetMg()
    inst = c4d.BaseObject(c4d.Oinstance)
    inst.SetName(original.GetName())
    inst[c4d.INSTANCEOBJECT_LINK] = master
    set_render_instance(inst, render_inst)
    inst.InsertAfter(original)      # same parent, keep hierarchy position
    inst.SetMg(mg)                  # preserve placement
    doc.AddUndo(c4d.UNDOTYPE_NEW, inst)
    doc.AddUndo(c4d.UNDOTYPE_DELETE, original)
    original.Remove()


# ---------------------------------------------------------------------------
# Progress bar user area
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

    # --- layout -----------------------------------------------------------
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

    # --- helpers ----------------------------------------------------------
    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _progress(self, frac, label):
        self._prog.set(frac, label)
        c4d.StatusSetText(label)
        c4d.StatusSetBar(int(frac * 100))

    # --- events -----------------------------------------------------------
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

        # Cheap scan: gather leaf polygon objects and bucket by point/poly count.
        objs = leaf_polygon_objects(self._doc)
        self._log("=" * 58)
        self._log("%s" % ("ANALYZE" if mode == "analyze" else "CONVERT"))
        self._log("Polygon objects found: %d" % len(objs))
        if not objs:
            self._log("Nothing to do.")
            return

        buckets = {}
        for o in objs:
            key = (o.GetPointCount(), o.GetPolygonCount())
            buckets.setdefault(key, []).append(o)
        # Only objects sharing a (points, polys) signature can be duplicates.
        self._to_hash = [o for lst in buckets.values() if len(lst) >= 2
                         for o in lst]
        self._buckets = buckets
        self._hashes = {}
        self._groups = []
        self._tasks = []
        self._idx = 0
        self._converted = 0

        self._log("Candidates to fingerprint: %d" % len(self._to_hash))
        self._phase = "hash" if self._to_hash else "group"

        if mode == "convert":
            self._doc.StartUndo()

        self._running = True
        self.Enable(G_ANALYZE, False)
        self.Enable(G_CONVERT, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _build_groups(self):
        """Group contended objects by exact geometry hash."""
        groups = []
        for lst in self._buckets.values():
            if len(lst) < 2:
                continue
            by_hash = {}
            for o in lst:
                hsh = self._hashes.get(id(o))
                if hsh is None:
                    continue
                by_hash.setdefault(hsh, []).append(o)
            for members in by_hash.values():
                if len(members) >= 2:
                    groups.append(members)
        # Largest groups first (most impactful).
        groups.sort(key=len, reverse=True)
        return groups

    def _step(self):
        ph = self._phase

        if ph == "hash":
            if self._idx < len(self._to_hash):
                o = self._to_hash[self._idx]
                self._cur = "Fingerprinting %d/%d" % (
                    self._idx + 1, len(self._to_hash))
                try:
                    self._hashes[id(o)] = mesh_hash(o)
                except Exception:
                    pass
                self._idx += 1
            else:
                self._phase = "group"
            return False

        if ph == "group":
            self._groups = self._build_groups()
            dupes = sum(len(g) - 1 for g in self._groups)
            self._log("Duplicate groups: %d   Objects that can become "
                      "instances: %d" % (len(self._groups), dupes))
            for g in self._groups[:25]:
                self._log("   %-28s x%d"
                          % (g[0].GetName(), len(g)))
            if len(self._groups) > 25:
                self._log("   ... and %d more group(s)"
                          % (len(self._groups) - 25))

            if self._mode == "analyze":
                self._phase = "finish"
            else:
                # Flatten to (master, duplicate) tasks.
                self._tasks = [(g[0], dup) for g in self._groups
                               for dup in g[1:]]
                self._idx = 0
                self._phase = "convert"
            return False

        if ph == "convert":
            if self._idx < len(self._tasks):
                master, dup = self._tasks[self._idx]
                self._cur = "Instancing %d/%d" % (
                    self._idx + 1, len(self._tasks))
                try:
                    make_instance(self._doc, master, dup, self._render_inst)
                    self._converted += 1
                except Exception:
                    self._log("   ! failed on %s" % dup.GetName())
                self._idx += 1
            else:
                self._log("Converted %d object(s) to instances."
                          % self._converted)
                self._phase = "finish"
            return False

        return True

    def _update_progress(self):
        if self._phase == "hash":
            frac = self._idx / float(len(self._to_hash) or 1)
        elif self._phase == "convert":
            frac = self._idx / float(len(self._tasks) or 1)
        elif self._phase == "finish":
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

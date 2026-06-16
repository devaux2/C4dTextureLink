"""
group_objects.py  --  group objects already in the scene under Nulls
====================================================================

After merging a big FBX/glTF, the scene is a flat list of objects named like
'dire_tower002.dire_tower002.010'. This groups them under Nulls by name:
every 'dire_tower002.*' goes under a 'dire_tower002' Null, etc. Positions and
scale are preserved (it only re-parents).

It complements 1_import_objects.py (which groups during import, by filename) --
use this one when the objects are already in the scene.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, documents


# ---------------------------------------------------------------------------
# Name -> group key
# ---------------------------------------------------------------------------

def _tokenize(name):
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def base_key(obj_name, use_full):
    """Reduce an object name to its grouping base.

    Default takes everything before the first dot, which collapses the
    Blender/FBX 'name.name.NNN' pattern (dire_tower002.dire_tower002.010 ->
    dire_tower002). With `use_full`, only a trailing '.NNN' duplicate index is
    removed."""
    if use_full:
        return re.sub(r"\.\d+$", "", obj_name)
    return obj_name.split(".")[0]


def common_prefix(names):
    if len(names) < 2:
        return ""
    p = os.path.commonprefix([n.lower() for n in names])
    idx = max(p.rfind(s) for s in ("_", "-", ".", " "))
    if idx < 0:
        return ""
    return names[0][:idx + 1]


def strip_name_prefix(base, strip_prefix):
    name = base
    if strip_prefix and name.lower().startswith(strip_prefix.lower()):
        name = name[len(strip_prefix):]
    name = name.lstrip(" _-.")
    return name if name else base


def collapse_part_suffixes(names):
    """rocks001 / rocks001_1 / rocks001_2 -> rocks001 (only when the base is a
    real sibling); attached numbers (tower001 vs tower002) stay separate."""
    base_set = set(n.lower() for n in names)
    reduce_count = {}
    cands = []
    for n in names:
        c = re.sub(r"_\d+$", "", n)
        cands.append(c if c != n else None)
        if c != n:
            reduce_count[c.lower()] = reduce_count.get(c.lower(), 0) + 1
    out = []
    for n, c in zip(names, cands):
        if c is not None and (c.lower() in base_set
                              or reduce_count.get(c.lower(), 0) >= 2):
            out.append(c)
        else:
            out.append(n)
    return out


def top_level_objects(doc):
    out = []
    o = doc.GetFirstObject()
    while o:
        out.append(o)
        o = o.GetNext()
    return out


def plan_groups(objs, use_full, strip_prefix, do_collapse):
    """Return (names_per_obj, groups) where names_per_obj is the group name for
    each object, and groups = ordered [(name, count), ...]."""
    bases = [strip_name_prefix(base_key(o.GetName(), use_full), strip_prefix)
             for o in objs]
    names = collapse_part_suffixes(bases) if do_collapse else bases
    order, counts = [], {}
    for n in names:
        k = n.lower()
        if k not in counts:
            counts[k] = [n, 0]
            order.append(k)
        counts[k][1] += 1
    groups = [(counts[k][0], counts[k][1]) for k in order]
    return names, groups


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

G_FULL = 6001
G_PREFIX = 6002
G_COLLAPSE = 6003
G_MIN2 = 6004
G_PREVIEW = 6005
G_RUN = 6006
G_CANCEL = 6007
G_CLOSE = 6008
G_LOG = 6009
G_PROG = 6010

_dialog = None


class GroupDialog(gui.GeDialog):
    def __init__(self):
        super(GroupDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Group Objects under Nulls")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Strip prefix", 0)
        self.AddEditText(G_PREFIX, c4d.BFH_SCALEFIT, 0, 0)
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddCheckbox(G_FULL, c4d.BFH_LEFT, 0, 0,
                         "Use full name (only strip .NNN)")
        self.AddCheckbox(G_COLLAPSE, c4d.BFH_LEFT, 0, 0,
                         "Merge _N split parts")
        self.AddCheckbox(G_MIN2, c4d.BFH_LEFT, 0, 0,
                         "Only group 2+ objects")
        self.GroupEnd()

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 240,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_PREVIEW, c4d.BFH_LEFT, 120, 0, "Preview groups")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 90, 0, "Group")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetString(G_PREFIX, "")
        self.SetBool(G_FULL, False)
        self.SetBool(G_COLLAPSE, True)
        self.SetBool(G_MIN2, True)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Groups top-level objects under Nulls by name "
                              "(e.g. dire_tower002.* -> a 'dire_tower002' "
                              "Null). 'Preview groups' first.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _opts(self):
        return (self.GetBool(G_FULL), self.GetString(G_PREFIX).strip(),
                self.GetBool(G_COLLAPSE), self.GetBool(G_MIN2))

    def _resolve_prefix(self, objs, use_full):
        prefix = self.GetString(G_PREFIX).strip()
        if not prefix:
            prefix = common_prefix([base_key(o.GetName(), use_full)
                                    for o in objs])
            if prefix:
                self.SetString(G_PREFIX, prefix)
        return prefix

    def _summary(self, groups, total, kept, prefix):
        head = ("%d top-level object(s) -> %d Null group(s)"
                % (total, kept))
        if prefix:
            head += "  (stripping '%s')" % prefix
        lines = [head + ":", ""]
        shown = [g for g in groups if g[1] >= 1]
        for name, count in shown[:60]:
            lines.append("   %-32s x%d" % (name, count))
        if len(shown) > 60:
            lines.append("   ... and %d more" % (len(shown) - 60))
        return lines

    def Command(self, cid, msg):
        if cid == G_PREVIEW:
            self._preview()
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

    def _preview(self):
        doc = documents.GetActiveDocument()
        if doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        objs = top_level_objects(doc)
        if not objs:
            self.SetString(G_LOG, "No top-level objects in the scene.")
            return
        use_full, _pf, collapse, min2 = self._opts()
        prefix = self._resolve_prefix(objs, use_full)
        _names, groups = plan_groups(objs, use_full, prefix, collapse)
        kept = sum(1 for _n, c in groups if (c >= 2 or not min2))
        self._loglines = ["Group preview (nothing changed yet):", ""]
        self._loglines += self._summary(groups, len(objs), kept, prefix)
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _start(self):
        if self._running:
            return
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        objs = top_level_objects(self._doc)
        if not objs:
            self.SetString(G_LOG, "No top-level objects in the scene.")
            return

        use_full, _pf, collapse, min2 = self._opts()
        prefix = self._resolve_prefix(objs, use_full)
        names, groups = plan_groups(objs, use_full, prefix, collapse)

        # Which group names are large enough to make a Null for.
        count_by = {}
        for n in names:
            count_by[n.lower()] = count_by.get(n.lower(), 0) + 1
        # Build the work list: objects whose group will be created.
        self._tasks = []
        for obj, name in zip(objs, names):
            if (not min2) or count_by[name.lower()] >= 2:
                self._tasks.append((obj, name))

        if not self._tasks:
            self.SetString(G_LOG, "Nothing to group (no name shared by 2+ "
                                  "objects). Untick 'Only group 2+' to wrap "
                                  "every object.")
            return

        self._loglines = []
        self._log("=" * 58)
        self._log("GROUP  (%d object(s) into Nulls)" % len(self._tasks))
        self._cancel = False
        self._idx = 0
        self._nulls = {}
        self._cur = "Starting..."

        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_PREVIEW, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._tasks):
            return True
        obj, name = self._tasks[self._idx]
        self._cur = "Grouping %d/%d" % (self._idx + 1, len(self._tasks))
        key = name.lower()
        null = self._nulls.get(key)
        if null is None:
            null = c4d.BaseObject(c4d.Onull)
            null.SetName(name)
            self._doc.InsertObject(null)
            self._doc.AddUndo(c4d.UNDOTYPE_NEW, null)
            self._nulls[key] = null
        if obj != null:
            mg = obj.GetMg()
            self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
            obj.InsertUnder(null)
            obj.SetMg(mg)              # preserve world transform
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
                  + "Made %d group Null(s)." % len(self._nulls))
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_RUN, True)
        self.Enable(G_PREVIEW, True)
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
    _dialog = GroupDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=520)


if __name__ == "__main__":
    main()

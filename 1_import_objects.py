"""
1_import_objects.py  --  STAGE 1 of the pipeline
================================================

Import a folder of 3D objects (FBX / OBJ / glTF / ABC / ...) into the current
Cinema 4D scene, optionally organising each file's parts under a named Null.

This is a focused, single-purpose script -- importing only. The other stages
are separate scripts:
    2_mesh_instancer.py        - collapse duplicate meshes into instances
    3_octane_inspector.py      - read your Octane build's parameter IDs (once)
    4_octane_texture_linker.py - build Octane Universal materials + link maps

Tip on formats: FBX and glTF (.glb) keep materials, texture links and UVs;
OBJ/MTL does not. Prefer FBX or glTF for game assets.

Run: Script Manager (Shift+F11) -> open this file -> Execute.
"""

import os
import re
import time
import traceback
import c4d
from c4d import gui, storage, documents


# Offset (scene units) between spread-apart imports when "Spread" is on.
SPREAD_SPACING = 200.0

OBJECT_EXTENSIONS = {
    ".fbx", ".obj", ".gltf", ".glb", ".3ds", ".dae", ".stl", ".ply", ".abc",
    ".usd", ".usda", ".usdc", ".usdz", ".c4d", ".lwo", ".lws",
}


class Options(object):
    def __init__(self):
        self.objects_folder = ""
        self.recursive = False
        self.group = True
        self.strip_prefix = ""
        self.spread = False


# ---------------------------------------------------------------------------
# Grouping helpers (same logic used in the texture linker)
# ---------------------------------------------------------------------------

def _normalize(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _tokenize(name):
    return [t for t in re.split(r"[ _\-.]+", name.lower()) if t]


def gather_files(folder, recursive):
    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in OBJECT_EXTENSIONS:
                    found.append(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(folder)):
            full = os.path.join(folder, f)
            if (os.path.isfile(full)
                    and os.path.splitext(f)[1].lower() in OBJECT_EXTENSIONS):
                found.append(full)
    return sorted(found)


def file_base(path):
    return os.path.splitext(os.path.basename(path))[0]


def common_prefix(names):
    """Longest shared prefix across names, trimmed to a separator boundary."""
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


def _collapse_part_suffixes(names):
    """Collapse '_<number>' split parts onto a base only when that base is a
    real sibling. rocks001/_1/_2 -> rocks001; tree007 vs tree008 stay apart."""
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


def build_import_plan(paths, do_group, strip_prefix=""):
    """Return (plan, groups). plan = [(group_name_or_None, full), ...]."""
    if not do_group:
        return [(None, full) for full in paths], []
    bases = [strip_name_prefix(file_base(f), strip_prefix) for f in paths]
    names = _collapse_part_suffixes(bases)
    plan, order, counts = [], [], {}
    for full, name in zip(paths, names):
        plan.append((name, full))
        k = name.lower()
        if k not in counts:
            counts[k] = [name, 0]
            order.append(k)
        counts[k][1] += 1
    groups = [(counts[k][0], counts[k][1]) for k in order]
    return plan, groups


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def merge_collect(doc, full, log, ignore_null_names=()):
    """Merge one file; return (ok, [new top-level objects])."""
    flags = (c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS
             | c4d.SCENEFILTER_MERGESCENE)
    keep = []
    before_ids = set()
    obj = doc.GetFirstObject()
    while obj:
        keep.append(obj)
        before_ids.add(id(obj))
        obj = obj.GetNext()

    if not documents.MergeDocument(doc, full, flags):
        log("   ! failed to import: %s" % os.path.basename(full))
        return False, []

    roots = []
    obj = doc.GetFirstObject()
    while obj:
        if id(obj) not in before_ids:
            is_our_null = (obj.GetType() == c4d.Onull
                           and obj.GetName().lower() in ignore_null_names)
            if not is_our_null:
                roots.append(obj)
        obj = obj.GetNext()

    log("   [ok] %s  (%d object(s))" % (os.path.basename(full), len(roots)))
    return True, roots


def make_group_null(doc, name):
    null = c4d.BaseObject(c4d.Onull)
    null.SetName(name)
    doc.InsertObject(null)
    doc.AddUndo(c4d.UNDOTYPE_NEW, null)
    return null


def place_roots(doc, roots, group_null, opts, offset_index):
    """Parent/position merged objects, preserving world transform. Returns the
    new offset_index."""
    moved = False
    for root in roots:
        if group_null is not None:
            if root == group_null:
                continue
            mg = root.GetMg()
            root.InsertUnder(group_null)
            root.SetMg(mg)
        elif opts.spread:
            pos = root.GetRelPos()
            pos.x += offset_index * SPREAD_SPACING
            root.SetRelPos(pos)
            moved = True
        doc.AddUndo(c4d.UNDOTYPE_NEW, root)
    return offset_index + 1 if moved else offset_index


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

G_FOLDER = 5001
G_BROWSE = 5002
G_GROUP = 5003
G_PREFIX = 5004
G_RECURSE = 5005
G_SPREAD = 5006
G_PREVIEW = 5007
G_RUN = 5008
G_CANCEL = 5009
G_CLOSE = 5010
G_LOG = 5011
G_PROG = 5012

_dialog = None


class ImporterDialog(gui.GeDialog):
    def __init__(self):
        super(ImporterDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Stage 1 - Import Objects")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 95, 0, "Objects folder", 0)
        self.AddEditText(G_FOLDER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddButton(G_BROWSE, c4d.BFH_RIGHT, 0, 0, "Browse...")
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddCheckbox(G_GROUP, c4d.BFH_LEFT, 0, 0, "Group under Nulls")
        self.AddStaticText(0, c4d.BFH_RIGHT, 0, 0, "Strip prefix", 0)
        self.AddEditText(G_PREFIX, c4d.BFH_SCALEFIT, 0, 0)
        self.AddCheckbox(G_RECURSE, c4d.BFH_LEFT, 0, 0, "Recurse")
        self.GroupEnd()

        self.AddCheckbox(G_SPREAD, c4d.BFH_LEFT, 0, 0,
                         "Spread apart (moves objects; off = keep positions)")

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 220,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_PREVIEW, c4d.BFH_LEFT, 120, 0, "Preview groups")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 90, 0, "Import")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetBool(G_GROUP, True)
        self.SetString(G_PREFIX, "")
        self.SetBool(G_RECURSE, False)
        self.SetBool(G_SPREAD, False)
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Pick a folder of objects (FBX/glTF/OBJ/...), "
                              "then Import. 'Preview groups' shows how files "
                              "will be organised.")
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

    def _resolve_prefix(self, paths):
        prefix = self.GetString(G_PREFIX).strip()
        if not prefix:
            prefix = common_prefix([file_base(f) for f in paths])
            if prefix:
                self.SetString(G_PREFIX, prefix)
        return prefix

    def _summary(self, groups, total, prefix, limit=None):
        head = "%d file(s) -> %d Null group(s)" % (total, len(groups))
        if prefix:
            head += "  (stripping '%s')" % prefix
        lines = [head + ":", ""]
        shown = groups if limit is None else groups[:limit]
        for name, count in shown:
            lines.append("   %s%s" % (name,
                                      "  (%d files)" % count if count > 1
                                      else ""))
        if limit is not None and len(groups) > limit:
            lines.append("   ... and %d more (Preview groups for the full list)"
                         % (len(groups) - limit))
        return lines

    def Command(self, cid, msg):
        if cid == G_BROWSE:
            p = storage.LoadDialog(title="Select the OBJECTS folder",
                                   flags=c4d.FILESELECT_DIRECTORY)
            if p:
                self.SetString(G_FOLDER, p)
        elif cid == G_PREVIEW:
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
        folder = self.GetString(G_FOLDER).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Choose a valid objects folder to preview.")
            return
        paths = gather_files(folder, self.GetBool(G_RECURSE))
        if not paths:
            self.SetString(G_LOG, "No importable files found.")
            return
        if not self.GetBool(G_GROUP):
            self.SetString(G_LOG, "Grouping off: %d file(s) import "
                                  "individually." % len(paths))
            return
        prefix = self._resolve_prefix(paths)
        _plan, groups = build_import_plan(paths, True, prefix)
        self._loglines = ["Group preview (nothing imported yet):", ""]
        self._loglines += self._summary(groups, len(paths), prefix)
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _start(self):
        if self._running:
            return
        folder = self.GetString(G_FOLDER).strip()
        if not folder or not os.path.isdir(folder):
            self.SetString(G_LOG, "Please choose a valid objects folder.")
            return
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return

        opts = Options()
        opts.objects_folder = folder
        opts.recursive = self.GetBool(G_RECURSE)
        opts.group = self.GetBool(G_GROUP)
        opts.spread = self.GetBool(G_SPREAD)
        self._opts = opts

        paths = gather_files(folder, opts.recursive)
        if not paths:
            self.SetString(G_LOG, "No importable files found.")
            return
        prefix = self._resolve_prefix(paths) if opts.group else ""
        opts.strip_prefix = prefix
        plan, groups = build_import_plan(paths, opts.group, prefix)
        if opts.group:
            summary = "\n".join(self._summary(groups, len(paths), prefix,
                                              limit=20))
            if not gui.QuestionDialog("Import and organise these?\n\n"
                                      + summary):
                return

        self._plan = plan
        self._loglines = []
        self._log("=" * 58)
        self._log("IMPORT  (%s)" % folder)
        self._log("%d file(s) to import." % len(plan))
        self._cancel = False
        self._idx = 0
        self._offset = 0
        self._imported = 0
        self._group_nulls = {}
        self._last_event = 0
        self._cur = "Starting..."

        self._doc.StartUndo()
        self._running = True
        self.Enable(G_RUN, False)
        self.Enable(G_PREVIEW, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _step(self):
        if self._idx >= len(self._plan):
            return True
        gname, full = self._plan[self._idx]
        self._cur = "Importing %d/%d  %s" % (self._idx + 1, len(self._plan),
                                             os.path.basename(full))
        ok, roots = merge_collect(self._doc, full, self._log,
                                  set(self._group_nulls.keys()))
        if ok:
            self._imported += 1
        null = None
        if gname is not None:
            key = gname.lower()
            null = self._group_nulls.get(key)
            if null is None:
                null = make_group_null(self._doc, gname)
                self._group_nulls[key] = null
        self._offset = place_roots(self._doc, roots, null, self._opts,
                                   self._offset)
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
                  + "Imported %d file(s) into %d group(s)."
                  % (self._imported, len(self._group_nulls)))
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
            self._progress(self._idx / float(len(self._plan) or 1), self._cur)
            # Throttle scene refreshes (rebuilds caches); show progress live.
            if self._imported - self._last_event >= 25:
                self._last_event = self._imported
                c4d.EventAdd()
            if done:
                self._finish()
        except Exception:
            self._log("")
            self._log("ERROR -- stopped:")
            self._log(traceback.format_exc())
            self._finish()


def main():
    global _dialog
    _dialog = ImporterDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=640, defaulth=520)


if __name__ == "__main__":
    main()

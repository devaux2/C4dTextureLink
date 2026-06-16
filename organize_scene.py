"""
organize_scene.py  --  sort the whole top level under ~10 theme Nulls
=====================================================================

Re-sorts every top-level object (Nulls, loose meshes, instances) under a small
set of theme parent Nulls -- Water / Trees / Foliage / Rocks & Terrain /
Paths / Structures / Dire / Radiant / Props / Overlays / Misc -- so a sprawling
top level (hundreds of items) becomes ~10 you can drill into. The Octane
environment, cameras and lights are left where they are.

Matching is done on a *cleaned* version of each name (Source 2 boilerplate like
n0_lr0_agg_prop_/nomerge/meshset stripped first), so messy and clean names land
in the same theme. Positions/scale are preserved (it only re-parents).

Edit CATEGORY_RULES below to taste; order = priority (first keyword hit wins).
Run: Script Manager (Shift+F11) -> open -> Execute. Preview, then Organize.
"""

import re
import time
import traceback
import c4d
from c4d import gui, documents

# theme -> keywords (substring match on the cleaned name). Order = priority.
CATEGORY_RULES = [
    ("Water",          ["waterfall", "riverflow", "riveredge", "riverbed",
                        "river", "water", "lily", "cattail", "fish", "splash",
                        "pour", "vapor"]),
    ("Trees",          ["tree", "oak", "pine", "bamboo", "cine"]),
    ("Foliage",        ["leaves", "leaf", "frond", "flower", "petal", "plant",
                        "mushroom", "grass", "clump", "root", "vine", "fern",
                        "moss"]),
    ("Rocks_Terrain",  ["cliff", "rock", "stone", "boulder", "ground", "ramp",
                        "tile", "mesh_base", "worldspace", "lava", "pit",
                        "block", "angled"]),
    ("Paths",          ["mod_", "path", "riverbed"]),
    ("Camps",          ["creep_camp", "creep", "camp", "neutral_stash",
                        "stash"]),
    ("Radiant",        ["radiant"]),
    ("Dire",           ["dire"]),
    ("Structures",     ["barracks", "tower", "column", "temple", "wall",
                        "statue", "shrine", "fence", "stairs", "hut",
                        "outpost", "portal", "base", "pool", "shop",
                        "cathedral", "well", "mine", "bridge"]),
    ("Props",          ["torch", "urn", "candle", "bone", "chain", "stick",
                        "pot", "log", "crystal", "glow", "lantern", "banner",
                        "logo", "ward", "flag", "web", "snake", "wildlife",
                        "dark_statue", "shrine", "vines", "skeleton", "aegis",
                        "shield", "leanto", "fur", "generic", "skull",
                        "barrel"]),
    ("Overlays",       ["overlay", "blend", "decal"]),
]
MISC = "Misc"

# Whole-token boilerplate to drop before keyword matching.
_JUNK = [re.compile(p, re.I) for p in (
    r"n\d+", r"lr\d+", r"c\d+", r"s", r"cb", r"agg", r"merge", r"prop",
    r"nomerge\d*", r"nosplit\d*", r"meshset\d*",
)]

# Only these object types get moved; everything else (Octane env, cameras,
# lights, ...) is left alone.
MOVABLE = (c4d.Opolygon, c4d.Onull, c4d.Oinstance)


def clean_name(name):
    n = name.split(".")[0]                       # drop .meshset_N / .NNN / dup
    toks = [t for t in re.split(r"[_.]", n) if t]
    kept = [t for t in toks if not any(p.fullmatch(t) for p in _JUNK)]
    return ("_".join(kept) if kept else n).lower()


def categorize(name):
    c = clean_name(name)
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw in c:
                return cat
    return MISC


def top_level_movable(doc):
    out = []
    o = doc.GetFirstObject()
    while o:
        if o.GetType() in MOVABLE:
            out.append(o)
        o = o.GetNext()
    return out


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

G_PREVIEW = 9001
G_RUN = 9002
G_CANCEL = 9003
G_CLOSE = 9004
G_LOG = 9005
G_PROG = 9006

_dialog = None


class OrganizeDialog(gui.GeDialog):
    def __init__(self):
        super(OrganizeDialog, self).__init__()
        self._loglines = []
        self._prog = ProgressArea()
        self._running = False
        self._cancel = False
        self._cur = ""

    def CreateLayout(self):
        self.SetTitle("Organize Scene into Theme Nulls")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.AddMultiLineEditText(
            G_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 320,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)

        self.AddUserArea(G_PROG, c4d.BFH_SCALEFIT, 0, 18)
        self.AttachUserArea(self._prog, G_PROG)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 0, "")
        self.AddButton(G_PREVIEW, c4d.BFH_LEFT, 130, 0, "Preview")
        self.AddButton(G_RUN, c4d.BFH_LEFT, 100, 0, "Organize")
        self.AddButton(G_CANCEL, c4d.BFH_LEFT, 80, 0, "Cancel")
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 80, 0, "Close")
        self.GroupEnd()

        self.GroupEnd()
        return True

    def InitValues(self):
        self.Enable(G_CANCEL, False)
        self.SetString(G_LOG, "Preview shows how many objects land in each "
                              "theme (and what falls into Misc). Then Organize. "
                              "One undo step.")
        return True

    def _log(self, s=""):
        self._loglines.append(s)
        if len(self._loglines) > 4000:
            self._loglines = self._loglines[-4000:]
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _tally(self, objs):
        cats = {}
        misc = []
        for o in objs:
            cat = categorize(o.GetName())
            cats[cat] = cats.get(cat, 0) + 1
            if cat == MISC:
                misc.append(o.GetName())
        return cats, misc

    def _preview(self):
        doc = documents.GetActiveDocument()
        if doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        objs = top_level_movable(doc)
        if not objs:
            self.SetString(G_LOG, "No movable top-level objects.")
            return
        cats, misc = self._tally(objs)
        self._loglines = ["Preview (nothing changed yet):",
                          "%d top-level objects -> %d theme(s):"
                          % (len(objs), len(cats)), ""]
        order = [c for c, _ in CATEGORY_RULES] + [MISC]
        for cat in order:
            if cat in cats:
                self._log("  %-14s %d" % (cat, cats[cat]))
        if misc:
            self._log("")
            self._log("Misc (unmatched -- add keywords to sort these):")
            for nm in misc[:60]:
                self._log("   %s  (clean: %s)" % (nm, clean_name(nm)))
            if len(misc) > 60:
                self._log("   ... and %d more" % (len(misc) - 60))
        self.SetString(G_LOG, "\n".join(self._loglines))

    def _start(self):
        if self._running:
            return
        self._doc = documents.GetActiveDocument()
        if self._doc is None:
            self.SetString(G_LOG, "No active document.")
            return
        objs = top_level_movable(self._doc)
        if not objs:
            self.SetString(G_LOG, "No movable top-level objects.")
            return

        self._tasks = [(o, categorize(o.GetName())) for o in objs]
        self._loglines = []
        self._log("=" * 56)
        self._log("ORGANIZE  (%d objects into themes)" % len(self._tasks))
        self._cancel = False
        self._idx = 0
        self._nulls = {}
        self._moved = 0
        self._cur = "Starting..."

        self._doc.StartUndo()
        self._running = True
        self.Enable(G_PREVIEW, False)
        self.Enable(G_RUN, False)
        self.Enable(G_CANCEL, True)
        self.SetTimer(20)

    def _theme_null(self, cat):
        null = self._nulls.get(cat)
        if null is None:
            null = c4d.BaseObject(c4d.Onull)
            null.SetName(cat)
            self._doc.InsertObject(null)
            self._doc.AddUndo(c4d.UNDOTYPE_NEW, null)
            self._nulls[cat] = null
        return null

    def _step(self):
        if self._idx >= len(self._tasks):
            return True
        obj, cat = self._tasks[self._idx]
        self._cur = "Sorting %d/%d" % (self._idx + 1, len(self._tasks))
        null = self._theme_null(cat)
        if obj != null:
            mg = obj.GetMg()
            self._doc.AddUndo(c4d.UNDOTYPE_CHANGE, obj)
            obj.InsertUnder(null)
            obj.SetMg(mg)
            self._moved += 1
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
                  + "Sorted %d object(s) into %d theme(s)."
                  % (self._moved, len(self._nulls)))
        self._prog.set(self._prog.percent if self._cancel else 1.0,
                       "Cancelled" if self._cancel else "Finished")
        self.Enable(G_PREVIEW, True)
        self.Enable(G_RUN, True)
        self.Enable(G_CANCEL, False)
        print("\n".join(self._loglines))

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
    _dialog = OrganizeDialog()
    _dialog.Open(c4d.DLG_TYPE_ASYNC, defaultw=620, defaulth=560)


if __name__ == "__main__":
    main()

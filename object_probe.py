"""
object_probe.py  --  dump an object's type and parameters
=========================================================

Select an object (e.g. an Octane Scatter) and run this. It reports the object's
plugin type id, name, and every parameter (id, label, current value) so we can
see how to drive it from Python -- in particular whether Octane Scatter can be
fed explicit per-instance transforms.

Shows a copyable, scrollable window with a Save-to-file button. Read-only.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import c4d
from c4d import gui, storage, documents


def _short(v):
    try:
        if isinstance(v, c4d.Vector):
            return "Vector(%.3f, %.3f, %.3f)" % (v.x, v.y, v.z)
        if isinstance(v, c4d.BaseList2D):
            return "<%s '%s'>" % (v.GetType(), v.GetName())
        s = str(v)
        return s[:80]
    except Exception:
        return "?"


def describe(node):
    lines = []
    desc = node.GetDescription(c4d.DESCFLAGS_DESC_0)
    if desc is None:
        lines.append("(no description)")
        return lines
    count = 0
    for bc, descid, _gid in desc:
        if bc is None:
            continue
        try:
            top = descid[0].id
            leaf = descid[len(descid) - 1]
            dtype = leaf.dtype
        except Exception:
            top, dtype = "?", "?"
        name = bc[c4d.DESC_NAME] or bc[c4d.DESC_SHORT_NAME] or ""
        val = ""
        try:
            if len(descid) == 1:
                val = _short(node[top])
        except Exception:
            val = ""
        lines.append("  [%s] dtype=%s  %-28s %s" % (top, dtype, name, val))
        count += 1
    lines.append("  (%d parameters)" % count)
    return lines


def main():
    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return
    op = doc.GetActiveObject()
    if op is None:
        gui.MessageDialog("Select an object first.")
        return

    out = ["=" * 60, "OBJECT PROBE", "=" * 60,
           "Name: %s" % op.GetName(),
           "Type id: %s   (%s)" % (op.GetType(),
                                   op.GetTypeName() if hasattr(op, "GetTypeName")
                                   else "?"),
           "Children: %s" % ", ".join(
               "%s(%s)" % (c.GetName(), c.GetType())
               for c in op.GetChildren()) or "(none)",
           "-" * 60, "Parameters:"]
    out += describe(op)
    report = "\n".join(out)
    print(report)
    show_report(report)


_dialog = None
_T, _SAVE, _CLOSE = 1, 2, 3


class ReportDialog(gui.GeDialog):
    def __init__(self, text):
        super(ReportDialog, self).__init__()
        self._text = text

    def CreateLayout(self):
        self.SetTitle("Object Probe")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)
        self.AddMultiLineEditText(
            _T, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 440,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddButton(_SAVE, c4d.BFH_LEFT, 120, 0, "Save to file...")
        self.AddButton(_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetString(_T, self._text)
        return True

    def Command(self, cid, msg):
        if cid == _SAVE:
            p = storage.SaveDialog(title="Save probe", force_suffix="txt",
                                   def_file="object_probe.txt")
            if p:
                if not p.lower().endswith(".txt"):
                    p += ".txt"
                try:
                    with open(p, "w") as f:
                        f.write(self._text)
                    gui.MessageDialog("Saved to:\n%s" % p)
                except Exception as e:
                    gui.MessageDialog("Could not save: %s" % e)
        elif cid == _CLOSE:
            self.Close()
        return True


def show_report(text):
    global _dialog
    _dialog = ReportDialog(text)
    _dialog.Open(c4d.DLG_TYPE_MODAL_RESIZEABLE, defaultw=680, defaulth=560)


if __name__ == "__main__":
    main()

"""
3_octane_inspector.py  --  STAGE 3: capture this Octane build's parameter IDs
=============================================================================

Step 1 of the Octane texture-linker. Octane's Python parameter IDs differ
between versions, so instead of guessing them this script reads the *real* IDs
out of your install. Run it once and send me the report.

What it does
------------
- Reports every material in the scene by type (so we can see which ones are
  Octane vs standard).
- For the ACTIVE material (selected in the Material Manager) it dumps:
    * the plugin type ID and name,
    * every parameter: ID + label + data type,
    * the shader/node tree hanging off it (e.g. Image Texture nodes and their
      filename parameter).
- Writes the report to a text file and shows it in a window you can copy from.

How to use
----------
1. Create one Octane **Universal** material (Octane menu) and, inside it, add an
   **Image Texture** node on a channel (e.g. Albedo). This gives us a real
   example to read.
2. Select that material in the Material Manager.
3. Script Manager (Shift+F11) -> open this file -> Execute.
4. Copy the report (or grab the saved .txt) and send it back.

Nothing in the scene is modified.
"""

import os
import c4d
from c4d import gui, documents


def _safe(fn, default=""):
    try:
        return fn()
    except Exception:
        return default


def describe_params(node, log, indent="   "):
    """Dump every parameter of a node: ID, label, data type."""
    desc = node.GetDescription(c4d.DESCFLAGS_DESC_0)
    if desc is None:
        log(indent + "(no description)")
        return
    count = 0
    for bc, descid, _groupid in desc:
        if bc is None:
            continue
        try:
            top_id = descid[0].id
            leaf = descid[len(descid) - 1]
            leaf_id = leaf.id
            dtype = leaf.dtype
        except Exception:
            top_id, leaf_id, dtype = "?", "?", "?"
        name = bc[c4d.DESC_NAME] or bc[c4d.DESC_SHORT_NAME] or ""
        # Show both the top-level id and the leaf id (they match for simple
        # parameters; differ for nested ones).
        if top_id == leaf_id:
            idtxt = str(top_id)
        else:
            idtxt = "%s/%s" % (top_id, leaf_id)
        log("%s[%s]  dtype=%s  %s" % (indent, idtxt, dtype, name))
        count += 1
    log(indent + "(%d parameters)" % count)


def describe_shader_tree(node, log, depth=1):
    """Walk the shader/node tree under a material and dump each node."""
    shader = node.GetFirstShader()
    while shader:
        indent = "   " * depth
        log("%sShader: '%s'  type=%s (%d)" % (
            indent, shader.GetName(),
            _safe(lambda: shader.GetTypeName(), "?"), shader.GetType()))
        describe_params(shader, log, indent + "   ")
        describe_shader_tree(shader, log, depth + 1)
        shader = shader.GetNext()


def main():
    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return

    lines = []

    def log(s=""):
        lines.append(s)

    log("=" * 64)
    log("OCTANE INSPECTOR REPORT")
    log("=" * 64)
    log("C4D build: %s" % _safe(lambda: c4d.GetC4DVersion(), "?"))
    log("")

    # 1. All materials by type.
    mats = doc.GetMaterials()
    log("Materials in scene: %d" % len(mats))
    type_counts = {}
    for m in mats:
        key = (m.GetType(), _safe(lambda: m.GetTypeName(), "?"))
        type_counts[key] = type_counts.get(key, 0) + 1
    for (tid, tname), n in sorted(type_counts.items()):
        log("   type %s (%s):  %d material(s)" % (tid, tname, n))
    log("")

    # 2. Active material, in full.
    active = doc.GetActiveMaterial()
    if active is None:
        log("No ACTIVE material selected.")
        log("-> Select your Octane Universal material in the Material Manager")
        log("   and run this again so it can be dumped in full.")
    else:
        log("=" * 64)
        log("ACTIVE MATERIAL")
        log("=" * 64)
        log("Name: %s" % active.GetName())
        log("Type ID: %s   (%s)" % (active.GetType(),
                                    _safe(lambda: active.GetTypeName(), "?")))
        log("")
        log("Parameters:")
        describe_params(active, log)
        log("")
        log("Shader / node tree:")
        describe_shader_tree(active, log)

    report = "\n".join(lines)
    print(report)

    # Save to a file the user can grab easily.
    out_path = os.path.join(os.path.expanduser("~"),
                            "octane_inspector_report.txt")
    saved = False
    try:
        with open(out_path, "w") as f:
            f.write(report)
        saved = True
    except Exception:
        out_path = "(could not write file; copy from the console/window)"

    _show_report(report, out_path if saved else out_path)


# --- a simple scrollable, copyable report window ---------------------------

_dlg = None
G_TEXT = 2001
G_PATH = 2002
G_CLOSE = 2003


class ReportDialog(gui.GeDialog):
    def __init__(self, text, path):
        super(ReportDialog, self).__init__()
        self._text = text
        self._path = path

    def CreateLayout(self):
        self.SetTitle("Octane Inspector Report")
        self.GroupBegin(0, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)
        self.AddStaticText(G_PATH, c4d.BFH_SCALEFIT, 0, 0,
                           "Saved to: %s" % self._path, 0)
        self.AddMultiLineEditText(
            G_TEXT, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 420,
            c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_MONOSPACED)
        self.AddButton(G_CLOSE, c4d.BFH_RIGHT, 90, 0, "Close")
        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetString(G_TEXT, self._text)
        return True

    def Command(self, cid, msg):
        if cid == G_CLOSE:
            self.Close()
        return True


def _show_report(text, path):
    global _dlg
    _dlg = ReportDialog(text, path)
    _dlg.Open(c4d.DLG_TYPE_MODAL_RESIZEABLE, defaultw=720, defaulth=560)


if __name__ == "__main__":
    main()

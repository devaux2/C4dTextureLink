"""
octane_probe.py  --  diagnose how an Octane material is wired
=============================================================

Select an Octane material in the Material Manager (e.g. your working
'tree_pine_frond_CO'), then run this. It reports:
  - the material type,
  - what each channel link (Albedo/Specular/Roughness/Normal/Opacity/...) points
    at, and for Image Texture nodes the Type(1105) / Linear-invert(1117) / file,
  - the full shader/node tree with the same per-node detail.

It shows the report in a window and writes it to:
    <your home folder>/octane_probe_report.txt

Nothing is modified. Send me the report.

Run: Script Manager (Shift+F11) -> open -> Execute.
"""

import os
import c4d
from c4d import gui, documents

OCT_IMG_ID = 1029508          # Octane Image Texture node
OCT_IMG_FILE = 1100
OCT_IMG_TYPE = 1105
OCT_IMG_INVERT = 1117
OCT_IMG_COLORSPACE = 1118

# channel name -> Universal "Texture" link parameter id (from the inspector)
OCT_LINKS = [
    ("Albedo/Diffuse", 2517),
    ("Specular",       2524),
    ("Specular map",   2587),
    ("Roughness",      2533),
    ("Reflection",     2528),
    ("Bump",           2539),
    ("Normal",         2542),
    ("Displacement",   2580),
    ("Opacity",        2545),
    ("Transmission",   2555),
    ("Emission",       2557),
]


def _img_detail(sh):
    try:
        return ("Type(1105)=%s  Inv(1117)=%s  ColorSpace(1118)=%s  file=%s"
                % (sh[OCT_IMG_TYPE], sh[OCT_IMG_INVERT],
                   sh[OCT_IMG_COLORSPACE], sh[OCT_IMG_FILE]))
    except Exception as e:
        return "(could not read image params: %s)" % e


def main():
    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return
    m = doc.GetActiveMaterial()
    if m is None:
        gui.MessageDialog("Select an Octane material in the Material Manager "
                          "first, then run again.")
        return

    out = []
    out.append("=" * 60)
    out.append("OCTANE PROBE")
    out.append("=" * 60)
    out.append("Material: %s" % m.GetName())
    out.append("Type ID:  %s" % m.GetType())
    out.append("")

    # 1) Channel links: what does each channel point at?
    out.append("CHANNEL LINKS")
    out.append("-" * 60)
    for label, pid in OCT_LINKS:
        try:
            linked = m[pid]
        except Exception:
            linked = None
        if linked is None:
            out.append("  %-16s [%s] : (empty)" % (label, pid))
        else:
            tname = "ImageTexture" if linked.GetType() == OCT_IMG_ID \
                else str(linked.GetType())
            out.append("  %-16s [%s] : %s  '%s'"
                       % (label, pid, tname, linked.GetName()))
            if linked.GetType() == OCT_IMG_ID:
                out.append("        " + _img_detail(linked))
    out.append("")

    # 2) Full shader / node tree
    out.append("SHADER / NODE TREE")
    out.append("-" * 60)
    found = [0]

    def rec(sh, depth):
        while sh:
            found[0] += 1
            pad = "  " * depth
            if sh.GetType() == OCT_IMG_ID:
                out.append("%sIMG '%s'  %s"
                           % (pad, sh.GetName(), _img_detail(sh)))
            else:
                out.append("%sshader %s  '%s'"
                           % (pad, sh.GetType(), sh.GetName()))
            rec(sh.GetDown(), depth + 1)
            sh = sh.GetNext()

    rec(m.GetFirstShader(), 0)
    if found[0] == 0:
        out.append("  (no classic shaders -- this is a pure node-graph "
                   "material; channel links above tell the real story)")

    report = "\n".join(out)
    print(report)

    path = os.path.join(os.path.expanduser("~"), "octane_probe_report.txt")
    try:
        with open(path, "w") as f:
            f.write(report)
        report += "\n\nSaved to: %s" % path
    except Exception:
        pass

    gui.MessageDialog(report)


if __name__ == "__main__":
    main()

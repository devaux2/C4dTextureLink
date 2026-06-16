"""
dump_names.py  --  export every object name in the scene
=========================================================

Writes the full object list (name + type + hierarchy depth) to a text file so
the grouping logic can be built around the real naming instead of guesswork.

Output: <your home folder>/scene_object_names.txt   (also shown in a window)

Run: Script Manager (Shift+F11) -> open -> Execute. Send me the file.
Nothing is modified.
"""

import os
import c4d
from c4d import gui, documents

# Friendly names for the common object types we care about.
TYPE_NAMES = {
    c4d.Opolygon: "polygon",
    c4d.Onull: "null",
    c4d.Oinstance: "instance",
}


def type_label(o):
    return TYPE_NAMES.get(o.GetType(), str(o.GetType()))


def main():
    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return

    lines = []
    counts = {}
    total = [0]

    def rec(o, depth):
        while o:
            total[0] += 1
            t = type_label(o)
            counts[t] = counts.get(t, 0) + 1
            lines.append("%s[%s] %s" % ("  " * depth, t, o.GetName()))
            rec(o.GetDown(), depth + 1)
            o = o.GetNext()

    rec(doc.GetFirstObject(), 0)

    header = ["=" * 60, "SCENE OBJECT NAMES", "=" * 60,
              "Total objects: %d" % total[0]]
    for t, c in sorted(counts.items()):
        header.append("  %-10s %d" % (t, c))
    header.append("-" * 60)
    report = "\n".join(header + lines)

    print(report)

    path = os.path.join(os.path.expanduser("~"), "scene_object_names.txt")
    saved = ""
    try:
        with open(path, "w") as f:
            f.write(report)
        saved = "\n\nSaved to: %s" % path
    except Exception as e:
        saved = "\n\n(could not write file: %s -- copy from here)" % e

    # Show a window with a scrollable, copyable view (first part) + the path.
    preview = report if len(report) < 6000 else report[:6000] + "\n... (full list in the file)"
    gui.MessageDialog(preview + saved)


if __name__ == "__main__":
    main()

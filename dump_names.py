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
from c4d import gui, documents, storage

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

    # Let the user choose where to save the full list.
    path = storage.SaveDialog(
        type=c4d.FILESELECTTYPE_ANYTHING,
        title="Save object name list",
        force_suffix="txt",
        def_file="scene_object_names.txt")
    if not path:
        gui.MessageDialog("Cancelled - nothing saved.\n(%d objects; full list "
                          "also printed to the Console.)" % total[0])
        return
    if not path.lower().endswith(".txt"):
        path += ".txt"
    try:
        with open(path, "w") as f:
            f.write(report)
        gui.MessageDialog("Wrote %d objects to:\n%s" % (total[0], path))
    except Exception as e:
        gui.MessageDialog("Could not write file:\n%s\n\n%s\n\n(Full list is in "
                          "the Console.)" % (path, e))


if __name__ == "__main__":
    main()

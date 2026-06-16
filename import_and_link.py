"""
import_and_link.py
===================

One-shot pipeline for Cinema 4D S24:

    1. Pick a folder of 3D objects (OBJ, FBX, etc.) -> import them all
       into the current scene.
    2. Pick a folder of textures -> auto-connect every map to the correct
       material channel (BaseColor / Normal / Roughness / Bump / ...).

The texture-matching logic is shared with ``texture_linker.py`` -- this script
imports it so there is a single source of truth. Keep both files in the same
folder.

How to run
----------
Cinema 4D -> Script Manager (Shift+F11) -> open this file -> Execute.
You will be asked for the objects folder, then the texture folder.
"""

import os
import sys
import c4d
from c4d import gui, storage, documents


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# Hard-code folders to skip the pickers (None = always prompt).
OBJECTS_FOLDER = None
TEXTURE_FOLDER = None

# Look in sub-folders when collecting objects to import.
RECURSIVE_OBJECTS = False

# Spread imported files out so they do not overlap. Objects from each file are
# offset along X by SPREAD_SPACING (in scene units). Set False to import each
# file exactly where it was authored.
SPREAD_OBJECTS = True
SPREAD_SPACING = 200.0

# 3D formats to import. C4D resolves the right importer from the extension.
OBJECT_EXTENSIONS = {
    ".obj", ".fbx", ".3ds", ".dae", ".stl", ".ply", ".abc",
    ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz", ".c4d",
}


# ---------------------------------------------------------------------------
# Locate and import the shared texture-linking module
# ---------------------------------------------------------------------------

def _import_texture_linker():
    """Import texture_linker from this script's folder (robust in S24)."""
    here = None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = None  # __file__ not defined in some run contexts
    if here and here not in sys.path:
        sys.path.insert(0, here)
    try:
        import texture_linker
        return texture_linker
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Object import
# ---------------------------------------------------------------------------

def gather_objects(folder, recursive):
    """Return sorted full paths of importable 3D files in `folder`."""
    paths = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in OBJECT_EXTENSIONS:
                    paths.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            full = os.path.join(folder, f)
            if (os.path.isfile(full)
                    and os.path.splitext(f)[1].lower() in OBJECT_EXTENSIONS):
                paths.append(full)
    return sorted(paths)


def _top_level_objects(doc):
    """Set of id() for current top-level objects, for diffing after a merge."""
    result = set()
    obj = doc.GetFirstObject()
    while obj:
        result.add(obj)
        obj = obj.GetNext()
    return result


def import_objects(doc, paths, log):
    """Merge each file into `doc`. Returns number of files imported."""
    flags = c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS
    imported = 0
    offset_index = 0

    for path in paths:
        before = _top_level_objects(doc)
        ok = documents.MergeDocument(doc, path, flags)
        if not ok:
            log.append("   ! failed to import: %s" % os.path.basename(path))
            continue

        # Identify objects added by this merge.
        new_roots = []
        obj = doc.GetFirstObject()
        while obj:
            if obj not in before:
                new_roots.append(obj)
            obj = obj.GetNext()

        if SPREAD_OBJECTS and new_roots:
            for root in new_roots:
                doc.AddUndo(c4d.UNDOTYPE_CHANGE, root)
                pos = root.GetRelPos()
                pos.x += offset_index * SPREAD_SPACING
                root.SetRelPos(pos)
            offset_index += 1

        imported += 1
        log.append("   [ok] imported: %s  (%d object(s))"
                   % (os.path.basename(path), len(new_roots)))

    return imported


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    tl = _import_texture_linker()
    if tl is None:
        gui.MessageDialog(
            "Could not find 'texture_linker.py'.\n"
            "Keep it in the same folder as this script.")
        return

    doc = documents.GetActiveDocument()
    if doc is None:
        gui.MessageDialog("No active document.")
        return

    # 1. Objects folder.
    obj_folder = OBJECTS_FOLDER
    if not obj_folder:
        obj_folder = storage.LoadDialog(
            title="Select the OBJECTS folder to import",
            flags=c4d.FILESELECT_DIRECTORY)
    if not obj_folder or not os.path.isdir(obj_folder):
        gui.MessageDialog("No valid objects folder selected.")
        return

    object_paths = gather_objects(obj_folder, RECURSIVE_OBJECTS)
    if not object_paths:
        gui.MessageDialog("No importable 3D files found in:\n%s" % obj_folder)
        return

    # 2. Texture folder.
    tex_folder = TEXTURE_FOLDER
    if not tex_folder:
        tex_folder = storage.LoadDialog(
            title="Select the TEXTURE folder",
            flags=c4d.FILESELECT_DIRECTORY)
    if not tex_folder or not os.path.isdir(tex_folder):
        gui.MessageDialog("No valid texture folder selected.")
        return

    log = ["Objects folder: %s" % obj_folder,
           "Files to import: %d" % len(object_paths),
           "=" * 60,
           "IMPORT"]

    # Everything in one undo step.
    doc.StartUndo()
    try:
        imported = import_objects(doc, object_paths, log)
        # Push imported objects/materials into the scene before linking.
        c4d.EventAdd()

        log.append("")
        log.append("=" * 60)
        log.append("LINK TEXTURES")
        # link_all reuses this doc; we already own the undo block.
        _total, link_log = tl.link_all(doc, tex_folder, manage_undo=False)
        log += link_log
    finally:
        doc.EndUndo()

    log.insert(2, "Files imported:  %d" % imported)

    c4d.EventAdd()

    report = "\n".join(log)
    print(report)
    gui.MessageDialog(report)


if __name__ == "__main__":
    main()

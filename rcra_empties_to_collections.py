"""
rcra_empties_to_collections.py
Blender utility script — RCRA Forge v0.5.3

After importing a group GLB exported by RCRA Forge, run this script in
Blender's Script Editor to reorganise the scene:

  • Each top-level empty (e.g. "npc_grunthor") becomes its own Collection.
  • The mesh children of that empty are moved into the new Collection.
  • The now-empty empties are deleted.
  • The script is non-destructive for any objects that aren't direct children
    of a top-level empty.

Usage
-----
1. File > Import > glTF 2.0 (.glb) — import your RCRA Forge group export.
2. Open the Scripting workspace in Blender.
3. Open this file (or paste it), then click Run Script.

Tested with Blender 3.6 LTS / 4.x.
"""

import bpy


def empties_to_collections():
    scene       = bpy.context.scene
    root_col    = scene.collection          # master scene collection
    created     = 0
    moved       = 0
    removed     = 0

    # Collect top-level empties first (iterating while mutating is unsafe)
    top_empties = [
        obj for obj in root_col.objects
        if obj.type == 'EMPTY' and obj.parent is None
    ]

    for empty in top_empties:
        col_name = empty.name

        # Create a new collection for this part
        new_col = bpy.data.collections.new(col_name)
        root_col.children.link(new_col)

        # Move direct mesh/armature children into the new collection
        children = list(empty.children)
        for child in children:
            # Unlink from every collection the child currently lives in
            for col in list(child.users_collection):
                col.objects.unlink(child)
            new_col.objects.link(child)
            child.parent = None             # detach from empty
            moved += 1

        # Delete the now-childless empty
        bpy.data.objects.remove(empty, do_unlink=True)
        removed += 1
        created += 1

    print(
        f"[RCRA] empties_to_collections: "
        f"{created} collection(s) created, "
        f"{moved} object(s) moved, "
        f"{removed} empty/empties removed."
    )
    return created, moved, removed


if __name__ == "__main__":
    empties_to_collections()

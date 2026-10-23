"""One-time asset prep: bake Blender Studio's Rain into rig-free, textured meshes.

    python blender/tools/prepare_rain.py "<path to rain_v3.2.blend>"

Rain (c) Blender Foundation | studio.blender.org -- CC-BY 4.0.
Her production rig is replaced by our canonical rig (see anim_lab/swap.py), so here we
evaluate every visible mesh at the rest pose (modifiers applied), keep the materials and
textures, and drop everything else. Her embedded Python scripts are never executed.
"""
import os
import shutil
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "assets", "rain")
SKIP = ("nomask", "eyes_viewport")


def main(src):
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    bpy.ops.wm.open_mainfile(filepath=src)
    src_dir = os.path.dirname(src)
    dg = bpy.context.evaluated_depsgraph_get()

    baked = []
    for o in list(bpy.data.objects):
        if o.type != "MESH" or not o.name.startswith("GEO-") or o.hide_render:
            continue
        if any(k in o.name for k in SKIP):
            continue
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), depsgraph=dg)
        me.transform(o.matrix_world)
        me.name = o.name
        baked.append((o.name, me))

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    col = bpy.data.collections.new("Rain")
    bpy.context.scene.collection.children.link(col)
    for name, me in baked:
        ob = bpy.data.objects.new(name.replace("GEO-", ""), me)
        col.objects.link(ob)
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)

    # purge everything no longer referenced, then localise textures next to the output
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    tex_out = os.path.join(OUT_DIR, "textures")
    os.makedirs(tex_out, exist_ok=True)
    for img in bpy.data.images:
        if img.source not in ("FILE", "TILED"):
            continue
        path = bpy.path.abspath(img.filepath, library=img.library)
        base = os.path.basename(path)
        folder = os.path.dirname(path)
        if img.source == "TILED":  # UDIM: copy every tile
            for f in os.listdir(folder):
                if f.split(".")[0] == base.split(".")[0] and f.lower().endswith(".png"):
                    shutil.copy(os.path.join(folder, f), tex_out)
        elif os.path.exists(path):
            shutil.copy(path, tex_out)
        img.filepath = "//textures/" + base
    os.makedirs(OUT_DIR, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT_DIR, "rain_baked.blend"), compress=True)
    print("baked", [n for n, _ in baked])


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[-1]))

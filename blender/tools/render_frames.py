"""Render consecutive frames of a baked performance with a tight, character-following camera.

    python blender/tools/render_frames.py output/showcase/showcase.blend OUT_DIR START_FRAME COUNT \
        [--step 1] [--az 90] [--zoom 0.4] [--target 0.25] [--width 480] [--height 360]

`--zoom` scales camera distance (smaller = closer), `--target` is the look-at height as a
fraction of body height. Used to inspect feet / sit / jump motion frame by frame.
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

ap = argparse.ArgumentParser()
ap.add_argument("blend")
ap.add_argument("out")
ap.add_argument("start", type=int)
ap.add_argument("count", type=int)
ap.add_argument("--step", type=int, default=1)
ap.add_argument("--az", type=float, default=90)
ap.add_argument("--el", type=float, default=6)
ap.add_argument("--zoom", type=float, default=0.4)
ap.add_argument("--target", type=float, default=0.25)
ap.add_argument("--width", type=int, default=480)
ap.add_argument("--height", type=int, default=360)
ap.add_argument("--samples", type=int, default=8)
a = ap.parse_args()

bpy.context.preferences.filepaths.use_scripts_auto_execute = False
bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
sc = bpy.context.scene
arm = bpy.data.objects["Armature"]
H = arm["anim_lab_height"]
cam = bpy.data.objects["ShowCam"]
cam.animation_data_clear()                      # we drive the camera ourselves
sc.camera = cam
sc.render.engine = "CYCLES"
sc.cycles.device, sc.cycles.samples, sc.cycles.use_denoising = "CPU", a.samples, True
sc.render.resolution_x, sc.render.resolution_y = a.width, a.height
os.makedirs(a.out, exist_ok=True)
az, el, dist = math.radians(a.az), math.radians(a.el), 3.6 * H * a.zoom
for i in range(a.count):
    f = a.start + i * a.step
    sc.frame_set(f)
    p = arm.matrix_world.translation
    look = Vector((p.x, p.y, a.target * H + p.z * 0))
    cam.location = look + Vector((dist * math.sin(az) * math.cos(el), -dist * math.cos(az) * math.cos(el), dist * math.sin(el)))
    cam.rotation_euler = (look - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = os.path.join(a.out, f"f{f:05d}.png")
    bpy.ops.render.render(write_still=True)
    print("frame", f, flush=True)

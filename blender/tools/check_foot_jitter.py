"""Quantify foot wobble in a baked performance: frame-to-frame changes of foot pitch / yaw.

    python blender/tools/check_foot_jitter.py output/showcase/showcase.blend [t0 t1 ...]
"""
import math
import sys

import bpy
from mathutils import Vector

bpy.ops.wm.open_mainfile(filepath=sys.argv[-1] if sys.argv[-1].endswith(".blend") else sys.argv[1])
arm = bpy.data.objects["Armature"]
sc = bpy.context.scene
fps = sc.render.fps


def foot_angles(side):
    pb = arm.pose.bones[f"Ankle.{side}"]
    head = arm.matrix_world @ pb.head
    d = (arm.matrix_world @ pb.tail) - head
    char_fwd = arm.matrix_world.to_3x3() @ Vector((0, -1, 0))
    pitch = math.degrees(math.asin(max(-1, min(1, d.z / d.length))))
    left = Vector((0, 0, 1)).cross(char_fwd)
    horiz = (d.dot(char_fwd), d.dot(left))
    yaw = math.degrees(math.atan2(horiz[1], horiz[0])) if math.hypot(*horiz) > 0.3 * d.length else float('nan')  # undefined when the foot points straight down
    return pitch, yaw


windows = {"walk 20.5-22.5": (20.5, 22.5), "run 25.0-27.0": (25.0, 27.0), "walk-in 3-5": (3.0, 5.0)}
for label, (t0, t1) in windows.items():
    rows = {s: [] for s in "LR"}
    for f in range(int(t0 * fps), int(t1 * fps) + 1):
        sc.frame_set(f)
        for s in "LR":
            rows[s].append(foot_angles(s))
    for s in "LR":
        p = [r[0] for r in rows[s]]
        y = [r[1] for r in rows[s] if r[1] == r[1]]   # drop undefined (NaN) yaw samples
        dp = [abs(p[i + 1] - p[i]) for i in range(len(p) - 1)]
        dy = [abs(y[i + 1] - y[i]) for i in range(len(y) - 1)] or [0.0]
        # sign flips of the pitch velocity per second = oscillation count
        vel = [p[i + 1] - p[i] for i in range(len(p) - 1)]
        flips = sum(1 for i in range(len(vel) - 1) if vel[i] * vel[i + 1] < -0.5)
        print(f"{label} foot {s}: pitch range {min(p):+.0f}..{max(p):+.0f} deg, max step {max(dp):.1f} deg/frame; "
              f"yaw range {min(y):+.0f}..{max(y):+.0f}, max step {max(dy):.1f} deg/frame; pitch direction reversals {flips}")

"""Ankle angle (shin vs foot) over a time window of a baked performance.

Standing = 90 degrees; larger = toes pointed (plantar-flexion), smaller = toes pulled up
(dorsiflexion). A human ankle spans roughly 65-70 deg (very dorsiflexed) to ~140 deg
(fully pointed).    python blender/tools/check_ankle_angle.py baked.blend T0 T1
"""
import math
import sys

import bpy

bpy.ops.wm.open_mainfile(filepath=sys.argv[-3])
t0, t1 = float(sys.argv[-2]), float(sys.argv[-1])
arm = bpy.data.objects["Armature"]
sc = bpy.context.scene
worst = {"L": (999, 0), "R": (999, 0), "Lmax": (0, 0), "Rmax": (0, 0)}
for f in range(int(t0 * 24), int(t1 * 24) + 1):
    sc.frame_set(f)
    for s in "LR":
        shin_b = arm.pose.bones[f"LowerLeg.{s}"]
        foot_b = arm.pose.bones[f"Ankle.{s}"]
        shin = (shin_b.tail - shin_b.head).normalized()
        foot = (foot_b.tail - foot_b.head).normalized()
        ang = math.degrees(math.acos(max(-1, min(1, shin.dot(foot)))))
        if ang < worst[s][0]:
            worst[s] = (ang, f)
        if ang > worst[s + "max"][0]:
            worst[s + "max"] = (ang, f)
for s in "LR":
    print(f"foot {s}: ankle angle min {worst[s][0]:.0f} deg (frame {worst[s][1]}), max {worst[s+'max'][0]:.0f} deg (frame {worst[s+'max'][1]})")

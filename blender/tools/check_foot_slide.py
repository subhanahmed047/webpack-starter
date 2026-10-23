"""Measure foot skating in the baked showcase: while a foot is on the ground, its
world-space speed should be ~0.   python blender/tools/check_foot_slide.py output/showcase/showcase.blend
"""
import sys

import bpy

bpy.ops.wm.open_mainfile(filepath=sys.argv[-1])
arm = bpy.data.objects["Armature"]
sc = bpy.context.scene
fps = sc.render.fps
windows = {"walk (3.0-5.0s)": (3.0, 5.0), "run (23.8-25.8s)": (23.8, 25.8), "walk away (19.5-21.0s)": (19.5, 21.0)}
for label, (t0, t1) in windows.items():
    slide = []
    prev = {}
    for f in range(int(t0 * fps), int(t1 * fps) + 1):
        sc.frame_set(f)
        for side in "LR":
            pb = arm.pose.bones[f"LowerLeg.{side}"]
            tip = arm.matrix_world @ pb.tail          # ankle, driven by the IK foot target
            if side in prev and tip.z < 0.085:        # planted (ankle rest height is 0.077)
                slide.append(((tip - prev[side]).length * fps, side))
            prev[side] = tip.copy()
    if slide:
        v = sorted(s for s, _ in slide)
        print(f"{label}: ground-contact foot speed  median {v[len(v)//2]:.3f} m/s   p90 {v[int(len(v)*.9)]:.3f} m/s   ({len(v)} samples)")

# ---- contralateral arm/leg swing: each hand should move opposite to the foot on its side
import math
for label, (t0, t1) in {"walk": (3.0, 5.0), "run": (23.8, 25.8)}.items():
    import mathutils
    ser = {k: [] for k in ("footL", "handL", "footR", "handR")}
    for f in range(int(t0 * fps), int(t1 * fps) + 1):
        sc.frame_set(f)
        fwd = arm.matrix_world.to_3x3() @ mathutils.Vector((0, -1, 0))  # character's forward in world
        for side in "LR":
            foot = arm.matrix_world @ arm.pose.bones[f"LowerLeg.{side}"].tail
            hand = arm.matrix_world @ arm.pose.bones[f"LowerArm.{side}"].tail
            hip = arm.matrix_world @ arm.pose.bones["Body"].head
            ser[f"foot{side}"].append((foot - hip).dot(fwd))
            ser[f"hand{side}"].append((hand - hip).dot(fwd))
    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        return num / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    print(f"{label}: corr(left hand, left foot) = {corr(ser['handL'], ser['footL']):+.2f}   "
          f"corr(right hand, right foot) = {corr(ser['handR'], ser['footR']):+.2f}   (want strongly negative)")

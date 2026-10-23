"""Measure foot skating in the baked showcase: while a foot is on the ground, its
world-space speed should be ~0.   python blender/tools/check_foot_slide.py output/showcase/showcase.blend
"""
import sys

import bpy

bpy.ops.wm.open_mainfile(filepath=sys.argv[-1])
arm = bpy.data.objects["Armature"]
sc = bpy.context.scene
fps = sc.render.fps
windows = {
    "walk in (3.0-5.0s)": (3.0, 5.0),
    "pivot on the spot (7.8-9.3s)": (7.8, 9.3),
    "back-step to chair (9.4-10.7s)": (9.4, 10.7),
    "walk away (21.0-22.5s)": (21.0, 22.5),
    "run (25.0-27.0s)": (25.0, 27.0),
}
import json
import mathutils

prof = json.loads(arm["anim_lab_profile_json"])
H, az = prof["height"], prof["ankle_z"]
heel_m, ball_m = 0.034 * H, 0.086 * H


def contact_points(side):
    """Heel and ball of the foot: whichever touches the ground should stay put during stance."""
    pb = arm.pose.bones[f"Ankle.{side}"]
    head = arm.matrix_world @ pb.head
    toe_dir = ((arm.matrix_world @ pb.tail) - head).normalized()
    up = toe_dir.cross(mathutils.Vector((1, 0, 0)) if (arm.matrix_world.to_3x3() @ mathutils.Vector((1, 0, 0))).x > 0
                       else mathutils.Vector((-1, 0, 0)))
    up = (toe_dir.cross(arm.matrix_world.to_3x3() @ mathutils.Vector((1, 0, 0)))).normalized()
    heel = head - heel_m * toe_dir - az * up
    ball = head + ball_m * toe_dir - az * up
    return {"heel": heel, "ball": ball}


for label, (t0, t1) in windows.items():
    slide, prev = [], {}
    for f in range(int(t0 * fps), int(t1 * fps) + 1):
        sc.frame_set(f)
        for side in "LR":
            for part, c in contact_points(side).items():
                key = (side, part)
                planted = c.z < 0.012
                if key in prev and planted and prev[key][1]:
                    slide.append((c - prev[key][0]).length * fps)
                prev[key] = (c.copy(), planted)
    if slide:
        v = sorted(slide)
        print(f"{label}: sole-contact speed  median {v[len(v)//2]:.3f} m/s   p90 {v[int(len(v)*.9)]:.3f} m/s   max {v[-1]:.3f}  ({len(v)} samples)")

# ---- contralateral arm/leg swing: each hand should move opposite to the foot on its side
import math
for label, (t0, t1) in {"walk": (3.0, 5.0), "run": (25.0, 27.0)}.items():
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

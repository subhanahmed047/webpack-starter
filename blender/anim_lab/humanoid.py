"""Builds the canonical humanoid armature from a proportion profile.

Every animation in actions.py targets *this* rig (bone names are fixed), so a
different character only needs a different profile + mesh -- see swap.py.
"""
import json
import math

import bpy
from mathutils import Vector

REF_HEIGHT = 2.76  # height the animation constants in actions.py were authored for

# Bones that only exist to drive others -- skinning must ignore them.
CONTROL_BONES = ["Root", "Body", "Foot.L", "Foot.R", "PoleTarget.L", "PoleTarget.R"]


def load_profile(path):
    with open(path) as f:
        return json.load(f)


def build_armature(profile, name="Armature"):
    p, H = profile, profile["height"]
    bpy.ops.object.select_all(action="DESELECT")
    arm_data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm_data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj["anim_lab_height"] = H
    obj["anim_lab_profile_json"] = json.dumps(p)
    obj.location.y = p.get("y_offset", 0.0)
    obj["anim_lab_profile"] = p["name"]
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(n, head, tail, parent=None, connect=False):
        b = eb.new(n)
        b.head, b.tail = Vector(head), Vector(tail)
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        return b

    hz, kz, az = p["hip_z"], p["knee_z"], p["ankle_z"]
    bone("Root", (0, 0, 0), (0, 0, 0.04 * H))
    bone("Body", (0, 0, hz), (0, 0, hz + 0.05 * H), "Root")
    bone("Hips", (0, 0, hz - 0.04 * H), (0, 0, p["waist_z"]), "Body")
    bone("Abdomen", (0, 0, p["waist_z"]), (0, 0, p["chest_z"]), "Hips")
    bone("Torso", (0, 0, p["chest_z"]), (0, 0, p["neck_z"]), "Abdomen")
    bone("Neck", (0, 0, p["neck_z"]), (0, 0, p["neck_z"] + 0.05 * H), "Torso")
    bone("Head", (0, 0, p["neck_z"] + 0.05 * H), (0, 0, p["head_top"]), "Neck")

    for side, s in (("L", 1), ("R", -1)):
        sz, sx = p["shoulder_z"], p["shoulder_x"]
        bone(f"Shoulder.{side}", (s * 0.045 * H, 0, sz - 0.06 * H), (s * sx, 0, sz), "Torso")
        a1 = (s * (sx + p["arm_upper"]), 0, sz)
        a2 = (s * (sx + p["arm_upper"] + p["arm_lower"]), 0, sz)
        a3 = (s * (sx + p["arm_upper"] + p["arm_lower"] + p["hand"]), 0, sz)
        bone(f"UpperArm.{side}", (s * sx, 0, sz), a1, f"Shoulder.{side}", True)
        bone(f"LowerArm.{side}", a1, a2, f"UpperArm.{side}", True)
        bone(f"Fist.{side}", a2, a3, f"LowerArm.{side}", True)

        hx = s * p["hip_x"]
        knee_y = -0.02 * H  # slight forward knee bend so IK knows which way to fold
        bone(f"UpperLeg.{side}", (hx, 0, hz), (hx, knee_y, kz), "Body")
        bone(f"LowerLeg.{side}", (hx, knee_y, kz), (hx, 0, az), f"UpperLeg.{side}", True)
        bone(f"Foot.{side}", (hx, 0, az), (hx, -0.08 * H, az), "Root")
        # Ankle: the deforming foot. It hangs off the shin so the sole can stay flat on the
        # ground (and roll heel -> toe) while the leg angles above it.
        bone(f"Ankle.{side}", (hx, 0, az), (hx, -0.08 * H, az), f"LowerLeg.{side}")
        bone(f"PoleTarget.{side}", (hx, -0.35 * H, kz), (hx, -0.35 * H, kz + 0.1 * H), "Root")

    for n in CONTROL_BONES:
        eb[n].use_deform = False
    bpy.ops.object.mode_set(mode="POSE")
    for side in "LR":
        c = obj.pose.bones[f"LowerLeg.{side}"].constraints.new("IK")
        c.target, c.subtarget = obj, f"Foot.{side}"
        c.pole_target, c.pole_subtarget = obj, f"PoleTarget.{side}"
        c.pole_angle = -math.pi / 2
        c.chain_count = 2
        c.use_stretch = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_data.display_type = "STICK"
    return obj


def add_chain(arm_obj, prefix, points, parent):
    """Add a connected bone chain `prefix.1..n` through `points` (armature-local coords),
    parented to `parent`. Used for secondary motion such as a ponytail."""
    bpy.ops.object.select_all(action="DESELECT")
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_obj.data.edit_bones
    prev = eb[parent]
    for i in range(len(points) - 1):
        b = eb.new(f"{prefix}.{i + 1}")
        b.head, b.tail = Vector(points[i]), Vector(points[i + 1])
        b.parent = prev
        b.use_connect = i > 0
        b.use_deform = True
        prev = b
    bpy.ops.object.mode_set(mode="OBJECT")

"""Pose + keyframe helpers for the Quaternius "CharacterArmature" rig.

Rig facts (the canonical armature built by humanoid.py):
  * The character faces -Y, +X is its left, +Z is up.
  * Legs are driven by IK: move the `Foot.L/R` bones (children of `Root`) and the
    knees follow `PoleTarget.L/R`.
  * `Body` is the hip/pelvis controller (spine, arms and upper legs hang off it).

All rotations/translations below are expressed in *armature axes* relative to the
rest pose, so "rot Z=-30" means the same thing for every bone regardless of how
Blender happened to orient that bone's local axes.
"""
import json
import math

import bpy
from .humanoid import REF_HEIGHT
from mathutils import Euler, Matrix, Quaternion, Vector

FPS = 24
ARMATURE_NAME = "Armature"


class Rig:
    def __init__(self, armature_obj):
        self.obj = armature_obj
        # Animation constants are authored for REF_HEIGHT; scale them to this character.
        self.unit = armature_obj.get("anim_lab_height", REF_HEIGHT) / REF_HEIGHT
        self.profile = json.loads(armature_obj.get("anim_lab_profile_json", "{}"))
        # Seat height follows this character's knees, so any leg length sits correctly.
        self.seat_z = self.profile.get("knee_z", 0.58 * self.unit) * 0.97
        self.bones = armature_obj.pose.bones
        # Rest orientation (3x3) of every bone in armature space.
        self._rest3 = {
            b.name: b.matrix_local.to_3x3().normalized() for b in armature_obj.data.bones
        }
        self.action = None

    # -- pose ---------------------------------------------------------------
    def reset(self):
        for pb in self.bones:
            pb.rotation_mode = "QUATERNION"
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.scale = (1, 1, 1)

    def rot(self, bone, x=0.0, y=0.0, z=0.0, order="XYZ"):
        """Rotate `bone` by Euler degrees about armature axes (applied in `order`)."""
        r3 = self._rest3[bone]
        r = Euler((math.radians(x), math.radians(y), math.radians(z)), order).to_matrix()
        self.bones[bone].rotation_quaternion = (r3.inverted() @ r @ r3).to_quaternion()

    def rot_matrix(self, bone, R):
        """Apply a 3x3 rotation given in armature axes (same convention as `rot`)."""
        r3 = self._rest3[bone]
        self.bones[bone].rotation_quaternion = (r3.inverted() @ R @ r3).to_quaternion()

    def aim_chain(self, upper, lower, upper_dir, lower_dir, twist=0.0):
        """Point a two-bone limb: `upper` toward `upper_dir`, `lower` toward `lower_dir`.

        Directions are vectors in armature axes (they need not be normalised). The rest
        direction of each bone is taken from the armature, so this is rig-independent.
        """
        def rest_dir(name):
            b = self.obj.data.bones[name]
            return (b.tail_local - b.head_local).normalized()

        ud, ld = Vector(upper_dir).normalized(), Vector(lower_dir).normalized()
        r_up = rest_dir(upper).rotation_difference(ud).to_matrix()
        if twist:
            r_up = Quaternion(ud, math.radians(twist)).to_matrix() @ r_up
        r_lo_abs = rest_dir(lower).rotation_difference(ld).to_matrix()
        self.rot_matrix(upper, r_up)
        self.rot_matrix(lower, r_up.inverted() @ r_lo_abs)  # child rotation composes after parent

    def move(self, bone, x=0.0, y=0.0, z=0.0):
        """Translate `bone` by (x, y, z) armature units from its rest position."""
        r3 = self._rest3[bone]
        self.bones[bone].location = r3.inverted() @ (Vector((x, y, z)) * self.unit)

    # -- keyframing ---------------------------------------------------------
    def begin_action(self, name):
        act = bpy.data.actions.new(name)
        act.use_fake_user = True
        if not self.obj.animation_data:
            self.obj.animation_data_create()
        self.obj.animation_data.action = act
        self.action = act
        self.reset()
        return act

    def key(self, frame, interpolation="BEZIER"):
        for pb in self.bones:
            pb.keyframe_insert("location", frame=frame, group=pb.name)
            pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
        self._set_interp(frame, interpolation)

    def _set_interp(self, frame, interpolation):
        for fc in iter_fcurves(self.action):
            for kp in fc.keyframe_points:
                if kp.co.x == frame:
                    kp.interpolation = interpolation

    def end_action(self, loop=False):
        act = self.action
        frames = [kp.co.x for fc in iter_fcurves(act) for kp in fc.keyframe_points]
        act.use_frame_range = True
        act.frame_start, act.frame_end = min(frames), max(frames)
        act.use_cyclic = loop
        self.obj.animation_data.action = None
        self.reset()
        return act


def iter_fcurves(action):
    """F-curves of an action under Blender 4.4+/5.x layered actions."""
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield from bag.fcurves


def find_rig():
    obj = bpy.data.objects.get(ARMATURE_NAME)
    if obj is None:
        raise RuntimeError(f"No armature named {ARMATURE_NAME!r} in the scene")
    return Rig(obj)


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t

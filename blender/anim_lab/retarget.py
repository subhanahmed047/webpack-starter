"""Retarget professional keyframe animation onto the canonical rig.

Source: Quaternius' Universal Animation Library (CC0) -- hand-keyed walk, jog, sprint,
idle, sit, stand and jump clips on a UE-style humanoid skeleton (pelvis, spine_01..03, ...).

How motion is transferred (it must survive different proportions and rest poses):
  * pelvis / spine / neck / head: the *absolute rotation relative to rest* is copied, so
    torso twist, lean and head motion carry over exactly. Pelvis translation is scaled by
    the leg-length ratio.
  * arms: the *directions* of upper arm and forearm are copied (twist is dropped), so
    different arm lengths and T-pose/A-pose rests do not matter.
  * legs: the source ankle's offset from its rest position (scaled by leg length) drives the
    IK foot target, and the foot's pitch / yaw drive the Ankle bone. Knees follow poles.
  * fingers / face: not transferred.

Source skeleton sampling uses plain forward kinematics on the imported action curves, so no
scene evaluation is needed.
"""
import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

from .rig import iter_fcurves

# bones whose pose we read from the source
NEEDED = ["pelvis", "spine_01", "spine_03", "neck_01", "Head", "clavicle_l", "clavicle_r",
          "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r", "hand_l", "hand_r",
          "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r", "ball_l", "ball_r"]

HEAD_STABILISE = 0.6   # fraction of the head's forward/back pitch cancelled (keeps the gaze level)

# source clip -> (our clip name, loops?)
CLIPS = {
    "Walk_Loop": ("mc_walk", True),
    "Jog_Fwd_Loop": ("mc_jog", True),
    "Sprint_Loop": ("mc_sprint", True),
    "Idle_Loop": ("mc_idle", True),
    "Sitting_Enter": ("mc_sit_down", False),
    "Sitting_Idle_Loop": ("mc_sit_idle", True),
    "Sitting_Exit": ("mc_stand_up", False),
    "Jump_Start": ("mc_jump_start", False),
    "Jump_Loop": ("mc_jump_loop", True),
    "Jump_Land": ("mc_jump_land", False),
}


class _Frame(dict):
    """bone -> (head position, 3x3 orientation) in source armature space."""


def load_source(path, validate=False):
    """Import the glTF, sample every clip with FK, clean everything up.

    Returns (rest, clips): rest[bone] = (head, rot3); clips[src_name] = [ _Frame, ... ].
    With `validate`, checks the FK sampler against Blender's own pose evaluation.
    """
    before_objs, before_actions = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=path)
    new_objs = [o for o in bpy.data.objects if o not in before_objs]
    arm = next(o for o in new_objs if o.type == "ARMATURE")
    data = arm.data
    order, seen = [], set()

    def visit(b):
        if b.name in seen:
            return
        seen.add(b.name)
        order.append(b)
        for c in b.children:
            visit(c)

    for b in data.bones:
        if b.parent is None:
            visit(b)
    rest_rel = {b.name: (b.parent.matrix_local.inverted() @ b.matrix_local) if b.parent else b.matrix_local.copy()
                for b in data.bones}
    rest = {n: (data.bones[n].matrix_local.translation.copy(),
                data.bones[n].matrix_local.to_3x3().normalized()) for n in NEEDED}

    clips = {}
    for src in CLIPS:
        action = bpy.data.actions.get(src)
        if action is None:
            continue
        curves = {}
        for fc in iter_fcurves(action):
            if fc.data_path.startswith("pose.bones["):
                bone = fc.data_path.split('"')[1]
                prop = fc.data_path.rsplit(".", 1)[1]
                curves.setdefault((bone, prop), {})[fc.array_index] = fc
        f0, f1 = int(action.frame_range[0]), int(action.frame_range[1])
        frames = []
        for f in range(f0, f1 + 1):
            pose = {}
            for b in order:
                loc = curves.get((b.name, "location"))
                rot = curves.get((b.name, "rotation_quaternion"))
                sca = curves.get((b.name, "scale"))
                t = Vector([loc[i].evaluate(f) for i in range(3)]) if loc else Vector()
                q = Quaternion([rot[i].evaluate(f) for i in range(4)]) if rot else Quaternion()
                s = Vector([sca[i].evaluate(f) for i in range(3)]) if sca else Vector((1, 1, 1))
                basis = Matrix.LocRotScale(t, q, s)
                parent = pose[b.parent.name] if b.parent else Matrix.Identity(4)
                pose[b.name] = parent @ rest_rel[b.name] @ basis
            fr = _Frame()
            for n in NEEDED:
                m = pose[n]
                fr[n] = (m.translation.copy(), m.to_3x3().normalized())
            frames.append(fr)
        clips[src] = frames
        if validate:  # compare against the depsgraph for a few frames
            arm.animation_data_create()
            arm.animation_data.action = action
            worst = 0.0
            for f in (f0 + 3, (f0 + f1) // 2, f1 - 2):
                bpy.context.scene.frame_set(f)
                for n in ("foot_l", "hand_r", "Head", "ball_r"):
                    ev = arm.pose.bones[n].head
                    worst = max(worst, (ev - frames[f - f0][n][0]).length)
            print(f"  FK check {src}: worst position error {worst:.6f} m")
            arm.animation_data.action = None

    for o in new_objs:
        bpy.data.objects.remove(o)
    for a in set(bpy.data.actions) - before_actions:
        bpy.data.actions.remove(a)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    return rest, clips


class Retargeter:
    def __init__(self, rig, rest):
        self.r, self.rest = rig, rest
        self.foot_rel = {"L": None, "R": None}
        p = rig.profile
        src_leg = rest["thigh_l"][0].z - rest["foot_l"][0].z
        self.scale = (p["hip_z"] - p["ankle_z"]) / src_leg           # leg-length ratio
        bones = rig.obj.data.bones
        self.hip_rest = {s: bones[f"UpperLeg.{s}"].head_local.copy() for s in "LR"}
        self.ankle_rest = {s: bones[f"Foot.{s}"].head_local.copy() for s in "LR"}
        self.pole_rest = {s: bones[f"PoleTarget.{s}"].head_local.copy() for s in "LR"}
        self.height = p["height"]
        # the source foot bone points down at rest (ankle above the ground); flat = rest
        self.rest_pitch, self.rest_yaw = {}, {}
        for s in "lr":
            d = rest[f"ball_{s}"][0] - rest[f"foot_{s}"][0]
            self.rest_pitch[s] = math.degrees(math.asin(d.z / d.length))
            self.rest_yaw[s] = math.degrees(math.atan2(d.x, -d.y))

    def stabilise(self, D, k):
        """Reduce the forward/back pitch of a head rotation by fraction `k`.

        People keep their gaze roughly level while the trunk flexes (sitting down, running
        with a lean); copying the head's absolute orientation instead makes it bow with the
        trunk. Roll and yaw are untouched."""
        up = D @ Vector((0.0, 0.0, 1.0))
        pitch = math.asin(max(-1.0, min(1.0, -up.y)))     # forward lean positive
        return Matrix.Rotation(-k * pitch, 3, "X") @ D

    def delta(self, fd, bone):
        """Absolute rotation of a source bone relative to its rest."""
        return fd[bone][1] @ self.rest[bone][1].inverted()

    def pose(self, fd):
        """Set the rig (no keying) to the retargeted source frame `fd`."""
        r, S = self.r, self.scale
        r.reset()
        d_pelvis = (fd["pelvis"][0] - self.rest["pelvis"][0]) * S
        r.move_m("Body", d_pelvis)
        r.set_abs_delta("Body", self.delta(fd, "pelvis"))
        r.set_abs_delta("Hips", self.delta(fd, "pelvis"))
        r.set_abs_delta("Abdomen", self.delta(fd, "spine_01"))
        r.set_abs_delta("Torso", self.delta(fd, "spine_03"))
        r.set_abs_delta("Neck", self.delta(fd, "neck_01"))
        r.set_abs_delta("Head", self.stabilise(self.delta(fd, "Head"), HEAD_STABILISE))
        for s, side in (("l", "L"), ("r", "R")):
            r.set_abs_delta(f"Shoulder.{side}", self.delta(fd, f"clavicle_{s}"))
            up = fd[f"lowerarm_{s}"][0] - fd[f"upperarm_{s}"][0]
            lo = fd[f"hand_{s}"][0] - fd[f"lowerarm_{s}"][0]
            r.aim_chain(f"UpperArm.{side}", f"LowerArm.{side}", up, lo, absolute=True)
            # legs: IK targets, pole, ankle orientation
            d_foot = (fd[f"foot_{s}"][0] - self.rest[f"foot_{s}"][0]) * S
            r.move_m(f"Foot.{side}", d_foot)
            hip = self.hip_rest[side] + d_pelvis
            ankle = self.ankle_rest[side] + d_foot
            pole = (hip + ankle) / 2 + Vector((0, -0.35 * self.height, 0))
            pole.x = hip.x
            r.move_m(f"PoleTarget.{side}", pole - self.pole_rest[side])
            toe = fd[f"ball_{s}"][0] - fd[f"foot_{s}"][0]
            pitch = math.degrees(math.asin(toe.z / toe.length)) - self.rest_pitch[s]
            # Yaw from the foot's *lateral* axis: it stays horizontal however steeply the foot
            # pitches. (Using the toe direction flips +/-180 deg when the foot points straight down.)
            lat = self.delta(fd, f"foot_{s}") @ Vector((1.0, 0.0, 0.0))
            yaw = math.degrees(math.atan2(lat.y, lat.x))
            r.foot_pose[side] = (pitch, yaw)
            # ankle rotation relative to the shin (rest-referenced), for use when the foot is airborne
            self.foot_rel[side] = self.delta(fd, f"calf_{s}").inverted() @ self.delta(fd, f"foot_{s}")


def _smooth_meta(meta, loop):
    """Binomial 5-tap smoothing of the foot pitch/yaw series. The source's ankle animation has
    small high-frequency flicks (especially at toe-off) that read as wobble on a different
    character. Loops wrap around; one-shots clamp at their ends."""
    n = len(meta)
    taps = ((-2, 1), (-1, 4), (0, 6), (1, 4), (2, 1))
    out = []
    for i in range(n):
        acc = {s: [0.0, 0.0] for s in "LR"}
        for off, w in taps:
            j = (i + off) % n if loop else min(max(i + off, 0), n - 1)
            for s in "LR":
                for k in (0, 1):
                    acc[s][k] += w * meta[j][s][k] / 16.0
        out.append({s: tuple(acc[s]) for s in "LR"})
    return out


def add_clips(rig, path, wanted=None, validate=False):
    """Retarget the library onto `rig`; returns {our_name: Action} (keyed, looping flagged)."""
    rest, clips = load_source(path, validate)
    rt = Retargeter(rig, rest)
    out = {}
    for src, frames in clips.items():
        name, loop = CLIPS[src]
        if wanted and name not in wanted:
            continue
        rig.begin_action(name)
        meta, rel = [], []
        for f, fd in enumerate(frames):
            rt.pose(fd)
            meta.append(dict(rig.foot_pose))   # (pitch, yaw) per foot; the sequencer re-derives ankles from these
            rel.append(dict(rt.foot_rel))
            rig.key(f)          # settles the Ankle bones from foot_pose
        out[name] = rig.end_action(loop=loop)
        rig.foot_meta[name] = _smooth_meta(meta, loop)
        rig.foot_rel_meta[name] = rel
    return out

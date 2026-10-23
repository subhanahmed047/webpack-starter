"""Turns separate animations into one continuous, natural-looking performance.

Three ideas make the transitions believable:

1. **One locomotion model.** Walking, running and standing are not separate clips that get
   cross-faded. A single gait is driven by a speed curve: stride length, cadence, bounce,
   lean and arm style are all functions of speed, and the character's world position is the
   integral of that speed -- so feet stay planted and "walk -> run -> slow -> walk" is a
   smooth ramp rather than a pop. Acceleration leans the body; turning leads with the head.
2. **Posture clips** (sit_down, stand_up, jump, wave) start and end in poses that match
   their neighbours (standing / seated), and are cross-faded per bone (lerp + slerp).
3. **Sensible choreography.** To sit, the character slows, curves around so the chair is
   behind them, stops, then sits -- the way a person would.

Poses are dicts {bone: (location, quaternion)}; everything is baked into one Action.
"""
import math

import bpy
from mathutils import Vector

from . import actions as A
from .rig import iter_fcurves, lerp, smoothstep

XFADE = 0.30  # seconds of cross-fade between segments


# ---------------------------------------------------------------- pose maths
def capture(r):
    return {pb.name: (pb.location.copy(), pb.rotation_quaternion.copy()) for pb in r.bones}


def apply(r, pose):
    for n, (loc, q) in pose.items():
        pb = r.bones[n]
        pb.location, pb.rotation_quaternion = loc, q


def blend(a, b, w):
    if w <= 0.0:
        return a
    if w >= 1.0:
        return b
    out = {}
    for n, (la, qa) in a.items():
        lb, qb = b[n]
        if qa.dot(qb) < 0:  # shortest-path slerp
            qb = -qb
        out[n] = (la.lerp(lb, w), qa.slerp(qb, w))
    return out


class ClipSampler:
    """Evaluates a keyed Action at any frame into a pose dict."""

    def __init__(self, r, action):
        self.r, self.action = r, action
        self.curves = {}
        for fc in iter_fcurves(action):
            if not fc.data_path.startswith("pose.bones["):
                continue
            bone = fc.data_path.split('"')[1]
            prop = fc.data_path.rsplit(".", 1)[1]
            self.curves.setdefault((bone, prop), {})[fc.array_index] = fc
        self.f0, self.f1 = action.frame_start, action.frame_end

    def at(self, frame):
        frame = min(max(frame, self.f0), self.f1)
        out = {}
        for pb in self.r.bones:
            loc = self.curves[(pb.name, "location")]
            rot = self.curves[(pb.name, "rotation_quaternion")]
            out[pb.name] = (Vector([loc[i].evaluate(frame) for i in range(3)]),
                            _quat([rot[i].evaluate(frame) for i in range(4)]))
        return out


def _quat(v):
    from mathutils import Quaternion
    return Quaternion(v)


# ------------------------------------------------------------- profiles
def curve(keys, t):
    """Smoothly interpolate [(time, value), ...]."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return lerp(v0, v1, smoothstep((t - t0) / (t1 - t0)))
    return keys[-1][1]


def gait_params(speed, accel):
    """Everything about how the body moves, derived from speed (reference units / s)."""
    run = smoothstep((speed - 1.6) / 1.4)
    h = min(0.58, 0.10 + 0.17 * speed)                      # half stride
    return dict(
        h=h,
        lift=lerp(0.20, 0.55, run),
        bob=lerp(0.02, 0.07, run),
        lean=lerp(3, 16, run) + max(-8.0, min(8.0, accel * 4.0)),  # lean into acceleration
        arm_swing=lerp(24, 48, run),
        # lower the pelvis just enough for the stride to reach the ground (h^2 / 2L, L = leg length)
        drop=lerp(0.02 + 0.02 + 0.5 * h * h / 1.33, 0.27, run),
        runness=run,
        rate=speed / (4 * h),                              # cycles per second
    )


class Performance:
    """Scripted timeline -> baked Action + world motion + camera track."""

    def __init__(self, r, clips):
        self.r = r
        self.clips = {n: ClipSampler(r, a) for n, a in clips.items()}
        self.speed_keys = [(0.0, 0.0)]
        self.yaw_keys = [(0.0, 0.0)]  # heading in radians
        self.segments = []            # (t_start, kind, name)
        self.fps = 24

    # -- authoring -------------------------------------------------------
    def speed(self, *keys):
        self.speed_keys = list(keys)

    def heading(self, *keys):
        self.yaw_keys = list(keys)

    def seg(self, t, kind, name=None):
        self.segments.append((t, kind, name))

    def eff_speed(self, t):
        """Gait speed: real travel speed, or stepping speed while pivoting on the spot."""
        yaw_rate = abs(curve(self.yaw_keys, t + 0.05) - curve(self.yaw_keys, t)) / 0.05
        return max(abs(curve(self.speed_keys, t)), 0.35 * yaw_rate)

    # -- evaluation ------------------------------------------------------
    def _segment_pose(self, idx, t, state):
        t0, kind, name = self.segments[idx]
        r = self.r
        if kind == "loco":
            sp = self.eff_speed(t)
            accel = (self.eff_speed(t + 0.05) - sp) / 0.05
            gp = gait_params(sp, accel)
            yaw_rate = (curve(self.yaw_keys, t + 0.05) - curve(self.yaw_keys, t)) / 0.05
            A.idle_pose(r, t)
            idle = capture(r)
            if sp < 0.02:
                return idle
            A.gait_pose(r, state["phase"], gp["h"], gp["lift"], gp["bob"], gp["lean"],
                        gp["arm_swing"], gp["drop"], gp["runness"],
                        head_yaw=max(-22, min(22, math.degrees(yaw_rate) * 0.35)))
            return blend(idle, capture(r), smoothstep(sp / 0.45))
        if kind == "sitidle":
            A.sit_idle_pose(r, t)
            return capture(r)
        clip = self.clips[name]
        return clip.at((t - t0) * self.fps)

    def pose_at(self, t, state):
        i = max(k for k, s in enumerate(self.segments) if s[0] <= t) if t >= self.segments[0][0] else 0
        pose = self._segment_pose(i, t, state)
        # cross-fade with the neighbouring segment near each boundary
        if i + 1 < len(self.segments):
            tb = self.segments[i + 1][0]
            if t > tb - XFADE / 2:
                pose = blend(pose, self._segment_pose(i + 1, t, state), smoothstep((t - (tb - XFADE / 2)) / XFADE))
        if i > 0:
            tb = self.segments[i][0]
            if t < tb + XFADE / 2:
                pose = blend(self._segment_pose(i - 1, t, state), pose, smoothstep((t - (tb - XFADE / 2)) / XFADE))
        return pose

    def bake(self, duration, name="performance"):
        """Key every frame; returns per-frame (x, y, yaw) world transforms.

        Pass 1 poses the body and records where the head goes (plus world motion). Hair
        springs are then simulated from the head's acceleration. Pass 2 keys everything.
        """
        r, fps = self.r, self.fps
        n = int(duration * fps)
        arm_obj = r.obj
        base_loc = Vector(arm_obj.location)
        track, poses, head, state = [], [], [], {"phase": 0.0}
        x = y = 0.0
        for f in range(n + 1):  # ---- pass 1
            t = f / fps
            sp = curve(self.speed_keys, t)  # real travel speed
            se = self.eff_speed(t)
            gp = gait_params(se, (self.eff_speed(t + 0.05) - se) / 0.05)
            yaw = curve(self.yaw_keys, t)
            if f:  # integrate: phase advance and world position follow the same speed
                direction = -1.0 if sp < -0.02 else 1.0  # negative speed = walking backwards
                state["phase"] = (state["phase"] + direction * gp["rate"] / fps) % 1.0
                x += math.sin(yaw) * sp * r.unit / fps
                y += -math.cos(yaw) * sp * r.unit / fps
            pose = self.pose_at(t, state)
            poses.append(pose)
            track.append((x, y, yaw))
            arm_obj.location = (base_loc.x + x, base_loc.y + y, 0.0)
            arm_obj.rotation_euler = (0, 0, yaw)
            apply(r, pose)
            r.sync()
            head.append(arm_obj.matrix_world @ r.bones["Head"].head)
        hair = self._simulate_hair(head, track)

        r.begin_action(name)  # ---- pass 2
        for f in range(n + 1):
            apply(r, poses[f])
            for bone, (ax, ay) in hair[f].items():
                r.rot(bone, x=math.degrees(ax), y=math.degrees(ay))
            r.key(f)
            arm_obj.location = (base_loc.x + track[f][0], base_loc.y + track[f][1], 0.0)
            arm_obj.rotation_euler = (0, 0, track[f][2])
            arm_obj.keyframe_insert("location", frame=f)
            arm_obj.keyframe_insert("rotation_euler", frame=f)
        r.end_action()
        r.obj.animation_data.action = r.action
        return track

    def _simulate_hair(self, head, track):
        """Damped springs (one per ponytail bone) driven by the head's acceleration in the
        character's own frame. Forward acceleration swings the tip back; sideways
        acceleration swings it out. Returns [{bone: (pitch, roll)}] per frame."""
        r, dt = self.r, 1.0 / self.fps
        bones = [f"Ponytail.{i}" for i in range(1, 9) if f"Ponytail.{i}" in r.bones]
        if not bones:
            return [{} for _ in head]
        m = len(head)
        acc = []
        for f in range(m):
            a, b, c = head[max(f - 1, 0)], head[f], head[min(f + 1, m - 1)]
            acc.append((a - 2 * b + c) / (dt * dt))
        # light smoothing so single-frame spikes (foot strikes) don't whip the hair
        acc = [sum((acc[min(max(f + k, 0), m - 1)] for k in (-1, 0, 1)), Vector()) / 3 for f in range(m)]
        state = {b: [0.0, 0.0, 0.0, 0.0] for b in bones}  # pitch, pitch', roll, roll'
        out = []
        for f in range(m):
            yaw = track[f][2]
            fwd = Vector((math.sin(yaw), -math.cos(yaw), 0))
            left = Vector((math.cos(yaw), math.sin(yaw), 0))
            a_f, a_l = acc[f].dot(fwd), acc[f].dot(left)
            frame = {}
            for i, b in enumerate(bones):
                w = 2 * math.pi * (3.0 - 0.5 * i)  # natural frequency, lower toward the tip
                z = 0.30                           # damping ratio
                k = 0.04                           # response to acceleration (rad per m/s^2)
                st = state[b]
                for _ in range(4):                 # sub-steps keep the integration stable
                    h = dt / 4
                    st[1] += h * (w * w * (k * a_f - st[0]) - 2 * z * w * st[1])
                    st[0] += h * st[1]
                    st[3] += h * (w * w * (k * a_l - st[2]) - 2 * z * w * st[3])
                    st[2] += h * st[3]
                st[0] = max(-0.5, min(0.5, st[0]))
                st[2] = max(-0.5, min(0.5, st[2]))
                frame[b] = (st[0], st[2])
            out.append(frame)
        return out

"""Turns separate animations into one continuous, natural-looking performance.

What makes the transitions believable (see docs/MOTION_NOTES.md for the research):

1. **One locomotion model, planned in world space.** Walking, running, pivoting and
   backing up are all the same gait, driven by a speed curve and a heading curve. Cadence,
   stride, duty factor (double support when walking, a flight phase when running), lean and
   arm style are functions of speed. The character's path is the integral of that motion,
   and every foot is *latched* at a world position for its whole stance -- so feet never
   skate, including during turns, starts, stops and backward steps. Swing feet are
   interpolated from lift-off to a predicted landing spot.
2. **Posture clips** (sit_down, stand_up, jump, wave) start and end in poses that match
   their neighbours and are cross-faded per bone (lerp + slerp).
3. **Anticipation and lead.** Lean builds *before* the speed does, the head turns ahead of
   the body, arms lag the legs, and the gait always finishes its step into double support
   before the character settles into idle.
4. **Secondary motion.** Ponytail springs are simulated from the head's acceleration.

Poses are dicts {bone: (location, quaternion)}; everything is baked into one Action.
"""
import math
from types import SimpleNamespace

from mathutils import Euler, Quaternion, Vector

from . import actions as A
from .rig import iter_fcurves, lerp, smoothstep

XFADE = 0.30  # seconds of cross-fade between segments
FEET_BONES = ["Foot.L", "Foot.R", "PoleTarget.L", "PoleTarget.R", "Ankle.L", "Ankle.R"]


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
                            Quaternion([rot[i].evaluate(frame) for i in range(4)]))
        return out


# ------------------------------------------------------------- profiles
def curve(keys, t):
    """Smoothly interpolate [(time, value), ...]."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return lerp(v0, v1, smoothstep((t - t0) / (t1 - t0)))
    return keys[-1][1]


def wrap(a):
    """Wrap an angle in radians to [-pi, pi]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def gait_params(speed, accel):
    """Everything about how the body moves, derived from speed (reference units / s).

    Cadence rises with speed (~1.0 cycle/s walking, ~1.45 running); stride follows from
    cadence and duty factor so that a planted foot's backward sweep matches ground speed.
    """
    run = smoothstep((speed - 1.6) / 1.4)
    f = 0.80 + 0.20 * speed                    # gait cycles per second (two steps each)
    duty = lerp(0.60, 0.40, run)               # fraction of the cycle a foot is on the ground
    h = min(0.62, speed * duty / (2 * f))      # half stride
    return dict(
        h=h, f=f, duty=duty,
        lift=lerp(0.20, 0.55, run),
        bob=lerp(0.02, 0.07, run),
        lean=lerp(3, 16, run) + max(-8.0, min(8.0, accel * 4.0)),  # lean into acceleration
        arm_swing=lerp(24, 48, run),
        drop=lerp(0.05, 0.24, run),
        runness=run,
    )


# --------------------------------------------------------------- foot planner
def plan_feet(kin, hip_x):
    """World-latched feet. Returns {side: [(forward, lift, side_offset, yaw_deg), ...]} per
    frame, in the character's own frame (reference units), ready for `actions.gait_pose`.

    A foot on the ground stays exactly where it landed (so it cannot skate, whatever the
    body does). When it lifts off, its landing spot is predicted from where the body will be
    at the moment of touchdown; the swing interpolates there with a lift arc.
    """
    n = len(kin.sp)
    out = {"L": [None] * n, "R": [None] * n}
    for side, s, offset in (("L", 1, 0.0), ("R", -1, 0.5)):
        width = s * (hip_x + 0.02)
        stance = [((kin.phase[f] + offset) % 1.0) < kin.gp[f]["duty"] for f in range(n)]

        def world(f, fwd, left):
            yaw = kin.yaw[f]
            px, py = kin.pos[f]
            return (px + math.sin(yaw) * fwd + math.cos(yaw) * left,
                    py - math.cos(yaw) * fwd + math.sin(yaw) * left)

        plant, plant_yaw = world(0, 0.0, width), kin.yaw[0]
        cur, cur_yaw, swing, prev_stance = plant, plant_yaw, None, True
        for f in range(n):
            lift = 0.0
            if stance[f]:
                if not prev_stance:            # touchdown: latch where the foot really is
                    plant, plant_yaw = cur, cur_yaw
                cur, cur_yaw = plant, plant_yaw
            else:
                if prev_stance:                # lift-off: predict the landing spot
                    f_td = next((g for g in range(f + 1, n) if stance[g]), n - 1)
                    gp, sp = kin.gp[f_td], kin.sp[f_td]
                    h_td = min(0.62, abs(sp) * gp["duty"] / (2 * gp["f"]))   # 0 when not travelling
                    step = (-1.0 if sp < -0.02 else 1.0) * h_td
                    swing = (f, f_td, plant, plant_yaw, world(f_td, step, width), kin.yaw[f_td], gp["lift"])
                f0, f1, p0, y0, p1, y1, peak = swing
                tau = min(1.0, (f - f0 + 1) / max(f1 - f0, 1))
                u = smoothstep(tau)
                cur = (lerp(p0[0], p1[0], u), lerp(p0[1], p1[1], u))
                cur_yaw = y0 + wrap(y1 - y0) * u
                lift = peak * math.sin(math.pi * tau)
            prev_stance = stance[f]
            yaw = kin.yaw[f]
            rx, ry = cur[0] - kin.pos[f][0], cur[1] - kin.pos[f][1]
            fwd = rx * math.sin(yaw) - ry * math.cos(yaw)
            left = rx * math.cos(yaw) + ry * math.sin(yaw)
            out[side][f] = (fwd, lift, left - width, math.degrees(wrap(cur_yaw - yaw)))
    return out


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
        return max(abs(curve(self.speed_keys, t)), 0.55 * yaw_rate)

    # -- kinematics ------------------------------------------------------
    def _kinematics(self, n):
        """Speed, heading, gait clock and path for every frame."""
        r, fps = self.r, self.fps
        k = SimpleNamespace(sp=[], se=[], yaw=[], phase=[], pos=[], gp=[])
        phase = 0.55                  # start inside double support: both feet planted
        x = y = 0.0
        for f in range(n + 1):
            t = f / fps
            sp = curve(self.speed_keys, t)                 # signed travel speed
            se = self.eff_speed(t)
            accel = 0.5 * ((self.eff_speed(t + 0.05) - se) / 0.05 + (self.eff_speed(t + 0.35) - se) / 0.35)
            gp = gait_params(se, accel)                    # lean starts *before* the speed change
            yaw = curve(self.yaw_keys, t)
            if f:
                if se > 0.02:
                    phase = (phase + (-1.0 if sp < -0.02 else 1.0) * gp["f"] / fps) % 1.0
                elif not (0.5 <= phase < 0.6):             # finish the step into double support, then hold
                    phase = (phase + 0.9 / fps) % 1.0
                x += math.sin(yaw) * sp / fps
                y += -math.cos(yaw) * sp / fps
            k.sp.append(sp)
            k.se.append(se)
            k.yaw.append(yaw)
            k.phase.append(phase)
            k.pos.append((x, y))
            k.gp.append(gp)
        return k

    # -- evaluation ------------------------------------------------------
    def _segment_pose(self, idx, t, state):
        t0, kind, name = self.segments[idx]
        r = self.r
        if kind == "loco":
            f, kin = state["f"], state["kin"]
            gp, se = kin.gp[f], kin.se[f]
            plan = {s: state["plan"][s][f] for s in "LR"}
            A.idle_pose(r, t)
            idle = capture(r)
            # the head turns ahead of the body (looks where it is going)
            lead = curve(self.yaw_keys, t + 0.35) - curve(self.yaw_keys, t)
            A.gait_pose(r, kin.phase[f], gp["h"], gp["lift"], gp["bob"], gp["lean"], gp["arm_swing"],
                        gp["drop"], gp["runness"], head_yaw=max(-35, min(35, math.degrees(lead) * 0.6)),
                        duty=gp["duty"], plan=plan)
            r.settle_feet()
            gait = capture(r)
            pose = blend(idle, gait, smoothstep(se / 0.45))
            for b in FEET_BONES:           # planted feet are never blended: that would make them slide
                pose[b] = gait[b]
            return pose
        if kind == "sitidle":
            A.sit_idle_pose(r, t)
            r.settle_feet()
            return capture(r)
        return self.clips[name].at((t - t0) * self.fps)

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
        """Key every frame; returns per-frame (x, y, yaw) world transforms in metres.

        Pass 1 poses the body and records where the head goes. Hair springs are then
        simulated from the head's acceleration. Pass 2 keys everything.
        """
        r, fps = self.r, self.fps
        n = int(duration * fps)
        arm_obj = r.obj
        base_loc = Vector(arm_obj.location)
        kin = self._kinematics(n)
        plan = plan_feet(kin, r.hip_x)
        track = [(x * r.unit, y * r.unit, yaw) for (x, y), yaw in zip(kin.pos, kin.yaw)]
        poses, head = [], []
        for f in range(n + 1):  # ---- pass 1
            t = f / fps
            pose = self.pose_at(t, {"f": f, "kin": kin, "plan": plan})
            poses.append(pose)
            arm_obj.location = (base_loc.x + track[f][0], base_loc.y + track[f][1], 0.0)
            arm_obj.rotation_euler = (0, 0, track[f][2])
            apply(r, pose)
            r.sync()
            head.append(arm_obj.matrix_world @ r.bones["Head"].head)
        return self._write(poses, head, track, [0.0] * len(poses), base_loc, name)

    def _write(self, poses, head, track, lift, base_loc, name):
        """Pass 2: simulate the ponytail from the head's motion, then key every frame."""
        r, arm_obj = self.r, self.r.obj
        hair = self._simulate_hair(head, track)
        r.begin_action(name)
        for f in range(len(poses)):
            apply(r, poses[f])
            cum_x = cum_y = 0.0
            for bone, (ax, ay) in hair[f].items():    # chain order; swing accumulates down the chain
                cum_x += ax
                cum_y += ay
                r.set_abs_delta(bone, Euler((cum_x, cum_y, 0.0), "XYZ").to_matrix())
            r.key(f, settle=False)
            arm_obj.location = (base_loc.x + track[f][0], base_loc.y + track[f][1], lift[f])
            arm_obj.rotation_euler = (0, 0, track[f][2])
            arm_obj.keyframe_insert("location", frame=f)
            arm_obj.keyframe_insert("rotation_euler", frame=f)
        r.end_action()
        r.obj.animation_data.action = r.action
        return track

    def _simulate_hair(self, head, track):
        """Ponytail secondary motion. The chain hangs by gravity in *world* terms (it does
        not tilt up when the head pitches), swings from the head's acceleration through
        damped springs, and streams backward a little with speed (air drag).

        Returns [{bone: (pitch, roll)}] per frame: each bone's swing relative to hanging,
        which `bake` applies as an absolute rotation."""
        r, dt = self.r, 1.0 / self.fps
        bones = [f"Ponytail.{i}" for i in range(1, 9) if f"Ponytail.{i}" in r.bones]
        if not bones:
            return [{} for _ in head]
        m = len(head)
        acc = []
        for f in range(m):
            a, b, c = head[max(f - 1, 0)], head[f], head[min(f + 1, m - 1)]
            acc.append((a - 2 * b + c) / (dt * dt))
        acc = [sum((acc[min(max(f + k, 0), m - 1)] for k in (-2, -1, 0, 1, 2)), Vector()) / 5 for f in range(m)]
        state = {b: [0.0, 0.0, 0.0, 0.0] for b in bones}  # pitch, pitch', roll, roll'
        out = []
        for f in range(m):
            yaw = track[f][2]
            fwd = Vector((math.sin(yaw), -math.cos(yaw), 0))
            left = Vector((math.cos(yaw), math.sin(yaw), 0))
            vel = (head[min(f + 1, m - 1)] - head[max(f - 1, 0)]) / (2 * dt)
            speed_f = vel.dot(fwd)                       # signed forward speed of the head
            wind = max(-0.05, min(0.12, 0.022 * speed_f))  # radians per segment, streams back when running
            a_f, a_l = acc[f].dot(fwd), acc[f].dot(left)
            frame = {}
            for i, b in enumerate(bones):
                w = 2 * math.pi * (3.2 - 0.5 * i)  # natural frequency, lower toward the tip
                z = 0.35                           # damping ratio
                k = 0.010                          # response to acceleration (rad per m/s^2)
                st = state[b]
                for _ in range(4):
                    h = dt / 4
                    st[1] += h * (w * w * (k * a_f + wind - st[0]) - 2 * z * w * st[1])
                    st[0] += h * st[1]
                    st[3] += h * (w * w * (k * a_l - st[2]) - 2 * z * w * st[3])
                    st[2] += h * st[3]
                st[0] = max(-0.22, min(0.30, st[0]))
                st[2] = max(-0.22, min(0.22, st[2]))
                frame[b] = (st[0], st[2])
            out.append(frame)
        return out

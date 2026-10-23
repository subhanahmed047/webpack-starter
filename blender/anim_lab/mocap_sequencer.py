"""A performance driven by retargeted professional animation (see retarget.py).

Locomotion
    Walk, jog and sprint loops are blended by speed. Cadence and stride come from each clip's
    own measured natural values, so the blend's planted-foot sweep matches ground speed.
    Below walking speed the cadence floors and the stride shrinks, so stops are a shuffle
    into a stand; pivots step in place with tiny strides.
Foot locking
    Every pose -- locomotion, sitting, jumping -- goes through one foot-lock stage: when a
    sole touches the ground its world position is latched until it lifts. Where the clip
    wants the foot somewhere else (clips start/end in different stances) the difference is
    absorbed while the foot is in the air, never while it is planted.
Body
    The pelvis is lowered when a latched foot would otherwise be out of the leg's reach.
"""
import math

from mathutils import Matrix, Vector

from . import actions as A
from .rig import lerp, smoothstep
from .sequencer import (FEET_BONES, XFADE, ClipSampler, Performance, apply, blend, capture,
                        curve, wrap)

SRC_LEG = 0.828                     # source hip->ankle length at rest (metres)
# (clip, natural ground speed m/s, cycles/s) measured from the source clips (planted-foot sweep)
LOCO = [("mc_walk", 0.84, 0.75), ("mc_jog", 5.26, 1.09), ("mc_sprint", 8.11, 1.50)]
CONTACT_THR = 0.03                  # metres: sole height under which a foot counts as planted
RELEASE_TIME = 0.18                 # seconds over which a released foot rejoins its clip path
LATCH_MAX_SPEED = 2.0               # m/s: above this (running) clips are played as-is, without foot locking
STEP_ERROR = 0.14                   # metres a planted foot may lag its target before it takes a corrective step
STEP_TIME, STEP_LIFT = 0.42, 0.07   # duration (s) and clearance (m) of a corrective step


class PoseData:
    """A pose plus the (pitch, yaw) of each foot, which the Ankle bones are derived from."""

    def __init__(self, pose, foot, rel=None):
        self.pose, self.foot = pose, foot
        self.rel = rel or {"L": None, "R": None}      # ankle rotation relative to the shin (3x3), or None


def _blend_rel(a, b, w):
    if a is None or b is None:
        return b if a is None else a
    qa, qb = a.to_quaternion(), b.to_quaternion()
    if qa.dot(qb) < 0:
        qb = -qb
    return qa.slerp(qb, min(max(w, 0.0), 1.0)).to_matrix()


def blend_pd(a, b, w):
    if w <= 0.0:
        return a
    if w >= 1.0:
        return b
    foot = {s: (lerp(a.foot[s][0], b.foot[s][0], w), lerp(a.foot[s][1], b.foot[s][1], w)) for s in "LR"}
    rel = {s: _blend_rel(a.rel[s], b.rel[s], w) for s in "LR"}
    return PoseData(blend(a.pose, b.pose, w), foot, rel)


class ClipData:
    """A keyed clip sampled at any (fractional) frame, with its foot orientation metadata."""

    def __init__(self, r, action, name):
        self.cs = ClipSampler(r, action)
        self.n = int(action.frame_end - action.frame_start)
        self.meta = r.foot_meta.get(name)
        self.rel = r.foot_rel_meta.get(name)

    def at(self, frame, loop=False):
        if loop:
            frame = frame % self.n
        pose = self.cs.at(frame)
        if not self.meta:
            return PoseData(pose, {"L": (0.0, 0.0), "R": (0.0, 0.0)})
        i = min(max(int(frame), 0), len(self.meta) - 1)
        j, u = min(i + 1, len(self.meta) - 1), min(max(frame - int(frame), 0.0), 1.0)
        foot = {s: (lerp(self.meta[i][s][0], self.meta[j][s][0], u),
                    lerp(self.meta[i][s][1], self.meta[j][s][1], u)) for s in "LR"}
        rel = {s: _blend_rel(self.rel[i][s], self.rel[j][s], u) for s in "LR"} if self.rel else None
        return PoseData(pose, foot, rel)


class MocapPerformance(Performance):
    def __init__(self, r, actions):
        super().__init__(r, {})
        p = r.profile
        self.S = (p["hip_z"] - p["ankle_z"]) / SRC_LEG           # leg-length scale vs the source
        self.data = {n: ClipData(r, a, n) for n, a in actions.items()}
        bones = r.obj.data.bones
        self.ankle_rest = {s: bones[f"Foot.{s}"].head_local.copy() for s in "LR"}
        self.hip_rest = {s: bones[f"UpperLeg.{s}"].head_local.copy() for s in "LR"}
        self.leg_max = 0.997 * sum((bones[f"{n}.L"].tail_local - bones[f"{n}.L"].head_local).length
                                   for n in ("UpperLeg", "LowerLeg"))
        H = p["height"]
        self.heel, self.ball, self.az = 0.034 * H, 0.086 * H, p["ankle_z"]
        self.loco = [(n, v * self.S, c) for n, v, c in LOCO if n in self.data]
        self.offset, self.ds = {}, []
        for n, _, _ in self.loco:
            self.offset[n], ds = self._analyse(n)
            self.ds.append(ds)
        self.jumps = []                                           # (t_takeoff, t_land, apex metres)

    # ------------------------------------------------------------------ authoring
    def seg(self, t, kind, name=None, rate=1.0, f_from=0.0, f_to=None, zscale=1.0):
        """Add a segment. For clips: play from frame `f_from` (optionally cut at `f_to`) at
        `rate`; `zscale` < 1 softens a downward pelvis excursion (landings)."""
        self.segments.append((t, kind, name, rate, f_from, f_to, zscale))

    def jump_arc(self, t_takeoff, t_land, apex):
        self.jumps.append((t_takeoff, t_land, apex))

    def air_frames(self, name):
        """(first airborne frame, first grounded frame) of a clip, from its own foot data."""
        d, r = self.data[name], self.r
        air = []
        for f in range(d.n + 1):
            pose = d.at(float(f))
            soles = []
            for side in "LR":
                a = self.ankle_rest[side].z + (r._rest3[f"Foot.{side}"] @ pose.pose[f"Foot.{side}"][0]).z
                p = math.radians(pose.foot[side][0])
                soles.append(a + min(-self.heel * math.sin(p), self.ball * math.sin(p)) - self.az * math.cos(p))
            air.append(min(soles) > CONTACT_THR)
        take = next((f for f, a in enumerate(air) if a), 0)
        touch = next((f for f, a in enumerate(air) if not a), 0)
        return take, touch

    def add_jump(self, t0, air=0.52, rate=1.0, land_rate=1.15, land_depth=0.5, max_apex=0.45):
        """A standing hop from the Jump_Start / Jump_Land clips, driven by the airtime.

        The feet leave the ground at the Start clip's own take-off frame and the body follows a
        ballistic arc for `air` seconds. The Land clip is started early enough that its
        cross-fade finishes at touch-down (the clip begins with the toes about to land), and
        `land_depth` < 1 softens its crouch. Returns the time the hop is finished."""
        take, _ = self.air_frames("mc_jump_start")
        _, touch = self.air_frames("mc_jump_land")
        t_up = t0 + take / 24 / rate
        t_down = t_up + air
        t_land = t_down - XFADE / 2 - touch / 24 / land_rate        # Land clip takes over here
        cut = max((t_land - t0) * 24 * rate, take + 2.0)
        self.seg(t0, "clip", "mc_jump_start", rate, 0.0, cut)
        self.seg(t_land, "clip", "mc_jump_land", land_rate, 0.0, None, land_depth)
        self.jump_arc(t_up, t_down, min(max_apex, 9.81 * air * air / 8))
        return t_land + 30 / 24 / land_rate

    def lift_at(self, t):
        """Height of the body above the ground at time `t` (metres): ballistic arcs of hops."""
        for t0, t1, apex in self.jumps:
            if t0 < t < t1:
                u = (t - t0) / (t1 - t0)
                return 4 * apex * u * (1 - u)
        return 0.0

    def eff_speed(self, t):
        """Gait speed in m/s: real travel speed, or stepping speed while pivoting."""
        yaw_rate = abs(curve(self.yaw_keys, t + 0.05) - curve(self.yaw_keys, t)) / 0.05
        return max(abs(curve(self.speed_keys, t)), 0.5 * yaw_rate)

    # ------------------------------------------------------------------ analysis
    def _analyse(self, name):
        """Phase offset so that phase 0 = left foot furthest forward (heel strike), and the
        phase of double support (both feet low), from the clip's own foot data."""
        d, r = self.data[name], self.r
        n = d.n
        ys, zs = [], []
        for f in range(n):
            pose = d.cs.at(f)
            dl = r._rest3["Foot.L"] @ pose["Foot.L"][0]
            dr = r._rest3["Foot.R"] @ pose["Foot.R"][0]
            ys.append(dl.y)
            zs.append(max(dl.z, dr.z))
        off = min(range(n), key=lambda f: ys[f]) / n
        ds = min(range(n), key=lambda f: zs[f]) / n - off
        return off, ds % 1.0

    def _blend_info(self, s_eff, s_abs):
        """Which loops to play, at what cycle rate, and how much of the stride to keep."""
        loco = self.loco
        names, vs, cs = [l[0] for l in loco], [l[1] for l in loco], [l[2] for l in loco]
        if s_eff <= vs[0]:
            ratio = max(0.55, s_eff / vs[0])
            return [(names[0], 1.0)], cs[0] * ratio, min(1.0, s_abs / (vs[0] * ratio))
        for i in range(len(loco) - 1):
            if s_eff <= vs[i + 1]:
                sa, sb = vs[i] / cs[i], vs[i + 1] / cs[i + 1]      # stride per cycle (m)
                lo, hi = 0.0, 1.0
                for _ in range(30):                                # solve speed(alpha) = s_eff
                    a = (lo + hi) / 2
                    if lerp(cs[i], cs[i + 1], a) * lerp(sa, sb, a) < s_eff:
                        lo = a
                    else:
                        hi = a
                a = (lo + hi) / 2
                return [(names[i], 1 - a), (names[i + 1], a)], lerp(cs[i], cs[i + 1], a), 1.0
        return [(names[-1], 1.0)], cs[-1] * s_eff / vs[-1], 1.0

    # ------------------------------------------------------------------ kinematics
    def _kinematics(self, n):
        k = type("Kin", (), {})()
        k.sp, k.se, k.yaw, k.phase, k.pos, k.info, k.latch = [], [], [], [], [], [], []
        fps = self.fps
        phase, x, y = self.ds[0] if self.ds else 0.0, 0.0, 0.0   # start standing, both feet down
        for f in range(n + 1):
            t = f / fps
            sp, se = curve(self.speed_keys, t), self.eff_speed(t)
            yaw = curve(self.yaw_keys, t)
            weights, cps, stride = self._blend_info(se, abs(sp))
            if f:
                if se > 0.03:
                    phase = (phase + (-1.0 if sp < -0.02 else 1.0) * cps / fps) % 1.0
                else:   # finish the step into double support, then hold
                    dsd = min(abs(wrap((phase - d) * 2 * math.pi)) for d in (self.ds[0], (self.ds[0] + 0.5) % 1.0))
                    if dsd > 0.12:
                        phase = (phase + 0.6 / fps) % 1.0
                x += math.sin(yaw) * sp / fps
                y += -math.cos(yaw) * sp / fps
            k.sp.append(sp)
            k.se.append(se)
            k.yaw.append(yaw)
            k.phase.append(phase)
            k.pos.append((x, y))
            k.info.append(dict(weights=weights, cps=cps, stride=stride))
            k.latch.append(se < LATCH_MAX_SPEED)
        return k

    # ------------------------------------------------------------------ poses
    def _scale_stride(self, pd, k):
        """Shrink the fore/aft sweep of the feet (slow walking, pivots)."""
        if k >= 0.999:
            return
        r = self.r
        for bone in ("Foot.L", "Foot.R", "PoleTarget.L", "PoleTarget.R"):
            loc, q = pd.pose[bone]
            d = r._rest3[bone] @ loc
            d.y *= k
            pd.pose[bone] = (r._rest3[bone].inverted() @ d, q)

    def _segment(self, idx, t, state):
        t0, kind, name, rate, f_from, f_to, zscale = self.segments[idx]
        f, kin = state["f"], state["kin"]
        if kind == "loco":
            info, se = kin.info[f], kin.se[f]
            parts = []
            for clip, wgt in info["weights"]:
                d = self.data[clip]
                phase = (kin.phase[f] + self.offset[clip]) % 1.0
                parts.append((d.at(phase * d.n), wgt))
            loco = parts[0][0]
            acc = parts[0][1]
            for pd, w in parts[1:]:
                loco = blend_pd(loco, pd, w / (acc + w))
                acc += w
            self._scale_stride(loco, info["stride"])
            idle = self.data["mc_idle"].at(t * self.fps, loop=True)
            w = smoothstep(se / (0.5 * self.loco[0][1]))
            blended = blend_pd(idle, loco, w)       # incl. feet: at rest the stance is the idle clip's
            return blended
        if kind == "sitidle":
            return self.data["mc_sit_idle"].at((t - t0) * self.fps, loop=True)
        frame = f_from + (t - t0) * self.fps * rate
        if f_to is not None:
            frame = min(frame, f_to)
        pd = self.data[name].at(frame)
        if zscale != 1.0:                       # soften a downward pelvis excursion (e.g. landing crouch)
            rb = self.r._rest3["Body"]
            loc, q = pd.pose["Body"]
            d = rb @ loc
            if d.z < 0.0:
                d.z *= zscale
            pd.pose["Body"] = (rb.inverted() @ d, q)
        return pd

    def pose_at(self, t, state):
        segs = self.segments
        i = max((k for k, s in enumerate(segs) if s[0] <= t), default=0)
        pd = self._segment(i, t, state)
        if i + 1 < len(segs):
            tb = segs[i + 1][0]
            if t > tb - XFADE / 2:
                pd = blend_pd(pd, self._segment(i + 1, t, state), smoothstep((t - (tb - XFADE / 2)) / XFADE))
        if i > 0:
            tb = segs[i][0]
            if t < tb + XFADE / 2:
                pd = blend_pd(self._segment(i - 1, t, state), pd, smoothstep((t - (tb - XFADE / 2)) / XFADE))
        return pd

    # ------------------------------------------------------------------ foot lock
    def _finish(self, pd, f, state):
        """Latch planted feet in world space, keep legs within reach, derive the ankles."""
        r, kin, fps = self.r, state["kin"], self.fps
        apply(r, pd.pose)
        yaw = kin.yaw[f]
        ox, oy = state["base"].x + kin.pos[f][0], state["base"].y + kin.pos[f][1]
        cs, sn = math.cos(yaw), math.sin(yaw)
        lift = state["lift"][f]
        ankle = {}
        for side in "LR":
            bone, pole = f"Foot.{side}", f"PoleTarget.{side}"
            r3, r3p = r._rest3[bone], r._rest3[pole]
            d = r3 @ r.bones[bone].location
            a = self.ankle_rest[side] + d                       # ankle target, armature-local
            pitch, foot_yaw = pd.foot[side]
            p = math.radians(pitch)
            sole = a.z + lift + min(-self.heel * math.sin(p), self.ball * math.sin(p)) - self.az * math.cos(p)
            contact = sole < CONTACT_THR and kin.latch[f]
            r.foot_rel[side] = pd.rel[side]
            r.foot_wabs[side] = 1.0 - smoothstep((sole + 0.0 - 0.015) / 0.05)   # planted -> world pitch; airborne -> relative
            tgt = Vector((ox + cs * a.x - sn * a.y, oy + sn * a.x + cs * a.y))   # world xy wanted by the clip
            tgt_yaw = yaw + math.radians(foot_yaw)
            # While the sole is planted the *foot* is fixed and the ankle rolls about the heel
            # (toes up) or ball (heel up). So latch the ankle position the foot would have when
            # flat, and add the roll displacement for the current pitch back on top.
            roll = A.roll_offset(pitch, self.heel, self.ball, self.az)[0]
            st = state["latch"][side]
            other = state["latch"]["R" if side == "L" else "L"]
            extra_lift = 0.0
            if st["step"] is not None:                          # a corrective step is under way
                stp = st["step"]
                tau = (f - stp["f0"]) / (STEP_TIME * fps)
                if tau >= 1.0:                                  # arrived: plant it
                    fwd = Vector((math.sin(tgt_yaw), -math.cos(tgt_yaw)))
                    st.update({"in": True, "pos": tgt - fwd * roll, "yaw": tgt_yaw, "err": None, "step": None})
                    want, want_yaw = tgt, tgt_yaw
                else:
                    u = smoothstep(tau)
                    want = stp["from"].lerp(tgt, u)
                    want_yaw = stp["yaw"] + wrap(tgt_yaw - stp["yaw"]) * u
                    extra_lift = STEP_LIFT * math.sin(math.pi * u)
            elif contact:
                if not st["in"]:                                # touchdown: latch where it lands
                    fwd = Vector((math.sin(tgt_yaw), -math.cos(tgt_yaw)))
                    st.update({"in": True, "pos": tgt - fwd * roll, "yaw": tgt_yaw, "err": None})
                fwd = Vector((math.sin(st["yaw"]), -math.cos(st["yaw"])))
                want, want_yaw = st["pos"] + fwd * roll, st["yaw"]
                st["last"] = want.copy()
                if (want - tgt).length > STEP_ERROR and other["in"] and other["step"] is None:
                    # too far from where the motion wants this foot: lift it and step there
                    st.update({"in": False, "step": {"f0": f, "from": want.copy(), "yaw": want_yaw}})
                    extra_lift = 0.0
            else:
                if st["in"]:                                    # lift-off: remember the offset
                    st.update({"in": False, "f_rel": f, "err": (st["last"] - tgt, wrap(st["yaw"] - tgt_yaw))})
                want, want_yaw = tgt, tgt_yaw
                if st["err"]:                                   # let the foot rejoin its path in the air
                    k = 1.0 - smoothstep((f - st["f_rel"]) / (RELEASE_TIME * fps))
                    want = tgt + st["err"][0] * k
                    want_yaw = tgt_yaw + st["err"][1] * k
            dx, dy = want.x - ox, want.y - oy
            ax, ay = cs * dx + sn * dy, -sn * dx + cs * dy      # back to armature-local
            shift = Vector((ax - a.x, ay - a.y, extra_lift))
            r.bones[bone].location = r3.inverted() @ (d + shift)
            r.bones[pole].location = r3p.inverted() @ (r3p @ r.bones[pole].location + shift)
            r.foot_pose[side] = (pitch, math.degrees(wrap(want_yaw - yaw)))
            ankle[side] = Vector((ax, ay, a.z + extra_lift))
        # keep both legs within reach by lowering the pelvis (knees bend instead of stretching)
        rb = r._rest3["Body"]
        body = rb @ r.bones["Body"].location
        dz = 0.0
        for side in "LR":
            hip = self.hip_rest[side] + body
            v = ankle[side] - hip
            if v.length > self.leg_max:
                dxy2 = v.x * v.x + v.y * v.y
                new_z = ankle[side].z + math.sqrt(max(self.leg_max ** 2 - dxy2, 0.0))
                dz = min(dz, new_z - hip.z)
        if dz < 0.0:
            body.z += dz
            r.bones["Body"].location = rb.inverted() @ body
        r.settle_feet()
        return capture(r)

    # ------------------------------------------------------------------ bake
    def bake(self, duration, name="performance"):
        r, fps = self.r, self.fps
        n = int(duration * fps)
        arm_obj = r.obj
        base = Vector(arm_obj.location)
        kin = self._kinematics(n)
        track = [(x, y, yaw) for (x, y), yaw in zip(kin.pos, kin.yaw)]
        lift = [self.lift_at(f / fps) for f in range(n + 1)]
        state = {"kin": kin, "base": base, "lift": lift,
                 "latch": {s: {"in": False, "pos": None, "yaw": 0.0, "err": None, "f_rel": 0, "step": None} for s in "LR"}}
        poses, head = [], []
        for f in range(n + 1):
            state["f"] = f
            pd = self.pose_at(f / fps, state)
            arm_obj.location = (base.x + track[f][0], base.y + track[f][1], lift[f])
            arm_obj.rotation_euler = (0, 0, track[f][2])
            poses.append(self._finish(pd, f, state))
            head.append(arm_obj.matrix_world @ r.bones["Head"].head)
        return self._write(poses, head, track, lift, base, name)

    def sit_geometry(self):
        """Where the pelvis ends up when seated (armature-local metres): used to place a chair."""
        r = self.r
        pd = self.data["mc_sit_idle"].at(0)
        body = r._rest3["Body"] @ pd.pose["Body"][0]
        hip = self.hip_rest["L"] + body
        return hip

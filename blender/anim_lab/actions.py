"""The animation library. Each function authors one Blender Action on the rig.

Conventions (armature axes): the character faces -Y, +X is its left, +Z is up.
Distances are in "reference units" (a 2.76-tall character) and are scaled to the real
character automatically by `Rig.move`.

Arms are posed by *aiming* (see `arm`): give the direction the upper arm and forearm
point as (out, forward, up) -- positive `out` moves away from the body on either side.
Legs are IK: feet are moved with `feet`, knees follow the pole targets.
"""
import math

from mathutils import Vector

from .rig import lerp, smoothstep

SIDES = {"L": 1, "R": -1}
SIT_BACK = 0.30  # how far the pelvis slides back (+Y) over the seat
SIT_FOOT_FWD = 0.30  # feet slide forward so the thighs go roughly horizontal

RELAXED = ((0.28, 0.05, -1.0), (0.20, 0.55, -0.85))  # (upper arm dir, forearm dir)


# --------------------------------------------------------------------- poses
def arm(r, side, pose=RELAXED, twist=0.0):
    """Aim one arm. `pose` = (upper_dir, fore_dir), each (out, forward, up)."""
    s = SIDES[side]
    up, fo = ((s * d[0], -d[1], d[2]) for d in pose)  # character-relative -> armature axes
    r.aim_chain(f"UpperArm.{side}", f"LowerArm.{side}", up, fo, twist)


def arms(r, pose_l=RELAXED, pose_r=None):
    arm(r, "L", pose_l)
    arm(r, "R", pose_r or pose_l)


def mix(a, b, t):
    """Blend two arm poses."""
    return tuple(tuple(lerp(x, y, t) for x, y in zip(va, vb)) for va, vb in zip(a, b))


def swing(deg, bend=0.55, out=0.2):
    """Arm swinging fore/aft by `deg` (+ = forward) with a forearm that folds forward."""
    a = math.radians(deg)
    return ((out, math.sin(a), -math.cos(a)), (out, 0.5 + bend * max(0.0, math.sin(a)) + 0.15, -0.85 + 0.9 * bend * max(0.0, math.sin(a))))


def feet(r, l=(0, 0, 0), rt=(0, 0, 0), spread=0.0):
    """Place feet. Each is (forward, lift, sideways) offset from the standing position."""
    for side, (fwd, lift, side_off) in (("L", l), ("R", rt)):
        s = SIDES[side]
        r.move(f"Foot.{side}", x=s * spread + side_off, y=-fwd, z=lift)
        r.move(f"PoleTarget.{side}", x=s * spread * 0.5, y=-fwd * 0.3, z=lift * 0.5)


def stand(r, drop=0.0, lean=0.0, pose=RELAXED):
    """Neutral standing pose (arms relaxed unless `pose` is given)."""
    r.reset()
    r.move("Body", z=-drop)
    r.rot("Torso", x=lean)
    arms(r, pose)


def seated(r, t, lean=0.0, pull=0.0):
    """Blend stand (t=0) -> fully seated (t=1). `lean` is total trunk flexion in degrees,
    shared between abdomen and chest like a real spine, with the head counter-pitching so
    the gaze stays level. `pull` slides the feet back under the knees (used just before
    rising). Hands are solved with arm IK so the wrists rest on the thighs."""
    stand(r)
    r.rot("Abdomen", x=0.4 * lean + 4 * t)
    r.rot("Torso", x=0.6 * lean)
    r.rot("Head", x=-0.5 * lean)
    # pelvis settles so the hip joint rests just above the seat (derived from the profile)
    drop = (r.profile["hip_z"] - (r.seat_z + 0.045 * r.profile["height"])) / r.unit
    r.move("Body", y=SIT_BACK * t, z=-drop * t)
    fwd = SIT_FOOT_FWD * min(t, 1.0) - pull
    feet(r, l=(fwd, 0, 0), rt=(fwd, 0, 0), spread=0.04 * min(t, 1.0))
    r.rot("Hips", x=-8 * t)
    r.sync()
    thigh_r = 0.055 * r.profile["height"]
    for side in "LR":
        relaxed = r.bones[f"LowerArm.{side}"].tail.copy()      # wrist with relaxed arms
        hip = r.bones[f"UpperLeg.{side}"].head
        knee = r.bones[f"LowerLeg.{side}"].head
        lap = hip.lerp(knee, 0.55) + Vector((0, 0, thigh_r))   # on top of the thigh
        r.arm_ik(side, relaxed.lerp(lap, smoothstep(min(t, 1.0))), pole=(0.8, -0.6, -0.2))


# ------------------------------------------------------------- one-shot moves
# Sit-to-stand follows the phases in the biomechanics literature: (1) flexion-momentum --
# feet drawn back, trunk pitches forward so the centre of mass moves over the feet;
# (2) seat-off -- peak trunk flexion as the pelvis leaves the seat; (3) extension -- hips and
# knees straighten, trunk comes upright; (4) stabilisation -- a small settle. Sitting down is
# the controlled reverse: lean, lower with deceleration, then a slight settle into the seat.
def sit_down(r):
    r.begin_action("sit_down")
    keys = [  # frame, sit amount, trunk flexion deg
        (0, 0.00, 0), (8, 0.06, 14),     # reach back / begin to fold
        (18, 0.40, 34),                  # trunk forward, hips travelling back and down
        (28, 0.86, 26),                  # controlled descent (decelerating)
        (36, 1.04, 12),                  # touches down; slight settle past the rest height
        (44, 1.00, 4), (52, 1.00, 2),
    ]
    for f, t, lean in keys:
        seated(r, t, lean)
        r.key(f)
    return r.end_action()


def stand_up(r):
    r.begin_action("stand_up")
    keys = [  # frame, sit amount, trunk flexion deg, feet pulled back
        (0, 1.00, 2, 0.00),
        (7, 1.00, 24, 0.14),             # flexion-momentum: feet back, trunk forward
        (14, 0.97, 44, 0.14),            # seat-off: peak flexion, pelvis just leaving the seat
        (23, 0.55, 32, 0.10),            # extension: hips and knees drive up
        (32, 0.12, 14, 0.03),
        (40, 0.01, 4, 0.00),
        (48, 0.00, -2, 0.00),            # stabilise (tiny backward overshoot)
        (54, 0.00, 0, 0.00),
    ]
    for f, t, lean, pull in keys:
        seated(r, t, lean, pull)
        r.key(f)
    return r.end_action()


ARMS_BACK = ((0.25, -0.9, -0.6), (0.2, -0.5, -0.9))
ARMS_UP = ((0.30, 0.35, 1.0), (0.2, 0.3, 1.0))
ARMS_AIR = ((0.85, 0.2, 0.55), (0.9, 0.2, 0.35))
ARMS_LAND = ((0.35, 0.45, -0.85), (0.2, 0.9, -0.4))


def jump(r):
    r.begin_action("jump")

    def pose(f, drop=0.0, lean=0.0, arms_pose=RELAXED, air=0.0, root=0.0, tuck=0.0):
        stand(r, drop=drop, lean=lean, pose=arms_pose)
        feet(r, l=(-tuck * 0.15, air, 0), rt=(tuck * 0.15, air, 0), spread=0.03)
        r.move("Root", z=root)
        r.key(f)

    pose(0)
    pose(8, drop=0.38, lean=20, arms_pose=ARMS_BACK)                   # wind-up crouch
    pose(12, drop=0.0, lean=5, arms_pose=ARMS_UP, root=0.05)           # explosive extension
    pose(18, drop=-0.05, lean=2, arms_pose=ARMS_UP, root=0.85, air=0.18, tuck=0.4)
    pose(23, drop=0.05, lean=8, arms_pose=ARMS_AIR, root=1.15, air=0.34, tuck=1.0)  # apex tuck
    pose(29, drop=0.0, lean=4, arms_pose=ARMS_AIR, root=0.45, air=0.12, tuck=0.3)
    pose(33, drop=0.0, lean=0, arms_pose=ARMS_LAND, root=0.0)          # touch down
    pose(37, drop=0.36, lean=22, arms_pose=ARMS_LAND)                  # absorb landing
    pose(48)
    return r.end_action()


def wave(r):
    r.begin_action("wave")
    for i, f in enumerate(range(0, 49, 4)):
        up = smoothstep(f / 8) * (1 - smoothstep((f - 40) / 8))  # raise, hold, lower
        sway = math.sin(i * math.pi / 1.5)  # forearm waves side to side
        waving = ((1.0, 0.0, 0.25), (0.15 + 0.75 * sway, 0.25, 1.0))
        stand(r)
        arm(r, "R", mix(RELAXED, waving, up))
        r.rot("Head", z=-6 * up, x=-3 * up)
        r.rot("Torso", z=4 * up)
        r.key(f)
    return r.end_action()


def crouch(r):
    r.begin_action("crouch")
    reach = ((0.30, 0.7, -0.7), (0.20, 1.0, -0.25))
    for f, d, lean in [(0, 0, 0), (12, 0.55, 28), (36, 0.58, 30), (48, 0.55, 28), (60, 0, 0)]:
        k = d / 0.55
        stand(r, drop=d, lean=lean, pose=mix(RELAXED, reach, k))
        feet(r, l=(0.1 * d, 0, 0), rt=(-0.1 * d, 0, 0), spread=0.15 * d)
        r.rot("Head", x=-lean * 0.6)
        r.key(f)
    return r.end_action()


def punch(r):
    r.begin_action("punch")
    guard_l = ((0.45, 0.45, -0.9), (0.05, 1.0, 0.75))
    guard_r = ((0.5, 0.4, -0.9), (0.10, 1.0, 0.7))
    strike_r = ((0.10, 1.0, 0.15), (0.03, 1.0, 0.12))

    def pose(f, ext, twist, lunge=0.0):
        stand(r, drop=0.12 + 0.08 * lunge, lean=5 + 6 * ext)
        feet(r, l=(0.35 + 0.1 * lunge, 0, 0), rt=(-0.25, 0, 0), spread=0.15)
        r.rot("Torso", x=5 + 5 * ext, z=twist)
        r.rot("Abdomen", z=twist * 0.6)
        arm(r, "L", guard_l)
        arm(r, "R", mix(guard_r, strike_r, ext))
        r.key(f)

    pose(0, 0, 0)
    pose(6, 0, -18)        # wind back, twist away
    pose(9, 1, 28, 1)      # snap through
    pose(13, 1, 30, 1)     # hold
    pose(22, 0, 0)         # retract to guard
    pose(30, 0, 0)
    return r.end_action()


def cheer(r):
    r.begin_action("cheer")
    cheer_pose = ((0.55, 0.05, 1.0), (0.30, 0.05, 1.0))
    for i, f in enumerate(range(0, 49, 6)):
        up = smoothstep(f / 10)
        hop = (i % 2) * up
        pump = math.sin(i * math.pi) * 0.2 * up
        stand(r, drop=-0.04 * hop, lean=-6 * up,
              pose=mix(RELAXED, ((0.55 + pump, 0.05, 1.0), (0.30 + pump, 0.05, 1.0)), up))
        feet(r, l=(0, 0.06 * hop, 0), rt=(0, 0.06 * (up - hop), 0), spread=0.04)
        r.rot("Head", x=-8 * up)
        r.key(f)
    return r.end_action()


# ------------------------------------------------------------------ cycles
def foot_roll(ph, duty, run):
    """Ankle pitch in degrees (+ = toes up) at gait phase `ph` (0 = heel strike).

    Stance: heel strike -> foot flat (the first "rocker") -> heel rise about the ball ->
    toe-off. Swing: the foot plantar-flexes at toe-off, then dorsiflexes so the toes clear
    the ground and the heel lands first (forefoot first when running)."""
    heel_strike = lerp(15.0, 4.0, run)
    toe_off = lerp(-28.0, -35.0, run)
    if ph < duty:
        q = ph / duty
        if q < 0.12:
            return lerp(heel_strike, 0.0, smoothstep(q / 0.12))
        if q < 0.45:
            return 0.0
        return lerp(0.0, toe_off, smoothstep((q - 0.45) / 0.55))
    q = (ph - duty) / (1.0 - duty)
    if q < 0.5:
        return lerp(toe_off, 8.0, smoothstep(q / 0.5))
    return lerp(8.0, heel_strike, smoothstep((q - 0.5) / 0.5))


def roll_offset(alpha_deg, heel, ball, ankle_h):
    """Where the ankle moves (forward, up) when the foot pitches by `alpha_deg` about the
    heel (toes up) or the ball of the foot (heel up), so the contact point stays put."""
    a = math.radians(alpha_deg)
    if abs(a) < 1e-6:
        return 0.0, 0.0
    px = -heel if a > 0 else ball                 # pivot's forward coordinate vs the ankle
    rx, rz = -px, ankle_h                         # ankle relative to the pivot
    c, s = math.cos(a), math.sin(a)
    return (rx * c - rz * s) - rx, (rx * s + rz * c) - rz


def gait_pose(r, p, h, lift, bob, lean, arm_swing, drop, runness=0.0, head_yaw=0.0,
              duty=0.6, plan=None):
    """Set the rig to one frame of a gait.

    `p` is the cycle phase in [0,1) (left foot strikes at 0); `duty` is the fraction of the
    cycle a foot spends on the ground (>0.5 walking with double support, <0.5 running with
    a flight phase). Feet come from `plan` -- world-latched positions from the foot planner --
    or, for in-place loops, from a simple back-sweep/forward-swing cycle. Either way the
    ankle rolls heel -> flat -> toe, the pelvis rotates/drops/sways, arms lag the legs, and
    the pelvis is lowered just enough that neither leg over-extends.
    """
    stand(r)
    heel, ball, ankle_h = r.foot_dims
    phases = {"L": p % 1.0, "R": (p + 0.5) % 1.0}
    placed = {}
    for side in "LR":
        ph = phases[side]
        pitch = foot_roll(ph, duty, runness)
        d_fwd, d_up = roll_offset(pitch, heel, ball, ankle_h)
        if plan is not None:
            fwd, lift_now, side_off, yaw = plan[side]
        else:
            if ph < duty:
                fwd, lift_now = h - 2 * h * (ph / duty), 0.0
            else:
                q = (ph - duty) / (1.0 - duty)
                fwd, lift_now = -h + 2 * h * smoothstep(q), lift * math.sin(math.pi * q)
            side_off, yaw = 0.0, 0.0
        placed[side] = (fwd + d_fwd, lift_now + d_up, side_off)
        r.foot_pose[side] = (pitch, yaw)
    feet(r, l=placed["L"], rt=placed["R"], spread=0.02)

    # pelvis height: gait bounce, but never higher than the legs can reach the planted feet
    # (this also produces the loading-response knee flexion for free)
    w = 2 * math.pi * p
    mid = 2 * math.pi * (p - duty / 2)                   # 0 at left mid-stance
    bounce = lerp(1.0, -1.0, runness) * bob * math.cos(2 * mid)   # walk: up at mid-stance; run: down
    longest = r.leg_len * 0.995
    z_max = 0.0
    for side in "LR":
        fwd, up, side_off = placed[side]
        d2 = fwd * fwd + side_off * side_off
        if d2 < longest * longest:
            z_side = up - r.leg_len + math.sqrt(longest * longest - d2) - 0.004
        else:
            z_side = -r.leg_len * 0.15
        z_max = min(z_max, z_side)
    body_z = min(-drop + bounce, z_max)
    r.move("Body", x=0.035 * math.cos(mid), z=body_z)
    # pelvis: rotates with the leading leg (+/-6 deg), the swing side drops (+/-4 deg list)
    twist = arm_swing / 40
    r.rot("Hips", y=-4.0 * math.cos(mid) * (0.6 + 0.4 * runness), z=-6 * math.cos(w) * twist)
    # shoulders counter-rotate; each arm swings opposite its same-side leg and lags it slightly
    r.rot("Torso", x=lean, y=2.0 * math.cos(mid), z=8 * math.cos(w) * twist)
    r.rot("Head", z=-3 * math.cos(w) + head_yaw, x=-lean * 0.6)
    lag = 0.30
    for side, a in (("L", -arm_swing * math.cos(w - lag)), ("R", arm_swing * math.cos(w - lag))):
        arm(r, side, mix(swing(a, bend=0.35), run_arm(a), runness))


def run_arm(deg):
    """Runner's arm: elbow bent ~90 degrees, pumping fore/aft."""
    a = math.radians(deg)
    return ((0.30, math.sin(a), -math.cos(a)), (0.22, 1.0, 0.05 + 0.5 * math.sin(a)))


def _gait(r, name, frames, half_stride, lift, bob, lean, arm_swing, drop, duty, run=False):
    """Loopable in-place gait Action."""
    r.begin_action(name)
    for i in range(frames + 1):
        gait_pose(r, (i % frames) / frames, half_stride, lift, bob, lean, arm_swing, drop,
                  runness=1.0 if run else 0.0, duty=duty)
        r.key(i)
    return r.end_action(loop=True)


def walk(r):
    return _gait(r, "walk", 25, half_stride=0.34, lift=0.20, bob=0.02, lean=3,
                 arm_swing=24, drop=0.05, duty=0.6)


def run(r):
    return _gait(r, "run", 17, half_stride=0.46, lift=0.55, bob=0.07, lean=16,
                 arm_swing=48, drop=0.24, duty=0.4, run=True)


def idle_pose(r, t):
    """Standing idle at time `t` seconds: breathing, slight head drift."""
    a = math.sin(2 * math.pi * t / 2.5)
    stand(r)
    r.move("Body", z=0.012 * a)
    r.rot("Torso", x=1.5 * a)
    r.rot("Head", z=3 * math.sin(2 * math.pi * t / 1.25), x=-1.5 * a)


def sit_idle_pose(r, t):
    """Seated rest at time `t` seconds."""
    a = math.sin(2 * math.pi * t / 2.8)
    seated(r, 1.0, lean=2 + 1.0 * a)
    r.rot("Head", z=5 * math.sin(2 * math.pi * t / 4.0), x=-2)


def idle(r):
    r.begin_action("idle")
    for f in range(0, 61, 10):
        idle_pose(r, f / 24 * 2.5 / 2.5)
        r.key(f)
    return r.end_action(loop=True)


ALL = [idle, walk, run, jump, sit_down, stand_up, wave, crouch, punch, cheer]

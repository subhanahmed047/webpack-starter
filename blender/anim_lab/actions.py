"""The animation library. Each function authors one Blender Action on the rig.

Conventions (armature axes): the character faces -Y, +X is its left, +Z is up.
Distances are in "reference units" (a 2.76-tall character) and are scaled to the real
character automatically by `Rig.move`.

Arms are posed by *aiming* (see `arm`): give the direction the upper arm and forearm
point as (out, forward, up) -- positive `out` moves away from the body on either side.
Legs are IK: feet are moved with `feet`, knees follow the pole targets.
"""
import math

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


HANDS_ON_LAP = ((0.12, 0.35, -1.0), (0.10, 1.0, -0.40))


def seated(r, t, lean=0.0):
    """Blend stand (t=0) -> fully seated (t=1). `lean` adds torso pitch."""
    stand(r, lean=lean, pose=mix(RELAXED, HANDS_ON_LAP, t))
    # pelvis settles so the hip joint rests just above the seat (derived from the profile)
    drop = (r.profile["hip_z"] - (r.seat_z + 0.045 * r.profile["height"])) / r.unit
    r.move("Body", y=SIT_BACK * t, z=-drop * t)
    feet(r, l=(SIT_FOOT_FWD * t, 0, 0), rt=(SIT_FOOT_FWD * t, 0, 0), spread=0.04 * t)
    r.rot("Hips", x=-8 * t)
    r.rot("Abdomen", x=4 * t)


# ------------------------------------------------------------- one-shot moves
def sit_down(r):
    r.begin_action("sit_down")
    for f, t, lean in [(0, 0, 0), (10, 0.15, 22), (22, 0.60, 26), (34, 0.95, 14), (46, 1, 2)]:
        seated(r, t, lean)
        r.key(f)
    return r.end_action()


def stand_up(r):
    r.begin_action("stand_up")
    for f, t, lean in [(0, 1, 2), (10, 0.92, 26), (22, 0.45, 28), (34, 0.05, 10), (44, 0, 0)]:
        seated(r, t, lean)
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
def _gait(r, name, frames, half_stride, lift, bob, lean, arm_swing, drop, run=False):
    """Procedural loop. Phase p in [0,1); the right foot trails the left by half a cycle."""
    r.begin_action(name)
    n = frames
    for i in range(n + 1):
        p = (i % n) / n
        stand(r)

        def foot(ph):
            if ph < 0.5:                                 # stance: foot slides back under the body
                q = ph / 0.5
                return -half_stride + 2 * half_stride * q, 0.0
            q = (ph - 0.5) / 0.5                          # swing: foot arcs forward
            return half_stride - 2 * half_stride * smoothstep(q), lift * math.sin(math.pi * q)

        (ly, lz), (ry, rz) = foot(p), foot((p + 0.5) % 1.0)
        feet(r, l=(-ly, lz, 0), rt=(-ry, rz, 0), spread=0.02)

        w = 2 * math.pi * p
        r.move("Body", x=0.04 * math.sin(w), z=-drop - bob * math.cos(2 * w))
        r.rot("Hips", z=-6 * math.sin(w) * (arm_swing / 40))
        r.rot("Torso", x=lean, z=8 * math.sin(w) * (arm_swing / 40))
        r.rot("Head", z=-4 * math.sin(w), x=-lean * 0.6)
        # each arm swings opposite to the leg on its side
        a_l, a_r = -arm_swing * math.sin(w), arm_swing * math.sin(w)
        if run:
            arm(r, "L", run_arm(a_l))
            arm(r, "R", run_arm(a_r))
        else:
            arm(r, "L", swing(a_l, bend=0.35))
            arm(r, "R", swing(a_r, bend=0.35))
        r.key(i)
    return r.end_action(loop=True)


def run_arm(deg):
    """Runner's arm: elbow bent ~90 degrees, pumping fore/aft."""
    a = math.radians(deg)
    return ((0.30, math.sin(a), -math.cos(a)), (0.22, 1.0, 0.05 + 0.5 * math.sin(a)))


def walk(r):
    return _gait(r, "walk", 24, half_stride=0.30, lift=0.20, bob=0.02, lean=3,
                 arm_swing=24, drop=0.075)


def run(r):
    return _gait(r, "run", 16, half_stride=0.55, lift=0.55, bob=0.07, lean=16,
                 arm_swing=48, drop=0.20, run=True)


def idle(r):
    r.begin_action("idle")
    for f in range(0, 61, 10):
        a = math.sin(2 * math.pi * f / 60)
        stand(r)
        r.move("Body", z=0.012 * a)
        r.rot("Torso", x=1.5 * a)
        r.rot("Head", z=3 * math.sin(2 * math.pi * f / 60 * 2), x=-1.5 * a)
        r.key(f)
    return r.end_action(loop=True)


ALL = [idle, walk, run, jump, sit_down, stand_up, wave, crouch, punch, cheer]

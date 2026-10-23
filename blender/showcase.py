"""Render one continuous performance: idle -> walk -> curve -> stop -> sit -> stand ->
jump -> walk -> run -> slow -> walk -> stop -> wave.

    python blender/showcase.py                 # full video (output/showcase/showcase.mp4 + .gif)
    python blender/showcase.py --stills 14     # quick check: N stills along the timeline
"""
import argparse
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from anim_lab import actions, humanoid, mocap_sequencer, retarget, rig as rigmod, scene, sequencer, swap  # noqa: E402

DURATION = 36.8
T_SIT = 10.8
LIB = os.path.join(HERE, "assets", "animations", "UAL1_Standard.glb")


def choreography(perf):
    # Speeds are in reference units / s (walk ~1.2, run ~3.3); negative = walking backwards.
    perf.speed((0, 0), (1.2, 0), (2.1, 1.2), (5.6, 1.2), (7.6, 0.0),                # walk up, stop short of the chair
               (9.4, 0.0), (9.7, -0.6), (10.4, -0.6), (10.7, 0.0),                     # after turning: step back to the seat
               (19.1, 0.0), (20.0, 1.2), (22.6, 1.2), (24.4, 3.3),                     # walk away, speed up
               (27.1, 3.3), (29.1, 1.2), (30.6, 1.2), (32.1, 0.0))                     # run, slow, walk, stop
    perf.heading((0, 0.0), (7.7, 0.0), (9.3, math.pi))   # pivot 180 deg on the spot: chair is now behind
    perf.seg(0.0, "loco")
    perf.seg(T_SIT, "clip", "sit_down")
    perf.seg(T_SIT + 1.92, "sitidle")
    perf.seg(T_SIT + 3.5, "clip", "stand_up")
    perf.seg(T_SIT + 5.34, "loco")
    perf.seg(T_SIT + 6.1, "clip", "jump")
    perf.seg(T_SIT + 8.14, "loco")
    perf.seg(T_SIT + 21.7, "clip", "wave")
    perf.seg(T_SIT + 23.7, "loco")


def choreography_mocap(perf):
    """Timeline for the retargeted-clip performance. Speeds are in m/s."""
    t_sit, t_stand, t_jump, t_away, t_wave = 11.1, 14.0, 15.7, 18.7, 33.7
    perf.speed((0, 0), (1.2, 0), (2.6, 0.8), (5.8, 0.8), (7.4, 0.0),                 # walk up, stop short of the chair
               (9.6, 0.0), (9.9, -0.5), (10.5, -0.5), (10.8, 0.0),                     # after turning: back up to the seat
               (t_away, 0), (t_away + 1.0, 0.8), (t_away + 3.2, 0.8), (t_away + 6.0, 4.95),
               (t_away + 9.0, 4.95), (t_away + 11.5, 0.8), (t_away + 13.0, 0.8), (t_away + 14.8, 0.0))
    perf.heading((0, 0.0), (7.5, 0.0), (9.3, math.pi))                                # pivot: chair is behind
    perf.seg(0.0, "loco")
    perf.seg(t_sit, "clip", "mc_sit_down")
    perf.seg(t_sit + 31 / 24, "sitidle")
    perf.seg(t_stand, "clip", "mc_stand_up")
    perf.seg(t_stand + 1.0, "loco")
    t_hop_end = perf.add_jump(t_jump)                                                 # standing hop
    perf.seg(t_hop_end, "loco")
    perf.seg(t_wave, "clip", "wave")
    perf.seg(t_wave + 2.0, "loco")
    return t_sit


def smooth_track(track, fps, sigma=0.9):
    """Gaussian-smooth the camera target so it glides instead of tracking every step."""
    k = int(sigma * fps * 3)
    w = [math.exp(-0.5 * (i / (sigma * fps)) ** 2) for i in range(-k, k + 1)]
    out = []
    for i in range(len(track)):
        sx = sy = sw = 0.0
        for j, wt in zip(range(i - k, i + k + 1), w):
            jj = min(max(j, 0), len(track) - 1)
            sx += track[jj][0] * wt
            sy += track[jj][1] * wt
            sw += wt
        out.append((sx / sw, sy / sw))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", default="rain")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "output", "showcase"))
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=450)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--stills", type=int, help="render only N evenly spaced stills (quick check)")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--procedural", action="store_true", help="use the hand-written procedural motion instead of the retargeted clips")
    ap.add_argument("--times", type=float, nargs="*", help="render stills at these times (s)")
    ap.add_argument("--az", type=float, default=58, help="camera azimuth in degrees (90 = pure side view)")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    frames_dir = os.path.join(out, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    scene.reset_scene()
    profile = humanoid.load_profile(os.path.join(HERE, "characters", f"{args.character}.json"))
    arm = humanoid.build_armature(profile)
    swap.load_and_bind(profile, arm, HERE)
    r = rigmod.find_rig()
    use_mocap = not args.procedural and os.path.exists(LIB)
    if use_mocap:
        clips = retarget.add_clips(r, LIB)
        clips["wave"] = actions.wave(r)
        perf = mocap_sequencer.MocapPerformance(r, clips)
        perf.fps = args.fps
        t_sit = choreography_mocap(perf)
    else:
        clips = {fn.__name__: fn(r) for fn in actions.ALL}
        perf = sequencer.Performance(r, clips)
        perf.fps = args.fps
        choreography(perf)
        t_sit = T_SIT
    track = perf.bake(DURATION)
    n = len(track)

    # world dressing: floor, chair at the spot where the character sits, lights
    sit_frame = int(t_sit * args.fps)
    sx, sy, syaw = track[sit_frame]
    scene.add_ground()
    if use_mocap:
        hip = perf.sit_geometry()                      # where the pelvis lands when seated (armature-local)
        holder = scene.add_chair(r.unit, hip.z - 0.10, (hip.y - 0.13, hip.y + 0.40))
    else:
        holder = scene.add_chair(r.unit, r.seat_z)
    holder.location = (arm.location.x + sx, arm.location.y + sy, 0) if use_mocap else (sx, sy + arm.location.y, 0)
    holder.rotation_euler = (0, 0, syaw)
    scene.setup_lighting()

    # smoothed follow camera
    H = profile["height"]
    cam = bpy.data.objects.new("ShowCam", bpy.data.cameras.new("ShowCam"))
    bpy.context.scene.collection.objects.link(cam)
    cam.data.lens = 35
    bpy.context.scene.camera = cam
    target = smooth_track([(t[0], t[1]) for t in track], args.fps)
    az, el, dist = math.radians(args.az), math.radians(9), 3.6 * H
    for f, (tx, ty) in enumerate(target):
        look = Vector((tx, ty, 0.52 * H))
        cam.location = look + Vector((dist * math.sin(az) * math.cos(el),
                                      -dist * math.cos(az) * math.cos(el), dist * math.sin(el)))
        cam.rotation_euler = (look - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam.keyframe_insert("location", frame=f)
        cam.keyframe_insert("rotation_euler", frame=f)

    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 0, n - 1
    scene.render_settings(args.width, args.height, args.samples)
    sc.cycles.use_denoising = True
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "showcase.blend"))
    print(f"baked {n} frames, {n / args.fps:.1f}s; chair at ({sx:.2f},{sy:.2f}) yaw {math.degrees(syaw):.0f}")
    if args.no_render:
        return

    frames = range(n)
    if args.times:
        frames = [min(int(t * args.fps), n - 1) for t in args.times]
        args.stills = len(frames)
    elif args.stills:
        frames = [round(i * (n - 1) / (args.stills - 1)) for i in range(args.stills)]
    for f in frames:
        path = os.path.join(frames_dir, f"f{f:05d}.png")
        if os.path.exists(path):
            continue
        sc.frame_set(f)
        scene.render_frame(path)
        print("frame", f, flush=True)
    if args.stills:
        return

    mp4 = os.path.join(out, "showcase.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i",
                    os.path.join(frames_dir, "f%05d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", mp4], check=True)
    gif = os.path.join(out, "showcase.gif")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i",
                    os.path.join(frames_dir, "f%05d.png"), "-vf",
                    "fps=12,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", gif],
                   check=True)
    print("wrote", mp4, gif)


if __name__ == "__main__":
    main()

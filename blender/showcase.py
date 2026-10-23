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

from anim_lab import actions, humanoid, rig as rigmod, scene, sequencer, swap  # noqa: E402

DURATION = 34.5
T_SIT = 9.7


def choreography(perf):
    # speeds are in reference units / s (walk ~1.2, run ~3.3)
    perf.speed((0, 0), (1.2, 0), (2.1, 1.2), (5.6, 1.2), (7.6, 0.0),                # walk up to the chair, stop
               (18.0, 0.0), (18.9, 1.2), (21.5, 1.2), (23.3, 3.3),                     # walk away, speed up
               (26.0, 3.3), (28.0, 1.2), (29.5, 1.2), (31.0, 0.0))                     # run, slow, walk, stop
    perf.heading((0, 0.0), (7.7, 0.0), (9.3, math.pi))   # pivot 180 deg on the spot: chair is now behind
    perf.seg(0.0, "loco")
    perf.seg(T_SIT, "clip", "sit_down")
    perf.seg(11.62, "sitidle")
    perf.seg(13.2, "clip", "stand_up")
    perf.seg(15.04, "loco")
    perf.seg(15.8, "clip", "jump")
    perf.seg(17.84, "loco")
    perf.seg(31.4, "clip", "wave")
    perf.seg(33.4, "loco")


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
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    frames_dir = os.path.join(out, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    scene.reset_scene()
    profile = humanoid.load_profile(os.path.join(HERE, "characters", f"{args.character}.json"))
    arm = humanoid.build_armature(profile)
    swap.load_and_bind(profile, arm, HERE)
    r = rigmod.find_rig()
    clips = {fn.__name__: fn(r) for fn in actions.ALL}

    perf = sequencer.Performance(r, clips)
    perf.fps = args.fps
    choreography(perf)
    track = perf.bake(DURATION)
    n = len(track)

    # world dressing: floor, chair at the spot where the character sits, lights
    sit_frame = int(T_SIT * args.fps)
    sx, sy, syaw = track[sit_frame]
    scene.add_ground()
    holder = scene.add_chair(r.unit, r.seat_z)
    holder.location = (sx, sy + arm.location.y, 0)
    holder.rotation_euler = (0, 0, syaw)
    scene.setup_lighting()

    # smoothed follow camera
    H = profile["height"]
    cam = bpy.data.objects.new("ShowCam", bpy.data.cameras.new("ShowCam"))
    bpy.context.scene.collection.objects.link(cam)
    cam.data.lens = 35
    bpy.context.scene.camera = cam
    target = smooth_track([(t[0], t[1]) for t in track], args.fps)
    az, el, dist = math.radians(58), math.radians(9), 3.6 * H
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
    if args.stills:
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

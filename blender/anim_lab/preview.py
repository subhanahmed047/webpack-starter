"""Render contact sheets / GIFs of actions with headless Cycles (CPU)."""
import os

import bpy

from . import scene

# per-action camera framing: (zoom_out, target height as a fraction of body height)
FRAMING = {"jump": (1.45, 0.62)}
SIDE_VIEW = {"walk", "run", "jump", "sit_down", "stand_up", "crouch", "punch",
             "mc_walk", "mc_jog", "mc_sprint", "mc_sit_down", "mc_stand_up", "mc_sit_idle",
             "mc_jump_start", "mc_jump_land"}


def _frames(action, n):
    f0, f1 = int(action.frame_start), int(action.frame_end)
    if f1 - f0 + 1 <= n:
        return list(range(f0, f1 + 1))
    return [round(f0 + (f1 - f0) * i / (n - 1)) for i in range(n)]


def render_action(rig, action, outdir, n_frames=8, view=None, chair=None, size=(300, 420)):
    """Render `n_frames` stills of `action`; returns the list of PNG paths."""
    from PIL import Image  # only needed for previews

    os.makedirs(outdir, exist_ok=True)
    rig.obj.animation_data.action = action
    view = view or ("side" if action.name in SIDE_VIEW else "three_quarter")
    zoom, tz = FRAMING.get(action.name, (1.0, 0.5))
    scene.setup_camera(view, u=rig.unit, zoom_out=zoom, target_z=tz)
    scene.render_settings(*size)
    for o in bpy.data.objects:
        if o.name.startswith("Chair"):
            o.hide_render = action.name not in ("sit_down", "stand_up", "mc_sit_down", "mc_stand_up", "mc_sit_idle")
    paths = []
    for f in _frames(action, n_frames):
        bpy.context.scene.frame_set(f)
        p = os.path.join(outdir, f"{action.name}_{f:03d}.png")
        scene.render_frame(p)
        paths.append(p)
    return paths


def contact_sheet(paths, out_png, cols=4):
    from PIL import Image

    ims = [Image.open(p).convert("RGB") for p in paths]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows))
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out_png)
    return out_png


def gif(paths, out_gif, ms=90):
    from PIL import Image

    ims = [Image.open(p).convert("P", palette=Image.ADAPTIVE) for p in paths]
    ims[0].save(out_gif, save_all=True, append_images=ims[1:], duration=ms, loop=0)

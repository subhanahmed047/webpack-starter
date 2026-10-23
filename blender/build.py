"""Build an animated character .blend (+ .glb) from a profile and (optionally) a mesh.

    python blender/build.py                         # Rain, all animations
    python blender/build.py --preview sit_down walk # also render contact sheets
    python blender/build.py --character superhero_male
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from anim_lab import actions, humanoid, preview, rig as rigmod, scene, swap  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", default="rain", help="profile name in blender/characters/")
    ap.add_argument("--mesh", help="override the profile's mesh with another file (.fbx/.glb/.obj/.blend)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "output"))
    ap.add_argument("--preview", nargs="*", help="render contact sheets for these actions (none given = all)")
    ap.add_argument("--only", nargs="*", help="build only these actions")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    scene.reset_scene()
    profile = humanoid.load_profile(os.path.join(HERE, "characters", f"{args.character}.json"))
    arm = humanoid.build_armature(profile)
    if args.mesh:
        swap.load_and_bind(profile, arm, HERE, [os.path.abspath(args.mesh)])
    else:
        swap.load_and_bind(profile, arm, HERE)

    r = rigmod.find_rig()
    built = {}
    for fn in actions.ALL:
        if args.only and fn.__name__ not in args.only:
            continue
        built[fn.__name__] = fn(r)
        print(f"  action {fn.__name__:10s} frames {built[fn.__name__].frame_start:.0f}-{built[fn.__name__].frame_end:.0f}")

    scene.add_ground()
    scene.add_chair(r.unit, r.seat_z)
    scene.setup_lighting()

    if args.preview is not None:
        names = args.preview or list(built)
        for n in names:
            paths = preview.render_action(r, built[n], os.path.join(out, "frames", args.character))
            preview.contact_sheet(paths, os.path.join(out, f"{args.character}_{n}.png"))
            print("  sheet", n)

    if not args.no_export:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, f"{args.character}_animated.blend"))
        # glTF export: every action becomes a clip; IK is baked into plain bone motion
        for a in built.values():
            a.use_fake_user = True
        bpy.ops.export_scene.gltf(
            filepath=os.path.join(out, f"{args.character}_animated.glb"),
            export_format="GLB", export_animation_mode="ACTIONS",
            export_force_sampling=True, export_apply=False,
            use_visible=True, export_yup=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

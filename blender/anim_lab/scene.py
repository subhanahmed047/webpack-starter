"""Scene helpers: load the character, add ground/chair/lights, render stills."""
import math
import os

import bpy
from mathutils import Vector

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

# Chair geometry in armature units (character faces -Y; chair sits behind it at +Y).
SEAT_Z = 0.58
SEAT_Y = (-0.1, 0.8)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False


def _material(name, rgba):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = rgba
    m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
    return m


def _box(name, center, size, mat):
    bpy.ops.mesh.primitive_cube_add(location=center)
    o = bpy.context.active_object
    o.name = name
    o.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    o.data.materials.append(mat)
    return o


def add_ground(checker=True):
    """Big floor. A checkerboard (1 m tiles) makes locomotion readable on camera."""
    bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 0, 0))
    o = bpy.context.active_object
    o.name = "Ground"
    m = bpy.data.materials.new("Ground")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.9
    if checker:
        tex = nt.nodes.new("ShaderNodeTexChecker")
        tex.inputs["Scale"].default_value = 60.0  # 120 m plane / 60 = 1 m per square pair
        tex.inputs["Color1"].default_value = (0.55, 0.60, 0.68, 1)
        tex.inputs["Color2"].default_value = (0.40, 0.45, 0.54, 1)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (0.32, 0.36, 0.42, 1)
    o.data.materials.append(m)
    return o


def add_chair(u=1.0, seat_z=None):
    wood = _material("Chair", (0.55, 0.33, 0.16, 1))
    y0, y1 = SEAT_Y[0] * u, SEAT_Y[1] * u
    sz = seat_z if seat_z is not None else SEAT_Z * u
    cy, depth = (y0 + y1) / 2, y1 - y0
    t = 0.08 * u
    parts = [_box("Chair.Seat", (0, cy, sz - t / 2), (1.0 * u, depth, t), wood),
             _box("Chair.Back", (0, y1 - t / 2, sz + 0.55 * u), (1.0 * u, t, 1.1 * u), wood)]
    for sx in (-1, 1):
        for y in (y0 + 0.06 * u, y1 - 0.06 * u):
            parts.append(_box("Chair.Leg", (sx * 0.44 * u, y, (sz - t) / 2),
                              (t, t, sz - t), wood))
    holder = bpy.data.objects.new("ChairHolder", None)
    bpy.context.scene.collection.objects.link(holder)
    for p in parts:
        p.parent = holder
    return holder


def setup_lighting(world_strength=0.9):
    sc = bpy.context.scene
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.62, 0.72, 0.85, 1)
    bg.inputs["Strength"].default_value = world_strength
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4.0
    so = bpy.data.objects.new("Sun", sun)
    so.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))
    sc.collection.objects.link(so)


def setup_camera(view="three_quarter", u=1.0, lens=50, zoom_out=1.0, target_z=0.5):
    H = 2.76 * u  # character height
    target, distance = (0, 0, target_z * H), 2.15 * H * zoom_out
    sc = bpy.context.scene
    cam = bpy.data.objects.get("AnimLabCamera")
    if cam is None:
        cam = bpy.data.objects.new("AnimLabCamera", bpy.data.cameras.new("AnimLabCamera"))
        sc.collection.objects.link(cam)
    cam.data.lens = lens
    az, el = {"three_quarter": (-35, 12), "side": (90, 5), "front": (0, 8)}[view]
    az, el = math.radians(az), math.radians(el)
    # azimuth 0 = in front of the character (-Y); positive azimuth swings toward +X.
    t = Vector(target)
    cam.location = t + Vector((distance * math.sin(az) * math.cos(el),
                               -distance * math.cos(az) * math.cos(el),
                               distance * math.sin(el)))
    cam.rotation_euler = (t - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    return cam


def render_settings(width=360, height=520, samples=24):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"          # CPU-only container: no EGL for EEVEE/Workbench
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = width, height
    sc.render.resolution_percentage = 100
    sc.render.fps = 24
    sc.view_settings.view_transform = "Standard"


def render_frame(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

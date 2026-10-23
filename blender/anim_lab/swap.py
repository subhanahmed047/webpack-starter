"""Swap in any humanoid character: import its mesh, drop its own armature, bind the
mesh to the canonical rig with automatic weights. All actions then work on it."""
import os

import bpy


def import_character_mesh(path, exclude=()):
    """Import a .fbx/.glb/.gltf/.obj/.blend and return its mesh objects (armatures removed)."""
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".blend":
        imgs_before = set(bpy.data.images)
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = [n for n in src.objects]
        for o in dst.objects:
            if o is not None:
                bpy.context.scene.collection.objects.link(o)
        # relative '//textures/..' paths were relative to the source file, not this scene
        for img in set(bpy.data.images) - imgs_before:
            base = os.path.basename(img.filepath)
            cand = os.path.join(os.path.dirname(path), "textures", base)
            if base and os.path.exists(cand.replace("<UDIM>", "1001")):
                img.filepath = cand
                if img.source == "TILED":
                    img.reload()
    else:
        raise ValueError(f"Unsupported character format: {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH" and not any(x in o.name for x in exclude)]
    for o in new:
        if o not in meshes:
            bpy.data.objects.remove(o)
    for m in meshes:  # strip the old skinning; world transform is kept
        mw = m.matrix_world.copy()
        m.parent = None
        m.matrix_world = mw
        m.vertex_groups.clear()
        for mod in [x for x in m.modifiers if x.type == "ARMATURE"]:
            m.modifiers.remove(mod)
    return meshes


def bind_meshes(meshes, arm):
    """Automatic-weight skinning of `meshes` to the canonical armature."""
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    for m in meshes:
        fill_unweighted(m)


def fill_unweighted(mesh_obj):
    """Heat-weighting can skip stray islands (laces, eyelets...). Give each connected
    unweighted island the weights of the weighted vertex nearest its centre, so it moves
    rigidly with the body part it sits on instead of being left behind."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    me = mesh_obj.data
    deform = {g.index for g in mesh_obj.vertex_groups}
    has_w = [any(g.group in deform and g.weight > 1e-4 for g in v.groups) for v in me.vertices]
    bare = [i for i, w in enumerate(has_w) if not w]
    if not bare or all(not w for w in has_w):
        return 0
    adj = {i: [] for i in bare}
    for e in me.edges:
        a, b = e.vertices
        if not has_w[a] and not has_w[b]:
            adj[a].append(b)
            adj[b].append(a)
    tree = KDTree(len(me.vertices) - len(bare))
    for i, w in enumerate(has_w):
        if w:
            tree.insert(me.vertices[i].co, i)
    tree.balance()
    seen, islands = set(), 0
    for start in bare:
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            comp.append(v)
            for n in adj[v]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        centre = sum((me.vertices[v].co for v in comp), Vector()) / len(comp)
        _, src, _ = tree.find(centre)
        for g in me.vertices[src].groups:
            mesh_obj.vertex_groups[g.group].add(comp, g.weight, "REPLACE")
        islands += 1
    print(f"  {mesh_obj.name}: {len(bare)} unweighted vertices in {islands} islands attached rigidly")
    return len(bare)


def bind_rigid(meshes, arm, bone):
    """Attach small rigid parts (eyes, hair...) 100% to one bone."""
    for m in meshes:
        m.vertex_groups.new(name=bone).add(range(len(m.data.vertices)), 1.0, "REPLACE")
        m.parent = arm
        mod = m.modifiers.new("Armature", "ARMATURE")
        mod.object = arm


def bind_feet(mesh_obj, profile, zone):
    """Make the foot part of `mesh_obj` follow the Ankle bones.

    Vertices lower than `ankle_z + zone[0]*height` go 100% to the Ankle bone of their side;
    up to `zone[1]*height` they blend into whatever weights heat-mapping gave them.
    """
    H, az = profile["height"], profile["ankle_z"]
    lo, hi = az + zone[0] * H, az + zone[1] * H
    me = mesh_obj.data
    groups = mesh_obj.vertex_groups
    for v in me.vertices:
        z = v.co.z
        if z >= hi:
            continue
        b = 1.0 if z <= lo else (hi - z) / max(hi - lo, 1e-6)
        ankle = groups.get("Ankle.L" if v.co.x > 0 else "Ankle.R")
        for g in list(v.groups):
            if groups[g.group] is not ankle:
                groups[g.group].add([v.index], g.weight * (1.0 - b), "REPLACE")
        ankle.add([v.index], b, "REPLACE")


def bend_hanging(obj, root_angle, tip_angle, n_bones):
    """Re-shape a straight hair tube (running along +Y) so it hangs down under gravity.

    The centre line leaves the head at `root_angle` degrees below horizontal and curls to
    `tip_angle`. Returns (bone_points, t_per_vertex) for building a bone chain + weights.
    """
    import math

    from mathutils import Vector

    me = obj.data
    ys = [v.co.y for v in me.vertices]
    y0, y1 = min(ys), max(ys)
    length = y1 - y0
    root = [v.co for v in me.vertices if v.co.y < y0 + 0.05]
    cx = sum(c.x for c in root) / len(root)
    cz = sum(c.z for c in root) / len(root)
    a0, a1 = math.radians(root_angle), math.radians(tip_angle)
    phi = lambda t: a0 + (a1 - a0) * t  # noqa: E731
    steps = 64
    pts = [Vector((cx, y0, cz))]
    for i in range(steps):
        ph = phi((i + 0.5) / steps)
        pts.append(pts[-1] + Vector((0, math.cos(ph), -math.sin(ph))) * (length / steps))

    def centre(t):
        k = min(t * steps, steps - 1e-6)
        i = int(k)
        return pts[i].lerp(pts[i + 1], k - i)

    ts = []
    for v in me.vertices:
        t = min(max((v.co.y - y0) / length, 0.0), 1.0)
        ph = phi(t)
        dx, dz = v.co.x - cx, v.co.z - cz
        v.co = centre(t) + Vector((dx, 0, 0)) + Vector((0, math.sin(ph), math.cos(ph))) * dz
        ts.append(t)
    me.update()
    return [centre(i / n_bones) for i in range(n_bones + 1)], ts


def bind_chain(obj, arm, prefix, n_bones, ts):
    """Weight each vertex to the bones of chain `prefix` by its position along the chain."""
    groups = [obj.vertex_groups.new(name=f"{prefix}.{i + 1}") for i in range(n_bones)]
    for vi, t in enumerate(ts):
        s = t * n_bones
        for i, g in enumerate(groups):
            w = max(0.0, 1.0 - abs(s - (i + 0.5)))
            if i == 0 and s < 0.5:
                w = 1.0
            if i == n_bones - 1 and s > n_bones - 0.5:
                w = 1.0
            if w > 1e-3:
                g.add([vi], w, "REPLACE")
    obj.parent = arm
    obj.modifiers.new("Armature", "ARMATURE").object = arm


def load_and_bind(profile, arm, base_dir, mesh_paths=None):
    """Import every mesh listed in the profile and skin it to `arm`."""
    from . import humanoid

    smooth, rigid, pony = [], [], None
    spec = profile.get("ponytail")
    for rel in mesh_paths or profile["meshes"]:
        for m in import_character_mesh(os.path.join(base_dir, rel), profile.get("exclude", ())):
            if spec and spec["mesh"] in m.name:
                pony = m
            elif any(k in m.name for k in profile.get("rigid_head", ())):
                rigid.append(m)
            else:
                smooth.append(m)
    bind_meshes(smooth, arm)
    for m in smooth:
        for key, zone in profile.get("foot_meshes", {}).items():
            if key in m.name:
                bind_feet(m, profile, zone)
    bind_rigid(rigid, arm, "Head")
    if pony is not None:
        n = spec.get("bones", 3)
        pts, ts = bend_hanging(pony, spec["root_angle"], spec["tip_angle"], n)
        off = arm.location
        humanoid.add_chain(arm, "Ponytail", [p - off for p in pts], "Head")  # after auto-weights
        bind_chain(pony, arm, "Ponytail", n, ts)
        rigid.append(pony)
    return smooth + rigid

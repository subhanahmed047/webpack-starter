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


def load_and_bind(profile, arm, base_dir, mesh_paths=None):
    """Import every mesh listed in the profile and skin it to `arm`."""
    smooth, rigid = [], []
    for rel in mesh_paths or profile["meshes"]:
        for m in import_character_mesh(os.path.join(base_dir, rel), profile.get("exclude", ())):
            (rigid if any(k in m.name for k in profile.get("rigid_head", ())) else smooth).append(m)
    bind_meshes(smooth, arm)
    bind_rigid(rigid, arm, "Head")
    return smooth + rigid

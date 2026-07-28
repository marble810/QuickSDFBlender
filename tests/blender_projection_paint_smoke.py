"""Interactive Blender 5.1/5.2 regression for a real 3D Projection Paint stroke.

Unlike array-level Smart Paint tests, this drives Quick SDF's public paint
macro with ``PAINT_OT_image_paint`` stroke elements in a VIEW_3D region. Two
overlapping material shells reproduce avatar face overlays. Quick SDF must
paint only the artist-selected Face Area without inferring intent from names,
surface area, or the active material.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402


RESULT_PATH = ROOT / "build" / "projection_paint_smoke_result.txt"
ACTIVE_SAVE_PATH = ROOT / "build" / "projection_paint_active_save.blend"
ACTIVE_SAVE_COPY_PATH = ROOT / "build" / "projection_paint_active_save_copy.blend"
STATE: dict[str, object] = {"waits": 0, "phase": "OPENING"}


def _plane() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    vertices = (
        (-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0),
        (-0.5, -0.5, 0.01), (0.5, -0.5, 0.01), (0.5, 0.5, 0.01), (-0.5, 0.5, 0.01),
        (-0.1, -0.1, -0.01), (0.1, -0.1, -0.01), (0.1, 0.1, -0.01), (-0.1, 0.1, -0.01),
    )
    mesh = bpy.data.meshes.new("Quick SDF Layered Face Mesh")
    mesh.from_pydata(
        vertices,
        (),
        ((0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11)),
    )
    mesh.polygons[0].material_index = 0
    mesh.polygons[1].material_index = 1
    mesh.polygons[2].material_index = 0
    obj = bpy.data.objects.new("Quick SDF Layered Face", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    uv_layer = mesh.uv_layers.new(name="PaintUV")
    wrong_uv = mesh.uv_layers.new(name="WrongUV")
    for polygon in mesh.polygons:
        u0, u1 = ((0.05, 0.45) if polygon.material_index == 0 else (0.55, 0.95))
        coordinates = ((u0, 0.05), (u1, 0.05), (u1, 0.95), (u0, 0.95))
        for loop_index, uv in zip(polygon.loop_indices, coordinates, strict=True):
            uv_layer.data[loop_index].uv = uv
            wrong_uv.data[loop_index].uv = (1.0 - uv[0], uv[1])
    mesh.uv_layers.active = uv_layer
    for name in ("Surface A", "Surface B"):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        material.node_tree.nodes.clear()
        obj.data.materials.append(material)
    obj.active_material_index = 1
    mesh.use_paint_mask = False
    mesh.polygons[0].select = False
    mesh.polygons[1].select = True
    mesh.polygons[2].select = True
    mesh.polygons[2].hide = True
    return obj


def _check_ambiguous_operator(runtime) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.object
    obj.name = "Quick SDF Ambiguous Surface"
    for name in ("Material One", "Material Two"):
        material = bpy.data.materials.new(name)
        obj.data.materials.append(material)
    for index, polygon in enumerate(obj.data.polygons):
        polygon.material_index = index % 2
    assert runtime.material_slot_candidates(obj) == (0, 1)
    scene = bpy.context.scene
    scene.quick_sdf_settings.resolution = 512
    scene.quick_sdf_settings.initialization = "WHITE"
    try:
        bpy.ops.quicksdf.project_create()
    except RuntimeError as error:
        assert "select Face Area polygons" in str(error)
    else:
        raise AssertionError("Ambiguous material selection must require an explicit slot")
    assert len(scene.quick_sdf_projects) == 0
    assert bpy.ops.quicksdf.project_create(material_slot="1") == {"FINISHED"}
    project = runtime.active_project(scene)
    assert project is not None
    assert runtime.face_region_material_slots(project) == (1,)
    region = runtime.face_region_mask(project)
    assert all(bool(region[index]) == (polygon.material_index == 1) for index, polygon in enumerate(obj.data.polygons))
    assert bpy.ops.quicksdf.project_remove() == {"FINISHED"}


def _check_explicit_face_area(runtime) -> None:
    import bmesh

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new("Quick SDF Shared Body Mesh")
    mesh.from_pydata(
        ((-1.0, 0.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0),
         (-1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        (),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
    )
    obj = bpy.data.objects.new("Quick SDF Shared Face And Body", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    material = bpy.data.materials.new("One Shared Material")
    mesh.materials.append(material)
    uv_layer = mesh.uv_layers.new(name="FaceBodyUV")
    for polygon_index, polygon in enumerate(mesh.polygons):
        u0, u1 = ((0.05, 0.45) if polygon_index == 0 else (0.55, 0.95))
        coordinates = ((u0, 0.05), (u1, 0.05), (u1, 0.95), (u0, 0.95))
        for loop_index, uv in zip(polygon.loop_indices, coordinates, strict=True):
            uv_layer.data[loop_index].uv = uv
    modifier = obj.modifiers.new("Subdivision carries Face Area", "SUBSURF")
    modifier.levels = 1
    bpy.ops.object.mode_set(mode="EDIT")
    edit_mesh = bmesh.from_edit_mesh(mesh)
    edit_mesh.faces.ensure_lookup_table()
    for face in edit_mesh.faces:
        face.select = face.index == 0
    bmesh.update_edit_mesh(mesh)
    scene = bpy.context.scene
    scene.quick_sdf_settings.resolution = 512
    scene.quick_sdf_settings.initialization = "NORMAL_GUIDE"
    assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
    project = runtime.active_project(scene)
    assert project is not None
    bpy.ops.object.mode_set(mode="OBJECT")
    np.testing.assert_array_equal(runtime.face_region_mask(project), (True, False))
    assert runtime.face_region_material_slots(project) == (0,)
    uv_mask = runtime.face_region_uv_mask(project)
    assert bool(uv_mask[256, 128])
    assert not bool(uv_mask[256, 384])
    attribute_name = str(project.face_region_attribute_name)
    assert mesh.attributes.get(attribute_name) is not None
    assert bpy.ops.quicksdf.project_remove() == {"FINISHED"}
    assert mesh.attributes.get(attribute_name) is None


def _check_overlapping_shared_material_face_area_rejected(runtime) -> None:
    """A texture cannot isolate selected faces from overlapping body UVs."""

    import bmesh

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new("Quick SDF Overlapping Shared Material Mesh")
    mesh.from_pydata(
        (
            (-1.0, 0.0, 1.0),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
            (-1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        (),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
    )
    obj = bpy.data.objects.new("Quick SDF Overlapping Face And Body", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    material = bpy.data.materials.new("Quick SDF Shared Face Body Material")
    material.use_nodes = True
    mesh.materials.append(material)
    uv_layer = mesh.uv_layers.new(name="OverlappingFaceBodyUV")
    coordinates = ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9))
    for polygon in mesh.polygons:
        for loop_index, uv in zip(polygon.loop_indices, coordinates, strict=True):
            uv_layer.data[loop_index].uv = uv

    bpy.ops.object.mode_set(mode="EDIT")
    edit_mesh = bmesh.from_edit_mesh(mesh)
    edit_mesh.faces.ensure_lookup_table()
    for face in edit_mesh.faces:
        face.select = face.index == 0
    bmesh.update_edit_mesh(mesh)
    scene = bpy.context.scene
    scene.quick_sdf_settings.resolution = 512
    scene.quick_sdf_settings.initialization = "WHITE"
    try:
        result = bpy.ops.quicksdf.project_create()
    except RuntimeError as error:
        assert "overlaps body UVs" in str(error)
    else:
        assert result == {"CANCELLED"}, result

    assert len(scene.quick_sdf_projects) == 0
    assert runtime.active_project(scene) is None
    assert not any(
        str(attribute.name).startswith(".qsdf_region_")
        for attribute in mesh.attributes
    ), "Failed project creation leaked its persistent Face Area attribute"
    assert obj.mode == "EDIT"
    bpy.ops.object.mode_set(mode="OBJECT")


def _check_multi_material_face_area_preview(runtime) -> None:
    """An explicit Face Area may span slots and preview must restore all of them."""

    import bmesh
    from quick_sdf_blender import preview

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new("Quick SDF Multi Material Face Area Mesh")
    mesh.from_pydata(
        (
            (-1.0, 0.0, 1.0),
            (0.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
            (-1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        (),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
    )
    mesh.polygons[0].material_index = 0
    mesh.polygons[1].material_index = 1
    obj = bpy.data.objects.new("Quick SDF Two Slot Face Area", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    originals = []
    for name in ("Quick SDF Face Skin", "Quick SDF Face Detail"):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        mesh.materials.append(material)
        originals.append(material)
    uv_layer = mesh.uv_layers.new(name="MultiSlotFaceUV")
    for polygon_index, polygon in enumerate(mesh.polygons):
        u0, u1 = ((0.05, 0.45) if polygon_index == 0 else (0.55, 0.95))
        coordinates = ((u0, 0.05), (u1, 0.05), (u1, 0.95), (u0, 0.95))
        for loop_index, uv in zip(polygon.loop_indices, coordinates, strict=True):
            uv_layer.data[loop_index].uv = uv

    bpy.ops.object.mode_set(mode="EDIT")
    edit_mesh = bmesh.from_edit_mesh(mesh)
    for face in edit_mesh.faces:
        face.select = True
    bmesh.update_edit_mesh(mesh)
    scene = bpy.context.scene
    scene.quick_sdf_settings.resolution = 512
    scene.quick_sdf_settings.initialization = "WHITE"
    assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
    project = runtime.active_project(scene)
    assert project is not None
    bpy.ops.object.mode_set(mode="OBJECT")
    assert runtime.face_region_material_slots(project) == (0, 1)
    np.testing.assert_array_equal(runtime.face_region_mask(project), (True, True))

    original_links = tuple(slot.link for slot in obj.material_slots)
    canvas = runtime.resolve_display_image(project, runtime.active_angle(project))
    assert canvas is not None
    preview.assign_preview_material(project, canvas)
    assert bool(project.material_override_active)
    assert tuple(int(state.slot_index) for state in project.preview_slots) == (0, 1)
    for index, original in enumerate(originals):
        assigned = obj.material_slots[index].material
        assert assigned is not None and assigned != original
        state = next(
            item for item in project.preview_slots if int(item.slot_index) == index
        )
        assert assigned == bpy.data.materials.get(str(state.preview_material_name))
        assert state.original_material == original

    assert preview.restore_preview_materials(project) == 2
    assert not bool(project.material_override_active)
    assert len(project.preview_slots) == 0
    for index, original in enumerate(originals):
        assert obj.material_slots[index].material == original
        assert obj.material_slots[index].link == original_links[index]
    assert bpy.ops.quicksdf.project_remove() == {"FINISHED"}


def _assert_preview_graph(obj, project, canvas) -> None:
    state = next(item for item in project.preview_slots if int(item.slot_index) == 0)
    material = obj.material_slots[0].material
    stored = bpy.data.materials.get(str(state.preview_material_name))
    assert material is not None and material == stored
    assert material != state.original_material
    nodes = material.node_tree.nodes
    assert nodes.get("QSDF Face Area") is not None
    assert nodes.get("QSDF Face Area Strength") is not None
    mix = nodes.get("QSDF Original Overlay")
    group = nodes.get("QSDF Preview")
    texture = nodes.get("QSDF Mask")
    assert mix is not None and group is not None and texture is not None
    assert texture.image == canvas
    assert len(mix.inputs[1].links) == 1
    assert len(mix.inputs[2].links) == 1
    assert mix.inputs[1].links[0].from_node.as_pointer() != mix.as_pointer()
    assert mix.inputs[2].links[0].from_node.as_pointer() == group.as_pointer()
    assert not any(
        link.from_node.as_pointer() == link.to_node.as_pointer()
        for link in material.node_tree.links
    )
    outputs = [
        node
        for node in nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and bool(getattr(node, "is_active_output", False))
    ]
    assert len(outputs) == 1
    surface_links = tuple(outputs[0].inputs["Surface"].links)
    assert len(surface_links) == 1
    assert surface_links[0].from_node.as_pointer() == mix.as_pointer()


def _finish(error: BaseException | None = None) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is None:
        RESULT_PATH.write_text("PASS", encoding="utf-8")
    else:
        RESULT_PATH.write_text(
            "".join(traceback.format_exception(error)), encoding="utf-8"
        )
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _check_active_save_restoration(obj: bpy.types.Object) -> None:
    ACTIVE_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_SAVE_PATH.unlink(missing_ok=True)
    ACTIVE_SAVE_COPY_PATH.unlink(missing_ok=True)
    assert bpy.ops.wm.save_as_mainfile(filepath=str(ACTIVE_SAVE_PATH)) == {"FINISHED"}

    # save_post must resume the live isolated paint surface.
    assert obj.data.use_paint_mask
    assert int(obj.active_material_index) == 0
    assert obj.data.uv_layers.active.name == "PaintUV"
    assert [bool(polygon.select) for polygon in obj.data.polygons] == [True, False, False]
    assert [bool(polygon.hide) for polygon in obj.data.polygons] == [False, False, True]

    # The serialized .blend itself must contain the artist's original state.
    shutil.copy2(ACTIVE_SAVE_PATH, ACTIVE_SAVE_COPY_PATH)
    with bpy.data.libraries.load(str(ACTIVE_SAVE_COPY_PATH), link=False) as (data_from, data_to):
        assert obj.name in data_from.objects
        data_to.objects = [obj.name]
    saved_obj = data_to.objects[0]
    assert saved_obj is not None
    saved_mesh = saved_obj.data
    try:
        assert not saved_mesh.use_paint_mask
        assert int(saved_obj.active_material_index) == 1
        assert saved_mesh.uv_layers.active.name == "WrongUV"
        assert [bool(polygon.select) for polygon in saved_mesh.polygons] == [False, True, True]
        assert [bool(polygon.hide) for polygon in saved_mesh.polygons] == [False, False, True]
    finally:
        bpy.data.objects.remove(saved_obj)
        if saved_mesh.users == 0:
            bpy.data.meshes.remove(saved_mesh)


def _paint() -> float | None:
    try:
        from quick_sdf_blender import preview, runtime, studio
        from quick_sdf_blender.tools import VIEW_TOOL_ID

        project = STATE["project"]
        obj = STATE["object"]
        if studio.current_session() is None:
            STATE["waits"] = int(STATE["waits"]) + 1
            if int(STATE["waits"]) > 200:
                raise AssertionError(
                    project.diagnostic_message or "Studio did not finish opening"
                )
            return 0.05

        view_area = next(
            area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D"
        )
        view_region = next(region for region in view_area.regions if region.type == "WINDOW")
        view_space = view_area.spaces.active

        if STATE["phase"] == "OPENING":
            assert obj.data.use_paint_mask
            assert not bool(getattr(obj.data, "use_paint_mask_vertex", False))
            assert int(obj.active_material_index) == 0
            assert obj.data.uv_layers.active.name == str(project.uv_map_name) == "PaintUV"
            assert [bool(polygon.select) for polygon in obj.data.polygons] == [True, False, False]
            assert [bool(polygon.hide) for polygon in obj.data.polygons] == [False, False, True]
            _check_active_save_restoration(obj)
            STATE["phase"] = "VIEW_READY"
            with bpy.context.temp_override(
                window=bpy.context.window,
                screen=bpy.context.window.screen,
                area=view_area,
                region=view_region,
            ):
                assert bpy.ops.view3d.view_axis(type="TOP", align_active=False) == {
                    "FINISHED"
                }
                assert bpy.ops.view3d.view_selected(use_all_regions=False) == {
                    "FINISHED"
                }
            # Let Blender update RegionView3D matrices before deriving the
            # screen-space stroke coordinates.
            return 0.5

        assert obj.mode == "TEXTURE_PAINT"
        assert view_space.shading.type == "MATERIAL"
        tool = bpy.context.workspace.tools.from_space_view3d_mode(
            "PAINT_TEXTURE", create=False
        )
        assert tool is not None and tool.idname == VIEW_TOOL_ID

        project.preview_mode = "MASK"
        project.paint_value = 0  # Shadow over the all-white initialization.
        canvas = runtime.resolve_display_image(project, runtime.active_angle(project))
        angle_item = runtime.active_angle(project)
        assert canvas is not None and angle_item is not None
        runtime.sync_canvas(bpy.context, project)
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas

        # Reusing a preview graph must never connect the Mix Shader to itself.
        preview.ensure_preview_material(project, canvas)
        preview.ensure_preview_material(project, canvas)
        assert bpy.ops.quicksdf.key_select(index=int(project.active_angle_index)) == {
            "FINISHED"
        }
        _assert_preview_graph(obj, project, canvas)

        brush = bpy.context.scene.tool_settings.image_paint.brush
        assert brush is not None, "Factory-startup Texture Paint has no active Brush Asset"
        brush.size = 96
        brush.strength = 1.0
        brush.blend = "MIX"

        before = runtime.image_rgba(canvas)
        coverage_before = runtime.coverage_mask(angle_item)
        assert np.all(before[..., :3] == 1.0)
        assert not np.any(coverage_before)

        center = view3d_utils.location_3d_to_region_2d(
            view_region,
            view_space.region_3d,
            obj.matrix_world.translation,
        )
        assert center is not None
        x, y = float(center.x), float(center.y)
        assert 24.0 < x < view_region.width - 24.0
        assert 24.0 < y < view_region.height - 24.0
        stroke = [
            {
                "name": "Quick SDF regression stroke",
                "location": (0.0, 0.0, 0.0),
                "mouse": (x - 8.0, y),
                "mouse_event": (x - 8.0, y),
                "pressure": 1.0,
                "size": 96.0,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
                "time": 0.0,
                "is_start": True,
            },
            {
                "name": "Quick SDF regression stroke",
                "location": (0.0, 0.0, 0.0),
                "mouse": (x + 8.0, y),
                "mouse_event": (x + 8.0, y),
                "pressure": 1.0,
                "size": 96.0,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
                "time": 0.1,
                "is_start": False,
            },
        ]
        with bpy.context.temp_override(
            window=bpy.context.window,
            screen=bpy.context.window.screen,
            area=view_area,
            region=view_region,
        ):
            assert bpy.ops.quicksdf.range_paint.poll()
            started = time.perf_counter()
            result = bpy.ops.quicksdf.range_paint(
                "EXEC_DEFAULT",
                PAINT_OT_image_paint={"stroke": stroke, "mode": "NORMAL"},
            )
            elapsed = time.perf_counter() - started
        assert result == {"FINISHED"}, result
        assert elapsed < 0.75, f"1024px paint macro took {elapsed:.3f}s"

        after = runtime.image_rgba(canvas)
        coverage_after = runtime.coverage_mask(angle_item)
        changed = np.any(np.abs(after[..., :3] - before[..., :3]) > (0.5 / 255.0), axis=2)
        assert np.any(changed)
        assert np.any(changed[:, : changed.shape[1] // 2])
        assert not np.any(changed[:, changed.shape[1] // 2 :])
        assert np.all(coverage_after[changed])
        assert np.any(after[..., 0] < 0.5)
        assert project.first_stroke_complete
        assert project.dirty
        assert bpy.ops.quicksdf.history_undo.poll()

        # The macro's final Canvas synchronization must not drop the visible
        # 3D material or leave the editor in Solid mode.
        assert view_space.shading.type == "MATERIAL"
        assert obj.mode == "TEXTURE_PAINT"
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        _assert_preview_graph(obj, project, canvas)

        assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
        restored = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(restored, before)
        assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
        redone = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(redone, after)
        assert view_space.shading.type == "MATERIAL"
        _assert_preview_graph(obj, project, canvas)

        # The typed history path records Coverage as a 1-bit plane at stroke
        # completion. Rebake must preserve both that footprint and its RGB.
        project.guide_shadow_amount = 0.0
        assert bpy.ops.quicksdf.bake_base() == {"FINISHED"}
        from quick_sdf_blender import operators

        bake_deadline = time.monotonic() + 15.0
        while operators._BAKE_JOB is not None and time.monotonic() < bake_deadline:
            delay = operators._poll_bake_job()
            if delay:
                time.sleep(min(float(delay), 0.02))
        assert operators._BAKE_JOB is None, "Rebake did not settle"
        if bpy.app.timers.is_registered(operators._poll_bake_job):
            bpy.app.timers.unregister(operators._poll_bake_job)
        rebaked = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(rebaked[..., :3][changed], after[..., :3][changed])
        coverage_rebaked = runtime.coverage_mask(angle_item)
        assert np.all(coverage_rebaked[changed])
        _assert_preview_graph(obj, project, canvas)

        print("PROJECTION_PAINT_CHANGED_PIXELS", int(np.count_nonzero(changed)))
        print("PROJECTION_PAINT_SECONDS", f"{elapsed:.4f}")
        obj.name = "Quick SDF Layered Face Renamed"
        obj.data.name = "Quick SDF Layered Face Mesh Renamed"
        assert bpy.ops.quicksdf.studio_exit() == {"FINISHED"}
        assert not obj.data.use_paint_mask
        assert int(obj.active_material_index) == 1
        assert obj.data.uv_layers.active.name == "WrongUV"
        assert [bool(polygon.select) for polygon in obj.data.polygons] == [False, True, True]
        assert [bool(polygon.hide) for polygon in obj.data.polygons] == [False, False, True]
        print("[Quick SDF projection paint smoke] PASS")
        _finish()
    except Exception as error:
        _finish(error)
    return None


def _run() -> None:
    try:
        RESULT_PATH.unlink(missing_ok=True)
        assert not bpy.app.background
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {
            "FINISHED"
        }
        from quick_sdf_blender import runtime

        _check_ambiguous_operator(runtime)
        _check_explicit_face_area(runtime)
        _check_overlapping_shared_material_face_area_rejected(runtime)
        _check_multi_material_face_area_preview(runtime)
        obj = _plane()
        scene = bpy.context.scene
        scene.quick_sdf_settings.resolution = 1024
        scene.quick_sdf_settings.initialization = "WHITE"
        assert bpy.ops.quicksdf.project_create(material_slot="0") == {"FINISHED"}
        project = runtime.active_project(scene)
        assert project is not None
        assert runtime.face_region_material_slots(project) == (0,)
        assert str(project.uv_map_name) == "PaintUV"
        obj.data.uv_layers.active = obj.data.uv_layers["WrongUV"]
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(object=obj, project=project)
        bpy.app.timers.register(_paint, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_run, first_interval=0.25)

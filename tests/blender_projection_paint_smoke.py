"""Interactive Blender 5.1/5.2 regression for a real 3D Projection Paint stroke.

Unlike array-level Smart Paint tests, this drives Quick SDF's public paint
macro with ``PAINT_OT_image_paint`` stroke elements in a VIEW_3D region. Two
overlapping material shells reproduce avatar face overlays: the saved active
slot is transparent detail, while Quick SDF must select and paint only the
underlying Face surface.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import time
import traceback
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402


RESULT_PATH = ROOT / "build" / "projection_paint_smoke_result.txt"
ACTIVE_SAVE_PATH = ROOT / "build" / "projection_paint_active_save.blend"
ACTIVE_SAVE_COPY_PATH = ROOT / "build" / "projection_paint_active_save_copy.blend"
STATE: dict[str, object] = {"waits": 0, "phase": "OPENING"}


def _check_material_suggestions(runtime) -> None:
    def suggestion(names, active, assignments):
        slots = tuple(
            SimpleNamespace(material=SimpleNamespace(name=name) if name else None)
            for name in names
        )
        polygons = tuple(
            SimpleNamespace(material_index=int(index)) for index in assignments
        )
        obj = SimpleNamespace(
            material_slots=slots,
            active_material_index=int(active),
            data=SimpleNamespace(polygons=polygons),
        )
        return runtime.suggest_material_slot(obj)

    assert suggestion(("Face", "Face_transparent", "Eye"), 1, (0, 0, 1, 1, 2)) == 0
    assert suggestion(("Face", "FaceTransparent"), 1, (0, 0, 1, 1)) == 0
    assert suggestion(("顔", "顔透明"), 1, (0, 0, 1, 1)) == 0
    assert suggestion(("Skin_Head", "Skin_Body"), 0, (0, 0, 1, 1, 1)) == 0
    assert suggestion(("Unused Face", "Face_transparent"), 1, (1, 1)) == 1
    assert suggestion(("Torso", "Eye"), 1, (0, 0, 0, 1)) == 1
    assert suggestion((None, "Face.001"), 0, (0, 1, 1)) == 1


def _plane() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    vertices = (
        (-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0),
        (-1.0, -1.0, 0.01), (1.0, -1.0, 0.01), (1.0, 1.0, 0.01), (-1.0, 1.0, 0.01),
        (-1.0, -1.0, -0.01), (1.0, -1.0, -0.01), (1.0, 1.0, -0.01), (-1.0, 1.0, -0.01),
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
    for name in ("Face", "Face_transparent"):
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


def _assert_preview_graph(obj, project, canvas) -> None:
    material = obj.material_slots[int(project.material_slot_index)].material
    stored = bpy.data.materials.get(str(project.preview_material_name))
    assert material is not None and material == stored
    assert material != project.original_material
    nodes = material.node_tree.nodes
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

        _check_material_suggestions(runtime)
        obj = _plane()
        scene = bpy.context.scene
        scene.quick_sdf_settings.resolution = 1024
        scene.quick_sdf_settings.initialization = "WHITE"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project(scene)
        assert project is not None and int(project.material_slot_index) == 0
        assert str(project.uv_map_name) == "PaintUV"
        obj.data.uv_layers.active = obj.data.uv_layers["WrongUV"]
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(object=obj, project=project)
        bpy.app.timers.register(_paint, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_run, first_interval=0.25)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Restricted native Bézier Curve session for the light/shadow terminator.

Authoring uses Blender's own Curve Edit tools. Studio watches selection and
mode so the artist cannot linger in Curve Object Mode, without a blocking
modal that would swallow Pen and handle shortcuts. Closed curves are sampled,
projected onto the face mesh, converted to UV, and written into the current
angle's generated Display. Hand-painted Coverage is left untouched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

from .curve_fill import compose_generated_fill, sample_closed_bezier_3d


TERMINATOR_FLAG_KEY = "quick_sdf_terminator"
SAMPLES_PER_SEGMENT = 16
_HEADER_TEXT = "Terminator: edit the curve · Enter/Tab fill · Esc back to Paint"
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []
_WATCHDOG_REGISTERED = False


def is_terminator_object(obj: Any | None) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.get(TERMINATOR_FLAG_KEY, False))
    except (AttributeError, ReferenceError, TypeError):
        return False


def is_curve_edit_active(context: Any | None = None) -> bool:
    try:
        from .studio import active_session

        session = active_session(context)
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        return False
    return bool(session is not None and getattr(session, "curve_edit_active", False))


def _operator_succeeded(result: Any) -> bool:
    return bool(result) and "FINISHED" in result


def _view_area(window: Any) -> Any | None:
    if window is None or getattr(window, "screen", None) is None:
        return None
    return next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)


def _mode_set(context: Any, window: Any, area: Any, mode: str) -> None:
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        if getattr(context.object, "mode", "OBJECT") != mode:
            result = bpy.ops.object.mode_set(mode=mode)
            if not _operator_succeeded(result):
                raise RuntimeError(f"Could not enter Blender mode: {mode}")


def _set_active(context: Any, obj: Any) -> None:
    view_layer = context.view_layer
    for selected in tuple(getattr(context, "selected_objects", ())):
        selected.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj


def _header_set(window: Any, text: str | None) -> None:
    area = _view_area(window)
    if area is not None:
        with contextlib.suppress(AttributeError, TypeError, RuntimeError):
            area.header_text_set(text)
    workspace = getattr(window, "workspace", None)
    if workspace is not None and hasattr(workspace, "status_text_set"):
        with contextlib.suppress(AttributeError, TypeError, RuntimeError):
            workspace.status_text_set(text or "")


def _helper_name(project: Any, angle_item: Any) -> str:
    angle_uuid = str(getattr(angle_item, "uuid", ""))[:8]
    return f".qsdf_terminator_{angle_uuid}"


def ensure_terminator_curve(context: Any, project: Any, angle_item: Any) -> Any:
    """Return the re-editable native curve for this angle, creating it if needed."""

    from . import runtime

    helper = getattr(angle_item, "terminator_object", None)
    if helper is not None and helper.name not in bpy.data.objects:
        helper = None
    if helper is None or helper.type != "CURVE":
        curve = bpy.data.curves.new(_helper_name(project, angle_item), type="CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 12
        curve.twist_mode = "MINIMUM"
        helper = bpy.data.objects.new(curve.name, curve)
        helper[TERMINATOR_FLAG_KEY] = 1
        helper[runtime.PROJECT_UUID_KEY] = str(project.uuid)
        helper[runtime.ANGLE_UUID_KEY] = str(angle_item.uuid)
        helper.hide_render = True
        helper.hide_viewport = True
        helper.hide_select = True
        scene = getattr(context, "scene", None) or bpy.context.scene
        if helper.name not in scene.collection.objects:
            scene.collection.objects.link(helper)
        angle_item.terminator_object = helper
    data = helper.data
    if data is not None and not data.splines:
        spline = data.splines.new("BEZIER")
        spline.use_cyclic_u = True
    helper[TERMINATOR_FLAG_KEY] = 1
    helper[runtime.PROJECT_UUID_KEY] = str(project.uuid)
    helper[runtime.ANGLE_UUID_KEY] = str(getattr(angle_item, "uuid", ""))
    helper.hide_render = True
    return helper


def remove_terminator_objects(project: Any | None) -> None:
    if project is None:
        return
    from . import runtime

    project_uuid = str(getattr(project, "uuid", ""))
    stale = [
        obj
        for obj in bpy.data.objects
        if is_terminator_object(obj)
        and str(obj.get(runtime.PROJECT_UUID_KEY, "")) == project_uuid
    ]
    for angle_item in getattr(project, "angles", ()):
        helper = getattr(angle_item, "terminator_object", None)
        if helper is not None and helper not in stale:
            stale.append(helper)
        with contextlib.suppress(AttributeError, ReferenceError):
            angle_item.terminator_object = None
    for obj in stale:
        with contextlib.suppress(ReferenceError, RuntimeError):
            bpy.data.objects.remove(obj, do_unlink=True)


def _sample_helper_world_polylines(helper: Any) -> list[list[tuple[float, float, float]]]:
    data = getattr(helper, "data", None)
    if data is None:
        return []
    matrix = helper.matrix_world
    polylines: list[list[tuple[float, float, float]]] = []
    for spline in data.splines:
        if spline.type == "BEZIER":
            points = list(spline.bezier_points)
            if len(points) < 3:
                continue
            knots = [
                (
                    tuple(point.co),
                    tuple(point.handle_left),
                    tuple(point.handle_right),
                )
                for point in points
            ]
            sampled = sample_closed_bezier_3d(knots, samples_per_segment=SAMPLES_PER_SEGMENT)
        else:
            points = list(getattr(spline, "points", ()))
            if len(points) < 3:
                continue
            sampled = [
                (float(point.co[0]), float(point.co[1]), float(point.co[2]))
                for point in points
            ]
        if not bool(getattr(spline, "use_cyclic_u", False)):
            first, last = sampled[0], sampled[-1]
            gap = sum((float(first[i]) - float(last[i])) ** 2 for i in range(3))
            if gap > 1.0e-8:
                continue
        world = [matrix @ Vector(point) for point in sampled]
        polylines.append([(float(item.x), float(item.y), float(item.z)) for item in world])
    return polylines


def _project_world_points_to_uv(
    project: Any,
    world_points: Sequence[tuple[float, float, float]],
) -> list[tuple[float, float]]:
    from .boundary import uv_from_mesh_hit

    target = project.target_object
    if target is None or target.type != "MESH":
        raise ValueError("The Quick SDF paint surface is unavailable")
    mesh = target.data
    uv_name = str(getattr(project, "uv_map_name", ""))
    layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
    if layer is None:
        raise ValueError("The Quick SDF UV map is unavailable")
    inverse = target.matrix_world.inverted_safe()
    uvs: list[tuple[float, float]] = []
    for point in world_points:
        local = inverse @ Vector(point)
        hit, location, _normal, face_index = target.closest_point_on_mesh(local)
        if not hit:
            continue
        uv = uv_from_mesh_hit(mesh, layer, location, int(face_index))
        if uv is not None:
            uvs.append(uv)
    if len(uvs) < 3:
        raise ValueError("The curve could not be projected onto the face mesh")
    return uvs


def _rasterize_uv_fill(project: Any, uv_points: Sequence[tuple[float, float]], width: int, height: int) -> Any:
    import numpy as np

    from .boundary import (
        _project_uv_boundaries,
        point_in_polygon,
        rasterize_closed_curve,
    )

    perimeters = _project_uv_boundaries(project)
    island_mask = None
    if perimeters:
        centroid = (
            sum(point[0] for point in uv_points) / len(uv_points),
            sum(point[1] for point in uv_points) / len(uv_points),
        )
        island_index = next(
            (
                index
                for index, perimeter in enumerate(perimeters)
                if point_in_polygon(centroid, perimeter)
            ),
            0,
        )
        island_mask = rasterize_closed_curve(perimeters[island_index], width, height)
    packed = rasterize_closed_curve(
        uv_points,
        width,
        height,
        fill_inside=True,
        island_mask=island_mask,
    )
    return np.frombuffer(packed, dtype=np.uint8).reshape(height, width).astype(np.bool_)


def apply_terminator_fill(context: Any, project: Any) -> str:
    """Sample the helper, fill the current angle, and keep Coverage pixels."""

    from . import runtime
    from .boundary import validate_curve
    from .studio import active_session

    session = active_session(context)
    helper = bpy.data.objects.get(str(getattr(session, "curve_object_name", ""))) if session else None
    if helper is None:
        angle_item = runtime.active_angle(project)
        helper = getattr(angle_item, "terminator_object", None) if angle_item is not None else None
    if helper is None or helper.type != "CURVE":
        raise ValueError("The terminator curve is missing")
    polylines = _sample_helper_world_polylines(helper)
    if not polylines:
        raise ValueError("Close the curve (Cyclic) and add at least three points before filling")
    angle_item = runtime.active_angle(project)
    image = runtime.resolve_display_image(project, angle_item) if angle_item is not None else None
    if angle_item is None or image is None:
        raise ValueError("The active angle paint layers are incomplete")
    import numpy as np

    current = runtime.image_gray8(image)
    coverage = runtime.coverage_mask(angle_item)
    height, width = current.shape
    region = np.zeros((height, width), dtype=np.bool_)
    for polyline in polylines:
        uvs = _project_world_points_to_uv(project, polyline)
        valid, message = validate_curve(uvs, closed=True)
        if not valid:
            raise ValueError(message)
        region |= _rasterize_uv_fill(project, uvs, width, height)
    if not np.any(region):
        raise ValueError("The terminator fill is empty")
    generated = compose_generated_fill(
        current, coverage, region, int(getattr(project, "paint_value", 0))
    )
    from .operators import record_display_layer_change

    record_display_layer_change(
        project,
        angle_item,
        current,
        generated,
        "Terminator fill",
    )
    return f"Filled {int(np.count_nonzero(region)):,} pixels"


def _capture_tool_settings(scene: Any, session: Any) -> None:
    tool_settings = scene.tool_settings
    session.curve_previous_use_snap = bool(tool_settings.use_snap)
    session.curve_previous_snap_individual = tuple(
        sorted(str(item) for item in tool_settings.snap_elements_individual)
    )
    session.curve_previous_use_snap_translate = bool(tool_settings.use_snap_translate)
    session.curve_previous_use_snap_nonedit = bool(tool_settings.use_snap_nonedit)
    paint = tool_settings.curve_paint_settings
    session.curve_previous_depth_mode = str(paint.depth_mode)
    session.curve_previous_curve_type = str(paint.curve_type)
    session.curve_previous_surface_offset = float(paint.surface_offset)
    session.curve_previous_project_only_selected = bool(paint.use_project_only_selected)


def _apply_curve_tool_settings(scene: Any) -> None:
    tool_settings = scene.tool_settings
    tool_settings.use_snap = True
    tool_settings.snap_elements_individual = {"FACE_PROJECT"}
    tool_settings.use_snap_translate = True
    tool_settings.use_snap_nonedit = True
    if hasattr(tool_settings, "use_snap_selectable"):
        tool_settings.use_snap_selectable = False
    paint = tool_settings.curve_paint_settings
    paint.depth_mode = "SURFACE"
    paint.curve_type = "BEZIER"
    paint.surface_offset = 0.0
    paint.use_project_only_selected = False


def _restore_tool_settings(scene: Any, session: Any) -> None:
    tool_settings = scene.tool_settings
    if session.curve_previous_use_snap is not None:
        tool_settings.use_snap = bool(session.curve_previous_use_snap)
    tool_settings.snap_elements_individual = set(session.curve_previous_snap_individual)
    if session.curve_previous_use_snap_translate is not None:
        tool_settings.use_snap_translate = bool(session.curve_previous_use_snap_translate)
    if session.curve_previous_use_snap_nonedit is not None:
        tool_settings.use_snap_nonedit = bool(session.curve_previous_use_snap_nonedit)
    paint = tool_settings.curve_paint_settings
    if session.curve_previous_depth_mode:
        paint.depth_mode = session.curve_previous_depth_mode
    if session.curve_previous_curve_type:
        paint.curve_type = session.curve_previous_curve_type
    if session.curve_previous_surface_offset is not None:
        paint.surface_offset = float(session.curve_previous_surface_offset)
    if session.curve_previous_project_only_selected is not None:
        paint.use_project_only_selected = bool(session.curve_previous_project_only_selected)
    session.curve_previous_use_snap = None
    session.curve_previous_snap_individual = ()
    session.curve_previous_use_snap_translate = None
    session.curve_previous_use_snap_nonedit = None
    session.curve_previous_depth_mode = ""
    session.curve_previous_curve_type = ""
    session.curve_previous_surface_offset = None
    session.curve_previous_project_only_selected = None


def _restrict_selection(view_layer: Any, helper: Any, session: Any) -> None:
    previous: list[tuple[str, bool]] = []
    for obj in view_layer.objects:
        previous.append((obj.name, bool(obj.hide_select)))
        obj.hide_select = obj != helper
    session.curve_previous_hide_select = tuple(previous)
    helper.hide_select = False
    helper.hide_viewport = False
    helper.hide_set(False)
    helper.show_in_front = True
    helper.select_set(True)
    view_layer.objects.active = helper


def _restore_selection(view_layer: Any, session: Any, helper: Any | None) -> None:
    for name, value in session.curve_previous_hide_select:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            with contextlib.suppress(ReferenceError, RuntimeError):
                obj.hide_select = value
    session.curve_previous_hide_select = ()
    if helper is not None:
        with contextlib.suppress(ReferenceError, RuntimeError):
            helper.show_in_front = False
            helper.hide_select = True
            helper.hide_viewport = True
            if helper.select_get():
                helper.select_set(False)


def _try_activate_curve_tool(context: Any, window: Any, area: Any) -> None:
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        for tool_id in ("builtin.pen", "builtin.draw"):
            try:
                result = bpy.ops.wm.tool_set_by_id(name=tool_id, space_type="VIEW_3D")
            except RuntimeError:
                continue
            if _operator_succeeded(result):
                return


def _start_watchdog() -> None:
    global _WATCHDOG_REGISTERED
    if not bpy.app.timers.is_registered(_curve_session_watchdog):
        bpy.app.timers.register(_curve_session_watchdog, first_interval=0.05)
    _WATCHDOG_REGISTERED = True


def _stop_watchdog() -> None:
    global _WATCHDOG_REGISTERED
    if bpy.app.timers.is_registered(_curve_session_watchdog):
        bpy.app.timers.unregister(_curve_session_watchdog)
    _WATCHDOG_REGISTERED = False


def _curve_session_watchdog() -> float | None:
    from .studio import (
        StudioError,
        current_session,
        find_window,
        resolve_session_project,
    )

    session = current_session()
    if session is None or not session.curve_edit_active:
        _stop_watchdog()
        return None
    if session.curve_edit_reconciling:
        return 0.05
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    helper = bpy.data.objects.get(session.curve_object_name)
    if window is None or project is None or helper is None:
        try:
            leave_curve_edit(bpy.context, apply=False)
        except (AttributeError, ReferenceError, RuntimeError, StudioError, ValueError):
            session.curve_edit_active = False
            _stop_watchdog()
        return None
    view_layer = window.view_layer
    active = view_layer.objects.active
    mode = str(getattr(active, "mode", "OBJECT")) if active is not None else "OBJECT"
    mesh = project.target_object
    if active == helper and mode == "EDIT":
        drifted = False
        for obj in view_layer.objects:
            if obj != helper and obj.select_get():
                obj.select_set(False)
                drifted = True
            if obj != helper and not obj.hide_select:
                obj.hide_select = True
                drifted = True
        if not helper.select_get():
            helper.select_set(True)
            drifted = True
        if drifted:
            view_layer.objects.active = helper
        return 0.05
    context = bpy.context
    if active == helper and mode == "OBJECT":
        try:
            leave_curve_edit(context, apply=True)
        except (AttributeError, ReferenceError, RuntimeError, ValueError):
            try:
                leave_curve_edit(context, apply=False)
            except (AttributeError, ReferenceError, RuntimeError, StudioError, ValueError):
                session.curve_edit_active = False
                _stop_watchdog()
        return None
    if mesh is not None and active == mesh and mode == "TEXTURE_PAINT":
        try:
            leave_curve_edit(context, apply=False, restore_paint=False)
        except (AttributeError, ReferenceError, RuntimeError, StudioError, ValueError):
            session.curve_edit_active = False
            _stop_watchdog()
        return None
    session.curve_edit_reconciling = True
    try:
        _set_active(context, helper)
        view = _view_area(window)
        if view is not None:
            _mode_set(context, window, view, "EDIT")
        helper.hide_select = False
        helper.select_set(True)
        view_layer.objects.active = helper
        for obj in view_layer.objects:
            if obj != helper:
                obj.hide_select = True
                if obj.select_get():
                    obj.select_set(False)
    except (AttributeError, ReferenceError, RuntimeError):
        try:
            leave_curve_edit(context, apply=False)
        except (AttributeError, ReferenceError, RuntimeError, StudioError, ValueError):
            session.curve_edit_active = False
            _stop_watchdog()
            return None
    finally:
        session.curve_edit_reconciling = False
    return 0.05


def enter_curve_edit(context: Any, project: Any) -> None:
    from . import runtime
    from .studio import (
        StudioError,
        active_session,
        discard_provisional,
        find_window,
        leave_export_adjustment_review,
    )

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        raise StudioError("Open Quick SDF Paint before editing the terminator")
    if session.curve_edit_active:
        return
    if session.editing_aux_mask_uuid:
        raise StudioError("Finish additional-mask editing before the terminator curve")
    if session.export_review_active:
        raise StudioError("Leave export review before the terminator curve")
    if bool(getattr(project, "job_running", False)):
        raise StudioError("Wait for the current Quick SDF job to finish")
    mesh = project.target_object
    if mesh is None or mesh.type != "MESH":
        raise StudioError("The Quick SDF paint surface is unavailable")
    angle_item = runtime.active_angle(project)
    if angle_item is None:
        raise StudioError("Select an angle key first")
    window = find_window(session.window_pointer) or getattr(context, "window", None)
    view = _view_area(window)
    if window is None or view is None:
        raise StudioError("Quick SDF Paint has no 3D View")
    discard_provisional(context, project)
    leave_export_adjustment_review(project, session=session)
    session.curve_edit_reconciling = True
    try:
        if str(getattr(mesh, "mode", "OBJECT")) != "OBJECT":
            _set_active(context, mesh)
            _mode_set(context, window, view, "OBJECT")
        helper = ensure_terminator_curve(context, project, angle_item)
        scene = bpy.data.scenes.get(session.scene_name) or context.scene
        _capture_tool_settings(scene, session)
        _apply_curve_tool_settings(scene)
        _restrict_selection(window.view_layer, helper, session)
        _set_active(context, helper)
        _mode_set(context, window, view, "EDIT")
        for space in (
            area.spaces.active
            for area in window.screen.areas
            if area.type == "VIEW_3D"
        ):
            overlay = getattr(space, "overlay", None)
            if overlay is not None:
                overlay.show_overlays = True
        _try_activate_curve_tool(context, window, view)
        session.curve_edit_active = True
        session.curve_object_name = helper.name
        _header_set(window, _HEADER_TEXT)
        _start_watchdog()
    finally:
        session.curve_edit_reconciling = False


def leave_curve_edit(
    context: Any | None = None,
    *,
    apply: bool = False,
    restore_paint: bool = True,
) -> str:
    from .studio import (
        active_session,
        current_session,
        ensure_studio_material_preview,
        find_window,
        resolve_session_project,
        tag_studio_redraw,
    )

    context = context or bpy.context
    session = active_session(context) or current_session()
    if session is None or not session.curve_edit_active:
        return ""
    session.curve_edit_reconciling = True
    message = ""
    try:
        project = resolve_session_project(session)
        window = find_window(session.window_pointer) or getattr(context, "window", None)
        view = _view_area(window)
        helper = bpy.data.objects.get(session.curve_object_name)
        if apply and project is not None:
            message = apply_terminator_fill(context, project)
        if helper is not None and window is not None and view is not None:
            try:
                if str(getattr(helper, "mode", "OBJECT")) != "OBJECT":
                    _set_active(context, helper)
                    _mode_set(context, window, view, "OBJECT")
            except (ReferenceError, RuntimeError):
                pass
        view_layer = window.view_layer if window is not None else getattr(context, "view_layer", None)
        if view_layer is not None:
            _restore_selection(view_layer, session, helper)
        scene = bpy.data.scenes.get(session.scene_name) if session.scene_name else None
        scene = scene or getattr(window, "scene", None) or getattr(context, "scene", None)
        if scene is not None:
            _restore_tool_settings(scene, session)
        _header_set(window, None)
        session.curve_edit_active = False
        session.curve_object_name = ""
        _stop_watchdog()
        mesh = project.target_object if project is not None else None
        if restore_paint and mesh is not None and window is not None and view is not None:
            try:
                _set_active(context, mesh)
                _mode_set(context, window, view, "TEXTURE_PAINT")
                from .tools import activate_tools

                activate_tools(context, window=window)
                ensure_studio_material_preview(context, session=session)
            except (AttributeError, ImportError, ReferenceError, RuntimeError):
                pass
        tag_studio_redraw()
        return message
    finally:
        session.curve_edit_reconciling = False


class QUICKSDF_OT_curve_edit_enter(bpy.types.Operator):
    bl_idname = "quicksdf.curve_edit_enter"
    bl_label = "Edit Terminator Curve"
    bl_description = "Edit a Blender Bézier curve as the light/shadow terminator, then fill the current angle"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        if is_curve_edit_active(context):
            return False
        try:
            from .studio import active_session, is_studio_active

            session = active_session(context)
            if session is None or not is_studio_active(context, session.project_uuid):
                return False
            if session.editing_aux_mask_uuid or session.export_review_active:
                return False
            from . import runtime

            project = runtime.active_project(getattr(context, "scene", None))
            return project is not None and not bool(getattr(project, "job_running", False))
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            return False

    def execute(self, context):
        from . import runtime
        from .studio import StudioError

        project = runtime.active_project(context.scene)
        if project is None:
            self.report({"ERROR"}, "Create or select a Quick SDF project first")
            return {"CANCELLED"}
        try:
            enter_curve_edit(context, project)
        except (RuntimeError, StudioError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_curve_edit_confirm(bpy.types.Operator):
    bl_idname = "quicksdf.curve_edit_confirm"
    bl_label = "Fill Terminator"
    bl_description = "Fill the current angle from the closed curve and return to Paint"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return is_curve_edit_active(context)

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        from .studio import StudioError

        try:
            message = leave_curve_edit(context, apply=True)
        except (RuntimeError, StudioError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if message:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class QUICKSDF_OT_curve_edit_cancel(bpy.types.Operator):
    bl_idname = "quicksdf.curve_edit_cancel"
    bl_label = "Back to Paint"
    bl_description = "Leave the terminator curve without filling"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return is_curve_edit_active(context)

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        from .studio import StudioError

        try:
            leave_curve_edit(context, apply=False)
        except (RuntimeError, StudioError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def register_curve_session() -> None:
    keyconfig = getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None)
    addon = getattr(keyconfig, "addon", None)
    if addon is None:
        return
    keymap = addon.keymaps.new(name="Curve", space_type="EMPTY")
    for operator, key in (
        ("quicksdf.curve_edit_confirm", "RET"),
        ("quicksdf.curve_edit_confirm", "NUMPAD_ENTER"),
        ("quicksdf.curve_edit_confirm", "TAB"),
        ("quicksdf.curve_edit_cancel", "ESC"),
    ):
        item = keymap.keymap_items.new(operator, type=key, value="PRESS")
        _ADDON_KEYMAPS.append((keymap, item))


def unregister_curve_session() -> None:
    _stop_watchdog()
    with contextlib.suppress(AttributeError, ReferenceError, RuntimeError, ValueError):
        leave_curve_edit(bpy.context, apply=False)
    while _ADDON_KEYMAPS:
        keymap, item = _ADDON_KEYMAPS.pop()
        with contextlib.suppress(ReferenceError, RuntimeError, ValueError):
            keymap.keymap_items.remove(item)


CLASSES = (
    QUICKSDF_OT_curve_edit_enter,
    QUICKSDF_OT_curve_edit_confirm,
    QUICKSDF_OT_curve_edit_cancel,
)


__all__ = [
    "CLASSES",
    "TERMINATOR_FLAG_KEY",
    "apply_terminator_fill",
    "compose_generated_fill",
    "enter_curve_edit",
    "ensure_terminator_curve",
    "is_curve_edit_active",
    "is_terminator_object",
    "leave_curve_edit",
    "register_curve_session",
    "remove_terminator_objects",
    "sample_closed_bezier_3d",
    "unregister_curve_session",
]

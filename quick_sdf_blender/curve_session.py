# SPDX-License-Identifier: GPL-3.0-or-later
"""Restricted native Bézier Curve session for the light/shadow terminator.

Authoring uses Blender's own Curve Edit tools. Studio watches selection and
mode so the artist cannot linger in Curve Object Mode, without a blocking
modal that would swallow Pen and handle shortcuts. The ``SDF Curve`` tool
(see :mod:`quick_sdf_blender.tools`) takes over the plain LMB press and drops
a control point onto the face surface, while the Curve Pen keeps every other
action. Closed curves are sampled, projected onto the face mesh, converted to
UV, and written into the current angle's generated Display. After a cast
(and again before fill) handles are aligned to the previous/next knot
direction in the mesh tangent plane. Hand-painted Coverage is left untouched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from time import monotonic
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

from .curve_fill import compose_generated_fill, sample_closed_bezier_3d


TERMINATOR_FLAG_KEY = "quick_sdf_terminator"
SAMPLES_PER_SEGMENT = 16
_HEADER_TEXT = "Terminator: click the face to cast points · Enter/Tab fill · Esc back to Paint"
# Native Pen edits settle before they become one session-local undo step.
_CURVE_UNDO_SETTLE_SECONDS = 0.15
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []
_WATCHDOG_REGISTERED = False

# Picking mirrors Blender's own tools (#ED_view3d_select_dist_px * factor).
_SELECT_DISTANCE_PX = 75.0
_ELEMENT_HIT_FACTOR = 0.2
# Mirrors the plain LMB press of "3D View Tool: Edit Curve, Curve Pen".
_NATIVE_PEN_PROPERTIES = {
    "extrude_point": True,
    "move_segment": True,
    "select_point": True,
    "move_point": True,
    "close_spline_method": "ON_CLICK",
}
_HANDLE_TYPES = ("AUTO", "VECTOR")
# `curve.handle_type_set` spells the auto/free types differently.
_HANDLE_TYPE_OPERATOR = {
    "AUTO": "AUTOMATIC",
    "VECTOR": "VECTOR",
    "ALIGNED": "ALIGNED",
    "FREE": "FREE_ALIGN",
}


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


def _bezier_point_record(point: Any) -> tuple[Any, ...]:
    return (
        tuple(float(value) for value in point.co),
        tuple(float(value) for value in point.handle_left),
        tuple(float(value) for value in point.handle_right),
        str(point.handle_left_type),
        str(point.handle_right_type),
        float(point.radius),
        float(point.tilt),
        bool(point.select_control_point),
        bool(point.select_left_handle),
        bool(point.select_right_handle),
    )


def _spline_record(spline: Any) -> tuple[Any, ...]:
    kind = str(getattr(spline, "type", "BEZIER"))
    cyclic = bool(getattr(spline, "use_cyclic_u", False))
    resolution = int(getattr(spline, "resolution_u", 12))
    if kind == "BEZIER":
        points: tuple[Any, ...] = tuple(
            _bezier_point_record(point) for point in spline.bezier_points
        )
    else:
        points = tuple(
            (
                tuple(float(value) for value in point.co),
                float(getattr(point, "radius", 1.0)),
                float(getattr(point, "weight", 1.0)),
                bool(getattr(point, "select", False)),
            )
            for point in getattr(spline, "points", ())
        )
    return (kind, cyclic, resolution, points)


def curve_state(helper: Any) -> tuple[Any, ...]:
    """Snapshot every spline of the terminator helper (geometry + selection)."""

    data = getattr(helper, "data", None)
    if data is None:
        return ()
    return tuple(_spline_record(spline) for spline in data.splines)


def _geometry_key(state: tuple[Any, ...]) -> tuple[Any, ...]:
    """Strip selection flags so clicking points does not create undo steps."""

    key: list[Any] = []
    for kind, cyclic, resolution, points in state:
        if kind == "BEZIER":
            trimmed = tuple(point[:7] for point in points)
        else:
            trimmed = tuple(point[:3] for point in points)
        key.append((kind, cyclic, resolution, trimmed))
    return tuple(key)


def _write_spline(spline: Any, record: tuple[Any, ...]) -> None:
    kind, cyclic, resolution, points = record
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        spline.use_cyclic_u = bool(cyclic)
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        spline.resolution_u = int(resolution)
    if kind == "BEZIER":
        bezier = spline.bezier_points
        if points:
            bezier.add(max(0, len(points) - len(bezier)))
        for point, values in zip(bezier, points):
            (co, handle_left, handle_right, left_type, right_type, radius, tilt,
             selected, left_selected, right_selected) = values
            point.handle_left_type = str(left_type)
            point.handle_right_type = str(right_type)
            point.co = co
            point.handle_left = handle_left
            point.handle_right = handle_right
            point.radius = float(radius)
            point.tilt = float(tilt)
            point.select_control_point = bool(selected)
            point.select_left_handle = bool(left_selected)
            point.select_right_handle = bool(right_selected)
        return
    spline_points = spline.points
    if points:
        spline_points.add(max(0, len(points) - len(spline_points)))
    for point, values in zip(spline_points, points):
        co, radius, weight, selected = values
        point.co = co
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            point.radius = float(radius)
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            point.weight = float(weight)
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            point.select = bool(selected)


def _spline_structure_matches(spline: Any, record: tuple[Any, ...]) -> bool:
    kind, _cyclic, _resolution, points = record
    if str(getattr(spline, "type", "")) != kind:
        return False
    if kind == "BEZIER":
        return len(spline.bezier_points) == len(points)
    return len(getattr(spline, "points", ())) == len(points)


def write_curve_state(helper: Any, state: tuple[Any, ...]) -> None:
    """Restore a snapshot in place so Curve Edit keeps its context.

    Spline point collections can only grow, so a state with fewer points
    rebuilds the affected splines instead of deleting points.
    """

    data = getattr(helper, "data", None)
    if data is None:
        return
    matched = len(data.splines) == len(state) and all(
        _spline_structure_matches(spline, record)
        for spline, record in zip(data.splines, state)
    )
    if matched:
        for spline, record in zip(data.splines, state):
            _write_spline(spline, record)
    else:
        data.splines.clear()
        for record in state:
            _write_spline(data.splines.new(record[0]), record)
    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        data.update()


def _modal_operator_running() -> bool:
    """True while a modal operator owns input (Pen drag, G/R/S, navigation)."""

    window = getattr(bpy.context, "window", None)
    if window is None:
        return False
    try:
        return bool(window.modal_operators)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _track_curve_history(session: Any, helper: Any) -> None:
    """Record settled native Pen edits as one session-local undo step."""

    state = curve_state(helper)
    if session.curve_tracked_state is None:
        session.curve_tracked_state = state
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
        return
    if _geometry_key(state) == _geometry_key(session.curve_tracked_state):
        # Selection-only changes never become undo steps.
        session.curve_tracked_state = state
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
        return
    now = monotonic()
    if session.curve_pending_state is not None and _geometry_key(
        state
    ) == _geometry_key(session.curve_pending_state):
        if now - session.curve_pending_time < _CURVE_UNDO_SETTLE_SECONDS:
            return
        if _modal_operator_running():
            # One drag stays one step even when the pointer pauses.
            return
        session.curve_undo_stack.append(session.curve_tracked_state)
        session.curve_redo_stack.clear()
        session.curve_tracked_state = state
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
        return
    session.curve_pending_state = state
    session.curve_pending_time = now


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


def _closest_world_on_mesh(target: Any, world_point: Any) -> tuple[Vector, Vector] | None:
    inverse = target.matrix_world.inverted_safe()
    local = inverse @ Vector(world_point)
    hit, location, normal, _face_index = target.closest_point_on_mesh(local)
    if not hit:
        return None
    world = target.matrix_world @ Vector(location)
    world_normal = inverse.transposed().to_3x3() @ Vector(normal)
    if world_normal.length_squared <= 1.0e-16:
        world_normal = Vector((0.0, 0.0, 1.0))
    else:
        world_normal.normalize()
    return world, world_normal


def _cast_bezier_handles_to_mesh(helper: Any, target: Any) -> None:
    """Set ALIGNED handles from previous/next knot direction in the tangent plane."""

    if target is None or getattr(target, "type", "") != "MESH":
        return
    data = getattr(helper, "data", None)
    if data is None:
        return
    matrix = helper.matrix_world
    inverse = matrix.inverted_safe()
    changed = False
    for spline in data.splines:
        if spline.type != "BEZIER":
            continue
        points = spline.bezier_points
        count = len(points)
        if count < 3:
            continue
        cyclic = bool(getattr(spline, "use_cyclic_u", False))
        world = [matrix @ Vector(point.co) for point in points]
        for index, point in enumerate(points):
            if not cyclic and (index == 0 or index == count - 1):
                continue
            origin = world[index]
            incoming = origin - world[(index - 1) % count]
            outgoing = world[(index + 1) % count] - origin
            if incoming.length_squared <= 1.0e-16 and outgoing.length_squared <= 1.0e-16:
                continue
            if incoming.length_squared <= 1.0e-16:
                tangent = outgoing.normalized()
            elif outgoing.length_squared <= 1.0e-16:
                tangent = incoming.normalized()
            else:
                tangent = incoming.normalized() + outgoing.normalized()
                if tangent.length_squared <= 1.0e-16:
                    continue
                tangent.normalize()
            hit = _closest_world_on_mesh(target, origin)
            if hit is not None:
                _location, normal = hit
                tangent = tangent - normal * tangent.dot(normal)
                if tangent.length_squared <= 1.0e-16:
                    continue
                tangent.normalize()
            left_length = incoming.length / 3.0 if incoming.length_squared > 1.0e-16 else outgoing.length / 3.0
            right_length = outgoing.length / 3.0 if outgoing.length_squared > 1.0e-16 else incoming.length / 3.0
            point.handle_left_type = "FREE"
            point.handle_right_type = "FREE"
            point.handle_left = inverse @ (origin - tangent * left_length)
            point.handle_right = inverse @ (origin + tangent * right_length)
            point.handle_left_type = "ALIGNED"
            point.handle_right_type = "ALIGNED"
            changed = True
    if changed:
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            data.update()
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            helper.update_tag(refresh={"DATA", "OBJECT"})


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
    _cast_bezier_handles_to_mesh(helper, project.target_object)
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
    from .tools import CURVE_TOOL_ID

    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        for tool_id in (CURVE_TOOL_ID, "builtin.pen", "builtin.draw"):
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
    try:
        _track_curve_history(session, helper)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        # Never let snapshot bookkeeping take the watchdog down.
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
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
        session.curve_undo_stack.clear()
        session.curve_redo_stack.clear()
        session.curve_tracked_state = curve_state(helper)
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
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
        session.curve_undo_stack.clear()
        session.curve_redo_stack.clear()
        session.curve_tracked_state = None
        session.curve_pending_state = None
        session.curve_pending_time = 0.0
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


def _event_region_coord(context: Any, event: Any) -> tuple[float, float] | None:
    """Return the click position in 3D View coordinates, or ``None`` elsewhere."""

    region = getattr(context, "region", None)
    area = getattr(context, "area", None)
    if region is None or area is None or getattr(context, "region_data", None) is None:
        return None
    if str(getattr(region, "type", "")) != "WINDOW":
        return None
    if str(getattr(area, "type", "")) != "VIEW_3D":
        return None
    try:
        return float(event.mouse_region_x), float(event.mouse_region_y)
    except (AttributeError, TypeError, ValueError):
        return None


def _select_distance_px(context: Any) -> float:
    """Mirror #ED_view3d_select_dist_px so picking matches Blender's own tools."""

    try:
        pixel_size = float(context.preferences.system.pixel_size)
    except (AttributeError, ReferenceError, TypeError, ValueError):
        pixel_size = 1.0
    return _SELECT_DISTANCE_PX * max(pixel_size, 0.0)


def _active_bezier_spline(helper: Any) -> Any | None:
    for spline in getattr(getattr(helper, "data", None), "splines", ()):
        if spline.type == "BEZIER":
            return spline
    return None


def _select_only_bezier_point(spline: Any, index: int) -> None:
    for position, point in enumerate(spline.bezier_points):
        selected = position == index
        point.select_control_point = selected
        point.select_left_handle = selected
        point.select_right_handle = selected


def _is_seed_spline(spline: Any) -> bool:
    """True while the spline still holds only the untouched creation point."""

    points = spline.bezier_points
    if len(points) != 1:
        return False
    return float(Vector(points[0].co).length) <= 1.0e-6


def _bezier_points_under_cursor(context: Any, helper: Any, coord: tuple[float, float]) -> bool:
    """Report whether a control point or handle is within Blender's pick radius."""

    from bpy_extras import view3d_utils

    region = getattr(context, "region", None)
    region_data = getattr(context, "region_data", None)
    data = getattr(helper, "data", None)
    if region is None or region_data is None or data is None:
        return False
    overlay = getattr(getattr(context, "space_data", None), "overlay", None)
    # `display_handle` is the RNA name of the C `handle_display` overlay option.
    handle_display = str(
        getattr(overlay, "display_handle", getattr(overlay, "handle_display", "ALL"))
    )
    threshold = _select_distance_px(context) * _ELEMENT_HIT_FACTOR
    matrix = helper.matrix_world
    for spline in data.splines:
        if spline.type != "BEZIER":
            continue
        for point in spline.bezier_points:
            selected = bool(
                point.select_control_point
                or point.select_left_handle
                or point.select_right_handle
            )
            if handle_display == "NONE" or (handle_display == "SELECTED" and not selected):
                candidates = (point.co,)
            else:
                candidates = (point.handle_left, point.co, point.handle_right)
            for candidate in candidates:
                screen = view3d_utils.location_3d_to_region_2d(
                    region, region_data, matrix @ Vector(candidate)
                )
                if screen is None:
                    continue
                if abs(screen[0] - coord[0]) + abs(screen[1] - coord[1]) < threshold:
                    return True
    return False


def _cast_target_surface(context: Any, project: Any, coord: tuple[float, float]) -> Vector | None:
    """Return the world-space surface point under the cursor, or ``None``."""

    from bpy_extras import view3d_utils

    target = getattr(project, "target_object", None)
    region = getattr(context, "region", None)
    region_data = getattr(context, "region_data", None)
    if target is None or target.type != "MESH" or region is None or region_data is None:
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, region_data, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, region_data, coord)
    inverse = target.matrix_world.inverted_safe()
    local_origin = inverse @ origin
    local_direction = (inverse.to_3x3() @ direction).normalized()
    try:
        depsgraph: Any = context.evaluated_depsgraph_get()
    except (AttributeError, RuntimeError):
        depsgraph = None
    hit, location, _normal, _face_index = target.ray_cast(
        local_origin, local_direction, depsgraph=depsgraph
    )
    if not hit:
        return None
    return target.matrix_world @ Vector(location)


def _cast_curve_point(
    helper: Any,
    world_point: Vector,
    handle_type: str,
    target: Any | None = None,
) -> None:
    """Append one control point at the surface position, like the native pen."""

    spline = _active_bezier_spline(helper)
    if spline is None:
        raise ValueError("The terminator curve has no Bézier spline")
    points = spline.bezier_points
    if _is_seed_spline(spline):
        index = 0
    else:
        points.add(1)
        index = len(points) - 1
    local = helper.matrix_world.inverted_safe() @ world_point
    point = points[index]
    point.co = local
    point.handle_left = local
    point.handle_right = local
    handle = str(handle_type).upper()
    if handle not in _HANDLE_TYPES:
        handle = "VECTOR"
    point.handle_left_type = handle
    point.handle_right_type = handle
    point.radius = 1.0
    point.tilt = 0.0
    _select_only_bezier_point(spline, index)
    if target is not None:
        _cast_bezier_handles_to_mesh(helper, target)
    if len(points) < 3:
        operator_type = _HANDLE_TYPE_OPERATOR.get(handle)
        if operator_type is not None:
            with contextlib.suppress(AttributeError, RuntimeError, TypeError):
                bpy.ops.curve.handle_type_set(type=operator_type)
    data = getattr(helper, "data", None)
    if data is not None:
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            data.update()


def _invoke_native_pen() -> set[str]:
    """Hand the click to Blender's Curve Pen so it keeps its own interaction."""

    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        bpy.ops.curve.pen("INVOKE_DEFAULT", **_NATIVE_PEN_PROPERTIES)
    # The pen owns the interaction from here; its modal handler outlives this call.
    return {"FINISHED"}


class QUICKSDF_OT_sdf_curve_pen(bpy.types.Operator):
    bl_idname = "quicksdf.sdf_curve_pen"
    bl_label = "Cast Terminator Point"
    bl_description = (
        "Add a terminator control point cast onto the face surface; clicking an "
        "existing point or handle keeps Blender's Curve Pen behavior"
    )
    bl_options = {"UNDO", "INTERNAL"}

    extrude_handle: bpy.props.EnumProperty(
        name="Handle Type",
        description="Handle type of the cast control point",
        items=(
            ("AUTO", "Auto", "Smooth handles"),
            ("VECTOR", "Vector", "Straight handles"),
        ),
        default="VECTOR",
    )

    @classmethod
    def poll(cls, context):
        active = getattr(context, "object", None)
        if not is_terminator_object(active):
            return False
        if str(getattr(context, "mode", "")) != "EDIT_CURVE":
            return False
        return is_curve_edit_active(context)

    def invoke(self, context, event):
        from .studio import active_session, resolve_session_project

        project = resolve_session_project(active_session(context))
        helper = getattr(context, "object", None)
        coord = _event_region_coord(context, event)
        if project is None or helper is None or coord is None:
            return {"PASS_THROUGH"}
        if _bezier_points_under_cursor(context, helper, coord):
            return _invoke_native_pen()
        world_point = _cast_target_surface(context, project, coord)
        if world_point is None:
            return {"PASS_THROUGH"}
        try:
            _cast_curve_point(
                helper,
                world_point,
                str(self.extrude_handle),
                getattr(project, "target_object", None),
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            return {"CANCELLED"}
        return {"FINISHED"}


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


class _CurveHistoryOperator(bpy.types.Operator):
    """Shared plumbing for the session-local terminator Undo/Redo."""

    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return is_curve_edit_active(context)

    def _targets(self, context):
        from .studio import active_session

        session = active_session(context)
        helper = (
            bpy.data.objects.get(session.curve_object_name)
            if session is not None
            else None
        )
        if session is None or helper is None:
            return None, None
        try:
            _track_curve_history(session, helper)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            session.curve_pending_state = None
            session.curve_pending_time = 0.0
        return session, helper

    def _restore(self, session, helper, target):
        session.curve_edit_reconciling = True
        try:
            write_curve_state(helper, target)
            session.curve_tracked_state = target
            session.curve_pending_state = None
            session.curve_pending_time = 0.0
        finally:
            session.curve_edit_reconciling = False
        from .studio import tag_studio_redraw

        tag_studio_redraw()


class QUICKSDF_OT_curve_undo(_CurveHistoryOperator):
    bl_idname = "quicksdf.curve_undo"
    bl_label = "Undo Terminator Edit"
    bl_description = (
        "Undo the last terminator curve edit without leaving Curve Edit or "
        "touching Blender's global Undo history"
    )

    def execute(self, context):
        session, helper = self._targets(context)
        if session is None or helper is None:
            return {"CANCELLED"}
        current = curve_state(helper)
        if current != session.curve_tracked_state:
            # The edit just made is not settled into the stack yet.
            target = session.curve_tracked_state
            session.curve_redo_stack.clear()
            session.curve_redo_stack.append(current)
        elif session.curve_undo_stack:
            target = session.curve_undo_stack.pop()
            session.curve_redo_stack.append(current)
        else:
            self.report({"INFO"}, "Nothing to undo in the terminator curve")
            return {"FINISHED"}
        self._restore(session, helper, target)
        return {"FINISHED"}


class QUICKSDF_OT_curve_redo(_CurveHistoryOperator):
    bl_idname = "quicksdf.curve_redo"
    bl_label = "Redo Terminator Edit"
    bl_description = (
        "Redo a terminator curve edit without leaving Curve Edit or touching "
        "Blender's global Undo history"
    )

    def execute(self, context):
        session, helper = self._targets(context)
        if session is None or helper is None:
            return {"CANCELLED"}
        current = curve_state(helper)
        if current != session.curve_tracked_state:
            # A fresh edit supersedes the Redo branch, exactly like Blender.
            session.curve_undo_stack.append(session.curve_tracked_state)
            session.curve_redo_stack.clear()
            session.curve_tracked_state = current
            session.curve_pending_state = None
            session.curve_pending_time = 0.0
            self.report({"INFO"}, "Nothing to redo in the terminator curve")
            return {"FINISHED"}
        if not session.curve_redo_stack:
            self.report({"INFO"}, "Nothing to redo in the terminator curve")
            return {"FINISHED"}
        target = session.curve_redo_stack.pop()
        session.curve_undo_stack.append(current)
        self._restore(session, helper, target)
        return {"FINISHED"}


def register_curve_session() -> None:
    keyconfig = getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None)
    addon = getattr(keyconfig, "addon", None)
    if addon is None:
        return
    keymap = addon.keymaps.new(name="Curve", space_type="EMPTY")
    for operator, key, modifiers in (
        ("quicksdf.curve_edit_confirm", "RET", {}),
        ("quicksdf.curve_edit_confirm", "NUMPAD_ENTER", {}),
        ("quicksdf.curve_edit_confirm", "TAB", {}),
        ("quicksdf.curve_edit_cancel", "ESC", {}),
        ("quicksdf.curve_undo", "Z", {"ctrl": True}),
        ("quicksdf.curve_redo", "Z", {"ctrl": True, "shift": True}),
    ):
        item = keymap.keymap_items.new(operator, type=key, value="PRESS", **modifiers)
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
    QUICKSDF_OT_curve_undo,
    QUICKSDF_OT_curve_redo,
    QUICKSDF_OT_sdf_curve_pen,
)


__all__ = [
    "CLASSES",
    "TERMINATOR_FLAG_KEY",
    "apply_terminator_fill",
    "compose_generated_fill",
    "curve_state",
    "enter_curve_edit",
    "ensure_terminator_curve",
    "is_curve_edit_active",
    "is_terminator_object",
    "leave_curve_edit",
    "register_curve_session",
    "remove_terminator_objects",
    "sample_closed_bezier_3d",
    "unregister_curve_session",
    "write_curve_state",
]

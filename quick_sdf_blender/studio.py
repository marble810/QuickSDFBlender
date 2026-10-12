# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient Quick SDF Paint session and workspace management.

A Studio session belongs to one Blender window and is valid only for the
current process. This keeps save/load and undo from resurrecting half-active
paint state.
"""

from __future__ import annotations

from array import array
import base64
from dataclasses import dataclass
import ctypes
import json
import time
from typing import Any, Callable

import bpy
from bpy.app.handlers import persistent


WORKSPACE_PROJECT_TAG = "quick_sdf_studio_project_uuid"
WORKSPACE_VERSION_TAG = "quick_sdf_studio_layout_version"
WORKSPACE_LAYOUT_VERSION = 3
WORKSPACE_BASENAME = "Quick SDF Paint"
TIMELINE_HEIGHT = 104
TIMELINE_SPACE_TYPE = "NODE_EDITOR"
PROVISIONAL_DISPLAY_ROLE = "provisional_display"
TIMELINE_HOST_TAG = "_qsdf_runtime_timeline_host"
TIMELINE_HOST_NAME = ".Quick SDF Timeline Runtime"
PROJECTION_RECOVERY_KEY = "_qsdf_projection_recovery_v1"


class StudioError(RuntimeError):
    """Base error raised by Studio lifecycle operations."""


class StudioUnavailableError(StudioError):
    """Raised when an interactive Blender window is unavailable."""


class StudioBusyError(StudioError):
    """Raised when another window/project already owns the singleton session."""


@dataclass(slots=True)
class StudioSession:
    project_uuid: str
    window_pointer: int
    scene_name: str
    workspace_name: str
    previous_workspace_name: str
    previous_active_object_name: str
    previous_selected_object_names: tuple[str, ...]
    previous_mode: str
    previous_image_paint_mode: str
    previous_canvas_name: str
    workspace_created: bool = False
    preview_suspended: bool = False
    onion_suspended: bool = False
    show_first_stroke_hint: bool = True
    first_hint_text: str = ""
    view_mode: str = "EDIT"
    paint_key_uuid: str = ""
    paint_key_angle: float = 0.0
    seek_angle: float = 0.0
    previous_normal_falloff: bool | None = None
    previous_clip_starts: tuple[tuple[int, float], ...] = ()
    projection_settings_suspended: bool = False
    paint_surface_object_name: str = ""
    paint_surface_mesh_name: str = ""
    paint_surface_object_pointer: int = 0
    paint_surface_mesh_pointer: int = 0
    paint_surface_polygon_count: int = 0
    paint_surface_region_revision: int = -1
    paint_surface_previous_use_mask: bool | None = None
    paint_surface_previous_use_mask_vertex: bool | None = None
    paint_surface_previous_selection: bytes = b""
    paint_surface_previous_hidden: bytes = b""
    paint_surface_previous_active_material_index: int = -1
    paint_surface_previous_uv_index: int = -1
    paint_surface_suspended: bool = False
    stroke_brush_name: str = ""
    stroke_brush_color: tuple[float, float, float] | None = None
    stroke_brush_secondary_color: tuple[float, float, float] | None = None
    stroke_unified_use_color: bool | None = None
    stroke_unified_color: tuple[float, float, float] | None = None
    stroke_unified_secondary_color: tuple[float, float, float] | None = None
    stroke_watchdog_deadline: float = 0.0
    stroke_watchdog_seen_modal: bool = False
    stroke_watchdog_absent_since: float = 0.0
    stroke_from_view3d: bool = False
    projection_hint: str = ""
    export_review_active: bool = False
    editing_aux_mask_uuid: str = ""
    aux_previous_preview_mode: str = ""
    aux_previous_onion_enabled: bool = False
    provisional_uuid: str = ""
    provisional_angle: float = -1.0
    provisional_side: str = ""
    provisional_image_name: str = ""
    provisional_base_blob: bytes = b""
    provisional_coverage_blob: bytes = b""
    provisional_base_revision: int = 0
    provisional_coverage_revision: int = 0
    provisional_state: str = "NONE"
    provisional_source_token: tuple[Any, ...] = ()
    provisional_promoting: bool = False
    provisional_previous_key_uuid: str = ""
    provisional_history: Any = None
    provisional_rebuild_after_save: bool = False
    provisional_rebuild_angle: float = -1.0


@dataclass(slots=True)
class _PendingEnter:
    session: StudioSession
    manage_preview: bool
    state: str = "JOIN"


@dataclass(slots=True)
class _PendingSwitch:
    project_uuid: str
    attempts: int = 0


_SESSION: StudioSession | None = None
_PENDING: _PendingEnter | None = None
_SWITCH_PENDING: _PendingSwitch | None = None
_HANDLERS_REGISTERED = False
_PROVISIONAL_JOB: dict[str, Any] | None = None


def current_session() -> StudioSession | None:
    return _SESSION


def active_session(context: Any | None = None) -> StudioSession | None:
    session = _SESSION
    if session is None:
        return None
    context = context or bpy.context
    window = getattr(context, "window", None)
    if window is None or window.as_pointer() != session.window_pointer:
        return None
    return session


def is_studio_active(context: Any | None = None, project_uuid: str = "") -> bool:
    session = active_session(context)
    if session is None:
        return False
    if project_uuid and session.project_uuid != project_uuid:
        return False
    workspace = getattr(context or bpy.context, "workspace", None)
    return bool(workspace and workspace.get(WORKSPACE_PROJECT_TAG) == session.project_uuid)


def find_window(pointer: int) -> Any | None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        if window.as_pointer() == pointer:
            return window
    return None


def resolve_session_project(session: StudioSession | None = None) -> Any | None:
    session = session or _SESSION
    if session is None:
        return None
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(project.uuid) == session.project_uuid:
                return project
    return None


def _resolve_project_uuid(project_uuid: str) -> Any | None:
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(project.uuid) == str(project_uuid):
                return project
    return None


def _project_location(project: Any) -> tuple[Any, int] | tuple[None, None]:
    project_uuid = str(project.uuid)
    if not project_uuid:
        return None, None
    for scene in bpy.data.scenes:
        for index, candidate in enumerate(getattr(scene, "quick_sdf_projects", ())):
            if str(candidate.uuid) == project_uuid:
                return scene, index
    return None, None


def _activate_project(project: Any) -> tuple[Any, int]:
    scene, index = _project_location(project)
    if scene is None or index is None:
        raise StudioError("The Quick SDF project is no longer part of a scene")
    scene.quick_sdf_active_project_index = int(index)
    return scene, int(index)


def _preflight_studio_target(context: Any, project: Any) -> tuple[Any, int, Any, Any]:
    """Resolve every structural dependency before changing the live session."""

    scene, index = _project_location(project)
    if scene is None or index is None:
        raise StudioError("The Quick SDF project is no longer part of a scene")
    context_scene = getattr(context, "scene", None)
    if context_scene is None or scene.as_pointer() != context_scene.as_pointer():
        raise StudioError("Open the target scene before editing this Quick SDF project")
    obj = project.target_object
    if obj is None or getattr(obj, "type", "") != "MESH":
        raise StudioError("The Quick SDF target must be a local mesh")
    if obj.name not in context.view_layer.objects:
        raise StudioError("The Quick SDF target is not visible in the current View Layer")
    try:
        from . import runtime

        runtime.migrate_legacy_face_region(context, project)
        runtime.face_region_mask(project, obj)
        runtime.face_region_material_slots(project)
        runtime.face_region_uv_mask(project)
    except (ValueError, RuntimeError) as exc:
        raise StudioError(str(exc)) from exc
    uv_name = str(project.uv_map_name)
    if not uv_name or getattr(obj.data, "uv_layers", None) is None or obj.data.uv_layers.get(uv_name) is None:
        raise StudioError("The Quick SDF UV map is unavailable")
    _index, item = _resolve_paint_key(project)
    if item is None:
        raise StudioError("The Quick SDF project has no paint angle")
    from .runtime import resolve_display_image

    image = resolve_display_image(project, item)
    if image is None:
        raise StudioError("The active angle image could not be used as a paint canvas")
    return scene, int(index), obj, image


def _resolve_paint_key(
    project: Any,
    *,
    key_uuid: str = "",
    index: int = -1,
) -> tuple[int, Any] | tuple[None, None]:
    angles = project.angles
    if key_uuid:
        for candidate_index, item in enumerate(angles):
            if str(item.uuid) == str(key_uuid):
                return candidate_index, item
    if 0 <= int(index) < len(angles):
        return int(index), angles[int(index)]
    active_uuid = str(project.active_angle_uuid)
    if active_uuid:
        for candidate_index, item in enumerate(angles):
            if str(item.uuid) == active_uuid:
                return candidate_index, item
    if angles:
        active_index = max(0, min(int(project.active_angle_index), len(angles) - 1))
        return active_index, angles[active_index]
    return None, None


def select_paint_key(
    context: Any,
    project: Any,
    *,
    key_uuid: str = "",
    index: int = -1,
) -> bool:
    """Select one edit key and atomically synchronize every paint surface."""

    session = active_session(context)
    if session is not None and session.provisional_uuid and not session.provisional_promoting:
        discard_provisional(context, project)

    resolved_index, item = _resolve_paint_key(project, key_uuid=key_uuid, index=index)
    if item is None or resolved_index is None:
        return False
    angle = float(item.angle)
    project.active_angle_index = resolved_index
    project.active_angle_uuid = str(item.uuid)
    project.active_side = str(item.side)
    project.seek_angle = angle

    session = active_session(context)
    if (
        session is not None
        and session.project_uuid == str(project.uuid)
        and session.editing_aux_mask_uuid
    ):
        return False
    if session is not None and session.project_uuid == str(project.uuid):
        leave_export_adjustment_review(project, session=session)
        ensure_studio_material_preview(context, session=session)
        session.view_mode = "EDIT"
        session.paint_key_uuid = str(item.uuid)
        session.paint_key_angle = angle
        session.seek_angle = angle
        session.projection_hint = ""

    from .runtime import sync_canvas

    image = sync_canvas(context, project)
    if session is not None and image is not None:
        try:
            _assign_preview(project, image)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    tag_studio_redraw()
    return True


def enter_aux_mask_edit(context: Any, project: Any, mask_uuid: str) -> bool:
    """Switch both paint surfaces to one angle-independent project mask."""

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        raise StudioError("Open Quick SDF Paint before editing an additional mask")
    discard_provisional(context, project)
    from . import runtime

    item = runtime.aux_mask_for_uuid(project, mask_uuid)
    image = runtime.resolve_aux_mask_image(project, item)
    if item is None or image is None:
        raise StudioError("The selected additional mask is missing")
    leave_export_adjustment_review(project, session=session)
    if not session.editing_aux_mask_uuid:
        session.aux_previous_preview_mode = str(project.preview_mode)
        session.aux_previous_onion_enabled = bool(project.onion_enabled)
    if bool(project.onion_enabled):
        project.onion_enabled = False
    session.editing_aux_mask_uuid = str(item.uuid)
    session.view_mode = "AUX_MASK"
    project.active_aux_mask_uuid = str(item.uuid)
    project.active_aux_mask_index = next(
        (
            index
            for index, candidate in enumerate(project.aux_masks)
            if str(candidate.uuid) == str(item.uuid)
        ),
        0,
    )
    project.preview_mode = "MASK"
    image = runtime.sync_canvas(context, project)
    if image is None:
        raise StudioError("The additional mask could not be used as a paint canvas")
    try:
        _assign_preview(project, image)
    except (ReferenceError, RuntimeError, ValueError):
        pass
    tag_studio_redraw()
    return True


def leave_aux_mask_edit(context: Any, project: Any) -> bool:
    """Return from an angle-independent mask to the stored face-shadow key."""

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        return False
    if not session.editing_aux_mask_uuid:
        return select_paint_key(
            context,
            project,
            key_uuid=session.paint_key_uuid,
            index=int(project.active_angle_index),
        )
    session.editing_aux_mask_uuid = ""
    session.view_mode = "EDIT"
    if session.aux_previous_preview_mode:
        project.preview_mode = session.aux_previous_preview_mode
    if session.aux_previous_onion_enabled:
        project.onion_enabled = True
    session.aux_previous_preview_mode = ""
    session.aux_previous_onion_enabled = False
    return select_paint_key(
        context,
        project,
        key_uuid=session.paint_key_uuid,
        index=int(project.active_angle_index),
    )


def show_export_adjustment_review(context: Any, project: Any, image: Any) -> bool:
    """Show an export-only heatmap without leaving a paint surface armed."""

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        return False
    window = find_window(session.window_pointer)
    if window is None:
        return False
    shown = False
    for area in window.screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        space = area.spaces.active
        if hasattr(space, "ui_mode"):
            space.ui_mode = "VIEW"
        elif hasattr(space, "mode"):
            space.mode = "VIEW"
        space.image = image
        area.tag_redraw()
        shown = True
    session.export_review_active = shown
    tag_studio_redraw()
    return shown


def leave_export_adjustment_review(
    project: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> bool:
    """Restore the authoring canvas after the read-only export review."""

    session = session or _SESSION
    if session is None or not session.export_review_active:
        return False
    project = project or resolve_session_project(session)
    image = None
    if project is not None:
        try:
            from .runtime import active_angle, resolve_display_image

            item = active_angle(project)
            image = resolve_display_image(project, item) if item is not None else None
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            image = None
    window = find_window(session.window_pointer)
    if window is not None:
        for area in window.screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            space = area.spaces.active
            if hasattr(space, "ui_mode"):
                space.ui_mode = "PAINT"
            elif hasattr(space, "mode"):
                space.mode = "PAINT"
            if image is not None:
                space.image = image
            area.tag_redraw()
    session.export_review_active = False
    tag_studio_redraw()
    return True


def _provisional_lane(project: Any, side: str) -> list[Any]:
    return sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )


def _provisional_token(project: Any, side: str) -> tuple[Any, ...]:
    from . import runtime

    records = []
    for item in _provisional_lane(project, side):
        image = runtime.resolve_display_image(project, item)
        records.append(
            (
                str(item.uuid),
                float(item.angle),
                "" if image is None else str(image.name),
                -1
                if image is None
                else int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                runtime.bitplane_revision_token(item, "BASE"),
                runtime.bitplane_revision_token(item, "COVERAGE"),
            )
        )
    return tuple(records)


def _cancel_provisional_job() -> None:
    global _PROVISIONAL_JOB

    job = _PROVISIONAL_JOB
    _PROVISIONAL_JOB = None
    if job is None:
        return
    flag = job.get("cancel_flag")
    if flag is not None:
        flag.value = 1
    manager = job.get("manager")
    if manager is not None:
        try:
            manager.cancel()
            manager.shutdown(wait=False)
        except RuntimeError:
            pass
    if bpy.app.timers.is_registered(_poll_provisional_job):
        bpy.app.timers.unregister(_poll_provisional_job)


def _remove_provisional_image(session: StudioSession) -> None:
    image = bpy.data.images.get(str(session.provisional_image_name))
    if image is None:
        return
    try:
        from . import runtime

        if (
            str(image.get(runtime.PROJECT_UUID_KEY, "")) == session.project_uuid
            and str(image.get(runtime.ANGLE_UUID_KEY, "")) == session.provisional_uuid
        ):
            bpy.data.images.remove(image)
    except (ReferenceError, RuntimeError):
        pass


def discard_provisional(
    context: Any | None = None,
    project: Any | None = None,
    *,
    keep_preview_angle: bool = False,
) -> None:
    """Cancel and remove the session-only in-between paint key."""

    session = _SESSION
    if session is None:
        _cancel_provisional_job()
        return
    project = project or resolve_session_project(session)
    _cancel_provisional_job()
    if session.provisional_promoting and project is not None and session.provisional_uuid:
        index = next(
            (
                index
                for index, item in enumerate(project.angles)
                if str(item.uuid) == session.provisional_uuid
                and bool(item.get("_qsdf_provisional", False))
            ),
            -1,
        )
        if index >= 0:
            project.angles.remove(index)
    _remove_provisional_image(session)
    angle = session.provisional_angle
    session.provisional_uuid = ""
    session.provisional_angle = -1.0
    session.provisional_side = ""
    session.provisional_image_name = ""
    session.provisional_base_blob = b""
    session.provisional_coverage_blob = b""
    session.provisional_base_revision = 0
    session.provisional_coverage_revision = 0
    session.provisional_state = "NONE"
    session.provisional_source_token = ()
    session.provisional_promoting = False
    session.provisional_previous_key_uuid = ""
    session.provisional_history = None
    if keep_preview_angle and angle >= 0.0:
        session.seek_angle = angle


def _compute_provisional_masks(
    lower_display: Any,
    upper_display: Any,
    lower_base: Any,
    upper_base: Any,
    lower_coverage: Any,
    upper_coverage: Any,
    factor: float,
    cancel_flag: Any,
) -> tuple[Any, Any, Any]:
    import numpy as np

    from .native import interpolate_binary_masks

    display = interpolate_binary_masks(
        lower_display, upper_display, factor, cancel_flag=cancel_flag
    )
    base = interpolate_binary_masks(
        lower_base, upper_base, factor, cancel_flag=cancel_flag
    )
    inherited_coverage = np.ascontiguousarray(
        lower_coverage | upper_coverage, dtype=np.bool_
    )
    return display, base, inherited_coverage


def _assign_provisional_canvas(
    context: Any,
    project: Any,
    session: StudioSession,
    image: Any,
) -> None:
    window = find_window(session.window_pointer)
    scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
    if scene is None:
        raise StudioError("The Quick SDF Paint scene disappeared while preparing this angle")
    scene.tool_settings.image_paint.mode = "IMAGE"
    scene.tool_settings.image_paint.canvas = image
    if window is not None:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
            area.tag_redraw()
    try:
        _assign_preview(project, image)
    except (ReferenceError, RuntimeError, ValueError):
        pass
    tag_studio_redraw()


def _publish_provisional_result(job: dict[str, Any], result: tuple[Any, Any, Any]) -> None:
    import numpy as np

    from . import runtime
    from .bitplane import BitplaneRole, encode_bitplane

    session = _SESSION
    project = resolve_session_project(session)
    if (
        session is None
        or project is None
        or session.project_uuid != job["project_uuid"]
        or session.provisional_uuid != job["provisional_uuid"]
    ):
        return
    if (
        session.provisional_source_token != job["source_token"]
        or _provisional_token(project, job["side"]) != job["source_token"]
    ):
        discard_provisional(bpy.context, project, keep_preview_angle=True)
        return
    interpolated_display, base, inherited_coverage = result
    resolution = int(project.resolution)
    if interpolated_display.shape != (resolution, resolution):
        raise StudioError("The prepared angle has an unexpected resolution")
    # Boundary is a generated layer, never a hand-painted override. Evaluate it
    # at the provisional angle before deriving Coverage so Boundary-only pixels
    # cannot accidentally become permanent paint when the key is promoted.
    generated = base
    if project.boundary_tracks:
        from .boundary import evaluate_boundary_mask

        generated = evaluate_boundary_mask(
            project,
            float(job["angle"]),
            str(job["side"]),
            base,
        )
    coverage = np.ascontiguousarray(inherited_coverage, dtype=np.bool_)
    display = np.ascontiguousarray(
        np.where(coverage, interpolated_display, generated), dtype=np.bool_
    )
    _remove_provisional_image(session)
    image = bpy.data.images.new(
        f"QSDF Pending {str(project.uuid)[:8]} {float(job['angle']):04.1f}",
        width=resolution,
        height=resolution,
        alpha=True,
        float_buffer=False,
    )
    runtime.tag_image(
        image,
        str(project.uuid),
        str(job["provisional_uuid"]),
        PROVISIONAL_DISPLAY_ROLE,
    )
    runtime.make_image_opaque(image)
    rgba = np.ones((resolution, resolution, 4), dtype=np.float32)
    rgba[..., :3] = np.asarray(display, dtype=np.float32)[..., None]
    runtime.write_image_rgba(image, rgba)
    session.provisional_image_name = image.name
    session.provisional_base_blob = encode_bitplane(
        np.ascontiguousarray(base, dtype=np.bool_), BitplaneRole.BASE
    )
    session.provisional_coverage_blob = encode_bitplane(
        np.ascontiguousarray(coverage, dtype=np.bool_), BitplaneRole.COVERAGE
    )
    session.provisional_base_revision = 1
    session.provisional_coverage_revision = 1
    session.provisional_history = None
    session.provisional_state = "READY"
    session.view_mode = "PROVISIONAL"
    _assign_provisional_canvas(bpy.context, project, session, image)


def _poll_provisional_job() -> float | None:
    global _PROVISIONAL_JOB

    job = _PROVISIONAL_JOB
    if job is None:
        return None
    from .jobs import JobState

    manager = job["manager"]
    state = manager.poll()
    if state in {JobState.PENDING, JobState.RUNNING}:
        return 0.05
    _PROVISIONAL_JOB = None
    try:
        result = manager.take_result()
        _publish_provisional_result(job, result)
    except Exception as exc:
        session = _SESSION
        if session is not None and session.provisional_uuid == job["provisional_uuid"]:
            session.provisional_state = "FAILED"
            session.projection_hint = f"Could not prepare this angle: {exc}"
    finally:
        manager.shutdown(wait=False)
    return None


def settle_seek(context: Any, project: Any, angle: float) -> float:
    """Snap to an existing key or prepare a session-only key for painting."""

    from . import runtime
    from .jobs import GenerationJobManager
    from .model import AUTO_KEY_ANGLE_STEP, AUTO_KEY_SNAP_DEGREES, MAX_KEYS_PER_SIDE

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        return seek_preview(context, project, angle)
    clamped = max(0.0, min(90.0, float(angle)))
    value = float(int(clamped / AUTO_KEY_ANGLE_STEP + 0.5)) * AUTO_KEY_ANGLE_STEP
    if (
        session.provisional_uuid
        and not session.provisional_promoting
        and abs(float(session.provisional_angle) - value) <= 1.0e-6
        and session.provisional_state in {"PREPARING", "READY", "LIMIT"}
    ):
        project.seek_angle = value
        session.seek_angle = value
        tag_studio_redraw()
        return value
    mode = str(project.symmetry_mode)
    side = str(
        project.active_side
        if mode == "INDEPENDENT"
        else project.authoring_side
    )
    items = _provisional_lane(project, side)
    if not items:
        return seek_preview(context, project, value)
    nearest = min(items, key=lambda item: abs(float(item.angle) - value))
    if abs(float(nearest.angle) - value) <= AUTO_KEY_SNAP_DEGREES:
        discard_provisional(context, project)
        select_paint_key(context, project, key_uuid=str(nearest.uuid))
        return float(nearest.angle)

    seek_preview(context, project, value, show_in_image_editor=True)
    discard_provisional(context, project, keep_preview_angle=True)
    session.provisional_uuid = runtime.new_uuid()
    session.provisional_angle = value
    session.provisional_side = side
    session.provisional_previous_key_uuid = session.paint_key_uuid
    session.seek_angle = value
    session.view_mode = "PREVIEW"
    if len(items) >= MAX_KEYS_PER_SIDE:
        session.provisional_state = "LIMIT"
        session.projection_hint = (
            f"Maximum {MAX_KEYS_PER_SIDE} keys. Select or delete an existing key."
        )
        tag_studio_redraw()
        return value

    lower = max((item for item in items if float(item.angle) < value), key=lambda item: float(item.angle))
    upper = min((item for item in items if float(item.angle) > value), key=lambda item: float(item.angle))
    lower_angle = float(lower.angle)
    upper_angle = float(upper.angle)
    factor = (value - lower_angle) / (upper_angle - lower_angle)
    token = _provisional_token(project, side)
    session.provisional_source_token = token
    session.provisional_state = "PREPARING"
    session.projection_hint = f"Preparing {value:.0f} degrees..."
    manager = GenerationJobManager(thread_name_prefix="QuickSDFAutoKey")
    cancel_flag = ctypes.c_int(0)
    manager.submit(
        _compute_provisional_masks,
        runtime.image_mask(runtime.resolve_display_image(project, lower)),
        runtime.image_mask(runtime.resolve_display_image(project, upper)),
        runtime.base_mask(lower).copy(),
        runtime.base_mask(upper).copy(),
        runtime.coverage_mask(lower).copy(),
        runtime.coverage_mask(upper).copy(),
        factor,
        cancel_flag,
    )
    global _PROVISIONAL_JOB
    _PROVISIONAL_JOB = {
        "manager": manager,
        "cancel_flag": cancel_flag,
        "project_uuid": str(project.uuid),
        "provisional_uuid": session.provisional_uuid,
        "source_token": token,
        "side": side,
        "angle": value,
    }
    if not bpy.app.timers.is_registered(_poll_provisional_job):
        bpy.app.timers.register(_poll_provisional_job, first_interval=0.05)
    tag_studio_redraw()
    return value


def activate_provisional_for_stroke(context: Any, project: Any) -> Any | None:
    """Arm the ready session Image for paint without adding a project key."""

    from . import runtime

    session = active_session(context)
    if session is None or not session.provisional_uuid:
        return None
    if session.provisional_state == "PREPARING":
        raise StudioError("This angle is still being prepared")
    if session.provisional_state == "LIMIT":
        raise StudioError("Maximum 16 keys. Select or delete an existing key")
    if session.provisional_state != "READY":
        raise StudioError("This angle is not ready for painting")
    image = bpy.data.images.get(session.provisional_image_name)
    if image is None:
        raise StudioError("The prepared paint canvas is missing")
    # Generated Images can reload ``generated_color`` when Blender changes the
    # projection-paint canvas.  Persist the prepared pixels before the native
    # modal operator starts; otherwise the canvas can become entirely black.
    image.pack()
    session.provisional_promoting = True
    session.view_mode = "PROVISIONAL_STROKE"
    _assign_provisional_canvas(context, project, session, image)
    return image


def promote_provisional_after_stroke(context: Any, project: Any) -> Any:
    """Persist an armed provisional angle after crossing the binary boundary."""

    from . import runtime

    session = active_session(context)
    if (
        session is None
        or not session.provisional_promoting
        or not session.provisional_uuid
    ):
        raise StudioError("There is no painted in-between angle to add")
    image = bpy.data.images.get(session.provisional_image_name)
    if image is None:
        raise StudioError("The prepared paint canvas is missing")
    provisional_uuid = str(session.provisional_uuid)
    provisional_angle = float(session.provisional_angle)
    provisional_side = str(session.provisional_side)
    if any(str(candidate.uuid) == provisional_uuid for candidate in project.angles):
        return next(
            candidate
            for candidate in project.angles
            if str(candidate.uuid) == provisional_uuid
        )
    item = project.angles.add()
    item.uuid = provisional_uuid
    item.angle = provisional_angle
    item.side = provisional_side
    item.display_image = image
    item.display_image_name = image.name
    for property_name, payload in (
        (runtime.BASE_BITPLANE_KEY, session.provisional_base_blob),
        (runtime.COVERAGE_BITPLANE_KEY, session.provisional_coverage_blob),
    ):
        if property_name in item:
            del item[property_name]
        item[property_name] = bytes(payload)
    item.base_revision = session.provisional_base_revision
    item.coverage_revision = session.provisional_coverage_revision
    item["_qsdf_provisional"] = True
    desired = [
        str(candidate.uuid)
        for candidate in sorted(
            project.angles,
            key=lambda candidate: (
                0 if str(candidate.side) == "RIGHT" else 1,
                float(candidate.angle),
            ),
        )
    ]
    for target, candidate_uuid in enumerate(desired):
        current = next(
            index
            for index, candidate in enumerate(project.angles)
            if str(candidate.uuid) == candidate_uuid
        )
        if current != target:
            project.angles.move(current, target)
    # Collection.move() can rebind an existing PropertyGroup wrapper to a
    # different element. Resolve the promoted key again by its captured UUID.
    index = next(
        index
        for index, candidate in enumerate(project.angles)
        if str(candidate.uuid) == provisional_uuid
    )
    item = project.angles[index]
    project.active_angle_index = index
    project.active_angle_uuid = provisional_uuid
    project.active_side = provisional_side
    project.seek_angle = provisional_angle
    runtime.tag_image(image, str(project.uuid), provisional_uuid, runtime.DISPLAY_ROLE)
    tag_studio_redraw()
    return project.angles[index]


def provisional_created_metadata(project: Any, item: Any) -> dict[str, Any] | None:
    session = _SESSION
    if (
        session is None
        or not session.provisional_promoting
        or str(getattr(item, "uuid", "")) != session.provisional_uuid
    ):
        return None
    from . import runtime

    return {
        "kind": "CREATE_KEY",
        "uuid": str(item.uuid),
        "angle": float(item.angle),
        "side": str(item.side),
        "display_image_name": str(item.display_image_name),
        "base_blob": runtime.bitplane_blob(item, "BASE"),
        "coverage_blob": runtime.bitplane_blob(item, "COVERAGE"),
        "base_revision": int(item.base_revision),
        "coverage_revision": int(item.coverage_revision),
    }


def retain_provisional_after_stroke(
    context: Any,
    project: Any,
    *,
    gray_before: Any,
    gray: Any,
    touched: Any,
    coverage_before: Any,
) -> None:
    """Keep a soft provisional stroke without creating a persistent key.

    Display values below the binary 0.5 boundary do not affect the exported
    threshold map yet.  They still need to remain on the session Canvas so a
    low-strength brush can build up naturally across several strokes.  The
    accumulated paint coverage is promoted together with the key once a later
    stroke crosses the boundary.
    """

    import numpy as np

    from . import runtime
    from .bitplane import BitplaneRole, encode_bitplane

    session = active_session(context)
    if (
        session is None
        or not session.provisional_promoting
        or not session.provisional_uuid
    ):
        raise StudioError("There is no prepared in-between angle to retain")
    image = bpy.data.images.get(session.provisional_image_name)
    if image is None:
        raise StudioError("The prepared paint canvas is missing")
    before_values = np.ascontiguousarray(gray_before, dtype=np.uint8)
    values = np.ascontiguousarray(gray, dtype=np.uint8)
    changed = np.ascontiguousarray(touched, dtype=np.bool_)
    coverage = np.ascontiguousarray(coverage_before, dtype=np.bool_).copy()
    if (
        before_values.shape != values.shape
        or values.shape != changed.shape
        or coverage.shape != values.shape
    ):
        raise StudioError("The prepared paint layers changed size during the stroke")
    coverage[changed] = True
    from .history import History

    history = session.provisional_history
    if history is None:
        history = History(compression_level=1)
        session.provisional_history = history
    transaction = history.begin_transaction("Paint Prepared Angle")
    transaction.add_delta("display", before_values, values)
    transaction.add_delta(
        "coverage",
        np.ascontiguousarray(coverage_before, dtype=np.bool_),
        coverage,
    )
    if not transaction.commit():
        if history.active_transaction is transaction:
            transaction.rollback()
        raise StudioError("This stroke is too large for the Quick SDF Undo history")
    session.provisional_coverage_blob = encode_bitplane(
        coverage,
        BitplaneRole.COVERAGE,
    )
    session.provisional_coverage_revision = max(
        1,
        int(session.provisional_coverage_revision) + 1,
    )
    runtime.cache_image_gray8(image, values)
    dismiss_first_stroke_hint()
    finish_provisional_stroke(context, project, changed=False)


def provisional_history_state(
    context: Any,
    project: Any,
    *,
    redo: bool = False,
) -> tuple[bool, bool]:
    """Return whether the temporary Canvas owns Undo and has an action."""

    session = active_session(context)
    active = bool(
        session is not None
        and session.project_uuid == str(project.uuid)
        and session.provisional_uuid
        and session.provisional_state == "READY"
        and not session.provisional_promoting
    )
    if not active:
        return False, False
    history = session.provisional_history
    available = bool(
        history is not None
        and (history.can_redo if redo else history.can_undo)
    )
    return True, available


def apply_provisional_history(
    context: Any,
    project: Any,
    *,
    redo: bool = False,
) -> bool:
    """Undo or redo one soft stroke without touching persistent history."""

    import numpy as np

    from . import runtime
    from .bitplane import BitplaneRole, decode_bitplane, encode_bitplane

    active, available = provisional_history_state(context, project, redo=redo)
    if not active or not available:
        return False
    session = active_session(context)
    assert session is not None
    image = bpy.data.images.get(session.provisional_image_name)
    if image is None:
        raise StudioError("The prepared paint canvas is missing")
    history = session.provisional_history
    coverage = decode_bitplane(
        bytes(session.provisional_coverage_blob),
        expected_role=BitplaneRole.COVERAGE,
    )
    current = {
        "display": runtime.image_gray8(image),
        "coverage": np.ascontiguousarray(coverage, dtype=np.bool_),
    }
    action = history.redo_action(current) if redo else history.undo_action(current)
    if action is None:
        return False
    display = action.images.get("display", current["display"])
    restored_coverage = action.images.get("coverage", current["coverage"])
    runtime.write_image_gray8(image, display)
    runtime.cache_image_gray8(image, display)
    session.provisional_coverage_blob = encode_bitplane(
        np.ascontiguousarray(restored_coverage, dtype=np.bool_),
        BitplaneRole.COVERAGE,
    )
    session.provisional_coverage_revision = max(
        1,
        int(session.provisional_coverage_revision) + 1,
    )
    _assign_provisional_canvas(context, project, session, image)
    tag_studio_redraw()
    return True


def finish_provisional_stroke(context: Any, project: Any, *, changed: bool) -> None:
    session = active_session(context)
    if session is None or not session.provisional_promoting:
        return
    uuid = session.provisional_uuid
    item = next((candidate for candidate in project.angles if str(candidate.uuid) == uuid), None)
    image = bpy.data.images.get(session.provisional_image_name)
    if changed and item is not None:
        if "_qsdf_provisional" in item:
            del item["_qsdf_provisional"]
        if image is not None:
            from . import runtime

            runtime.tag_image(image, str(project.uuid), uuid, runtime.DISPLAY_ROLE)
        session.provisional_uuid = ""
        session.provisional_angle = -1.0
        session.provisional_side = ""
        session.provisional_image_name = ""
        session.provisional_base_blob = b""
        session.provisional_coverage_blob = b""
        session.provisional_base_revision = 0
        session.provisional_coverage_revision = 0
        session.provisional_state = "NONE"
        session.provisional_source_token = ()
        session.provisional_promoting = False
        session.provisional_previous_key_uuid = ""
        session.provisional_history = None
        session.view_mode = "EDIT"
        session.paint_key_uuid = uuid
        session.paint_key_angle = float(item.angle)
        return
    if item is not None:
        index = next(index for index, candidate in enumerate(project.angles) if str(candidate.uuid) == uuid)
        project.angles.remove(index)
    if image is not None:
        from . import runtime

        runtime.tag_image(image, str(project.uuid), uuid, PROVISIONAL_DISPLAY_ROLE)
    session.provisional_promoting = False
    session.provisional_state = "READY"
    session.view_mode = "PROVISIONAL"
    previous = next(
        (
            candidate
            for candidate in project.angles
            if str(candidate.uuid) == session.provisional_previous_key_uuid
        ),
        None,
    )
    if previous is not None:
        previous_index = next(
            index
            for index, candidate in enumerate(project.angles)
            if str(candidate.uuid) == str(previous.uuid)
        )
        project.active_angle_index = previous_index
        project.active_angle_uuid = str(previous.uuid)
        project.active_side = str(previous.side)
    if image is not None:
        _assign_provisional_canvas(context, project, session, image)


def seek_preview(
    context: Any,
    project: Any,
    angle: float,
    *,
    show_in_image_editor: bool = False,
) -> float:
    """Scrub a transient result without changing the edit key or paint canvas."""

    value = max(0.0, min(90.0, float(angle)))
    session = active_session(context)
    if (
        session is not None
        and session.provisional_uuid
        and not session.provisional_promoting
        and abs(float(session.provisional_angle) - value) > 1.0e-6
    ):
        discard_provisional(context, project)
    if (
        session is not None
        and session.project_uuid == str(project.uuid)
        and session.editing_aux_mask_uuid
    ):
        return float(project.seek_angle)
    project.seek_angle = value
    if session is not None and session.project_uuid == str(project.uuid):
        ensure_studio_material_preview(context, session=session)
        session.view_mode = "PREVIEW"
        session.seek_angle = value
    preview_image = None
    try:
        from .live_preview import update_seek_preview

        preview_image = update_seek_preview(project, value)
    except (ImportError, ReferenceError, RuntimeError, ValueError):
        pass
    if show_in_image_editor and session is not None and preview_image is not None:
        window = find_window(session.window_pointer)
        if window is not None:
            for area in window.screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                area.spaces.active.image = preview_image
                area.tag_redraw()
    tag_studio_redraw()
    return value


def back_to_paint(context: Any, project: Any) -> bool:
    """Return Preview to the stored edit key before any mutating action."""

    session = active_session(context)
    if (
        session is not None
        and session.project_uuid == str(project.uuid)
        and session.provisional_uuid
    ):
        if session.provisional_state == "READY" and not session.provisional_promoting:
            image = bpy.data.images.get(session.provisional_image_name)
            if image is not None:
                _assign_provisional_canvas(context, project, session, image)
                return True
        if session.provisional_state in {"PREPARING", "LIMIT", "FAILED"}:
            return False
    if (
        session is not None
        and session.project_uuid == str(project.uuid)
        and session.editing_aux_mask_uuid
    ):
        from .runtime import sync_canvas

        return sync_canvas(context, project) is not None
    if session is not None and session.project_uuid == str(project.uuid):
        active_uuid = str(project.active_angle_uuid)
        canvas = getattr(context.scene.tool_settings.image_paint, "canvas", None)
        canvas_uuid = str(canvas.get("quick_sdf_angle_uuid", "")) if canvas is not None else ""
        # The common paint path is already synchronized. Re-selecting the key
        # here used to rebuild the preview material on every mouse press, which
        # caused a black shader-compilation frame and severe input latency.
        if (
            session.view_mode == "EDIT"
            and session.paint_key_uuid == active_uuid
            and canvas_uuid == active_uuid
            and not session.export_review_active
        ):
            return True
    key_uuid = session.paint_key_uuid if session is not None else str(project.active_angle_uuid)
    if select_paint_key(context, project, key_uuid=key_uuid):
        return True
    return select_paint_key(context, project, index=int(project.active_angle_index))


def ensure_paint_surface(context: Any | None = None) -> bool:
    """Return Studio Image Editors to the authoring Paint surface.

    Blender resets ``SpaceImage.ui_mode`` to ``VIEW`` whenever the active
    ``image_paint.canvas`` is cleared.  The pixel upload path restores the mode
    around its own detach, but a global Undo/Redo can still strand the editor
    on the read-only View surface.  Quick SDF Paint owns that surface while a
    session is active, so put it back unless the export review is showing a
    heatmap on purpose.
    """

    context = context or bpy.context
    session = active_session(context)
    if session is None or bool(getattr(session, "export_review_active", False)):
        return False
    window = find_window(session.window_pointer) or getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    if screen is None:
        return False
    restored = False
    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        space = area.spaces.active
        if hasattr(space, "ui_mode"):
            if str(space.ui_mode) != "PAINT":
                space.ui_mode = "PAINT"
                restored = True
        elif hasattr(space, "mode"):
            if str(space.mode) != "PAINT":
                space.mode = "PAINT"
                restored = True
        else:
            continue
        area.tag_redraw()
    return restored


def reconcile_view_state(context: Any | None = None) -> bool:
    """Re-resolve transient key IDs after Undo/Redo or key list edits."""

    context = context or bpy.context
    session = active_session(context)
    project = resolve_session_project(session)
    if session is None or project is None:
        return False
    if session.editing_aux_mask_uuid:
        from .runtime import aux_mask_for_uuid, resolve_aux_mask_image, sync_canvas

        item = aux_mask_for_uuid(project, session.editing_aux_mask_uuid)
        if resolve_aux_mask_image(project, item) is None:
            session.editing_aux_mask_uuid = ""
            session.view_mode = "EDIT"
        else:
            return sync_canvas(context, project) is not None
    resolved_index, item = _resolve_paint_key(project, key_uuid=session.paint_key_uuid)
    if item is None:
        resolved_index, item = _resolve_paint_key(project)
    if item is None or resolved_index is None:
        return False
    session.paint_key_uuid = str(getattr(item, "uuid", ""))
    session.paint_key_angle = float(getattr(item, "angle", 0.0))
    project.active_angle_index = resolved_index
    project.active_angle_uuid = session.paint_key_uuid
    if session.view_mode == "PREVIEW":
        from .runtime import sync_canvas

        sync_canvas(context, project)
        seek_preview(context, project, session.seek_angle)
        return True
    return select_paint_key(context, project, key_uuid=session.paint_key_uuid)


def _arm_stroke_watchdog(session: StudioSession) -> None:
    now = time.monotonic()
    session.stroke_watchdog_deadline = now + 0.75
    session.stroke_watchdog_seen_modal = False
    session.stroke_watchdog_absent_since = 0.0
    if not bpy.app.timers.is_registered(_stroke_restore_watchdog):
        bpy.app.timers.register(_stroke_restore_watchdog, first_interval=0.05)


def prepare_stroke_brush(context: Any, project: Any) -> None:
    """Temporarily apply the selected binary colour without changing assets."""

    session = active_session(context)
    if session is None:
        return
    ensure_studio_material_preview(context, session=session)
    restore_stroke_brush(context)
    image_paint = context.scene.tool_settings.image_paint
    brush = getattr(image_paint, "brush", None)
    session.stroke_from_view3d = getattr(getattr(context, "area", None), "type", "") == "VIEW_3D"
    session.projection_hint = ""
    value = 1.0 if int(project.paint_value) else 0.0
    inverse = 1.0 - value
    unified = getattr(image_paint, "unified_paint_settings", None)
    if unified is not None:
        session.stroke_unified_use_color = bool(unified.use_unified_color)
        session.stroke_unified_color = tuple(
            float(channel) for channel in unified.color[:3]
        )
        session.stroke_unified_secondary_color = tuple(
            float(channel) for channel in unified.secondary_color[:3]
        )
        try:
            # Force the transient unified colour path so the active Brush Asset
            # is never modified. Blender 5.1 otherwise chooses between Brush
            # and Unified colours based on the user's setting.
            unified.use_unified_color = True
            unified.color = (value, value, value)
            unified.secondary_color = (inverse, inverse, inverse)
            _arm_stroke_watchdog(session)
            return
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            try:
                if session.stroke_unified_color is not None:
                    unified.color = session.stroke_unified_color
                if session.stroke_unified_secondary_color is not None:
                    unified.secondary_color = session.stroke_unified_secondary_color
                if session.stroke_unified_use_color is not None:
                    unified.use_unified_color = session.stroke_unified_use_color
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass
            session.stroke_unified_use_color = None
            session.stroke_unified_color = None
            session.stroke_unified_secondary_color = None
    # Blender 5.1 exposes brush colour through the Brush instead.
    if brush is None:
        return
    session.stroke_brush_name = str(getattr(brush, "name", ""))
    session.stroke_brush_color = tuple(float(channel) for channel in brush.color[:3])
    session.stroke_brush_secondary_color = tuple(
        float(channel) for channel in brush.secondary_color[:3]
    )
    try:
        brush.color = (value, value, value)
        brush.secondary_color = (inverse, inverse, inverse)
        brush.update_tag()
        _arm_stroke_watchdog(session)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        session.stroke_brush_name = ""
        session.stroke_brush_color = None
        session.stroke_brush_secondary_color = None


def restore_stroke_brush(
    context: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> None:
    """Restore a Brush Asset after the native stroke, cancel, save, or exit."""

    session = session or _SESSION
    if session is None:
        return
    brush = bpy.data.brushes.get(session.stroke_brush_name) if session.stroke_brush_name else None
    if brush is not None and session.stroke_brush_color is not None:
        try:
            brush.color = session.stroke_brush_color
            if session.stroke_brush_secondary_color is not None:
                brush.secondary_color = session.stroke_brush_secondary_color
            brush.update_tag()
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None or str(getattr(scene, "name", "")) != session.scene_name:
        scene = bpy.data.scenes.get(session.scene_name)
    image_paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
    unified = getattr(image_paint, "unified_paint_settings", None)
    if unified is not None and session.stroke_unified_color is not None:
        try:
            unified.color = session.stroke_unified_color
            if session.stroke_unified_secondary_color is not None:
                unified.secondary_color = session.stroke_unified_secondary_color
            if session.stroke_unified_use_color is not None:
                unified.use_unified_color = session.stroke_unified_use_color
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    session.stroke_brush_name = ""
    session.stroke_brush_color = None
    session.stroke_brush_secondary_color = None
    session.stroke_unified_use_color = None
    session.stroke_unified_color = None
    session.stroke_unified_secondary_color = None
    session.stroke_watchdog_deadline = 0.0
    session.stroke_watchdog_seen_modal = False
    session.stroke_watchdog_absent_since = 0.0


def _stroke_restore_watchdog() -> float | None:
    """Restore temporary colours when Blender cancels a native modal stroke."""

    session = _SESSION
    if session is None:
        return None
    override_active = bool(
        session.stroke_brush_color is not None
        or session.stroke_unified_color is not None
    )
    if not override_active:
        return None
    window = find_window(session.window_pointer)
    modal_ids = {
        str(
            getattr(operator, "bl_idname", "")
            or getattr(getattr(operator, "bl_rna", None), "identifier", "")
        )
        for operator in getattr(window, "modal_operators", ())
    }
    paint_modal = any(
        identifier in {
            "QUICKSDF_OT_range_paint",
            "QUICKSDF_OT_range_paint_invert",
            "PAINT_OT_image_paint",
        }
        or identifier.endswith("_OT_image_paint")
        for identifier in modal_ids
    )
    if paint_modal:
        session.stroke_watchdog_seen_modal = True
        session.stroke_watchdog_absent_since = 0.0
        return 0.05
    now = time.monotonic()
    if now < float(session.stroke_watchdog_deadline):
        return 0.05
    if session.stroke_watchdog_seen_modal:
        if session.stroke_watchdog_absent_since <= 0.0:
            session.stroke_watchdog_absent_since = now
            return 0.05
        if now - session.stroke_watchdog_absent_since < 0.6:
            return 0.05
    project = resolve_session_project(session)
    restore_stroke_brush(session=session)
    if project is not None:
        try:
            from . import runtime

            snapshot = runtime.consume_interactive_paint_snapshot(project)
            if snapshot is not None:
                _uuid, image_name, before_gray, _coverage_uuid, _coverage = snapshot
                image = bpy.data.images.get(str(image_name))
                if image is not None:
                    runtime.write_image_gray8(image, before_gray)
            aux_snapshot = runtime.consume_aux_paint_snapshot(project)
            if aux_snapshot is not None:
                _mask_uuid, image_name, before_rgba = aux_snapshot
                image = bpy.data.images.get(str(image_name))
                if image is not None:
                    runtime.write_image_rgba8(image, before_rgba)
            runtime.discard_paint_snapshots(project)
        except (
            AttributeError,
            ImportError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
        # If Blender cancelled the native modal stroke, the propagation macro
        # never gets a chance to decide whether an auto key changed. Restore
        # the session-only canvas and remove any partially promoted key.
        if session.provisional_promoting:
            try:
                finish_provisional_stroke(bpy.context, project, changed=False)
            except (AttributeError, ReferenceError, RuntimeError):
                discard_provisional(bpy.context, project)
    return None


def _cancel_stroke_watchdog() -> None:
    if bpy.app.timers.is_registered(_stroke_restore_watchdog):
        try:
            bpy.app.timers.unregister(_stroke_restore_watchdog)
        except ValueError:
            pass


def _release_project_history(project: Any | None = None) -> None:
    try:
        from .operators import clear_histories

        if project is None:
            clear_histories()
        else:
            clear_histories(
                str(project.uuid),
                release_fence=True,
            )
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass


def _discard_project_paint_snapshots(project: Any | None) -> None:
    if project is None:
        return
    try:
        from .runtime import discard_paint_snapshots

        discard_paint_snapshots(project)
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass


def set_projection_hint(context: Any | None, *, no_change: bool) -> None:
    """Show a quiet 3D-only projection hint after a no-op native stroke."""

    session = active_session(context or bpy.context)
    if session is None:
        return
    session.projection_hint = (
        "No paint landed · move back or press Numpad 5"
        if no_change and session.stroke_from_view3d
        else ""
    )
    tag_studio_redraw()


def studio_areas(window: Any) -> tuple[Any | None, Any | None, Any | None]:
    screen = getattr(window, "screen", None)
    areas = getattr(screen, "areas", ()) if screen else ()
    view = next((area for area in areas if area.type == "VIEW_3D"), None)
    image = next((area for area in areas if area.type == "IMAGE_EDITOR"), None)
    timeline = next((area for area in areas if area.type == TIMELINE_SPACE_TYPE), None)
    return view, image, timeline


def _window_region(area: Any) -> Any | None:
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _operator_succeeded(result: Any) -> bool:
    return bool(result and "FINISHED" in result)


def _valid_studio_layout(workspace: Any) -> bool:
    return any(
        any(area.type == "VIEW_3D" for area in screen.areas)
        and any(area.type == "IMAGE_EDITOR" for area in screen.areas)
        for screen in workspace.screens
    )


def find_studio_workspace(project_uuid: str) -> Any | None:
    for workspace in bpy.data.workspaces:
        name = str(getattr(workspace, "name", ""))
        has_current_name = name == WORKSPACE_BASENAME or name.startswith(
            f"{WORKSPACE_BASENAME}."
        )
        if (
            has_current_name
            and workspace.get(WORKSPACE_PROJECT_TAG) == project_uuid
            and int(workspace.get(WORKSPACE_VERSION_TAG, 0)) == WORKSPACE_LAYOUT_VERSION
        ):
            return workspace
    return None


def _duplicate_workspace(context: Any, window: Any, project: Any) -> Any:
    previous = window.workspace
    before = {candidate.as_pointer() for candidate in bpy.data.workspaces}
    with context.temp_override(window=window):
        result = bpy.ops.workspace.duplicate()
    workspace = next(
        (candidate for candidate in bpy.data.workspaces if candidate.as_pointer() not in before),
        None,
    )
    if not _operator_succeeded(result) or workspace is None:
        raise StudioError("Could not create the Quick SDF Paint workspace")
    window.workspace = workspace
    workspace.name = WORKSPACE_BASENAME
    workspace[WORKSPACE_PROJECT_TAG] = str(project.uuid)
    workspace[WORKSPACE_VERSION_TAG] = WORKSPACE_LAYOUT_VERSION
    return workspace


def _collapse_screen(context: Any, window: Any) -> Any:
    screen = window.screen
    # ``screen.area_close`` is a modal UI command and cannot be used inside an
    # atomic Python transaction. Explicit-coordinate area_join is synchronous.
    attempts = 0
    while len(screen.areas) > 1:
        attempts += 1
        if attempts > 32:
            raise StudioError(
                "Quick SDF Paint layout changes did not settle; try opening it again"
            )
        _join_one_area(context, window)
        screen = window.screen
    return screen.areas[0]


def _split_area(context: Any, window: Any, area: Any, direction: str, factor: float) -> None:
    region = _window_region(area)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.screen.area_split(direction=direction, factor=factor)
    if not _operator_succeeded(result):
        raise StudioError("Could not split the Quick SDF Paint workspace")


def _timeline_host_tree(project_uuid: str) -> Any:
    """Return a process-only empty tree for the timeline's Node Editor host.

    A plain Node Editor follows the active material.  The opaque timeline draw
    handler then hides, but does not disable, the real node buttons underneath;
    clicking the seek rail can consequently open a Principled BSDF enum.  A
    pinned empty tree leaves no hidden UI blocks for Blender to dispatch first.
    """

    for tree in bpy.data.node_groups:
        if bool(tree.get(TIMELINE_HOST_TAG, False)):
            tree[TIMELINE_HOST_TAG] = str(project_uuid)
            return tree
    tree = bpy.data.node_groups.new(TIMELINE_HOST_NAME, "ShaderNodeTree")
    tree[TIMELINE_HOST_TAG] = str(project_uuid)
    tree.use_fake_user = False
    return tree


def _bind_timeline_host(area: Any, project_uuid: str) -> Any:
    space = area.spaces.active
    if hasattr(space, "tree_type"):
        space.tree_type = "ShaderNodeTree"
    tree = _timeline_host_tree(project_uuid)
    if hasattr(space, "pin"):
        space.pin = True
    if hasattr(space, "node_tree"):
        space.node_tree = tree
    return tree


def _remove_timeline_hosts() -> None:
    """Detach and delete every runtime timeline host before save/load/exit."""

    # Blender exposes ``_RestrictData`` while enabling an add-on. Registration
    # and failed-registration cleanup must never enumerate datablocks there.
    if not hasattr(bpy.data, "node_groups") or not hasattr(bpy.data, "screens"):
        return
    hosts = {
        tree.as_pointer(): tree
        for tree in bpy.data.node_groups
        if bool(tree.get(TIMELINE_HOST_TAG, False))
    }
    if not hosts:
        return
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != TIMELINE_SPACE_TYPE:
                continue
            for space in area.spaces:
                tree = getattr(space, "node_tree", None)
                if tree is None or tree.as_pointer() not in hosts:
                    continue
                try:
                    if hasattr(space, "pin"):
                        space.pin = False
                    space.node_tree = None
                except (AttributeError, ReferenceError, RuntimeError, TypeError):
                    pass
    for tree in tuple(hosts.values()):
        try:
            bpy.data.node_groups.remove(tree)
        except (ReferenceError, RuntimeError):
            pass


def _ensure_session_timeline_host(session: StudioSession | None = None) -> bool:
    session = session or _SESSION
    if session is None:
        return False
    window = find_window(session.window_pointer)
    if window is None:
        return False
    _view, _image, timeline_area = studio_areas(window)
    if timeline_area is None:
        return False
    _bind_timeline_host(timeline_area, session.project_uuid)
    return True


def configure_workspace(context: Any, window: Any) -> tuple[Any, Any, Any]:
    """Create Canvas/3D; lighting controls live inside the 3D viewport."""

    base = _collapse_screen(context, window)
    _split_area(context, window, base, "VERTICAL", 0.5)
    return _configure_area_types(window)


def _configure_legacy_workspace(context: Any, window: Any) -> tuple[Any, Any, Any]:
    """Retained standalone timeline layout, no longer the default."""

    base = _collapse_screen(context, window)
    factor = max(0.08, min(0.24, TIMELINE_HEIGHT / max(1.0, float(base.height))))
    _split_area(context, window, base, "HORIZONTAL", factor)
    screen = window.screen
    bottom = min(screen.areas, key=lambda area: area.y + area.height * 0.5)
    top = max(screen.areas, key=lambda area: area.y + area.height * 0.5)
    _split_area(context, window, top, "VERTICAL", 0.5)

    areas = list(screen.areas)
    bottom = min(areas, key=lambda area: area.y + area.height * 0.5)
    top_areas = sorted((area for area in areas if area != bottom), key=lambda area: area.x)
    if len(top_areas) != 2:
        raise StudioError("Quick SDF Paint did not produce the expected three areas")
    image_area, view_area = top_areas
    image_area.type = "IMAGE_EDITOR"
    view_area.type = "VIEW_3D"
    # A Dope Sheet exposes Blender's native frame playhead even with all of
    # its regions hidden.  That cursor looks like the Quick SDF angle seek,
    # changes the animated pose, and can mark the normal guide stale.  A Node
    # Editor gives the custom timeline a non-temporal host with no playhead.
    bottom.type = TIMELINE_SPACE_TYPE

    image_space = image_area.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    for space in (image_space, view_area.spaces.active):
        if hasattr(space, "show_region_tool_header"):
            space.show_region_tool_header = True
        if hasattr(space, "show_region_header"):
            space.show_region_header = True
    timeline_space = bottom.spaces.active
    _bind_timeline_host(
        bottom,
        str(getattr(window, "workspace", {}).get(WORKSPACE_PROJECT_TAG, "")),
    )
    if hasattr(timeline_space, "show_gizmo"):
        timeline_space.show_gizmo = True
    if hasattr(timeline_space, "show_gizmo_active_node"):
        timeline_space.show_gizmo_active_node = False
    for name in (
        "show_region_header", "show_region_footer", "show_region_toolbar",
        "show_region_tool_header", "show_region_ui",
    ):
        if hasattr(timeline_space, name):
            setattr(timeline_space, name, False)
    return view_area, image_area, bottom


def _set_active_object(context: Any, obj: Any) -> None:
    for selected in tuple(getattr(context, "selected_objects", ())):
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _mode_set(context: Any, window: Any, area: Any, mode: str) -> None:
    region = _window_region(area)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        if getattr(context.object, "mode", "OBJECT") != mode:
            result = bpy.ops.object.mode_set(mode=mode)
            if not _operator_succeeded(result):
                raise StudioError(f"Could not enter Blender mode: {mode}")


def _restore_selection_and_mode(context: Any, session: StudioSession, window: Any) -> None:
    for obj in context.view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for name in session.previous_selected_object_names:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.name in context.view_layer.objects:
            obj.select_set(True)
    active = bpy.data.objects.get(session.previous_active_object_name)
    if active is not None and active.name in context.view_layer.objects:
        context.view_layer.objects.active = active
        if session.previous_mode and session.previous_mode != "OBJECT":
            view = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
            if view is not None:
                try:
                    _mode_set(context, window, view, session.previous_mode)
                except (RuntimeError, StudioError):
                    pass


def _restore_paint_settings(scene: Any, session: StudioSession) -> None:
    image_paint = scene.tool_settings.image_paint
    if session.previous_image_paint_mode in {"MATERIAL", "IMAGE"}:
        image_paint.mode = session.previous_image_paint_mode
    image_paint.canvas = bpy.data.images.get(session.previous_canvas_name) if session.previous_canvas_name else None


def _studio_view_spaces(window: Any | None) -> tuple[Any, ...]:
    if window is None or getattr(window, "screen", None) is None:
        return ()
    return tuple(
        area.spaces.active
        for area in window.screen.areas
        if area.type == "VIEW_3D" and hasattr(area.spaces.active, "clip_start")
    )


def ensure_studio_material_preview(
    context: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> bool:
    """Keep the 3D paint surface visible after users change viewport shading.

    Texture Paint can remain active while a View3D is switched back to Solid.
    In that state Blender still edits the Image canvas, but the temporary Quick
    SDF material is not displayed.  Studio owns its cloned workspace, so it is
    safe to restore Material Preview whenever editing resumes.
    """

    session = session or active_session(context or bpy.context)
    if session is None:
        return False
    window = find_window(session.window_pointer)
    changed = False
    for space in _studio_view_spaces(window):
        shading = getattr(space, "shading", None)
        if shading is not None and str(getattr(shading, "type", "")) != "MATERIAL":
            shading.type = "MATERIAL"
            changed = True
    if changed:
        tag_studio_redraw()
    return True


def _adaptive_clip_start(obj: Any | None) -> float:
    dimensions = tuple(abs(float(value)) for value in getattr(obj, "dimensions", (1.0, 1.0, 1.0)))
    extent = max(dimensions or (1.0,))
    return max(1.0e-6, extent * 1.0e-4)


def _clear_paint_surface_snapshot(session: StudioSession) -> None:
    session.paint_surface_object_name = ""
    session.paint_surface_mesh_name = ""
    session.paint_surface_object_pointer = 0
    session.paint_surface_mesh_pointer = 0
    session.paint_surface_polygon_count = 0
    session.paint_surface_region_revision = -1
    session.paint_surface_previous_use_mask = None
    session.paint_surface_previous_use_mask_vertex = None
    session.paint_surface_previous_selection = b""
    session.paint_surface_previous_hidden = b""
    session.paint_surface_previous_active_material_index = -1
    session.paint_surface_previous_uv_index = -1
    session.paint_surface_suspended = False


def _paint_surface_ids(session: StudioSession) -> tuple[Any | None, Any | None]:
    obj = None
    mesh = None
    object_pointer = int(session.paint_surface_object_pointer)
    mesh_pointer = int(session.paint_surface_mesh_pointer)
    if object_pointer:
        obj = next(
            (
                candidate
                for candidate in bpy.data.objects
                if int(candidate.as_pointer()) == object_pointer
            ),
            None,
        )
    if mesh_pointer:
        mesh = next(
            (
                candidate
                for candidate in bpy.data.meshes
                if int(candidate.as_pointer()) == mesh_pointer
            ),
            None,
        )
    if obj is None:
        obj = bpy.data.objects.get(session.paint_surface_object_name)
    if mesh is None:
        mesh = bpy.data.meshes.get(session.paint_surface_mesh_name)
    if obj is not None and getattr(obj, "type", "") == "MESH":
        obj_mesh = getattr(obj, "data", None)
        if mesh is None or obj_mesh == mesh:
            mesh = obj_mesh
    if obj is None and mesh is not None:
        obj = next(
            (candidate for candidate in bpy.data.objects if candidate.data == mesh),
            None,
        )
    return obj, mesh


def _paint_surface_recovery_record(session: StudioSession) -> dict[str, Any] | None:
    if not session.paint_surface_object_name:
        return None
    obj, mesh = _paint_surface_ids(session)
    object_name = str(getattr(obj, "name", session.paint_surface_object_name))
    mesh_name = str(getattr(mesh, "name", session.paint_surface_mesh_name))
    session.paint_surface_object_name = object_name
    session.paint_surface_mesh_name = mesh_name
    return {
        "project_uuid": str(session.project_uuid),
        "object": object_name,
        "mesh": mesh_name,
        "polygon_count": int(session.paint_surface_polygon_count),
        "use_mask": session.paint_surface_previous_use_mask,
        "use_mask_vertex": session.paint_surface_previous_use_mask_vertex,
        "selection": base64.b64encode(
            session.paint_surface_previous_selection
        ).decode("ascii"),
        "hidden": base64.b64encode(
            session.paint_surface_previous_hidden
        ).decode("ascii"),
        "active_material_index": int(
            session.paint_surface_previous_active_material_index
        ),
        "uv_index": int(session.paint_surface_previous_uv_index),
    }


def _restore_paint_surface_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return record is None
    project = _resolve_project_uuid(str(record.get("project_uuid", "")))
    project_obj = getattr(project, "target_object", None) if project is not None else None
    obj = bpy.data.objects.get(str(record.get("object", "")))
    if obj is None and getattr(project_obj, "type", "") == "MESH":
        obj = project_obj
    mesh = bpy.data.meshes.get(str(record.get("mesh", "")))
    if mesh is None and getattr(obj, "type", "") == "MESH":
        mesh = getattr(obj, "data", None)
    if obj is None and mesh is not None:
        obj = next(
            (candidate for candidate in bpy.data.objects if candidate.data == mesh),
            None,
        )
    count = int(record.get("polygon_count", 0))
    if mesh is None:
        return False
    success = True
    try:
        selection = base64.b64decode(str(record.get("selection", "")), validate=True)
        hidden = base64.b64decode(str(record.get("hidden", "")), validate=True)
    except (TypeError, ValueError):
        selection = b""
        hidden = b""
        success = False
    if len(mesh.polygons) == count and len(selection) == count and len(hidden) == count:
        try:
            mesh.polygons.foreach_set("select", array("b", selection))
            mesh.polygons.foreach_set("hide", array("b", hidden))
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            success = False
    else:
        success = False
    use_mask = record.get("use_mask")
    if use_mask is not None:
        try:
            mesh.use_paint_mask = bool(use_mask)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            success = False
    use_mask_vertex = record.get("use_mask_vertex")
    if use_mask_vertex is not None and hasattr(mesh, "use_paint_mask_vertex"):
        try:
            mesh.use_paint_mask_vertex = bool(use_mask_vertex)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            success = False
    active_material_index = int(record.get("active_material_index", -1))
    if obj is not None and 0 <= active_material_index < len(obj.material_slots):
        try:
            obj.active_material_index = active_material_index
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            success = False
    uv_index = int(record.get("uv_index", -1))
    if getattr(mesh, "uv_layers", None) and 0 <= uv_index < len(mesh.uv_layers):
        try:
            mesh.uv_layers.active_index = uv_index
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            success = False
    try:
        mesh.update()
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        success = False
    return success


def _restore_paint_surface_isolation(
    session: StudioSession,
    *,
    clear: bool,
) -> bool:
    record = _paint_surface_recovery_record(session)
    restored = _restore_paint_surface_record(record)
    if restored:
        if clear:
            _clear_paint_surface_snapshot(session)
        else:
            session.paint_surface_suspended = True
    return restored


def _apply_paint_surface_isolation(
    project: Any,
    session: StudioSession,
    *,
    scene: Any | None = None,
    window: Any | None = None,
) -> None:
    """Limit native Projection Paint to the artist-defined Face Area."""

    obj = getattr(project, "target_object", None)
    if obj is None or getattr(obj, "type", "") != "MESH":
        raise StudioError("The Quick SDF paint surface is unavailable")
    mesh = obj.data
    try:
        from . import runtime

        region = runtime.face_region_mask(project, obj)
        slot_indices = runtime.face_region_material_slots(project)
    except (ValueError, RuntimeError) as exc:
        raise StudioError(str(exc)) from exc
    uv_layer = mesh.uv_layers.get(str(project.uv_map_name))
    if uv_layer is None:
        raise StudioError("The Quick SDF UV map is unavailable")

    snapshot_matches = bool(
        session.paint_surface_object_name == obj.name
        and session.paint_surface_mesh_name == mesh.name
        and session.paint_surface_polygon_count == len(mesh.polygons)
        and session.paint_surface_region_revision == int(project.face_region_revision)
    )
    if session.paint_surface_object_name and not snapshot_matches:
        if not _restore_paint_surface_isolation(session, clear=True):
            raise StudioError("The previous Quick SDF paint surface could not be restored")
    if not session.paint_surface_object_name:
        count = len(mesh.polygons)
        selected = array("b", [0]) * count
        hidden = array("b", [0]) * count
        mesh.polygons.foreach_get("select", selected)
        mesh.polygons.foreach_get("hide", hidden)
        session.paint_surface_object_name = obj.name
        session.paint_surface_mesh_name = mesh.name
        session.paint_surface_object_pointer = int(obj.as_pointer())
        session.paint_surface_mesh_pointer = int(mesh.as_pointer())
        session.paint_surface_polygon_count = count
        session.paint_surface_region_revision = int(project.face_region_revision)
        session.paint_surface_previous_use_mask = bool(mesh.use_paint_mask)
        session.paint_surface_previous_use_mask_vertex = (
            bool(mesh.use_paint_mask_vertex)
            if hasattr(mesh, "use_paint_mask_vertex")
            else None
        )
        session.paint_surface_previous_selection = selected.tobytes()
        session.paint_surface_previous_hidden = hidden.tobytes()
        session.paint_surface_previous_active_material_index = int(
            obj.active_material_index
        )
        session.paint_surface_previous_uv_index = int(mesh.uv_layers.active_index)

    if scene is not None and window is not None:
        _store_projection_recovery(scene, session, window)

    count = len(mesh.polygons)
    original_hidden = session.paint_surface_previous_hidden
    selection = array(
        "b",
        (
            1
            if bool(region[index]) and not original_hidden[index]
            else 0
            for index in range(count)
        ),
    )
    hidden = array("b", original_hidden)
    if len(selection) != count or len(hidden) != count:
        raise StudioError("The Quick SDF mesh topology changed while painting")
    if count and not any(selection):
        raise StudioError("All faces in the Quick SDF Face Area are hidden")
    mesh.polygons.foreach_set("select", selection)
    mesh.polygons.foreach_set("hide", hidden)
    mesh.use_paint_mask = True
    if hasattr(mesh, "use_paint_mask_vertex"):
        mesh.use_paint_mask_vertex = False
    obj.active_material_index = int(slot_indices[0])
    mesh.uv_layers.active_index = next(
        index for index, candidate in enumerate(mesh.uv_layers) if candidate == uv_layer
    )
    mesh.update()
    session.paint_surface_suspended = False


def _store_projection_recovery(
    scene: Any,
    session: StudioSession,
    window: Any,
) -> None:
    """Journal settings that Blender 5.2 autosave can capture mid-session."""

    workspace = getattr(window, "workspace", None)
    screen = getattr(window, "screen", None)
    if workspace is None or screen is None:
        return
    image_paint = scene.tool_settings.image_paint
    previous_clip_starts = dict(session.previous_clip_starts)
    normal_falloff = (
        bool(session.previous_normal_falloff)
        if session.previous_normal_falloff is not None
        else bool(getattr(image_paint, "use_normal_falloff", True))
    )
    clip_starts = [
        float(previous_clip_starts.get(int(space.as_pointer()), space.clip_start))
        for space in _studio_view_spaces(window)
    ]
    workspace[PROJECTION_RECOVERY_KEY] = json.dumps(
        {
            "scene": str(getattr(scene, "name", "")),
            "screen": str(getattr(screen, "name", "")),
            "normal_falloff": normal_falloff,
            "clip_starts": clip_starts,
            "paint_surface": _paint_surface_recovery_record(session),
        },
        separators=(",", ":"),
    )


def _clear_projection_recovery(
    window: Any | None,
    session: StudioSession | None = None,
) -> None:
    workspace = getattr(window, "workspace", None)
    if workspace is None and session is not None:
        workspace = bpy.data.workspaces.get(session.workspace_name)
    if workspace is not None and PROJECTION_RECOVERY_KEY in workspace:
        del workspace[PROJECTION_RECOVERY_KEY]


def _restore_serialized_projection_settings() -> None:
    """Restore settings from an autosave that bypassed normal save handlers."""

    for workspace in bpy.data.workspaces:
        serialized = workspace.get(PROJECTION_RECOVERY_KEY)
        if not serialized:
            continue
        remove_record = False
        try:
            record = json.loads(str(serialized))
            scene = bpy.data.scenes.get(str(record.get("scene", "")))
            if scene is not None and hasattr(scene.tool_settings.image_paint, "use_normal_falloff"):
                scene.tool_settings.image_paint.use_normal_falloff = bool(
                    record.get("normal_falloff", True)
                )
            screen = bpy.data.screens.get(str(record.get("screen", "")))
            spaces = tuple(
                area.spaces.active
                for area in getattr(screen, "areas", ())
                if area.type == "VIEW_3D" and hasattr(area.spaces.active, "clip_start")
            )
            for space, value in zip(spaces, record.get("clip_starts", ())):
                space.clip_start = float(value)
            remove_record = _restore_paint_surface_record(record.get("paint_surface"))
        except json.JSONDecodeError:
            # A malformed record cannot be used on a later load either.
            remove_record = True
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            remove_record = False
        finally:
            if remove_record:
                try:
                    del workspace[PROJECTION_RECOVERY_KEY]
                except (KeyError, ReferenceError, TypeError):
                    pass


def _apply_projection_settings(scene: Any, session: StudioSession, window: Any, obj: Any | None) -> None:
    _store_projection_recovery(scene, session, window)
    image_paint = scene.tool_settings.image_paint
    if session.previous_normal_falloff is None and hasattr(image_paint, "use_normal_falloff"):
        session.previous_normal_falloff = bool(image_paint.use_normal_falloff)
    if hasattr(image_paint, "use_normal_falloff"):
        image_paint.use_normal_falloff = False

    spaces = _studio_view_spaces(window)
    if not session.previous_clip_starts:
        session.previous_clip_starts = tuple(
            (int(space.as_pointer()), float(space.clip_start)) for space in spaces
        )
    clip_start = _adaptive_clip_start(obj)
    for space in spaces:
        space.clip_start = min(float(space.clip_start), clip_start)
    session.projection_settings_suspended = False


def _restore_projection_settings(
    scene: Any | None,
    session: StudioSession,
    window: Any | None,
    *,
    clear_recovery: bool = True,
) -> None:
    if scene is not None and session.previous_normal_falloff is not None:
        image_paint = scene.tool_settings.image_paint
        if hasattr(image_paint, "use_normal_falloff"):
            image_paint.use_normal_falloff = session.previous_normal_falloff
    previous = dict(session.previous_clip_starts)
    for space in _studio_view_spaces(window):
        value = previous.get(int(space.as_pointer()))
        if value is not None:
            space.clip_start = value
    if clear_recovery:
        _clear_projection_recovery(window, session)
    session.projection_settings_suspended = True


def _restore_preview(project: Any | None) -> None:
    try:
        from .preview import restore_preview_materials

        restore_preview_materials(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass


def _assign_preview(project: Any, image: Any | None) -> None:
    from .preview import assign_preview_material, set_preview_image
    from . import runtime

    obj = project.target_object
    expected_slots = runtime.face_region_material_slots(project)
    states = tuple(getattr(project, "preview_slots", ()))
    state_slots = tuple(sorted(int(state.slot_index) for state in states))
    materials_match = bool(obj is not None and state_slots == expected_slots)
    if materials_match:
        for state in states:
            slot_index = int(state.slot_index)
            material = bpy.data.materials.get(str(state.preview_material_name))
            if (
                material is None
                or not 0 <= slot_index < len(obj.material_slots)
                or obj.material_slots[slot_index].material != material
            ):
                materials_match = False
                break
    if (
        materials_match
        and bool(project.material_override_active)
    ):
        # Key selection only changes the Image node. Rebuilding and rewiring
        # the whole material here used to compile a shader on every stroke and
        # also exposed the preview graph to accidental self-links.
        if image is not None:
            set_preview_image(project, image)
        project.preview_enabled = True
        project.material_override_active = True
        return
    assign_preview_material(project, image)


def _remove_workspace(workspace: Any | None) -> None:
    if workspace is None:
        return
    try:
        bpy.data.workspaces.remove(workspace)
    except (ReferenceError, RuntimeError):
        pass


def _join_area_pair(context: Any, window: Any, source: Any, target: Any) -> bool:
    """Ask Blender to join one offered area pair into the other."""

    region = _window_region(source)
    override = {"window": window, "screen": window.screen, "area": source}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.screen.area_join(
            source_xy=(source.x + source.width // 2, source.y + source.height // 2),
            target_xy=(target.x + target.width // 2, target.y + target.height // 2),
        )
    return _operator_succeeded(result)


def _join_one_area(context: Any, window: Any) -> bool:
    """Join the cleanest touching pair of areas and report whether one went.

    The gap between two touching areas is display scaled, so candidates are
    ranked instead of matched against a fixed tolerance, and Blender decides
    whether the offered pair really shares an edge.
    """

    from .area_geometry import join_candidates

    areas = tuple(window.screen.areas)
    if len(areas) <= 1:
        return False
    offered = False
    for source, target in join_candidates(areas):
        offered = True
        if _join_area_pair(context, window, source, target):
            return True
    if offered:
        raise StudioError("Could not normalize the Quick SDF Paint workspace layout")
    raise StudioError("Could not find adjacent workspace areas to join")


def _configure_area_types(window: Any) -> tuple[Any, Any, Any]:
    areas = sorted(window.screen.areas, key=lambda area: area.x)
    if len(areas) != 2:
        raise StudioError("Quick SDF Paint did not produce the expected two areas")
    image_area, view_area = areas
    image_area.type = "IMAGE_EDITOR"
    view_area.type = "VIEW_3D"
    image_space = image_area.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    for space in (image_space, view_area.spaces.active):
        if hasattr(space, "show_region_tool_header"):
            space.show_region_tool_header = True
        if hasattr(space, "show_region_header"):
            space.show_region_header = True
    view_space = view_area.spaces.active
    # The asset shelf is not editable until the new paint region is initialized.
    view_space.show_gizmo = True
    if hasattr(view_space, "shading"):
        view_space.shading.type = "MATERIAL"
    return view_area, image_area, None


_ASSET_SHELF_HIDE_RETRIES = 0


def _hide_studio_asset_shelf() -> float | None:
    """Hide the paint shelf only after Blender initializes its active shelf."""

    global _ASSET_SHELF_HIDE_RETRIES
    session = _SESSION
    window = find_window(session.window_pointer) if session is not None else None
    if window is None or window.workspace.name != session.workspace_name:
        return None
    view_area, _image_area, _timeline_area = studio_areas(window)
    if view_area is None:
        return None
    region = _window_region(view_area)
    if region is None:
        return None
    _ASSET_SHELF_HIDE_RETRIES -= 1
    try:
        with bpy.context.temp_override(window=window, area=view_area, region=region):
            space = view_area.spaces.active
            if space.is_property_readonly("show_region_asset_shelf"):
                return 0.05 if _ASSET_SHELF_HIDE_RETRIES > 0 else None
            if space.show_region_asset_shelf:
                bpy.ops.screen.region_toggle(region_type="ASSET_SHELF")
            view_area.tag_redraw()
    except (AttributeError, ReferenceError, RuntimeError):
        # Cosmetic setup must never abort project creation.
        return 0.05 if _ASSET_SHELF_HIDE_RETRIES > 0 else None
    return None


def _configure_legacy_area_types(window: Any) -> tuple[Any, Any, Any]:
    areas = list(window.screen.areas)
    if len(areas) != 3:
        raise StudioError("Quick SDF Paint did not produce the expected three areas")
    bottom = min(areas, key=lambda area: area.y + area.height * 0.5)
    top_areas = sorted((area for area in areas if area != bottom), key=lambda area: area.x)
    if len(top_areas) != 2:
        raise StudioError("The Quick SDF Paint workspace top row is incomplete")
    image_area, view_area = top_areas
    image_area.type = "IMAGE_EDITOR"
    view_area.type = "VIEW_3D"
    bottom.type = TIMELINE_SPACE_TYPE
    image_space = image_area.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    for space in (image_space, view_area.spaces.active):
        if hasattr(space, "show_region_tool_header"):
            space.show_region_tool_header = True
        if hasattr(space, "show_region_header"):
            space.show_region_header = True
    view_space = view_area.spaces.active
    if hasattr(view_space, "shading"):
        view_space.shading.type = "MATERIAL"
    timeline_space = bottom.spaces.active
    _bind_timeline_host(
        bottom,
        str(getattr(window, "workspace", {}).get(WORKSPACE_PROJECT_TAG, "")),
    )
    if hasattr(timeline_space, "show_gizmo"):
        timeline_space.show_gizmo = True
    if hasattr(timeline_space, "show_gizmo_active_node"):
        timeline_space.show_gizmo_active_node = False
    for name in (
        "show_region_header", "show_region_footer", "show_region_toolbar",
        "show_region_tool_header", "show_region_ui",
    ):
        if hasattr(timeline_space, name):
            setattr(timeline_space, name, False)
    return view_area, image_area, bottom


def _frame_studio_content(context: Any, window: Any, view_area: Any, image_area: Any) -> None:
    """Make both editors useful immediately without changing the source workspace."""

    view_region = _window_region(view_area)
    if view_region is not None:
        try:
            with context.temp_override(
                window=window,
                screen=window.screen,
                area=view_area,
                region=view_region,
            ):
                bpy.ops.view3d.view_selected(use_all_regions=False)
        except RuntimeError:
            pass
        obj = getattr(context.view_layer.objects, "active", None)
        region_3d = getattr(view_area.spaces.active, "region_3d", None)
        if obj is not None and region_3d is not None and getattr(obj, "bound_box", None):
            from mathutils import Vector

            corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            center = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
            radius = max((corner - center).length for corner in corners)
            region_3d.view_location = center
            region_3d.view_distance = max(0.05, radius * 2.4)
    image_region = _window_region(image_area)
    if image_region is not None:
        try:
            with context.temp_override(
                window=window,
                screen=window.screen,
                area=image_area,
                region=image_region,
            ):
                bpy.ops.image.view_all(fit_view=True)
        except RuntimeError:
            pass


def _rollback_pending(error: BaseException | None = None) -> None:
    global _PENDING
    pending = _PENDING
    _remove_timeline_hosts()
    _PENDING = None
    if pending is None:
        return
    session = pending.session
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    _restore_preview(project)
    if project is not None and error is not None:
        try:
            project.warning_message = ""
            project.diagnostic_message = f"Could not open Quick SDF Paint: {error}"
        except (AttributeError, ReferenceError):
            pass
    restore_stroke_brush(session=session)
    _discard_project_paint_snapshots(project)
    _cancel_stroke_watchdog()
    _release_project_history(project)
    surface_restored = _restore_paint_surface_isolation(session, clear=True)
    scene = bpy.data.scenes.get(session.scene_name)
    _restore_projection_settings(
        scene,
        session,
        window,
        clear_recovery=surface_restored,
    )
    if window is None:
        return
    failed_workspace = bpy.data.workspaces.get(session.workspace_name)
    previous = bpy.data.workspaces.get(session.previous_workspace_name)
    if previous is not None:
        window.workspace = previous
    if scene is not None:
        _restore_paint_settings(scene, session)
    try:
        _restore_selection_and_mode(bpy.context, session, window)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    if session.workspace_created and failed_workspace is not None and window.workspace is not failed_workspace:
        _remove_workspace(failed_workspace)


def _continue_enter() -> float | None:
    """Advance workspace topology by one event-loop-safe operation."""

    global _PENDING, _SESSION, _ASSET_SHELF_HIDE_RETRIES
    pending = _PENDING
    if pending is None:
        return None
    session = pending.session
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    if window is None or project is None:
        _rollback_pending(StudioError("The Quick SDF Paint target disappeared while opening"))
        return None
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if workspace is None:
        _rollback_pending(StudioError("The Quick SDF Paint workspace disappeared while opening"))
        return None
    window.workspace = workspace
    # Workspace changes are applied by Blender's event loop. Never join or
    # split the caller's screen while the requested Studio screen is pending.
    if window.workspace.as_pointer() != workspace.as_pointer() or not any(
        screen.as_pointer() == window.screen.as_pointer() for screen in workspace.screens
    ):
        return 0.03
    context = bpy.context
    try:
        if pending.state == "JOIN":
            if len(window.screen.areas) > 1:
                _join_one_area(context, window)
                return 0.03
            pending.state = "SPLIT_VIEW"
        if pending.state == "SPLIT_VIEW":
            base = window.screen.areas[0]
            _split_area(context, window, base, "VERTICAL", 0.5)
            pending.state = "FINISH"
            return 0.03
        if pending.state in {"FINISH", "RESUME"}:
            if len(window.screen.areas) < 2:
                return 0.03
            if pending.state == "RESUME":
                view_area, image_area, _timeline_area = studio_areas(window)
                if view_area is None or image_area is None:
                    raise StudioError("The preserved Studio layout is incomplete")
            else:
                view_area, image_area, _timeline_area = _configure_area_types(window)
            obj = project.target_object
            _set_active_object(context, obj)
            scene = bpy.data.scenes.get(session.scene_name) or context.scene
            scene.tool_settings.image_paint.mode = "IMAGE"
            from .runtime import sync_canvas

            image = sync_canvas(context, project)
            if image is None:
                raise StudioError("The active angle image could not be used as a paint canvas")
            _index, active_item = _resolve_paint_key(project)
            if active_item is not None:
                session.view_mode = "EDIT"
                session.paint_key_uuid = str(active_item.uuid)
                session.paint_key_angle = float(active_item.angle)
                session.seek_angle = session.paint_key_angle
                project.seek_angle = session.paint_key_angle
            session.show_first_stroke_hint = not bool(project.first_stroke_complete)
            if str(project.base_source) == "NORMAL_GUIDE":
                session.first_hint_text = (
                    "A light-sweep guide from rear oblique to full light is ready. "
                    "Paint only the areas you want to adjust. Paint between keys to add that angle."
                )
            else:
                session.first_hint_text = "Choose an angle · choose Light or Shadow · paint"
            _apply_paint_surface_isolation(
                project,
                session,
                scene=scene,
                window=window,
            )
            _mode_set(context, window, view_area, "TEXTURE_PAINT")
            from .tools import activate_tools

            activate_tools(context, window=window)
            if pending.state != "RESUME":
                _frame_studio_content(context, window, view_area, image_area)
            _apply_projection_settings(scene, session, window, obj)
            if pending.manage_preview:
                _assign_preview(project, image)
            project.warning_message = ""
            if str(project.diagnostic_message).startswith("Could not open Quick SDF Paint:"):
                project.diagnostic_message = ""
            _SESSION = session
            if pending.state != "RESUME":
                _ASSET_SHELF_HIDE_RETRIES = 20
                if not bpy.app.timers.is_registered(_hide_studio_asset_shelf):
                    bpy.app.timers.register(_hide_studio_asset_shelf, first_interval=0.05)
            _PENDING = None
            from .operators import arm_undo_fence

            arm_undo_fence(str(project.uuid))
            tag_studio_redraw()
            return None
    except Exception as error:
        _rollback_pending(error)
        return None
    return 0.03


def _leave_object_mode(context: Any, window: Any, obj: Any | None) -> None:
    if obj is None or str(getattr(obj, "mode", "OBJECT")) == "OBJECT":
        return
    view_area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
    if view_area is None:
        raise StudioError("Quick SDF Paint has no 3D View")
    _set_active_object(context, obj)
    _mode_set(context, window, view_area, "OBJECT")


def _configure_session_target(
    context: Any,
    session: StudioSession,
    project: Any,
    *,
    frame_content: bool,
) -> Any:
    """Synchronize an already-built Studio workspace to one project."""

    scene, project_index, obj, _preflight_image = _preflight_studio_target(context, project)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if window is None or workspace is None:
        raise StudioError("The Quick SDF Paint workspace is no longer available")
    window.workspace = workspace
    workspace[WORKSPACE_PROJECT_TAG] = str(project.uuid)
    workspace[WORKSPACE_VERSION_TAG] = WORKSPACE_LAYOUT_VERSION
    scene.quick_sdf_active_project_index = project_index
    session.project_uuid = str(project.uuid)
    session.scene_name = scene.name
    _ensure_session_timeline_host(session)

    view_area, image_area, _timeline_area = studio_areas(window)
    if view_area is None or image_area is None:
        raise StudioError("Quick SDF Paint needs both a 3D View and an Image Editor")
    _set_active_object(context, obj)
    scene.tool_settings.image_paint.mode = "IMAGE"
    _apply_paint_surface_isolation(
        project,
        session,
        scene=scene,
        window=window,
    )
    if not select_paint_key(
        context,
        project,
        key_uuid=str(project.active_angle_uuid),
        index=int(project.active_angle_index),
    ):
        raise StudioError("The active angle image could not be used as a paint canvas")
    _mode_set(context, window, view_area, "TEXTURE_PAINT")
    from .tools import activate_tools

    activate_tools(context, window=window)
    _apply_projection_settings(scene, session, window, obj)
    ensure_studio_material_preview(context, session=session)
    if frame_content:
        _frame_studio_content(context, window, view_area, image_area)
    project.warning_message = ""
    return project


def _focus_or_switch_studio(context: Any, project: Any) -> StudioSession:
    """Focus the current target or atomically retarget the live Studio session."""

    session = _SESSION
    if session is None:
        raise StudioError("Quick SDF Paint is not open")
    window = find_window(session.window_pointer)
    if window is None:
        raise StudioError("The Quick SDF Paint window is no longer available")
    context_window = getattr(context, "window", None)
    if context_window is None or context_window.as_pointer() != session.window_pointer:
        raise StudioError("Quick SDF Paint is open in another Blender window")

    target_uuid = str(project.uuid)
    if not target_uuid:
        raise StudioError("The Quick SDF project has no UUID")
    _preflight_studio_target(context, project)
    caller_workspace = window.workspace
    if session.project_uuid == target_uuid:
        _activate_project(project)
        if session.editing_aux_mask_uuid:
            workspace = bpy.data.workspaces.get(session.workspace_name)
            if workspace is not None:
                window.workspace = workspace
            from .runtime import sync_canvas

            sync_canvas(context, project)
            tag_studio_redraw()
            return session
        _configure_session_target(context, session, project, frame_content=False)
        tag_studio_redraw()
        return session

    old_project = resolve_session_project(session)
    if old_project is None:
        raise StudioError("The current Quick SDF Paint target is unavailable")
    discard_provisional(context, old_project)
    if session.editing_aux_mask_uuid:
        leave_aux_mask_edit(context, old_project)
    old_scene, old_index = _project_location(old_project)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if old_scene is None or old_index is None or workspace is None:
        raise StudioError("The current Quick SDF Paint state could not be restored")

    old_session_state = {
        "project_uuid": session.project_uuid,
        "scene_name": session.scene_name,
        "view_mode": session.view_mode,
        "paint_key_uuid": session.paint_key_uuid,
        "paint_key_angle": session.paint_key_angle,
        "seek_angle": session.seek_angle,
        "show_first_stroke_hint": session.show_first_stroke_hint,
        "first_hint_text": session.first_hint_text,
        "projection_hint": session.projection_hint,
        "export_review_active": session.export_review_active,
    }
    old_workspace_tag = str(workspace.get(WORKSPACE_PROJECT_TAG, ""))
    target_obj = project.target_object
    old_obj = old_project.target_object
    switched = False
    try:
        window.workspace = workspace
        _leave_object_mode(context, window, old_obj)
        if not _restore_paint_surface_isolation(session, clear=True):
            raise StudioError("The current Quick SDF paint surface could not be restored")
        restore_stroke_brush(session=session)
        _cancel_stroke_watchdog()
        _restore_preview(old_project)
        try:
            from .runtime import discard_paint_snapshots

            discard_paint_snapshots(old_project)
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            pass
        try:
            from .live_preview import release_project

            release_project(old_project)
        except (ImportError, ReferenceError, RuntimeError):
            pass

        session.view_mode = "EDIT"
        session.paint_key_uuid = ""
        session.paint_key_angle = 0.0
        session.seek_angle = 0.0
        session.projection_hint = ""
        session.export_review_active = False
        session.show_first_stroke_hint = not bool(project.first_stroke_complete)
        session.first_hint_text = (
            "A light-sweep guide from rear oblique to full light is ready. "
            "Paint only the areas you want to adjust. Paint between keys to add that angle."
            if str(project.base_source) == "NORMAL_GUIDE"
            else "Choose an angle · choose Light or Shadow · paint"
        )
        _configure_session_target(context, session, project, frame_content=True)
        switched = True
        _release_project_history(old_project)
        from .operators import arm_undo_fence

        arm_undo_fence(str(project.uuid))
        tag_studio_redraw()
        return session
    except Exception as error:
        # The target is disposable until the final synchronization succeeds.
        _restore_preview(project)
        try:
            _leave_object_mode(context, window, target_obj)
        except (ReferenceError, RuntimeError, StudioError):
            pass
        _restore_paint_surface_isolation(session, clear=True)
        for name, value in old_session_state.items():
            setattr(session, name, value)
        workspace[WORKSPACE_PROJECT_TAG] = old_workspace_tag or session.project_uuid
        old_scene.quick_sdf_active_project_index = int(old_index)
        try:
            window.workspace = workspace
            _configure_session_target(context, session, old_project, frame_content=False)
        except Exception as rollback_error:
            # Never leave a half-switched paint session armed.
            try:
                exit_studio(context, reason="switch-rollback")
            except Exception:
                pass
            raise StudioError(
                f"Could not open this model, and Quick SDF Paint recovery failed: {rollback_error}"
            ) from error
        finally:
            if caller_workspace.as_pointer() != workspace.as_pointer():
                window.workspace = caller_workspace
        raise StudioError(f"Could not open this model in Quick SDF Paint: {error}") from error
    finally:
        if not switched:
            tag_studio_redraw()


def _continue_switch() -> float | None:
    """Complete a workspace focus/switch after Blender changes screens."""

    global _SWITCH_PENDING
    pending = _SWITCH_PENDING
    session = _SESSION
    if pending is None or session is None:
        _SWITCH_PENDING = None
        return None
    project = _resolve_project_uuid(pending.project_uuid)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if project is None or window is None or workspace is None:
        _SWITCH_PENDING = None
        return None
    window.workspace = workspace
    view, image, _timeline = studio_areas(window)
    if view is None or image is None:
        pending.attempts += 1
        if pending.attempts < 60:
            return 0.03
        try:
            project.diagnostic_message = "Could not focus the Quick SDF Paint workspace"
        except (AttributeError, ReferenceError):
            pass
        _SWITCH_PENDING = None
        return None
    _SWITCH_PENDING = None
    try:
        _focus_or_switch_studio(bpy.context, project)
    except Exception as error:
        try:
            project.diagnostic_message = str(error)
            project.warning_message = str(error)
        except (AttributeError, ReferenceError):
            pass
    return None


def _queue_studio_switch(context: Any, project: Any) -> StudioSession:
    global _SWITCH_PENDING
    session = _SESSION
    if session is None:
        raise StudioError("Quick SDF Paint is not open")
    context_window = getattr(context, "window", None)
    if context_window is None or context_window.as_pointer() != session.window_pointer:
        raise StudioError("Quick SDF Paint is open in another Blender window")
    _preflight_studio_target(context, project)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if window is None or workspace is None:
        raise StudioError("The Quick SDF Paint workspace is no longer available")
    _SWITCH_PENDING = _PendingSwitch(str(project.uuid))
    window.workspace = workspace
    if not bpy.app.timers.is_registered(_continue_switch):
        bpy.app.timers.register(_continue_switch, first_interval=0.01)
    return session


def open_or_switch_studio(
    context: Any,
    project: Any,
    *,
    manage_preview: bool = True,
    reset_layout: bool = False,
) -> StudioSession:
    """Open or switch Studio; the Open button can discard layout edits."""

    global _PENDING
    target_uuid = str(project.uuid)
    if _PENDING is not None:
        if _PENDING.session.project_uuid == target_uuid:
            window = find_window(_PENDING.session.window_pointer)
            workspace = bpy.data.workspaces.get(_PENDING.session.workspace_name)
            if window is not None and workspace is not None:
                window.workspace = workspace
            return _PENDING.session
        _rollback_pending()
    if reset_layout and _SESSION is not None:
        _preflight_studio_target(context, project)
        exit_studio(context, reason="reset-layout")
    if _SESSION is not None:
        session = _SESSION
        window = find_window(session.window_pointer)
        workspace = bpy.data.workspaces.get(session.workspace_name)
        if window is None or workspace is None:
            raise StudioError("The Quick SDF Paint workspace is no longer available")
        if window.workspace.as_pointer() != workspace.as_pointer():
            return _queue_studio_switch(context, project)
        return _focus_or_switch_studio(context, project)
    _activate_project(project)
    return enter_studio(context, project, manage_preview=manage_preview)


def enter_studio(
    context: Any,
    project: Any,
    *,
    sync_canvas_fn: Callable[[Any, Any], Any] | None = None,
    manage_preview: bool = True,
    preserve_layout: bool = False,
) -> StudioSession:
    """Atomically enter Studio and publish the singleton only after success."""

    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if bpy.app.background or getattr(context, "window", None) is None:
        raise StudioUnavailableError("Quick SDF Paint requires an interactive Blender window")
    project_uuid = str(project.uuid)
    if not project_uuid:
        raise StudioError("The Quick SDF project has no UUID")
    if _SESSION is not None:
        return open_or_switch_studio(context, project, manage_preview=manage_preview)
    if _PENDING is not None:
        if _PENDING.session.project_uuid == project_uuid:
            return _PENDING.session
        raise StudioBusyError("Quick SDF Paint is already opening")
    _preflight_studio_target(context, project)
    obj = project.target_object
    window = context.window
    scene = context.scene
    previous_workspace = window.workspace
    previous_active = context.view_layer.objects.active
    image_paint = scene.tool_settings.image_paint
    previous_canvas = getattr(image_paint, "canvas", None)
    session = StudioSession(
        project_uuid=project_uuid,
        window_pointer=window.as_pointer(),
        scene_name=scene.name,
        workspace_name="",
        previous_workspace_name=previous_workspace.name,
        previous_active_object_name=previous_active.name if previous_active else "",
        previous_selected_object_names=tuple(obj.name for obj in context.selected_objects),
        previous_mode=str(getattr(previous_active, "mode", "OBJECT")),
        previous_image_paint_mode=str(getattr(image_paint, "mode", "MATERIAL")),
        previous_canvas_name=previous_canvas.name if previous_canvas else "",
    )
    try:
        workspace = find_studio_workspace(project_uuid)
        if workspace is None:
            workspace = _duplicate_workspace(context, window, project)
            session.workspace_created = True
        else:
            window.workspace = workspace
        # Normal opens rebuild; the opt-in development reload preserves layout.
        session.workspace_name = workspace.name
        state = "RESUME" if preserve_layout and not session.workspace_created and _valid_studio_layout(workspace) else "JOIN"
        _PENDING = _PendingEnter(session=session, manage_preview=manage_preview, state=state)
        project.warning_message = "Opening Quick SDF Paint…"
        if not bpy.app.timers.is_registered(_continue_enter):
            bpy.app.timers.register(_continue_enter, first_interval=0.01)
        return session
    except Exception:
        _restore_preview(project)
        window.workspace = previous_workspace
        _restore_paint_settings(scene, session)
        _restore_selection_and_mode(context, session, window)
        raise


def exit_studio(
    context: Any | None = None,
    *,
    remove_workspace: bool = False,
    reason: str = "user",
) -> bool:
    """Leave Studio and restore the captured user workspace and paint state."""

    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if _PENDING is not None:
        _rollback_pending()
        if _SESSION is None:
            return True
    session = _SESSION
    if session is None:
        return False
    context = context or bpy.context
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    discard_provisional(context, project)
    _remove_timeline_hosts()
    if project is not None and session.editing_aux_mask_uuid:
        session.editing_aux_mask_uuid = ""
        if session.aux_previous_preview_mode:
            project.preview_mode = session.aux_previous_preview_mode
        if session.aux_previous_onion_enabled:
            project.onion_enabled = True
        session.aux_previous_preview_mode = ""
        session.aux_previous_onion_enabled = False
    _restore_preview(project)
    restore_stroke_brush(session=session)
    _discard_project_paint_snapshots(project)
    _cancel_stroke_watchdog()
    _release_project_history(project)
    scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
    surface_restored = _restore_paint_surface_isolation(session, clear=True)
    _restore_projection_settings(
        scene,
        session,
        window,
        clear_recovery=surface_restored,
    )
    try:
        from .live_preview import release_project

        release_project(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass
    if window is None:
        _SESSION = None
        return True
    studio_workspace = bpy.data.workspaces.get(session.workspace_name)
    try:
        target = project.target_object if project else None
        view_area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
        if target is not None and view_area is not None and target.mode == "TEXTURE_PAINT":
            context.view_layer.objects.active = target
            _mode_set(context, window, view_area, "OBJECT")
    except (ReferenceError, RuntimeError, StudioError):
        pass
    previous_workspace = bpy.data.workspaces.get(session.previous_workspace_name)
    if previous_workspace is not None:
        window.workspace = previous_workspace
    if scene is not None:
        try:
            _restore_paint_settings(scene, session)
        except (AttributeError, ReferenceError):
            pass
    try:
        _restore_selection_and_mode(context, session, window)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    _SESSION = None
    if remove_workspace and studio_workspace is not None and window.workspace is not studio_workspace:
        _remove_workspace(studio_workspace)
    tag_studio_redraw()
    return True


def dismiss_first_stroke_hint() -> None:
    if _SESSION is not None:
        _SESSION.show_first_stroke_hint = False
        tag_studio_redraw()


def tag_studio_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", TIMELINE_SPACE_TYPE}:
                area.tag_redraw()


@persistent
def _save_pre(_unused: Any) -> None:
    if _SESSION is None:
        return
    _remove_timeline_hosts()
    project = resolve_session_project()
    # Provisional canvases and structural Redo images are process-only state.
    # Never serialize them into the .blend. A ready angle is reconstructed
    # after saving so the artist can continue without changing the playhead.
    if _SESSION.provisional_uuid:
        _SESSION.provisional_rebuild_after_save = bool(
            _SESSION.provisional_state in {"PREPARING", "READY"}
            and not _SESSION.provisional_promoting
        )
        _SESSION.provisional_rebuild_angle = float(_SESSION.provisional_angle)
        discard_provisional(bpy.context, project, keep_preview_angle=True)
    else:
        _SESSION.provisional_rebuild_after_save = False
        _SESSION.provisional_rebuild_angle = -1.0
    if project is not None:
        _release_project_history(project)
    if (
        project is not None
        and _SESSION.editing_aux_mask_uuid
        and _SESSION.aux_previous_preview_mode
    ):
        # Aux edit mode is transient; save the artist's normal preview choice.
        project.preview_mode = _SESSION.aux_previous_preview_mode
    _restore_preview(project)
    window = find_window(_SESSION.window_pointer)
    scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
    restore_stroke_brush(session=_SESSION)
    _discard_project_paint_snapshots(project)
    _cancel_stroke_watchdog()
    surface_restored = _restore_paint_surface_isolation(_SESSION, clear=False)
    _restore_projection_settings(
        scene,
        _SESSION,
        window,
        clear_recovery=surface_restored,
    )
    if project is not None:
        _SESSION.onion_suspended = bool(project.onion_enabled)
        if _SESSION.onion_suspended:
            project.onion_enabled = False
        try:
            from .live_preview import purge_project_temporaries

            purge_project_temporaries(project)
        except (ImportError, ReferenceError, RuntimeError):
            pass
    _SESSION.preview_suspended = True


@persistent
def _save_post(_unused: Any) -> None:
    if _SESSION is None or not _SESSION.preview_suspended:
        return
    project = resolve_session_project()
    resume_error: BaseException | None = None
    try:
        if project is None:
            raise StudioError("The Quick SDF project disappeared while resuming after save")
        _ensure_session_timeline_host(_SESSION)
        window = find_window(_SESSION.window_pointer)
        scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
        if window is None or scene is None:
            raise StudioError("The Quick SDF window disappeared while resuming after save")
        _apply_paint_surface_isolation(
            project,
            _SESSION,
            scene=scene,
            window=window,
        )
        _apply_projection_settings(scene, _SESSION, window, project.target_object)
        from .runtime import (
            active_angle,
            aux_mask_for_uuid,
            resolve_aux_mask_image,
            resolve_display_image,
        )

        if _SESSION.editing_aux_mask_uuid:
            project.preview_mode = "MASK"
            aux_item = aux_mask_for_uuid(project, _SESSION.editing_aux_mask_uuid)
            image = resolve_aux_mask_image(project, aux_item)
        else:
            item = active_angle(project)
            image = resolve_display_image(project, item) if item else None
        _assign_preview(project, image)
        if _SESSION.view_mode == "PREVIEW":
            from .live_preview import update_seek_preview

            update_seek_preview(project, float(_SESSION.seek_angle))
        if _SESSION.onion_suspended:
            project.onion_enabled = True
        if (
            _SESSION.provisional_rebuild_after_save
            and _SESSION.provisional_rebuild_angle >= 0.0
            and not _SESSION.editing_aux_mask_uuid
        ):
            angle = float(_SESSION.provisional_rebuild_angle)
            _SESSION.provisional_rebuild_after_save = False
            _SESSION.provisional_rebuild_angle = -1.0
            settle_seek(bpy.context, project, angle)
    except (
        AttributeError,
        ImportError,
        ReferenceError,
        RuntimeError,
        StudioError,
        TypeError,
        ValueError,
    ) as error:
        resume_error = error

    if resume_error is None:
        _SESSION.preview_suspended = False
        _SESSION.onion_suspended = False
        return

    if project is not None:
        try:
            project.warning_message = (
                "Quick SDF Paint could not safely resume after saving. "
                "The Studio session was closed; reopen it to continue."
            )
            if _SESSION.onion_suspended:
                project.onion_enabled = True
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    if not bpy.app.timers.is_registered(_exit_after_failed_save_resume):
        bpy.app.timers.register(_exit_after_failed_save_resume, first_interval=0.01)


def _exit_after_failed_save_resume() -> None:
    if _SESSION is not None and _SESSION.preview_suspended:
        try:
            exit_studio(bpy.context, reason="save-resume-failed")
        except (AttributeError, ReferenceError, RuntimeError, StudioError):
            pass
    return None


@persistent
def _load_pre(_unused: Any) -> None:
    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    _remove_timeline_hosts()
    try:
        from .operators import shutdown_export_job

        shutdown_export_job(message="Export cancelled while loading a file")
    except (ImportError, RuntimeError):
        pass
    if _PENDING is not None:
        _rollback_pending()
    if _SESSION is not None:
        window = find_window(_SESSION.window_pointer)
        scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
        project = resolve_session_project(_SESSION)
        discard_provisional(bpy.context, project)
        if project is not None and _SESSION.editing_aux_mask_uuid:
            if _SESSION.aux_previous_preview_mode:
                project.preview_mode = _SESSION.aux_previous_preview_mode
            if _SESSION.aux_previous_onion_enabled:
                project.onion_enabled = True
        restore_stroke_brush(session=_SESSION)
        _discard_project_paint_snapshots(project)
        _cancel_stroke_watchdog()
        surface_restored = _restore_paint_surface_isolation(_SESSION, clear=True)
        _restore_projection_settings(
            scene,
            _SESSION,
            window,
            clear_recovery=surface_restored,
        )
    try:
        from .preview import restore_all_preview_materials

        restore_all_preview_materials()
    except (ImportError, ReferenceError, RuntimeError):
        pass
    _release_project_history()
    _SESSION = None


@persistent
def _load_post(_unused: Any) -> None:
    """Discard process-only Studio artifacts serialized by Blender autosave."""

    _remove_timeline_hosts()
    _restore_serialized_projection_settings()
    try:
        from . import runtime

        for image in tuple(bpy.data.images):
            if image.get(runtime.ROLE_KEY) == PROVISIONAL_DISPLAY_ROLE:
                bpy.data.images.remove(image)
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass


@persistent
def _undo_post(_unused: Any) -> None:
    global _SESSION
    if _SESSION is not None and (find_window(_SESSION.window_pointer) is None or resolve_session_project() is None):
        session = _SESSION
        window = find_window(session.window_pointer)
        scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
        try:
            exit_studio(bpy.context, reason="undo-target-lost")
        except (AttributeError, ReferenceError, RuntimeError, StudioError):
            surface_restored = _restore_paint_surface_isolation(session, clear=True)
            _restore_projection_settings(
                scene,
                session,
                window,
                clear_recovery=surface_restored,
            )
            _SESSION = None
    elif _SESSION is not None:
        try:
            reconcile_view_state(bpy.context)
            ensure_paint_surface(bpy.context)
            project = resolve_session_project(_SESSION)
            if project is not None:
                from .operators import arm_undo_fence

                arm_undo_fence(str(project.uuid))
        except (AttributeError, ImportError, ReferenceError, RuntimeError, ValueError):
            pass


def register_studio() -> None:
    global _HANDLERS_REGISTERED
    if _HANDLERS_REGISTERED:
        return
    for collection, handler in (
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (bpy.app.handlers.load_pre, _load_pre),
        (bpy.app.handlers.load_post, _load_post),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if handler not in collection:
            collection.append(handler)
    save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if save_post_fail is not None and _save_post not in save_post_fail:
        save_post_fail.append(_save_post)
    _HANDLERS_REGISTERED = True


def unregister_studio() -> None:
    global _HANDLERS_REGISTERED, _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if bpy.app.timers.is_registered(_hide_studio_asset_shelf):
        bpy.app.timers.unregister(_hide_studio_asset_shelf)
    if bpy.app.timers.is_registered(_continue_switch):
        bpy.app.timers.unregister(_continue_switch)
    if bpy.app.timers.is_registered(_continue_enter):
        bpy.app.timers.unregister(_continue_enter)
    if bpy.app.timers.is_registered(_exit_after_failed_save_resume):
        bpy.app.timers.unregister(_exit_after_failed_save_resume)
    _cancel_provisional_job()
    _cancel_stroke_watchdog()
    _remove_timeline_hosts()
    if _PENDING is not None:
        _rollback_pending()
    try:
        exit_studio(bpy.context)
    except (ReferenceError, RuntimeError, StudioError):
        _SESSION = None
    for collection, handler in (
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (bpy.app.handlers.load_pre, _load_pre),
        (bpy.app.handlers.load_post, _load_post),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        while handler in collection:
            collection.remove(handler)
    save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if save_post_fail is not None:
        while _save_post in save_post_fail:
            save_post_fail.remove(_save_post)
    _HANDLERS_REGISTERED = False


CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES", "StudioBusyError", "StudioError", "StudioSession", "StudioUnavailableError",
    "WORKSPACE_PROJECT_TAG", "active_session", "configure_workspace", "current_session",
    "activate_provisional_for_stroke", "back_to_paint", "discard_provisional",
    "dismiss_first_stroke_hint", "ensure_studio_material_preview",
    "enter_aux_mask_edit", "leave_aux_mask_edit",
    "enter_studio", "exit_studio", "open_or_switch_studio",
    "find_studio_workspace", "is_studio_active", "leave_export_adjustment_review",
    "TIMELINE_SPACE_TYPE",
    "ensure_paint_surface",
    "reconcile_view_state", "register_studio", "show_export_adjustment_review",
    "finish_provisional_stroke", "prepare_stroke_brush", "promote_provisional_after_stroke",
    "apply_provisional_history", "provisional_history_state",
    "retain_provisional_after_stroke",
    "provisional_created_metadata",
    "resolve_session_project", "restore_stroke_brush", "seek_preview", "settle_seek",
    "select_paint_key", "set_projection_hint", "studio_areas", "tag_studio_redraw",
    "unregister_studio",
]

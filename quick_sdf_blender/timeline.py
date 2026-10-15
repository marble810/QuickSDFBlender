# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive Quick SDF timeline hosted by a compact non-temporal area."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import time
from typing import Any, Sequence

import bpy

from . import bl_ui_rounded
from .model import SIDE_LIGHT_KEY_ANGLE
from .studio import TIMELINE_SPACE_TYPE, active_session
from .ui import context as ui_context


@dataclass(frozen=True, slots=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass(frozen=True, slots=True)
class TimelineGeometry:
    rail: Rect
    key_rects: tuple[tuple[int, Rect], ...]
    angle_min: float
    angle_max: float

    def angle_from_x(self, x: float) -> float:
        span = max(1.0, self.rail.x1 - self.rail.x0)
        factor = max(0.0, min(1.0, (x - self.rail.x0) / span))
        return self.angle_min + factor * (self.angle_max - self.angle_min)


_DRAW_HANDLE: Any = None
_IMAGE_SHADER: Any = None
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []
_SCRUB_PREVIEW_INTERVAL = 1.0 / 30.0
_GEOMETRY_CACHE: OrderedDict[tuple[Any, ...], TimelineGeometry] = OrderedDict()
_IMAGE_BATCH_CACHE: OrderedDict[tuple[float, ...], Any] = OrderedDict()
_GEOMETRY_CACHE_LIMIT = 64
_BATCH_CACHE_LIMIT = 256
# Rounded thumbnail corners are clipped geometrically (the image shader is a
# builtin, so it cannot mask); more segments keep those corners round.
_THUMBNAIL_CORNER_SEGMENTS = 8
_AUTHORING_ANGLE_MIN = 0.0
_AUTHORING_ANGLE_MAX = 90.0


def _progress_percent(angle: float) -> float:
    """Convert the internal lilToon sweep coordinate to artist-facing progress."""

    span = _AUTHORING_ANGLE_MAX - _AUTHORING_ANGLE_MIN
    factor = (float(angle) - _AUTHORING_ANGLE_MIN) / span
    return max(0.0, min(100.0, factor * 100.0))


def _format_progress(angle: float) -> str:
    value = f"{_progress_percent(angle):.1f}".rstrip("0").rstrip(".")
    return f"{value}%"


PREVIEW_YAW_KEY = "quick_sdf_preview_yaw"
_PREVIEW_YAW_BEHIND = 180.0
_PREVIEW_YAW_FRONT = 0.0
_AUTHORING_YAW_START = 120.0
_AUTHORING_YAW_END = 60.0


def _physical_light_yaw(angle: float) -> float:
    """Return horizontal light yaw in degrees from the character's front.

    The guide and lilToon preview use ``frontDot = progress - 0.5``.
    Therefore the corresponding unit light direction has a horizontal yaw of
    ``acos(frontDot)``: 120 degrees at Light Starts, 90 at Side Light, and 60
    at Full Light.
    """

    progress = _progress_percent(angle) / 100.0
    front_dot = max(-1.0, min(1.0, progress - 0.5))
    return math.degrees(math.acos(front_dot))


def _authoring_angle_for_yaw(yaw: float) -> float | None:
    """Return the 0..90 authoring angle for a baked yaw, or None outside it."""

    value = float(yaw)
    if value < _AUTHORING_YAW_END - 1.0e-4 or value > _AUTHORING_YAW_START + 1.0e-4:
        return None
    front_dot = math.cos(math.radians(max(_AUTHORING_YAW_END, min(_AUTHORING_YAW_START, value))))
    return max(0.0, min(90.0, (front_dot + 0.5) * 90.0))


# Each preview tail uses 5%: 4% for the editable-looking transition nearest
# the authoring boundary, 1% for the mostly constant shadow/light remainder.
# These knots affect display/hit testing only, never baking or export encoding.
_RAIL_YAW_KNOTS = (
    (0.0, 180.0), (0.01, 130.0), (0.05, 120.0),
    (0.95, 60.0), (0.99, 50.0), (1.0, 0.0),
)


def _yaw_from_rail(rail: Rect, x: float) -> float:
    """Piecewise-linear UI mapping: compressed tails, expanded authoring lane."""

    span = max(1.0, rail.x1 - rail.x0)
    factor = max(0.0, min(1.0, (float(x) - rail.x0) / span))
    for (start, behind), (end, front) in zip(_RAIL_YAW_KNOTS, _RAIL_YAW_KNOTS[1:]):
        if factor <= end:
            return behind + (factor - start) / (end - start) * (front - behind)
    return _PREVIEW_YAW_FRONT


def _x_for_yaw(rail: Rect, yaw: float) -> float:
    span = max(1.0, rail.x1 - rail.x0)
    value = max(_PREVIEW_YAW_FRONT, min(_PREVIEW_YAW_BEHIND, float(yaw)))
    for (start, behind), (end, front) in zip(_RAIL_YAW_KNOTS, _RAIL_YAW_KNOTS[1:]):
        if value >= front:
            factor = start + (behind - value) / (behind - front) * (end - start)
            return rail.x0 + factor * span
    return rail.x1


def _stored_preview_yaw(project: Any) -> float | None:
    try:
        if PREVIEW_YAW_KEY not in project:
            return None
        return float(project[PREVIEW_YAW_KEY])
    except (KeyError, TypeError, ValueError):
        return None


def _scrub_status_text(angle: float) -> str:
    from .i18n import tr

    yaw = f"{_physical_light_yaw(angle):.1f}".rstrip("0").rstrip(".")
    return tr("Sweep %s · Light yaw %s") % (_format_progress(angle), f"{yaw}°")


def _set_scrub_status(context: Any, angle: float) -> None:
    workspace = getattr(context, "workspace", None)
    status_text_set = getattr(workspace, "status_text_set", None)
    if status_text_set is None:
        return
    try:
        status_text_set(_scrub_status_text(angle))
    except (ReferenceError, RuntimeError, TypeError):
        pass


def _clear_scrub_status(context: Any) -> None:
    workspace = getattr(context, "workspace", None)
    status_text_set = getattr(workspace, "status_text_set", None)
    if status_text_set is None:
        return
    try:
        status_text_set(None)
    except (ReferenceError, RuntimeError, TypeError):
        pass


def _visible_keys(project: Any) -> list[tuple[int, Any]]:
    keys = list(enumerate(project.angles))
    independent = str(project.symmetry_mode) == "INDEPENDENT"
    edit_side = str(
        project.active_side
        if independent
        else project.authoring_side
    )
    keys = [(index, item) for index, item in keys if str(item.side) == edit_side]
    return sorted(keys, key=lambda pair: float(pair[1].angle))


def build_geometry(width: float, height: float, keys: Sequence[tuple[int, Any]]) -> TimelineGeometry:
    cache_key = (
        round(float(width), 3),
        round(float(height), 3),
        tuple(
            (int(index), round(float(getattr(item, "angle", 0.0)), 8))
            for index, item in keys
        ),
    )
    cached = _GEOMETRY_CACHE.get(cache_key)
    if cached is not None:
        _GEOMETRY_CACHE.move_to_end(cache_key)
        return cached
    margin = 16.0
    rail_y = max(20.0, height - 24.0)
    content_x0 = margin
    content_x1 = max(margin + 1.0, width - margin)
    content_width = max(1.0, content_x1 - content_x0)
    angles = [float(getattr(item, "angle", 0.0)) for _index, item in keys] or [0.0, 90.0]
    angle_min, angle_max = min(angles), max(angles)
    if abs(angle_max - angle_min) < 1.0e-6:
        angle_max = angle_min + 90.0
    thumb_width = max(28.0, min(68.0, content_width / max(1, len(keys)) - 4.0))
    thumb_width = min(content_width, thumb_width)
    endpoint_inset = thumb_width * 0.5 if keys else 0.0
    rail_x0 = content_x0 + endpoint_inset
    rail_x1 = content_x1 - endpoint_inset
    if rail_x1 <= rail_x0:
        rail_x0 = (content_x0 + content_x1) * 0.5
        rail_x1 = rail_x0 + 1.0
    rail = Rect(rail_x0, rail_y - 5.0, rail_x1, rail_y + 5.0)
    available = max(1.0, rail.x1 - rail.x0)
    thumb_height = max(24.0, min(64.0, rail.y0 - 18.0))
    rects: list[tuple[int, Rect]] = []
    for collection_index, item in keys:
        factor = (float(item.angle) - angle_min) / (angle_max - angle_min)
        center = rail.x0 + factor * available
        rects.append((collection_index, Rect(center - thumb_width * 0.5, 8.0, center + thumb_width * 0.5, 8.0 + thumb_height)))
    result = TimelineGeometry(rail, tuple(rects), angle_min, angle_max)
    _GEOMETRY_CACHE[cache_key] = result
    _GEOMETRY_CACHE.move_to_end(cache_key)
    while len(_GEOMETRY_CACHE) > _GEOMETRY_CACHE_LIMIT:
        _GEOMETRY_CACHE.popitem(last=False)
    return result


def _image_for_key(project: Any, item: Any) -> Any | None:
    image = getattr(item, "display_image", None)
    if image is not None:
        return image
    image_name = str(getattr(item, "display_image_name", ""))
    if image_name:
        image = bpy.data.images.get(image_name)
        if image is not None:
            return image
    return None


def _rect(rect: Rect, color: tuple[float, float, float, float], radius: Any = 0.0) -> None:
    from . import bl_ui_rounded

    bl_ui_rounded.draw(rect.x0, rect.y0, rect.x1, rect.y1, fill=color, radius=radius)


def _outline(rect: Rect, color: tuple[float, float, float, float], thickness: float = 2.0,
             radius: Any = 0.0) -> None:
    if radius:
        from . import bl_ui_rounded

        # Rounded outlines follow the same arc outline as the rounded fill.
        bl_ui_rounded.draw(rect.x0, rect.y0, rect.x1, rect.y1, radius=radius, border=color,
                           border_thickness=thickness)
        return
    _rect(Rect(rect.x0, rect.y0, rect.x1, rect.y0 + thickness), color)
    _rect(Rect(rect.x0, rect.y1 - thickness, rect.x1, rect.y1), color)
    _rect(Rect(rect.x0, rect.y0, rect.x0 + thickness, rect.y1), color)
    _rect(Rect(rect.x1 - thickness, rect.y0, rect.x1, rect.y1), color)


def _text(text: str, x: float, y: float, color=(0.9, 0.92, 0.96, 1.0), size: int = 11) -> None:
    import blf
    import gpu

    # ``blf`` leaves the GPU blend state at NONE, which turns every later
    # transparent draw (rounded fills, SDF rings, image masks) into a solid
    # black shape.  Restore the state the caller was using.
    previous = gpu.state.blend_get()
    try:
        try:
            blf.size(0, size)
        except TypeError:
            blf.size(0, size, 72)
        blf.color(0, *color)
        blf.position(0, x, y, 0)
        blf.draw(0, text)
    finally:
        gpu.state.blend_set(previous)


def _draw_thumbnail(
    image: Any,
    rect: Rect,
    uv_bbox: tuple[float, float, float, float],
    project: Any | None = None,
    active_image: Any | None = None,
    scale: float = 1.0,
    label_height: float | None = None,
    radius: Any = 0.0,
) -> bool:
    try:
        global _IMAGE_SHADER
        import gpu
        from gpu_extras.batch import batch_for_shader

        # Never call ``gpu.texture.from_image`` for a Display image here.  It
        # would make every full-resolution angle texture GPU resident merely
        # because the timeline was drawn.  The cache owns a 96 x 64 derivative.
        from .preview_cache import thumbnail_texture

        was_loaded = bool(getattr(image, "has_data", True))
        texture = thumbnail_texture(image, uv_bbox)
        if project is not None and not was_loaded:
            try:
                from .residency import reconcile_project

                reconcile_project(project, active_image)
            except (ImportError, ReferenceError, RuntimeError):
                pass
        if _IMAGE_SHADER is None:
            _IMAGE_SHADER = gpu.shader.from_builtin("IMAGE_SCENE_LINEAR_TO_REC709_SRGB")
        # Reserve the bottom strip for the progress label, not the top.
        strip = 16.0 * scale if label_height is None else float(label_height)
        inner = Rect(rect.x0 + 2.0 * scale, rect.y0 + strip,
                     rect.x1 - 2.0 * scale, rect.y1 - 2.0 * scale)
        # ``thumbnail_texture`` already returns the 96x64 derivative cropped to
        # the project's UV bbox, so the mesh samples the full 0..1 range.
        shape = None if not radius else bl_ui_rounded.mesh(
            inner.x0, inner.y0, inner.x1, inner.y1, radius,
            segments=_THUMBNAIL_CORNER_SEGMENTS,
        )
        key = (rect.x0, rect.y0, rect.x1, rect.y1, scale, strip,
               None if not radius else float(radius))
        batch = _IMAGE_BATCH_CACHE.get(key)
        if batch is None:
            if shape is None:
                positions = ((inner.x0, inner.y0), (inner.x1, inner.y0),
                             (inner.x1, inner.y1), (inner.x0, inner.y1))
                tex_coords = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
                indices = ((0, 1, 2), (2, 3, 0))
            else:
                positions, tex_coords, indices = shape
            batch = batch_for_shader(
                _IMAGE_SHADER,
                "TRIS",
                {"pos": positions, "texCoord": tex_coords},
                indices=indices,
            )
            _IMAGE_BATCH_CACHE[key] = batch
            while len(_IMAGE_BATCH_CACHE) > _BATCH_CACHE_LIMIT:
                _IMAGE_BATCH_CACHE.popitem(last=False)
        else:
            _IMAGE_BATCH_CACHE.move_to_end(key)
        _IMAGE_SHADER.bind()
        _IMAGE_SHADER.uniform_sampler("image", texture)
        batch.draw(_IMAGE_SHADER)
        return True
    except (ReferenceError, RuntimeError, SystemError, ValueError):
        return False


def _affected_indices(project: Any, keys: Sequence[tuple[int, Any]]) -> set[int]:
    if not keys:
        return set()
    active = int(project.active_angle_index)
    # Interactive strokes edit only the selected key so Blender remains as
    # responsive as its native Texture Paint. Export performs the non-
    # destructive cross-angle consistency repair.
    return {active}


def _seek_value(project: Any) -> float:
    return float(project.seek_angle)


def _x_for_angle(geometry: TimelineGeometry, angle: float) -> float:
    span = max(1.0e-6, geometry.angle_max - geometry.angle_min)
    factor = max(0.0, min(1.0, (float(angle) - geometry.angle_min) / span))
    return geometry.rail.x0 + factor * (geometry.rail.x1 - geometry.rail.x0)


def _playhead_layout(
    geometry: TimelineGeometry,
    width: float,
    height: float,
    angle: float,
) -> tuple[float, Rect, str]:
    x = _x_for_angle(geometry, angle)
    label = _format_progress(angle)
    label_width = max(30.0, 8.0 + 7.0 * len(label))
    label_x0 = max(2.0, min(width - label_width - 2.0, x - label_width * 0.5))
    label_y0 = geometry.rail.y1 + 2.0
    label_rect = Rect(
        label_x0,
        label_y0,
        label_x0 + label_width,
        max(label_y0 + 1.0, min(height - 1.0, label_y0 + 16.0)),
    )
    return x, label_rect, label


def _nearest_key_index(
    keys: Sequence[tuple[int, Any]],
    angle: float,
) -> int | None:
    """Return the visible key nearest to ``angle``, preferring lower on ties."""

    if not keys:
        return None
    index, _item = min(
        keys,
        key=lambda pair: (
            abs(float(getattr(pair[1], "angle", 0.0)) - angle),
            float(getattr(pair[1], "angle", 0.0)),
        ),
    )
    return index


def _draw_timeline() -> None:
    context = bpy.context
    if getattr(context, "area", None) is None or context.area.type != TIMELINE_SPACE_TYPE:
        return
    project = ui_context.studio_workspace_project(context)
    if project is None or context.region is None:
        return
    import gpu

    session = active_session(context)
    if session is not None and bool(getattr(session, "editing_aux_mask_uuid", "")):
        try:
            from .i18n import tr

            gpu.state.blend_set("ALPHA")
            _rect(
                Rect(0.0, 0.0, context.region.width, context.region.height),
                (0.025, 0.03, 0.04, 1.0),
            )
            _text(
                tr("This mask is shared by every light angle"),
                18.0,
                max(18.0, context.region.height * 0.5 - 6.0),
                color=(0.68, 0.74, 0.82, 1.0),
                size=12,
            )
        except (AttributeError, ReferenceError, RuntimeError, SystemError, ValueError):
            pass
        finally:
            try:
                gpu.state.blend_set("NONE")
            except (AttributeError, RuntimeError):
                pass
        return

    keys = _visible_keys(project)
    geometry = build_geometry(context.region.width, context.region.height, keys)
    active = int(project.active_angle_index)
    affected = _affected_indices(project, keys)
    previewing = bool(
        session is not None
        and session.view_mode in {"PREVIEW", "PROVISIONAL", "PROVISIONAL_STROKE"}
    )
    show_hint = bool(session is not None and session.show_first_stroke_hint)
    highlighted = active
    bbox_values = tuple(float(value) for value in project.thumbnail_uv_bbox)
    uv_bbox = bbox_values if (
        len(bbox_values) == 4
        and 0.0 <= bbox_values[0] < bbox_values[2] <= 1.0
        and 0.0 <= bbox_values[1] < bbox_values[3] <= 1.0
    ) else (0.0, 0.0, 1.0, 1.0)
    try:
        gpu.state.blend_set("ALPHA")
        _rect(Rect(0.0, 0.0, context.region.width, context.region.height), (0.025, 0.03, 0.04, 1.0))
        _rect(geometry.rail, (0.18, 0.20, 0.24, 1.0))
        seek_x, label_rect, label_text = _playhead_layout(
            geometry,
            context.region.width,
            context.region.height,
            _seek_value(project),
        )
        key_map = {index: item for index, item in keys}
        active_item = key_map.get(active)
        active_image = (
            _image_for_key(project, active_item) if active_item is not None else None
        )
        for collection_index, rect in geometry.key_rects:
            item = key_map[collection_index]
            _rect(rect, (0.10, 0.22, 0.34, 0.82) if collection_index in affected else (0.08, 0.09, 0.11, 1.0))
            image = _image_for_key(project, item)
            if image is None or not _draw_thumbnail(
                image, rect, uv_bbox, project, active_image
            ):
                _rect(Rect(rect.x0 + 2, rect.y0 + 16, rect.x1 - 2, rect.y1 - 2), (0.18, 0.19, 0.21, 1.0))
            angle = float(getattr(item, "angle", 0.0))
            _text(_format_progress(angle), rect.x0 + 4.0, rect.y0 + 3.0, size=10)
            if collection_index == highlighted:
                _outline(rect, (0.18, 0.62, 1.0, 1.0), 2.0)
            if bool(getattr(item, "has_violation", False)):
                _rect(Rect(rect.x1 - 9, rect.y1 - 9, rect.x1 - 2, rect.y1 - 2), (1.0, 0.08, 0.03, 1.0))
            elif bool(getattr(item, "is_manual", False)):
                _rect(Rect(rect.x1 - 7, rect.y1 - 7, rect.x1 - 3, rect.y1 - 3), (0.95, 0.95, 1.0, 1.0))
            else:
                _rect(Rect(rect.x1 - 6, rect.y1 - 6, rect.x1 - 4, rect.y1 - 4), (0.70, 0.74, 0.80, 1.0))
        from .i18n import tr

        semantic_y = max(2.0, geometry.rail.y0 - 12.0)
        side_x = _x_for_angle(geometry, SIDE_LIGHT_KEY_ANGLE)
        semantic_color = (0.58, 0.62, 0.68, 1.0)
        _rect(
            Rect(
                side_x - 1.0,
                geometry.rail.y0 - 2.0,
                side_x + 1.0,
                geometry.rail.y1 + 2.0,
            ),
            (0.45, 0.50, 0.58, 1.0),
        )
        _text(
            tr("Light Starts"), geometry.rail.x0, semantic_y,
            color=semantic_color, size=9,
        )
        side_label = tr("Side Light 90°")
        _text(
            side_label, side_x - max(12.0, len(side_label) * 2.4), semantic_y,
            color=semantic_color, size=9,
        )
        _text(
            tr("Full Light"), max(2.0, geometry.rail.x1 - 48.0), semantic_y,
            color=semantic_color, size=9,
        )
        # Keep the active playhead above the neutral Side Light anchor when
        # both occupy the exact midpoint.
        _rect(
            Rect(seek_x - 1.0, 4.0, seek_x + 1.0, geometry.rail.y1 + 5.0),
            (1.0, 0.48, 0.08, 1.0) if previewing else (0.25, 0.72, 1.0, 1.0),
        )
        if not show_hint:
            _rect(
                label_rect,
                (0.72, 0.30, 0.05, 1.0) if previewing else (0.16, 0.48, 0.78, 1.0),
            )
            _text(label_text, label_rect.x0 + 4.0, label_rect.y0 + 2.0, size=10)
        if session is not None:
            from .model import MAX_KEYS_PER_SIDE

            count = len(keys)
            _text(
                f"{count} / {MAX_KEYS_PER_SIDE}",
                max(2.0, context.region.width - 54.0),
                geometry.rail.y1 + 5.0,
                color=(0.58, 0.62, 0.68, 1.0),
                size=9,
            )
            state = str(getattr(session, "provisional_state", "NONE"))
            if state == "PREPARING":
                _text(
                    f"{tr('Preparing this angle')} "
                    f"{_format_progress(float(session.provisional_angle))}…",
                    geometry.rail.x0,
                    geometry.rail.y1 + 5.0,
                    color=(1.0, 0.63, 0.18, 1.0),
                    size=11,
                )
            elif state == "READY" and not show_hint:
                _text(
                    tr("Paint here to add this angle"),
                    geometry.rail.x0,
                    geometry.rail.y1 + 5.0,
                    color=(1.0, 0.72, 0.24, 1.0),
                    size=11,
                )
            elif state == "LIMIT":
                _text(
                    tr("Maximum 16 keys · select or delete a key"),
                    geometry.rail.x0,
                    geometry.rail.y1 + 5.0,
                    color=(1.0, 0.28, 0.18, 1.0),
                    size=11,
                )
            elif not show_hint and str(getattr(session, "projection_hint", "")):
                _text(
                    tr(str(session.projection_hint)),
                    geometry.rail.x0,
                    geometry.rail.y1 + 5.0,
                    color=(0.72, 0.76, 0.82, 1.0),
                    size=10,
                )
            elif previewing and not show_hint:
                _text(
                    _scrub_status_text(_seek_value(project)),
                    geometry.rail.x0,
                    geometry.rail.y1 + 5.0,
                    color=(1.0, 0.63, 0.18, 1.0),
                    size=10,
                )
        if (
            show_hint
            and session is not None
            and str(getattr(session, "provisional_state", "NONE"))
            not in {"PREPARING", "LIMIT"}
        ):
            message = session.first_hint_text or "Choose an angle · choose Light or Shadow · paint"
            _text(tr(message), geometry.rail.x0, geometry.rail.y1 + 5.0, size=11)
    except (AttributeError, ReferenceError, RuntimeError, SystemError, ValueError):
        return
    finally:
        try:
            gpu.state.blend_set("NONE")
        except (AttributeError, RuntimeError):
            pass


def _select_key(context: Any, project: Any, index: int) -> None:
    from .studio import select_paint_key

    select_paint_key(context, project, index=index)


def _set_seek(context: Any, project: Any, value: float) -> None:
    from .studio import seek_preview

    seek_preview(context, project, value, show_in_image_editor=True)


def _set_seek_position_only(context: Any, project: Any, value: float) -> None:
    """Move the visible playhead without rebuilding a preview every mouse event."""

    angle = max(0.0, min(90.0, float(value)))
    project.seek_angle = angle
    session = active_session(context)
    if session is not None and session.project_uuid == str(project.uuid):
        session.view_mode = "PREVIEW"
        session.seek_angle = angle
    tag_timeline_redraw()


def _settle_seek(context: Any, project: Any, value: float) -> None:
    from .studio import settle_seek

    settle_seek(context, project, value)
    tag_timeline_redraw()


class QSDF_GT_timeline_capture(bpy.types.Gizmo):
    bl_idname = "QSDF_GT_timeline_capture"
    __slots__ = (
        "_shape",
        "_initial_seek",
        "_initial_key_uuid",
        "_dragging",
        "_last_preview_time",
    )

    def setup(self):
        self._shape = self.new_custom_shape("TRIS", ((0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)))
        self._initial_seek = 0.0
        self._initial_key_uuid = ""
        self._dragging = False
        self._last_preview_time = 0.0

    def draw(self, _context):
        # The visible timeline is rendered by the shared draw handler.
        pass

    def test_select(self, context, location):
        x, y = location
        return 0 if 0.0 <= x <= context.region.width and 0.0 <= y <= context.region.height else -1

    def invoke(self, context, event):
        project = ui_context.studio_workspace_project(context)
        if project is None:
            return {"CANCELLED"}
        session = active_session(context)
        if session is not None and bool(getattr(session, "editing_aux_mask_uuid", "")):
            return {"CANCELLED"}
        keys = _visible_keys(project)
        geometry = build_geometry(context.region.width, context.region.height, keys)
        x, y = float(event.mouse_region_x), float(event.mouse_region_y)
        for index, rect in geometry.key_rects:
            if rect.contains(x, y):
                _select_key(context, project, index)
                return {"FINISHED"}
        _playhead_x, label_rect, _label = _playhead_layout(
            geometry,
            context.region.width,
            context.region.height,
            _seek_value(project),
        )
        scrub_lane = Rect(
            geometry.rail.x0,
            0.0,
            geometry.rail.x1,
            float(context.region.height),
        )
        if scrub_lane.contains(x, y) or label_rect.contains(x, y):
            self._initial_seek = _seek_value(project)
            active = int(project.active_angle_index)
            self._initial_key_uuid = str(
                project.angles[active].uuid
                if 0 <= active < len(project.angles)
                else ""
            )
            self._dragging = True
            value = geometry.angle_from_x(x)
            _set_seek(context, project, value)
            _set_scrub_status(context, value)
            self._last_preview_time = time.monotonic()
            return {"RUNNING_MODAL"}
        return {"FINISHED"}

    def _restore_initial_key(self, context: Any, project: Any) -> None:
        if self._initial_key_uuid:
            for index, item in enumerate(project.angles):
                if str(getattr(item, "uuid", "")) == self._initial_key_uuid:
                    _select_key(context, project, index)
                    return
        index = _nearest_key_index(_visible_keys(project), self._initial_seek)
        if index is not None:
            _select_key(context, project, index)
        else:
            _set_seek(context, project, self._initial_seek)

    def modal(self, context, event, _tweak):
        project = ui_context.studio_workspace_project(context)
        if project is None:
            _clear_scrub_status(context)
            return {"CANCELLED"}
        if event.type in {"ESC", "WINDOW_DEACTIVATE"} or (
            event.type == "RIGHTMOUSE" and event.value == "PRESS"
        ):
            self._restore_initial_key(context, project)
            _clear_scrub_status(context)
            self._dragging = False
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            geometry = build_geometry(context.region.width, context.region.height, _visible_keys(project))
            value = geometry.angle_from_x(float(event.mouse_region_x))
            now = time.monotonic()
            if now - self._last_preview_time >= _SCRUB_PREVIEW_INTERVAL:
                _set_seek(context, project, value)
                self._last_preview_time = now
            else:
                _set_seek_position_only(context, project, value)
            _set_scrub_status(context, value)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            geometry = build_geometry(
                context.region.width, context.region.height, _visible_keys(project)
            )
            angle = geometry.angle_from_x(float(event.mouse_region_x))
            _settle_seek(context, project, angle)
            _clear_scrub_status(context)
            self._dragging = False
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def exit(self, context, cancel):
        if not self._dragging:
            return
        project = ui_context.studio_workspace_project(context)
        if project is None:
            _clear_scrub_status(context)
            self._dragging = False
            return
        if cancel:
            self._restore_initial_key(context, project)
        else:
            _settle_seek(context, project, _seek_value(project))
        _clear_scrub_status(context)
        self._dragging = False


class QSDF_GGT_timeline(bpy.types.GizmoGroup):
    bl_idname = "QSDF_GGT_timeline"
    bl_label = "Quick SDF Timeline"
    bl_space_type = TIMELINE_SPACE_TYPE
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT"}

    @classmethod
    def poll(cls, context):
        if ui_context.studio_workspace_project(context) is None:
            return False
        session = active_session(context)
        return not bool(session is not None and getattr(session, "editing_aux_mask_uuid", ""))

    def setup(self, _context):
        gizmo = self.gizmos.new(QSDF_GT_timeline_capture.bl_idname)
        gizmo.use_draw_modal = True
        gizmo.use_tooltip = False
        self.capture = gizmo


class QUICKSDF_OT_timeline_block_context_menu(bpy.types.Operator):
    bl_idname = "quicksdf.timeline_block_context_menu"
    bl_label = "Quick SDF Timeline"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return bool(
            getattr(getattr(context, "area", None), "type", "") == TIMELINE_SPACE_TYPE
            and ui_context.studio_workspace_project(context) is not None
        )

    def execute(self, _context):
        # The timeline has no context menu. Consuming RMB prevents the host Node
        # Editor from exposing controls which do not belong to artist authoring.
        return {"FINISHED"}


CLASSES = (
    QUICKSDF_OT_timeline_block_context_menu,
    QSDF_GT_timeline_capture,
    QSDF_GGT_timeline,
)


def tag_timeline_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type in {TIMELINE_SPACE_TYPE, "VIEW_3D"}:
                area.tag_redraw()


def register_timeline() -> None:
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceNodeEditor.draw_handler_add(
            _draw_timeline, (), "WINDOW", "POST_PIXEL"
        )
    keyconfigs = getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None)
    addon = getattr(keyconfigs, "addon", None)
    if addon is not None and not _ADDON_KEYMAPS:
        keymap = addon.keymaps.new(
            name="Node Editor", space_type="NODE_EDITOR", region_type="WINDOW"
        )
        item = keymap.keymap_items.new(
            "quicksdf.timeline_block_context_menu",
            type="RIGHTMOUSE",
            value="PRESS",
        )
        _ADDON_KEYMAPS.append((keymap, item))


def unregister_timeline() -> None:
    global _DRAW_HANDLE, _IMAGE_SHADER
    while _ADDON_KEYMAPS:
        keymap, item = _ADDON_KEYMAPS.pop()
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ReferenceError, ValueError):
            pass
        _DRAW_HANDLE = None
    _IMAGE_SHADER = None
    _GEOMETRY_CACHE.clear()
    _IMAGE_BATCH_CACHE.clear()
    bl_ui_rounded.clear_caches()
    try:
        from .preview_cache import invalidate

        invalidate()
    except (ImportError, RuntimeError):
        pass


__all__ = [
    "CLASSES", "QSDF_GGT_timeline", "QSDF_GT_timeline_capture", "Rect", "TimelineGeometry",
    "build_geometry", "register_timeline", "tag_timeline_redraw", "unregister_timeline",
]

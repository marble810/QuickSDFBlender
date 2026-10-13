# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport lighting slider and angle-aligned paint-key thumbnails."""
from __future__ import annotations

from dataclasses import dataclass
import time

import bpy

from . import timeline
from .bl_ui_widgets.bl_ui_slider import BL_UI_Slider
from .model import DEFAULT_KEY_ANGLES, MAX_KEYS_PER_SIDE
from .studio import active_session

_DRAW_HANDLE = None
_SLIDER = None
_LAYOUT = None
_UI_SIZE_MULTIPLIER = 1.5
_THUMBNAIL_SIZE_MULTIPLIER = 0.75


@dataclass(frozen=True)
class ViewportLayout:
    panel: timeline.Rect
    geometry: timeline.TimelineGeometry
    scale: float
    controls: tuple
    count: int


def _project(context):
    if getattr(getattr(context, "area", None), "type", "") != "VIEW_3D":
        return None
    if getattr(getattr(context, "region", None), "type", "") != "WINDOW":
        return None
    if not context.space_data.show_gizmo:
        return None
    project = timeline._project_for_context(context)
    session = active_session(context)
    if session is not None and session.editing_aux_mask_uuid:
        return None
    return project


def _geometry(context):
    project = _project(context)
    if project is None:
        return None
    # Blender's official custom-UI multiplier already includes OS DPI and
    # Preferences > Interface > Resolution Scale. Do not multiply DPI again.
    from .preferences import timeline_ui_scale

    scale = (_UI_SIZE_MULTIPLIER * float(context.preferences.system.ui_scale)
             * timeline_ui_scale(context))
    region = context.region
    # Region coordinates, not area coordinates: headers must not shift hits.
    left, right = 12.0 * scale, float(region.width) - 12.0 * scale
    for other in context.area.regions:
        if other.type == "TOOLS" and other.width > 1:
            left = max(left, other.x + other.width - region.x + 12.0 * scale)
        elif other.type == "UI" and other.width > 1:
            right = min(right, other.x - region.x - 12.0 * scale)
    width = min(1200.0 * scale, right - left)
    if width < 220.0 * scale or region.height < 190.0 * scale:
        return None
    x = left + (right - left - width) * 0.5
    thumb_width = 64.0 * scale * _THUMBNAIL_SIZE_MULTIPLIER
    thumb_height = 60.0 * scale * _THUMBNAIL_SIZE_MULTIPLIER
    # Reserve half a card even if a key is placed at a rail boundary.
    margin = thumb_width * 0.5 + 12.0 * scale
    rail = timeline.Rect(x + margin, 34.0 * scale,
                         x + width - margin, 58.0 * scale)
    keys = timeline._visible_keys(project)
    count = len(keys)
    geometry = timeline.TimelineGeometry(rail, (), 0.0, 90.0)
    key_rects = []
    for index, item in keys:
        center = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(item.angle)))
        key_rects.append((index, timeline.Rect(center - thumb_width * 0.5, 80.0 * scale,
                                              center + thumb_width * 0.5,
                                              80.0 * scale + thumb_height)))
    key_rects = tuple(key_rects)
    panel = timeline.Rect(x, 12.0 * scale, x + width, 157.0 * scale)
    if panel.y1 > region.height - 12.0 * scale:
        return None
    active = next((item for index, item in keys if index == int(project.active_angle_index)), None)
    from . import operators
    session = active_session(context)
    busy = bool(operators._BAKE_JOB or operators._EXPORT_JOB
                or (session is not None and session.provisional_promoting))
    can_delete = not busy and active is not None and not _is_default_key(float(active.angle))
    preview_only = timeline._stored_preview_yaw(project) is not None
    definitions = (("add", "+", not busy and not preview_only and count < MAX_KEYS_PER_SIDE),
                   ("delete", "-", can_delete))
    controls = tuple((name, label, timeline.Rect(rail.x1 - (2 - i) * 27.0 * scale,
                      panel.y1 - 26.0 * scale, rail.x1 - (2 - i) * 27.0 * scale + 23.0 * scale,
                      panel.y1 - 6.0 * scale), enabled)
                     for i, (name, label, enabled) in enumerate(definitions))
    return ViewportLayout(panel, timeline.TimelineGeometry(rail, key_rects, 0.0, 90.0),
                          scale, controls, count)


def _draw_slider_zones(rail, keys):
    """Blue where keys can be added; red outside the first and last key."""

    if not keys:
        timeline._rect(rail, (0.72, 0.24, 0.20, 1.0))
        return
    first = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(keys[0][1].angle)))
    last = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(keys[-1][1].angle)))
    left, right = (first, last) if first <= last else (last, first)
    red = (0.72, 0.24, 0.20, 1.0)
    blue = (0.18, 0.45, 0.82, 1.0)
    if left > rail.x0:
        timeline._rect(timeline.Rect(rail.x0, rail.y0, left, rail.y1), red)
    if right > left:
        timeline._rect(timeline.Rect(left, rail.y0, right, rail.y1), blue)
    if rail.x1 > right:
        timeline._rect(timeline.Rect(right, rail.y0, rail.x1, rail.y1), red)


def _clear_preview_yaw(project):
    try:
        if timeline.PREVIEW_YAW_KEY in project:
            del project[timeline.PREVIEW_YAW_KEY]
    except (KeyError, TypeError):
        pass


def _scrub_from_x(context, project, rail, x, *, settle=False, preview=True):
    """Scrub the full 180..0 rail. Outside the baked keys, preview only."""

    from .live_preview import update_endpoint_preview
    from .studio import active_session, discard_provisional

    yaw = timeline._yaw_from_rail(rail, x)
    authoring = timeline._authoring_angle_for_yaw(yaw)
    if authoring is None:
        session = active_session(context)
        if session is not None and session.provisional_uuid and not session.provisional_promoting:
            discard_provisional(context, project)
        project[timeline.PREVIEW_YAW_KEY] = float(yaw)
        project.seek_angle = 0.0 if yaw >= 120.0 else 90.0
        if session is not None and session.project_uuid == str(project.uuid):
            session.view_mode = "PREVIEW"
            session.seek_angle = float(project.seek_angle)
        if preview:
            update_endpoint_preview(project, yaw)
        return
    _clear_preview_yaw(project)
    if settle:
        timeline._settle_seek(context, project, authoring)
    elif preview:
        timeline._set_seek(context, project, authoring)
    else:
        timeline._set_seek_position_only(context, project, authoring)


def _is_default_key(angle):
    return any(abs(angle - value) < 1.0e-5 for value in DEFAULT_KEY_ANGLES)


def _status_text(project, session):
    from .i18n import tr
    yaw = timeline._stored_preview_yaw(project)
    if yaw is None:
        yaw = timeline._physical_light_yaw(float(project.seek_angle))
        text = f"{timeline._progress_percent(project.seek_angle):.1f}% · {yaw:.1f}°"
    else:
        text = f"{yaw:.1f}°"
    if session is not None and session.provisional_uuid:
        state = str(session.provisional_state)
        label = {"PREPARING": "Preparing", "READY": "Pending paint",
                 "LIMIT": "Key limit reached", "FAILED": "Preparation failed"}.get(state, "Preview")
    else:
        label = "Paint" if session is not None and session.view_mode == "EDIT" else "Preview"
    return text + " · " + tr(label)


def _set_status(context, project):
    if context.workspace is not None:
        context.workspace.status_text_set(_status_text(project, active_session(context)))


def _draw():
    global _SLIDER, _LAYOUT
    context = bpy.context
    project = _project(context)
    layout = _geometry(context) if project is not None else None
    if project is None or layout is None:
        return
    import gpu
    from .i18n import tr

    panel, geometry, scale = layout.panel, layout.geometry, layout.scale
    rail = geometry.rail
    session = active_session(context)
    keys = timeline._visible_keys(project)
    key_map = dict(keys)
    if _SLIDER is None:
        _SLIDER = BL_UI_Slider(0, 0, 1, 1)
        _SLIDER.decimals = 1
        _SLIDER.show_min_max = False
        _SLIDER.bg_color = (0.22, 0.24, 0.27, 1.0)
        _SLIDER.color = (0.25, 0.72, 1.0, 1.0)
    slider = _SLIDER
    slider.context = context
    key = (rail, context.region.height, scale)
    if key != _LAYOUT:
        slider.width = rail.x1 - rail.x0
        slider.height = rail.y1 - rail.y0
        slider.text_size = round(11 * scale)
        slider.ui_scale = scale
        slider.update(rail.x0, context.region.height - rail.y1)
        _LAYOUT = key
    yaw = timeline._stored_preview_yaw(project)
    if yaw is None:
        yaw = timeline._physical_light_yaw(float(project.seek_angle))
    slider.set_value((timeline._x_for_yaw(rail, yaw) - rail.x0) / (rail.x1 - rail.x0) * 100.0)
    slider.value_text = f"{yaw:.1f}°"
    preview_only = timeline._stored_preview_yaw(project) is not None
    slider.bg_color = (0.0, 0.0, 0.0, 0.0)
    previous_blend = gpu.state.blend_get()
    try:
        gpu.state.blend_set("ALPHA")
        timeline._rect(panel, (0.045, 0.05, 0.06, 0.94))
        timeline._outline(panel, (0.22, 0.24, 0.27, 1.0), scale)
        side = str(keys[0][1].side) if keys else str(project.authoring_side)
        title = tr("Keys") + f" · {tr(side.title())} · {layout.count}/{MAX_KEYS_PER_SIDE}"
        if panel.x1 - panel.x0 < 340.0 * scale:
            title = f"{layout.count}/{MAX_KEYS_PER_SIDE}"
        timeline._text(title, rail.x0, panel.y1 - 21.0 * scale, size=round(11 * scale))
        for _name, label, rect, enabled in layout.controls:
            timeline._rect(rect, (0.16, 0.18, 0.21, 1.0) if enabled else (0.08, 0.09, 0.11, 1.0))
            timeline._text(label, rect.x0 + 7 * scale, rect.y0 + 5 * scale,
                           color=(0.9, 0.92, 0.96, 1.0) if enabled else (0.35, 0.37, 0.40, 1.0),
                           size=round(11 * scale))
        active = int(project.active_angle_index)
        active_item = key_map.get(active)
        active_image = timeline._image_for_key(project, active_item) if active_item is not None else None
        bbox = tuple(float(v) for v in project.thumbnail_uv_bbox)
        thumb_scale = scale * _THUMBNAIL_SIZE_MULTIPLIER
        # Single-row cards may overlap; keep the selected card on top.
        for index, rect in sorted(geometry.key_rects, key=lambda pair: pair[0] == active):
            item = key_map[index]
            timeline._rect(rect, (0.08, 0.09, 0.11, 1.0))
            image = timeline._image_for_key(project, item)
            if image is None or not timeline._draw_thumbnail(image, rect, bbox, project, active_image, scale=thumb_scale):
                timeline._rect(timeline.Rect(rect.x0 + 2 * thumb_scale, rect.y0 + 16 * thumb_scale,
                                            rect.x1 - 2 * thumb_scale, rect.y1 - 2 * thumb_scale),
                               (0.18, 0.19, 0.21, 1.0))
                timeline._text("?", rect.x0 + 5 * thumb_scale, rect.y0 + 25 * thumb_scale, size=round(11 * thumb_scale))
            timeline._text(timeline._format_progress(float(item.angle)), rect.x0 + 4 * thumb_scale,
                           rect.y0 + 4 * thumb_scale, size=round(10 * thumb_scale))
            if _is_default_key(float(item.angle)):
                timeline._outline(rect, (0.20, 0.80, 0.35, 1.0), 2.0 * thumb_scale)
            if index == active and not preview_only:
                # Blue outside the card leaves its green stage border visible.
                selected_rect = timeline.Rect(rect.x0 - 2 * thumb_scale, rect.y0 - 2 * thumb_scale,
                                              rect.x1 + 2 * thumb_scale, rect.y1 + 2 * thumb_scale)
                timeline._outline(selected_rect, (0.18, 0.62, 1.0, 1.0), thumb_scale)
            if _is_default_key(float(item.angle)):
                timeline._rect(timeline.Rect(rect.x1 - 8 * thumb_scale, rect.y0 + 5 * thumb_scale,
                                            rect.x1 - 4 * thumb_scale, rect.y0 + 9 * thumb_scale),
                               (0.65, 0.68, 0.72, 1.0))
            if bool(item.has_violation):
                timeline._rect(timeline.Rect(rect.x1 - 9 * thumb_scale, rect.y1 - 9 * thumb_scale,
                                            rect.x1 - 3 * thumb_scale, rect.y1 - 3 * thumb_scale),
                               (1.0, 0.15, 0.06, 1.0))
        timeline._text(_status_text(project, session), rail.x0, 65.0 * scale, size=round(11 * scale))
        _draw_slider_zones(rail, keys)
        slider.draw()
        for index, item in keys:
            position = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(item.angle)))
            color = (0.18, 0.62, 1.0, 1.0) if index == active and not preview_only else (0.55, 0.58, 0.62, 1.0)
            timeline._rect(timeline.Rect(position - scale, rail.y0 - 2 * scale,
                                        position + scale, rail.y0 + 3 * scale), color)
        if session is not None and session.provisional_uuid:
            position = timeline._x_for_yaw(
                rail, timeline._physical_light_yaw(float(project.seek_angle))
            )
            # Hollow marker distinguishes the uncommitted position from keys.
            timeline._outline(timeline.Rect(position - 3 * scale, rail.y1 + scale,
                                            position + 3 * scale, rail.y1 + 5 * scale),
                              (1.0, 0.68, 0.20, 1.0), scale)
        middle = (rail.x0 + rail.x1) * 0.5
        timeline._text("180°", rail.x0, panel.y0 + 7 * scale,
                       color=(0.85, 0.35, 0.30, 1.0), size=round(10 * scale))
        timeline._text(tr("Side Light 90°"), middle - 35 * scale,
                       panel.y0 + 7 * scale, size=round(10 * scale))
        timeline._text("0°", rail.x1 - 18 * scale, panel.y0 + 7 * scale,
                       color=(0.85, 0.35, 0.30, 1.0), size=round(10 * scale))
    finally:
        gpu.state.blend_set(previous_blend)


class QSDF_GT_viewport_lighting(bpy.types.Gizmo):
    bl_idname = "QSDF_GT_viewport_lighting"
    __slots__ = ("_shape", "_initial_seek", "_initial_key_uuid", "_initial_preview_yaw",
                 "_dragging", "_last_preview_time")

    setup = timeline.QSDF_GT_timeline_capture.setup
    draw = timeline.QSDF_GT_timeline_capture.draw
    _restore_initial_key = timeline.QSDF_GT_timeline_capture._restore_initial_key
    def exit(self, context, cancel):
        # Blender can end a gizmo tweak without delivering RELEASE to modal().
        # Never use the legacy exit: it settles the clamped authoring angle.
        if not self._dragging:
            return
        project = _project(context)
        if project is not None:
            if cancel:
                _clear_preview_yaw(project)
                self._restore_initial_key(context, project)
                initial_yaw = getattr(self, "_initial_preview_yaw", None)
                layout = _geometry(context)
                if initial_yaw is not None and layout is not None:
                    rail = layout.geometry.rail
                    _scrub_from_x(context, project, rail, timeline._x_for_yaw(rail, initial_yaw))
            elif (yaw := timeline._stored_preview_yaw(project)) is not None:
                from .live_preview import update_endpoint_preview
                update_endpoint_preview(project, yaw)
            else:
                timeline._settle_seek(context, project, float(project.seek_angle))
        timeline._clear_scrub_status(context)
        self._dragging = False

    def test_select(self, context, location):
        layout = _geometry(context)
        return 0 if layout is not None and layout.panel.contains(*location) else -1

    def invoke(self, context, event):
        project = _project(context)
        layout = _geometry(context)
        if project is None or layout is None:
            return {"CANCELLED"}
        location = (event.mouse_region_x, event.mouse_region_y)
        for name, _label, rect, enabled in layout.controls:
            if not rect.contains(*location):
                continue
            if enabled:
                if name == "add":
                    bpy.ops.quicksdf.key_add("INVOKE_DEFAULT", angle=-1.0, duplicate=False)
                elif name == "delete":
                    bpy.ops.quicksdf.key_delete("INVOKE_DEFAULT", uuid=str(project.active_angle_uuid))
            return {"FINISHED"}
        hits = [(index, rect) for index, rect in layout.geometry.key_rects if rect.contains(*location)]
        if hits:
            index, _rect = min(hits, key=lambda pair: abs(location[0] - (pair[1].x0 + pair[1].x1) * 0.5))
            _clear_preview_yaw(project)
            timeline._select_key(context, project, index)
            return {"FINISHED"}
        rail = layout.geometry.rail
        # Consume panel background clicks without changing light or painting.
        if not rail.contains(*location):
            return {"FINISHED"}
        self._initial_preview_yaw = timeline._stored_preview_yaw(project)
        self._initial_seek = float(project.seek_angle)
        index = int(project.active_angle_index)
        self._initial_key_uuid = str(project.angles[index].uuid) if 0 <= index < len(project.angles) else ""
        self._dragging = True
        _scrub_from_x(context, project, rail, event.mouse_region_x)
        _set_status(context, project)
        self._last_preview_time = time.monotonic()
        return {"RUNNING_MODAL"}

    def modal(self, context, event, _tweak):
        project = _project(context)
        layout = _geometry(context)
        if project is None or layout is None:
            if project is not None:
                self._restore_initial_key(context, project)
            timeline._clear_scrub_status(context)
            self._dragging = False
            return {"CANCELLED"}
        if event.type in {"ESC", "WINDOW_DEACTIVATE"} or (event.type == "RIGHTMOUSE" and event.value == "PRESS"):
            self.exit(context, True)
            return {"CANCELLED"}
        rail = layout.geometry.rail
        if event.type == "MOUSEMOVE":
            now = time.monotonic()
            preview = now - self._last_preview_time >= timeline._SCRUB_PREVIEW_INTERVAL
            _scrub_from_x(
                context, project, rail, event.mouse_region_x, preview=preview
            )
            if preview:
                self._last_preview_time = now
            _set_status(context, project)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            _scrub_from_x(
                context, project, rail, event.mouse_region_x, settle=True
            )
            timeline._clear_scrub_status(context)
            self._dragging = False
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


class QSDF_GGT_viewport_lighting(bpy.types.GizmoGroup):
    bl_idname = "QSDF_GGT_viewport_lighting"
    bl_label = "Quick SDF Lighting"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT"}

    @classmethod
    def poll(cls, context):
        return _project(context) is not None

    def setup(self, _context):
        self.capture = self.gizmos.new(QSDF_GT_viewport_lighting.bl_idname)
        self.capture.use_draw_modal = True
        self.capture.use_tooltip = False


CLASSES = (QSDF_GT_viewport_lighting, QSDF_GGT_viewport_lighting)


def register_overlay():
    global _DRAW_HANDLE
    from . import lighting_indicator
    if _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
    lighting_indicator.register_overlay()


def unregister_overlay():
    global _DRAW_HANDLE, _SLIDER, _LAYOUT
    from . import lighting_indicator
    lighting_indicator.unregister_overlay()
    if _DRAW_HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        _DRAW_HANDLE = None
    timeline._clear_scrub_status(bpy.context)
    _SLIDER = None
    _LAYOUT = None

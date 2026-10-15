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
from .ui import context as ui_context

_DRAW_HANDLE = None
_SLIDER = None
_LAYOUT = None
_UI_SIZE_MULTIPLIER = 1.5
_THUMBNAIL_SIZE_MULTIPLIER = 0.5625  # 75% of the previous 75%-size cards
_HANDLE_HEIGHT_MULTIPLIER = 1.2       # Handle is taller than the rail it rides
# All vertical metrics are in interface-scale units and are anchored to the
# panel bottom, so the overlay hugs its content instead of a fixed height.
_PANEL_PADDING = 3.0                  # Outer margin and side/bottom inner padding
_CARD_TOP_PADDING = 12.0              # Panel padding between its top edge and the cards
# The add/delete buttons are not given a row of panel background: like
# BlenderKit's free buttons they hang on the outside of the panel, standing
# proud of its top edge. This is the gap they keep from that edge; a negative
# value lets them straddle it instead of floating above it.
_BUTTON_MARGIN = 6.0

_LABEL_INSET = 2.0                    # Baseline inset of the 180/90/0 labels
_LABEL_SIZE = 10.0                    # Font size of the 180/90/0 labels
_RAIL_HEIGHT = 12.0
_VALUE_TEXT_RESERVE = 13.0            # Slider value label drawn above the rail
_BUTTON_HEIGHT = 20.0
# Blender draws widget corners with ``roundness * 0.5 * min(size)`` and the
# default theme roundness is 0.4. Large surfaces are capped so the overlay does
# not become a pill; the radius itself follows the theme in _palette().
_ROUNDNESS_FALLBACK = 0.4
_MAX_CORNER_RADIUS = 4.5
_HANDLE_HALF_WIDTH = 5.0              # Matches BL_UI_Slider.__slider_width
_TEXT_HEIGHT_CACHE: dict[int, float] = {}
# Semantic colours with no Python-exposed theme entry: Blender's alert red and
# the hard error red are C-only (TH_REDALERT). Everything else follows the
# theme in _palette().
_ALERT_RED = (0.85, 0.35, 0.30, 1.0)
_INVALID_RED = (0.72, 0.24, 0.20, 1.0)
_VIOLATION_RED = (1.0, 0.15, 0.06, 1.0)
_FALLBACK_WIDGET_TEXT = (0.902, 0.902, 0.902, 1.0)


@dataclass(frozen=True)
class ThemePalette:
    """Blender theme colours for the overlay, resolved once per draw."""

    panel_bg: tuple
    panel_outline: tuple
    card_bg: tuple
    card_outline: tuple
    button_bg: tuple
    button_outline: tuple
    button_outline_off: tuple
    button_bg_off: tuple
    text: tuple
    text_off: tuple
    text_muted: tuple
    accent: tuple
    keyed: tuple
    changed: tuple
    invalid: tuple
    alert: tuple
    violation: tuple
    roundness: float
    panel_roundness: float


def _rgba(value, fallback):
    """Normalise a theme colour sequence to RGBA, else return *fallback*."""

    if value is None:
        return fallback
    try:
        channels = [float(channel) for channel in value]
    except (TypeError, ValueError):
        return fallback
    if len(channels) == 3:
        channels.append(1.0)
    if len(channels) != 4:
        return fallback
    return tuple(max(0.0, min(1.0, channel)) for channel in channels)


def _widget_color(ui, name, slot, fallback):
    """Read one ``wcol_*`` slot, normalised to RGBA."""

    style = getattr(ui, name, None)
    return _rgba(getattr(style, slot, None) if style is not None else None, fallback)


def _theme_color(ui, name, fallback):
    """Read one plain RGBA theme colour (``panel_sub_back`` and friends)."""

    return _rgba(getattr(ui, name, None), fallback)


def _shade(color, factor, alpha=None):
    """Scale an RGBA colour; used for Blender's dimmed disabled widgets."""

    result = (color[0] * factor, color[1] * factor, color[2] * factor,
              color[3] if alpha is None else alpha)
    return tuple(max(0.0, min(1.0, channel)) for channel in result)


def _fallback_palette():
    return ThemePalette(
        panel_bg=(0.0, 0.0, 0.0, 0.1216), panel_outline=(0.22, 0.24, 0.27, 1.0),
        card_bg=(0.08, 0.09, 0.11, 1.0), card_outline=(0.18, 0.19, 0.21, 1.0),
        button_bg=(0.16, 0.18, 0.21, 1.0), button_outline=(0.22, 0.24, 0.27, 1.0),
        button_bg_off=(0.08, 0.09, 0.11, 1.0),
        button_outline_off=_shade((0.22, 0.24, 0.27, 1.0), 0.55),
        text=_FALLBACK_WIDGET_TEXT,
        text_off=(0.35, 0.37, 0.40, 1.0), text_muted=(0.65, 0.68, 0.72, 1.0),
        accent=(0.25, 0.72, 1.0, 1.0), keyed=(0.20, 0.80, 0.35, 1.0),
        changed=(1.0, 0.68, 0.20, 1.0), invalid=_INVALID_RED, alert=_ALERT_RED,
        violation=_VIOLATION_RED, roundness=_ROUNDNESS_FALLBACK,
        panel_roundness=_ROUNDNESS_FALLBACK,
    )


def _palette(context):
    """Map Blender's theme widgets onto the overlay, like native panels.

    - panel  ← ``panel_sub_back`` (the translucent sheet Blender lays over the
      region for a panel body, so the overlay keeps the viewport tone)
    - card   ← ``wcol_box`` (BlenderKit uses ``wcol_box.inner`` as the default
      Blender background)
    - button ← ``wcol_regular``
    - rail/handle/selection ← ``wcol_numslider.item`` (widget accent)
    - keyed key ← ``wcol_state.inner_anim`` (native "animated" green)
    - pending key ← ``wcol_state.inner_changed`` (native "changed" orange)
    """

    try:
        ui = context.preferences.themes[0].user_interface
    except (AttributeError, IndexError, KeyError, TypeError):
        return _fallback_palette()
    fallback = _fallback_palette()
    button_bg = _widget_color(ui, "wcol_regular", "inner", fallback.button_bg)
    button_outline = _widget_color(ui, "wcol_regular", "outline", fallback.button_outline)
    text = _widget_color(ui, "wcol_regular", "text", fallback.text)
    if len(text) == 3:
        text = text + (1.0,)
    try:
        roundness = max(0.0, float(getattr(ui.wcol_regular, "roundness", _ROUNDNESS_FALLBACK)))
    except (AttributeError, TypeError, ValueError):
        roundness = _ROUNDNESS_FALLBACK
    try:
        panel_roundness = max(0.0, float(getattr(ui, "panel_roundness", roundness)))
    except (TypeError, ValueError):
        panel_roundness = roundness
    return ThemePalette(
        panel_bg=_theme_color(ui, "panel_sub_back", fallback.panel_bg),
        panel_outline=_widget_color(ui, "wcol_menu_back", "outline", fallback.panel_outline),
        card_bg=_widget_color(ui, "wcol_box", "inner", fallback.card_bg),
        card_outline=_widget_color(ui, "wcol_box", "outline", fallback.card_outline),
        button_bg=button_bg,
        button_outline=button_outline,
        button_bg_off=_shade(button_bg, 0.55, alpha=button_bg[3]),
        button_outline_off=_shade(button_outline, 0.55, alpha=button_outline[3]),
        text=text,
        text_off=_shade(text, 0.45, alpha=text[3]),
        text_muted=_shade(text, 0.7, alpha=text[3]),
        accent=_widget_color(ui, "wcol_numslider", "item", fallback.accent),
        keyed=_widget_color(ui, "wcol_state", "inner_anim", fallback.keyed),
        changed=_widget_color(ui, "wcol_state", "inner_changed", fallback.changed),
        invalid=_INVALID_RED,
        alert=_ALERT_RED,
        violation=_VIOLATION_RED,
        roundness=roundness,
        panel_roundness=panel_roundness,
    )


def _corner_radius(rect, scale, roundness):
    """Native-style corner radius for one rect, clamped to the UI cap."""

    smallest = min(rect.x1 - rect.x0, rect.y1 - rect.y0)
    return min(roundness * 0.5 * smallest, _MAX_CORNER_RADIUS * scale)


def _text_height(size: int) -> float:
    """Cached pixel height of a card/axis label, used to size label strips."""

    height = _TEXT_HEIGHT_CACHE.get(size)
    if height is None:
        import blf

        try:
            blf.size(0, size)
        except TypeError:
            blf.size(0, size, 72)
        height = float(blf.dimensions(0, "100.0%")[1])
        _TEXT_HEIGHT_CACHE[size] = height
    return height


@dataclass(frozen=True)
class ViewportLayout:
    panel: timeline.Rect
    geometry: timeline.TimelineGeometry
    scale: float
    controls: tuple
    count: int


def _project(context):
    return ui_context.viewport_project(context)


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
    padding = _PANEL_PADDING * scale
    # Region coordinates, not area coordinates: headers must not shift hits.
    left, right = padding, float(region.width) - padding
    for other in context.area.regions:
        if other.type == "TOOLS" and other.width > 1:
            left = max(left, other.x + other.width - region.x + padding)
        elif other.type == "UI" and other.width > 1:
            right = min(right, other.x - region.x - padding)
    width = min(1200.0 * scale, right - left)
    if width < 220.0 * scale or region.height < 190.0 * scale:
        return None
    x = left + (right - left - width) * 0.5
    thumb_width = 64.0 * scale * _THUMBNAIL_SIZE_MULTIPLIER
    thumb_height = 60.0 * scale * _THUMBNAIL_SIZE_MULTIPLIER
    # Reserve half a card even if a key is placed at a rail boundary.
    margin = thumb_width * 0.5 + padding
    # Compact vertical stack, measured up from the panel bottom: axis labels,
    # rail, slider value label, thumbnail row, add/delete buttons.
    rail_y0 = _PANEL_PADDING + _LABEL_INSET + _LABEL_SIZE + _PANEL_PADDING
    rail_y1 = rail_y0 + _RAIL_HEIGHT
    thumb_y0 = rail_y1 + _VALUE_TEXT_RESERVE + _PANEL_PADDING
    thumb_y1 = thumb_y0 + thumb_height / scale
    # The panel stops just above the cards; the buttons protrude past its top
    # edge instead of occupying a strip of panel background.
    panel_y1 = thumb_y1 + _CARD_TOP_PADDING
    button_y0 = panel_y1 + _BUTTON_MARGIN
    button_y1 = button_y0 + _BUTTON_HEIGHT
    rail = timeline.Rect(x + margin, rail_y0 * scale,
                         x + width - margin, rail_y1 * scale)
    keys = timeline._visible_keys(project)
    count = len(keys)
    geometry = timeline.TimelineGeometry(rail, (), 0.0, 90.0)
    key_rects = []
    for index, item in keys:
        center = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(item.angle)))
        key_rects.append((index, timeline.Rect(center - thumb_width * 0.5, thumb_y0 * scale,
                                              center + thumb_width * 0.5,
                                              thumb_y1 * scale)))
    key_rects = tuple(key_rects)
    panel = timeline.Rect(x, _PANEL_PADDING * scale, x + width, panel_y1 * scale)
    if max(panel.y1, button_y1 * scale) > region.height - padding:
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
                      button_y0 * scale,
                      rail.x1 - (2 - i) * 27.0 * scale + 23.0 * scale,
                      button_y1 * scale), enabled)
                     for i, (name, label, enabled) in enumerate(definitions))
    return ViewportLayout(panel, timeline.TimelineGeometry(rail, key_rects, 0.0, 90.0),
                          scale, controls, count)


def _draw_slider_zones(rail, keys, radius=0.0, invalid=_INVALID_RED, authorable=(0.18, 0.45, 0.82, 1.0)):
    """Authorable zone where keys can be added; invalid zone outside the first and last key."""

    if not keys:
        timeline._rect(rail, invalid, radius=radius)
        return
    first = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(keys[0][1].angle)))
    last = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(keys[-1][1].angle)))
    left, right = (first, last) if first <= last else (last, first)
    red = invalid
    blue = authorable
    # Only the rail ends are rounded; rounded inner corners would look broken.
    left_corners = (radius, 0.0, 0.0, radius)
    right_corners = (0.0, radius, radius, 0.0)
    if left > rail.x0:
        timeline._rect(timeline.Rect(rail.x0, rail.y0, left, rail.y1), red, radius=left_corners)
    if right > left:
        timeline._rect(timeline.Rect(left, rail.y0, right, rail.y1), blue)
    if rail.x1 > right:
        timeline._rect(timeline.Rect(right, rail.y0, rail.x1, rail.y1), red, radius=right_corners)


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
        # The handle is drawn by this module so it can use rounded corners.
        _SLIDER.color = (0.25, 0.72, 1.0, 0.0)
    slider = _SLIDER
    slider.context = context
    key = (rail, context.region.height, scale)
    if key != _LAYOUT:
        rail_height = rail.y1 - rail.y0
        # Keep the taller handle centered on the rail instead of hanging below it.
        handle_height = rail_height * _HANDLE_HEIGHT_MULTIPLIER
        slider.width = rail.x1 - rail.x0
        slider.height = handle_height
        slider.text_size = round(11 * scale)
        slider.ui_scale = scale
        slider.update(rail.x0,
                      context.region.height - (rail.y1 + (handle_height - rail_height) * 0.5))
        _LAYOUT = key
    yaw = timeline._stored_preview_yaw(project)
    if yaw is None:
        yaw = timeline._physical_light_yaw(float(project.seek_angle))
    slider.set_value((timeline._x_for_yaw(rail, yaw) - rail.x0) / (rail.x1 - rail.x0) * 100.0)
    slider.value_text = f"{yaw:.1f}°"
    preview_only = timeline._stored_preview_yaw(project) is not None
    slider.bg_color = (0.0, 0.0, 0.0, 0.0)
    previous_blend = gpu.state.blend_get()
    palette = _palette(context)
    roundness = palette.roundness
    panel_radius = _corner_radius(panel, scale, palette.panel_roundness)
    rail_radius = _corner_radius(rail, scale, roundness)
    card_radius = (_corner_radius(geometry.key_rects[0][1], scale, roundness)
                   if geometry.key_rects else panel_radius)
    try:
        gpu.state.blend_set("ALPHA")
        timeline._rect(panel, palette.panel_bg, radius=panel_radius)
        timeline._outline(panel, palette.panel_outline, scale, radius=panel_radius)
        active = int(project.active_angle_index)
        active_item = key_map.get(active)
        active_image = timeline._image_for_key(project, active_item) if active_item is not None else None
        bbox = tuple(float(v) for v in project.thumbnail_uv_bbox)
        thumb_scale = scale * _THUMBNAIL_SIZE_MULTIPLIER
        # The card label keeps its full font size, so reserve a strip that is
        # tall enough to enclose it even though the card itself is scaled down.
        label_size = round(10 * scale)
        label_strip = 4.0 * thumb_scale + _text_height(label_size) + 1.5 * scale
        # Single-row cards may overlap; keep the selected card on top.
        for index, rect in sorted(geometry.key_rects, key=lambda pair: pair[0] == active):
            item = key_map[index]
            timeline._rect(rect, palette.card_bg, radius=card_radius)
            image = timeline._image_for_key(project, item)
            image_radius = max(0.0, card_radius - 2.0 * thumb_scale)
            if image is None or not timeline._draw_thumbnail(
                    image, rect, bbox, project, active_image, scale=thumb_scale,
                    label_height=label_strip, radius=image_radius):
                timeline._rect(timeline.Rect(rect.x0 + 2 * thumb_scale, rect.y0 + label_strip,
                                            rect.x1 - 2 * thumb_scale, rect.y1 - 2 * thumb_scale),
                               palette.card_outline, radius=image_radius)
                timeline._text("?", rect.x0 + 5 * thumb_scale, rect.y0 + label_strip + 8 * scale,
                               color=palette.text, size=round(11 * scale))
            timeline._text(timeline._format_progress(float(item.angle)), rect.x0 + 4 * thumb_scale,
                           rect.y0 + 4 * thumb_scale, color=palette.text, size=label_size)
            if _is_default_key(float(item.angle)):
                timeline._outline(rect, palette.keyed, 2.0 * thumb_scale, radius=card_radius)
            if index == active and not preview_only:
                # Selection sits outside the card so its keyed border stays visible.
                selected_rect = timeline.Rect(rect.x0 - 2 * thumb_scale, rect.y0 - 2 * thumb_scale,
                                              rect.x1 + 2 * thumb_scale, rect.y1 + 2 * thumb_scale)
                timeline._outline(selected_rect, palette.accent, thumb_scale,
                                  radius=card_radius + 2.0 * thumb_scale)
            if _is_default_key(float(item.angle)):
                timeline._rect(timeline.Rect(rect.x1 - 8 * thumb_scale, rect.y0 + 5 * thumb_scale,
                                            rect.x1 - 4 * thumb_scale, rect.y0 + 9 * thumb_scale),
                               palette.text_muted)
            if bool(item.has_violation):
                timeline._rect(timeline.Rect(rect.x1 - 9 * thumb_scale, rect.y1 - 9 * thumb_scale,
                                            rect.x1 - 3 * thumb_scale, rect.y1 - 3 * thumb_scale),
                               palette.violation)
        _draw_slider_zones(rail, keys, rail_radius, palette.invalid, palette.accent)
        slider.text_color = palette.text
        slider.draw()
        handle_half_height = (rail.y1 - rail.y0) * _HANDLE_HEIGHT_MULTIPLIER * 0.5
        handle_center = (rail.y0 + rail.y1) * 0.5
        handle_x = timeline._x_for_yaw(rail, yaw)
        timeline._rect(timeline.Rect(handle_x - _HANDLE_HALF_WIDTH * scale,
                                    handle_center - handle_half_height,
                                    handle_x + _HANDLE_HALF_WIDTH * scale,
                                    handle_center + handle_half_height),
                       palette.accent, radius="50%")
        for index, item in keys:
            position = timeline._x_for_yaw(rail, timeline._physical_light_yaw(float(item.angle)))
            color = (palette.accent if index == active and not preview_only
                     else palette.text_muted)
            timeline._rect(timeline.Rect(position - scale, rail.y0 - 2 * scale,
                                        position + scale, rail.y0 + 3 * scale), color)
        if session is not None and session.provisional_uuid:
            position = timeline._x_for_yaw(
                rail, timeline._physical_light_yaw(float(project.seek_angle))
            )
            # Hollow marker distinguishes the uncommitted position from keys.
            timeline._outline(timeline.Rect(position - 3 * scale, rail.y1 + scale,
                                            position + 3 * scale, rail.y1 + 5 * scale),
                              palette.changed, scale)
        middle = (rail.x0 + rail.x1) * 0.5
        timeline._text("180°", rail.x0, panel.y0 + _LABEL_INSET * scale,
                       color=palette.alert, size=round(10 * scale))
        timeline._text(tr("Side Light 90°"), middle - 35 * scale,
                       panel.y0 + _LABEL_INSET * scale, color=palette.text, size=round(10 * scale))
        timeline._text("0°", rail.x1 - 18 * scale, panel.y0 + _LABEL_INSET * scale,
                       color=palette.alert, size=round(10 * scale))
        # Drawn last: the buttons stand outside the panel and must stay on top
        # of the cards they protrude above.
        for _name, label, rect, enabled in layout.controls:
            radius = _corner_radius(rect, scale, roundness)
            timeline._rect(rect, palette.button_bg if enabled else palette.button_bg_off,
                           radius=radius)
            # BlenderKit's widgets draw a border over their fill
            # (bl_ui_widget.py: draw_rounded_rect_with_border); same ring here.
            timeline._outline(rect, palette.button_outline if enabled
                              else palette.button_outline_off, scale, radius=radius)
            timeline._text(label, rect.x0 + 7 * scale, rect.y0 + 5 * scale,
                           color=palette.text if enabled else palette.text_off,
                           size=round(11 * scale))
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
        if layout is None:
            return -1
        if layout.panel.contains(*location):
            return 0
        # The controls hang outside the panel, so they need their own hit test.
        return 0 if any(rect.contains(*location) for _n, _l, rect, _e in layout.controls) else -1

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

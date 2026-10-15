# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick SDF paint tools for Blender's 3D and Image editors.

The keymaps live on WorkSpaceTool definitions, so LMB is wrapped only while the
artist has explicitly selected Quick SDF Paint.  No global paint keymap is
installed by this module.
"""

from __future__ import annotations

from typing import Any

import bpy
from bpy.types import WorkSpaceTool

from . import runtime
from .i18n import tr
from .ui import context as ui_context
from .ui import widgets


# Blender synchronizes texture-paint brush tool IDs between VIEW_3D and
# IMAGE_EDITOR (wm_toolsystem.cc: toolsystem_brush_sync_for_texture_paint).
# Both toolbar definitions must resolve the same ID, or each refresh requests
# a tool that does not exist in the other editor and reports a warning.
VIEW_TOOL_ID = "quicksdf.paint_view"
IMAGE_TOOL_ID = VIEW_TOOL_ID
_REGISTERED: list[type[WorkSpaceTool]] = []
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []


def _project(context: Any) -> Any | None:
    return ui_context.session_project(context)


def _draw_tool_settings(context: Any, layout: Any, _tool: Any) -> None:
    project = _project(context)
    if project is None:
        layout.label(text=tr("Open Quick SDF Paint to paint"), icon="INFO")
        return

    mask_uuid = ui_context.editing_aux_mask_uuid(context, project)
    row = layout.row(align=True)
    widgets.paint_value_row(row, project, mask=bool(mask_uuid), guarded=True)

    if mask_uuid:
        name = ""
        try:
            item = runtime.aux_mask_for_uuid(project, mask_uuid)
            name = str(getattr(item, "name", ""))
        except (AttributeError, ReferenceError, RuntimeError):
            name = ""
        row.label(
            text=tr("Editing: %s") % (name or tr("Mask")), icon="IMAGE_DATA"
        )
        if widgets.operator_exists("quicksdf.aux_mask_back"):
            back = row.operator(
                "quicksdf.aux_mask_back", text=tr("Back to Face Shadow"), icon="LOOP_BACK"
            )
            back.mask_uuid = mask_uuid
    else:
        row.separator()
        if widgets.operator_exists("quicksdf.curve_edit_enter"):
            row.operator(
                "quicksdf.curve_edit_enter", text=tr("Terminator"), icon="CURVE_BEZCURVE"
            )
        widgets.display_row(
            row,
            project,
            guarded=True,
            image_editor=getattr(getattr(context, "area", None), "type", "") == "IMAGE_EDITOR",
        )

    widgets.header_status_row(
        row, project, session=ui_context.session(context), guarded=True
    )


_PAINT_KEYMAP = (
    ("quicksdf.range_paint", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
    ("quicksdf.range_paint_invert", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
    ("quicksdf.paint_value_toggle", {"type": "X", "value": "PRESS"}, None),
    ("quicksdf.angle_step", {"type": "LEFT_ARROW", "value": "PRESS"}, {"properties": [("step", -1)]}),
    ("quicksdf.angle_step", {"type": "RIGHT_ARROW", "value": "PRESS"}, {"properties": [("step", 1)]}),
    ("quicksdf.angle_set", {"type": "HOME", "value": "PRESS"}, {"properties": [("index", -1)]}),
    ("quicksdf.history_undo", {"type": "Z", "value": "PRESS", "ctrl": True}, None),
    ("quicksdf.history_redo", {"type": "Z", "value": "PRESS", "ctrl": True, "shift": True}, None),
)


class QSDF_WST_view_paint(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "PAINT_TEXTURE"
    bl_idname = VIEW_TOOL_ID
    bl_label = "Quick SDF Paint"
    bl_description = "Paint Light or Shadow on the selected angle; export keeps angles consistent"
    bl_icon = "brush.generic"
    bl_options = {"USE_BRUSHES"}
    bl_widget = None
    bl_keymap = _PAINT_KEYMAP
    draw_settings = staticmethod(_draw_tool_settings)


class QSDF_WST_image_paint(WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = IMAGE_TOOL_ID
    bl_label = "Quick SDF Paint"
    bl_description = "Paint Light or Shadow on the selected angle; export keeps angles consistent"
    bl_icon = "brush.generic"
    bl_options = {"USE_BRUSHES"}
    bl_widget = None
    bl_keymap = _PAINT_KEYMAP
    draw_settings = staticmethod(_draw_tool_settings)


TOOLS = (QSDF_WST_view_paint, QSDF_WST_image_paint)


def register_tools() -> None:
    if _REGISTERED:
        return
    try:
        for tool in TOOLS:
            bpy.utils.register_tool(tool, after={"builtin.brush"}, separator=True)
            _REGISTERED.append(tool)
        keyconfig = getattr(
            getattr(bpy.context, "window_manager", None), "keyconfigs", None
        )
        addon = getattr(keyconfig, "addon", None)
        if addon is not None:
            keymap = addon.keymaps.new(
                name="Node Editor",
                space_type="NODE_EDITOR",
                region_type="WINDOW",
            )
            for operator, shift in (
                ("quicksdf.history_undo", False),
                ("quicksdf.history_redo", True),
            ):
                item = keymap.keymap_items.new(
                    operator,
                    type="Z",
                    value="PRESS",
                    ctrl=True,
                    shift=shift,
                )
                _ADDON_KEYMAPS.append((keymap, item))
    except Exception:
        unregister_tools()
        raise


def unregister_tools() -> None:
    while _ADDON_KEYMAPS:
        keymap, item = _ADDON_KEYMAPS.pop()
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    while _REGISTERED:
        tool = _REGISTERED.pop()
        try:
            bpy.utils.unregister_tool(tool)
        except (ReferenceError, RuntimeError, ValueError):
            pass


def _activate_tool(context: Any, window: Any, area: Any, tool_id: str) -> None:
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.wm.tool_set_by_id(name=tool_id, space_type=area.type)
    if not result or "FINISHED" not in result:
        raise RuntimeError(f"Could not activate workspace tool: {tool_id}")


def activate_tools(context: Any, *, window: Any | None = None) -> None:
    """Select the Quick SDF tool in both editors of the Studio screen."""

    window = window or getattr(context, "window", None)
    if window is None:
        raise RuntimeError("Quick SDF tools require an interactive Blender window")
    areas = tuple(window.screen.areas)
    view = next((area for area in areas if area.type == "VIEW_3D"), None)
    image = next((area for area in areas if area.type == "IMAGE_EDITOR"), None)
    if view is None or image is None:
        raise RuntimeError("Quick SDF Paint needs both a 3D View and an Image Editor")
    image_space = image.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    _activate_tool(context, window, view, VIEW_TOOL_ID)
    _activate_tool(context, window, image, IMAGE_TOOL_ID)


CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES", "IMAGE_TOOL_ID", "QSDF_WST_image_paint", "QSDF_WST_view_paint", "TOOLS",
    "VIEW_TOOL_ID", "activate_tools", "register_tools", "unregister_tools",
]

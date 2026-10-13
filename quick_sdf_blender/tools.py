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


# Blender synchronizes texture-paint brush tool IDs between VIEW_3D and
# IMAGE_EDITOR (wm_toolsystem.cc: toolsystem_brush_sync_for_texture_paint).
# Both toolbar definitions must resolve the same ID, or each refresh requests
# a tool that does not exist in the other editor and reports a warning.
VIEW_TOOL_ID = "quicksdf.paint_view"
IMAGE_TOOL_ID = VIEW_TOOL_ID
_REGISTERED: list[type[WorkSpaceTool]] = []
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []


def _operator_exists(idname: str) -> bool:
    namespace, name = idname.split(".", 1)
    return hasattr(getattr(bpy.ops, namespace, object()), name)


def _project(context: Any) -> Any | None:
    try:
        from .studio import active_session, resolve_session_project

        return resolve_session_project(active_session(context))
    except (ImportError, AttributeError, ReferenceError):
        return None


def _draw_tool_settings(context: Any, layout: Any, _tool: Any) -> None:
    project = _project(context)
    if project is None:
        layout.label(text="Open Quick SDF Paint to paint", icon="INFO")
        return

    editing_aux_uuid = ""
    session = None
    try:
        from .studio import active_session

        session = active_session(context)
        editing_aux_uuid = str(
            getattr(session, "editing_aux_mask_uuid", "")
            if session is not None
            else ""
        )
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        pass

    row = layout.row(align=True)
    if _operator_exists("quicksdf.paint_value_set"):
        light = row.operator(
            "quicksdf.paint_value_set",
            text="White" if editing_aux_uuid else "Light",
            icon="RADIOBUT_ON" if editing_aux_uuid else "LIGHT_SUN",
            depress=int(project.paint_value) == 1,
        )
        light.value = 1
        shadow = row.operator(
            "quicksdf.paint_value_set",
            text="Black" if editing_aux_uuid else "Shadow",
            icon="RADIOBUT_OFF" if editing_aux_uuid else "SHADING_SOLID",
            depress=int(project.paint_value) == 0,
        )
        shadow.value = 0

    if editing_aux_uuid:
        try:
            from . import runtime

            item = runtime.aux_mask_for_uuid(project, editing_aux_uuid)
            row.label(text=f"Editing: {getattr(item, 'name', 'Mask')}", icon="IMAGE_DATA")
        except (ImportError, AttributeError, ReferenceError):
            pass
        if _operator_exists("quicksdf.aux_mask_back"):
            back = row.operator(
                "quicksdf.aux_mask_back", text="Back to Face Shadow", icon="LOOP_BACK"
            )
            back.mask_uuid = editing_aux_uuid
    else:
        row.separator()
    if not editing_aux_uuid and _operator_exists("quicksdf.mirror_toggle"):
        row.operator(
            "quicksdf.mirror_toggle", text="Mirror On", icon="MOD_MIRROR",
            depress=bool(project.mirror_enabled),
        )

    if not editing_aux_uuid:
        display = row.row(align=True)
        display.enabled = not bool(project.mesh_normals_preview)
        display.prop(project, "preview_mode", text="")
    if not editing_aux_uuid and getattr(getattr(context, "area", None), "type", "") == "IMAGE_EDITOR":
        row.prop(project, "onion_enabled", text="Onion", toggle=True, icon="IMAGE_ALPHA")
        row.prop(project, "limit_paint_to_face_uv", toggle=True)
        if bool(project.limit_paint_to_face_uv):
            row.prop(project, "paint_padding_px")

    row.separator()
    preparing_auto_key = bool(
        session is not None
        and str(getattr(session, "provisional_state", "NONE")) == "PREPARING"
    )
    if preparing_auto_key and _operator_exists("quicksdf.cancel_auto_key"):
        row.label(text="Preparing angle", icon="TIME")
        row.operator("quicksdf.cancel_auto_key", text="Cancel", icon="CANCEL")
    elif bool(project.job_running) and _operator_exists("quicksdf.cancel_job"):
        row.operator("quicksdf.cancel_job", text="Cancel", icon="CANCEL")
    elif _operator_exists("quicksdf.export_texture"):
        row.operator("quicksdf.export_texture", text="Export Threshold Map", icon="EXPORT")
    if _operator_exists("quicksdf.studio_exit"):
        row.operator("quicksdf.studio_exit", text="Exit Quick SDF", icon="X")


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

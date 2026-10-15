# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick SDF sidebar tab.

``PANELS`` lists every sidebar section in draw order, so the tab layout no
longer depends on add-on registration order.  ``CLASSES`` stays the public
registration surface used by the add-on entry point.
"""

from __future__ import annotations

import bpy  # type: ignore[import-not-found]  # Provided by Blender.

from .base import ImageEditorPanel, ProjectSidebarPanel, SidebarPanel
from .context import (
    active_aux_mask,
    aux_mask,
    curve_edit_active,
    editing_aux_mask_uuid,
    front_axis_id,
    is_studio_active,
    object_project,
    session,
    session_project,
    sidebar_project,
    studio_workspace_project,
    viewport_project,
)
from .panels import (
    QSDF_PT_additional_masks,
    QSDF_PT_image_studio,
    QSDF_PT_launcher,
    QSDF_PT_light_center,
    QSDF_PT_output_packing,
    QSDF_PT_ui_settings,
    QSDF_UL_aux_masks,
)

PANELS = (
    QSDF_PT_launcher,
    QSDF_PT_output_packing,
    QSDF_PT_additional_masks,
    QSDF_PT_light_center,
    QSDF_PT_ui_settings,
)


def tag_editors_for_redraw() -> None:
    """Redraw every editor that shows Quick SDF state."""

    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR"}:
                area.tag_redraw()


CLASSES = (QSDF_UL_aux_masks, QSDF_PT_image_studio, *PANELS)


__all__ = [
    "CLASSES", "PANELS", "ImageEditorPanel", "ProjectSidebarPanel", "SidebarPanel",
    "active_aux_mask", "aux_mask", "curve_edit_active", "editing_aux_mask_uuid",
    "front_axis_id", "is_studio_active", "object_project", "session",
    "session_project", "sidebar_project", "studio_workspace_project",
    "tag_editors_for_redraw", "viewport_project",
]

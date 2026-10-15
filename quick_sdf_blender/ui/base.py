# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel bases that keep space, region, category and poll rules in one place."""

from __future__ import annotations

import bpy  # type: ignore[import-not-found]  # Provided by Blender.

from .context import sidebar_project


class SidebarPanel(bpy.types.Panel):
    """Always visible section of the Quick SDF sidebar tab."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Quick SDF"


class ProjectSidebarPanel(SidebarPanel):
    """Sidebar section that exists only while a project is available."""

    @classmethod
    def poll(cls, context):
        return sidebar_project(context) is not None


class ImageEditorPanel(bpy.types.Panel):
    """Section of the Quick SDF sidebar tab in the Image Editor."""

    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Quick SDF"

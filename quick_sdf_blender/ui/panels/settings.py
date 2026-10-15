# SPDX-License-Identifier: GPL-3.0-or-later
"""UI Settings sidebar section: the user-level presentation preferences."""

from __future__ import annotations

from ..base import SidebarPanel


class QSDF_PT_ui_settings(SidebarPanel):
    bl_idname = "QSDF_PT_ui_settings"
    bl_label = "UI Settings"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 90

    def draw(self, context):
        from ...preferences import draw_ui_settings

        draw_ui_settings(self.layout, context)


__all__ = ["QSDF_PT_ui_settings"]

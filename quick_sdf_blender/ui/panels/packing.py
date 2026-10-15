# SPDX-License-Identifier: GPL-3.0-or-later
"""Output Packing sidebar section: one row per exported RGBA channel."""

from __future__ import annotations

from ... import runtime
from ...i18n import tr
from .. import context as ui_context
from ..base import ProjectSidebarPanel

# Validation lives in :mod:`runtime.packing_channel_error`, which reports one of
# these tokens.  The artist-facing wording stays here.
_ERROR_MESSAGES = {
    "unknown_source": "%s: Unknown source",
    "required_mask_missing": "%s: Required mask is missing",
    "select_custom_mask": "%s: Select a Custom Mask",
    "mask_image_missing": "%s: Mask image is missing",
    "resolution_mismatch": "%s: Mask resolution does not match the project",
}


def _packing_meaning(channel) -> str:
    """Describe the packed black/white result, including inversion."""

    source = str(channel.source_type).upper()
    invert = bool(channel.invert)
    meanings = {
        "RIGHT_THRESHOLD": ("lights late", "lights early"),
        "LEFT_THRESHOLD": ("lights late", "lights early"),
        "SDF_AREA": ("normal shading", "threshold shading"),
        "SHADOW_STRENGTH": ("shadow off", "full shadow"),
        "CUSTOM_MASK": ("mask off", "mask on"),
    }
    if source == "CONSTANT":
        value = max(0.0, min(1.0, float(channel.constant_value)))
        if invert:
            value = 1.0 - value
        return tr("Constant output: %s") % f"{value:.2f}"
    black, white = meanings.get(source, ("0", "1"))
    if invert:
        black, white = white, black
    return tr("Black: %s · White: %s") % (tr(black), tr(white))


class QSDF_PT_output_packing(ProjectSidebarPanel):
    bl_idname = "QSDF_PT_output_packing"
    bl_label = "Output Packing"
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        project = ui_context.sidebar_project(context)
        if project is None:
            return

        customized = bool(project.packing_customized)
        header = layout.row(align=True)
        header.label(
            text=tr("Packing: Custom — This Project") if customized else tr("Packing: lilToon"),
            icon="NODE_COMPOSITING",
        )
        if customized:
            header.operator("quicksdf.packing_reset_liltoon", text=tr("Reset to lilToon"))
        else:
            header.operator("quicksdf.packing_customize", text=tr("Customize"))

        channels = runtime.packing_channel_map(project)
        output_counts = runtime.packing_channel_counts(project)
        if not channels:
            warning = layout.box()
            warning.alert = True
            warning.label(
                text=tr("Packing data is not available for this project."), icon="ERROR"
            )
            return

        for output in "RGBA":
            channel = channels.get(output)
            box = layout.box()
            if channel is None:
                box.alert = True
                box.label(text=tr("%s: Missing channel mapping") % output, icon="ERROR")
                continue
            box.alert = bool(self._row_error(project, output, channel, output_counts))
            self._draw_row(box, context, project, output, channel, customized, output_counts)

        combined = layout.row()
        combined.enabled = ui_context.is_studio_active(context, project)
        preview = combined.operator(
            "quicksdf.packing_preview_channel",
            text=tr("Preview Packed RGB"),
            icon="IMAGE_RGB",
        )
        preview.output_channel = "RGB"

    @staticmethod
    def _row_error(project, output, channel, output_counts) -> str:
        """Return the visible error for one row, or an empty string."""

        if output_counts.get(output, 0) > 1:
            return tr("%s: Duplicate channel mapping") % output
        token = runtime.packing_channel_error(project, channel)
        return tr(_ERROR_MESSAGES[token]) % output if token else ""

    def _draw_row(self, box, context, project, output, channel, customized, output_counts) -> None:
        row_error = self._row_error(project, output, channel, output_counts)

        row = box.row(align=True)
        output_label = row.row(align=True)
        output_label.alignment = "CENTER"
        output_label.label(text=output)

        controls = row.row(align=True)
        controls.enabled = customized
        controls.prop(channel, "source_type", text="")
        controls.prop(
            channel,
            "invert",
            text=tr("Invert") if bool(channel.invert) else tr("Direct"),
            toggle=True,
        )

        preview_row = row.row(align=True)
        preview_row.enabled = ui_context.is_studio_active(context, project)
        preview = preview_row.operator(
            "quicksdf.packing_preview_channel", text=tr("Preview"), icon="HIDE_OFF"
        )
        preview.output_channel = output

        source = str(channel.source_type).upper()
        if source == "CUSTOM_MASK":
            self._draw_custom_mask_row(box, project, output, channel, customized)
        elif source == "CONSTANT":
            value_row = box.row(align=True)
            value_row.enabled = customized
            value_row.prop(channel, "constant_value", text=tr("Constant"), slider=True)

        meaning = box.row()
        meaning.enabled = False
        meaning.label(text=_packing_meaning(channel), icon="INFO")
        if row_error:
            box.label(text=row_error, icon="ERROR")

    @staticmethod
    def _draw_custom_mask_row(box, project, output, channel, customized) -> None:
        mask_row = box.row(align=True)
        mask_row.enabled = customized
        selected = ui_context.aux_mask(project, str(channel.auxiliary_mask_uuid))
        mask_row.label(
            text=str(getattr(selected, "name", "")) or tr("No Custom Mask Selected"),
            icon="IMAGE_DATA",
        )
        active_mask = ui_context.active_aux_mask(project)
        assign_row = mask_row.row(align=True)
        assign_row.enabled = bool(
            customized
            and active_mask is not None
            and str(getattr(active_mask, "role", "")) == "CUSTOM"
        )
        assign = assign_row.operator(
            "quicksdf.packing_assign_active_mask", text=tr("Use Selected Mask")
        )
        assign.output_channel = output


__all__ = ["QSDF_PT_output_packing"]

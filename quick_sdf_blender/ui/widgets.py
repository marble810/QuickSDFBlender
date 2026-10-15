# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw helpers shared by the sidebar panels, the Image Editor panel and the
WorkSpaceTool header.

A control that appears in more than one surface lives here once, so the sidebar
and the tool header cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore[import-not-found]  # Provided by Blender.

from ..i18n import tr
from ..preferences import get_preferences
from .context import aux_mask, editing_aux_mask_uuid

_FRONT_AXIS_COLUMNS = (
    (("POS_X", "+X"), ("NEG_X", "-X")),
    (("POS_Y", "+Y"), ("NEG_Y", "-Y")),
    (("POS_Z", "+Z"), ("NEG_Z", "-Z")),
)


def operator_exists(idname: str) -> bool:
    """Return True while the operator is registered.

    Header and overlay draw callbacks can run while the add-on unloads.
    """

    namespace, name = idname.split(".", 1)
    return hasattr(getattr(bpy.ops, namespace, object()), name)


def section(layout: Any, title: str, icon: str = "") -> Any:
    """Return one titled box, so every panel block reads the same."""

    box = layout.box()
    if icon:
        box.label(text=title, icon=icon)
    else:
        box.label(text=title)
    return box


def paint_value_row(layout: Any, project: Any, *, mask: bool = False,
                    guarded: bool = False, row: Any = None) -> Any:
    """Light/Shadow pair.  A mask edit uses White/Black on the same operator."""

    row = layout.row(align=True) if row is None else row
    if guarded and not operator_exists("quicksdf.paint_value_set"):
        return row
    value = int(getattr(project, "paint_value", 1))
    if mask:
        options = (("White", "RADIOBUT_ON", 1), ("Black", "RADIOBUT_OFF", 0))
    else:
        options = (("Light", "LIGHT_SUN", 1), ("Shadow", "SHADING_SOLID", 0))
    for label, icon, option in options:
        operator = row.operator(
            "quicksdf.paint_value_set",
            text=tr(label),
            icon=icon,
            depress=value == option,
        )
        operator.value = option
    return row


def display_row(layout: Any, project: Any, *, guarded: bool = False,
                image_editor: bool = False, row: Any = None) -> Any:
    """Mirror, preview mode and the Image Editor paint options."""

    row = layout.row(align=True) if row is None else row
    if not guarded or operator_exists("quicksdf.mirror_toggle"):
        row.operator(
            "quicksdf.mirror_toggle",
            text=tr("Mirror On"),
            icon="MOD_MIRROR",
            depress=bool(project.mirror_enabled),
        )
    display = row.row(align=True)
    display.enabled = not bool(project.mesh_normals_preview)
    display.prop(project, "preview_mode", text="")
    if image_editor:
        row.prop(project, "onion_enabled", text=tr("Onion"), toggle=True, icon="IMAGE_ALPHA")
        uv_padding_row(row, project, toggle=True)
    return row


def uv_padding_row(layout: Any, project: Any, *, toggle: bool = False) -> Any:
    """Limit-to-Face-UV switch and its padding value.

    ``toggle`` draws the compact header form, where the switch is a button.
    """

    if toggle:
        layout.prop(project, "limit_paint_to_face_uv", toggle=True)
        if bool(project.limit_paint_to_face_uv):
            layout.prop(project, "paint_padding_px")
        return layout
    layout.prop(project, "limit_paint_to_face_uv")
    padding = layout.row()
    padding.enabled = bool(project.limit_paint_to_face_uv)
    padding.prop(project, "paint_padding_px")
    return layout


def normals_toggle(layout: Any, project: Any, *, enabled: Any = None) -> Any:
    """Mesh normal preview switch.  ``enabled`` greys the row out."""

    row = layout.row() if enabled is not None else layout
    if enabled is not None:
        row.enabled = bool(enabled)
    row.prop(project, "mesh_normals_preview", toggle=True)
    return row


def export_section(layout: Any, project: Any, *, compact: bool = False) -> None:
    """Export button, running job progress and the last result message."""

    if bool(getattr(project, "job_running", False)):
        box = layout.box()
        message = str(getattr(project, "job_message", "")) or tr("Generating…")
        if hasattr(box, "progress"):
            box.progress(
                factor=float(getattr(project, "job_progress", 0.0)),
                type="BAR",
                text=message,
            )
        else:
            row = box.row()
            row.enabled = False
            row.prop(project, "job_progress", text=message, slider=True)
        box.operator("quicksdf.cancel_job", text=tr("Cancel"), icon="CANCEL")
        return
    export = layout.column()
    export.scale_y = 1.0 if compact else 1.45
    failed = bool(getattr(project, "export_failed", False))
    export.operator(
        "quicksdf.export_texture",
        text=tr("Retry Export") if failed else tr("Export Threshold Map"),
        icon="FILE_REFRESH" if failed else "EXPORT",
    )
    message = str(getattr(project, "job_message", ""))
    if message:
        if failed:
            icon = "ERROR"
        elif message.startswith("Exported"):
            icon = "CHECKMARK"
        else:
            icon = "INFO"
        layout.label(text=tr(message), icon=icon)


def header_status_row(layout: Any, project: Any, *, session: Any = None,
                      guarded: bool = False) -> Any:
    """Cancel, Export and Exit controls at the end of the tool header row."""

    layout.separator()
    preparing_auto_key = bool(
        session is not None
        and str(getattr(session, "provisional_state", "NONE")) == "PREPARING"
    )
    if preparing_auto_key and (not guarded or operator_exists("quicksdf.cancel_auto_key")):
        layout.label(text=tr("Preparing angle"), icon="TIME")
        layout.operator("quicksdf.cancel_auto_key", text=tr("Cancel"), icon="CANCEL")
    elif bool(project.job_running) and (not guarded or operator_exists("quicksdf.cancel_job")):
        layout.operator("quicksdf.cancel_job", text=tr("Cancel"), icon="CANCEL")
    elif not guarded or operator_exists("quicksdf.export_texture"):
        layout.operator(
            "quicksdf.export_texture", text=tr("Export Threshold Map"), icon="EXPORT"
        )
    if not guarded or operator_exists("quicksdf.studio_exit"):
        layout.operator("quicksdf.studio_exit", text=tr("Exit Quick SDF"), icon="X")
    return layout


def front_axis_section(layout: Any, context: Any, active: str | None) -> bool:
    """Draw the foldable front chooser.  Return True while it is open.

    -Y is the default Blender front.  The buttons fill three columns, one per
    world axis, with the positive and negative direction stacked in each.
    """

    preferences = get_preferences(context)
    expanded = (
        bool(getattr(preferences, "front_axes_open", True))
        if preferences is not None
        else True
    )
    header = layout.row(align=True)
    icon = "TRIA_DOWN" if expanded else "TRIA_RIGHT"
    if preferences is not None:
        header.prop(preferences, "front_axes_open", text="", emboss=False, icon=icon)
    else:
        header.label(text="", icon=icon)
    header.label(text=tr("Front"))
    if not expanded:
        return False
    grid = layout.row(align=True)
    for column_items in _FRONT_AXIS_COLUMNS:
        column = grid.column(align=True)
        for axis_id, label in column_items:
            operator = column.operator(
                "quicksdf.set_front_axis", text=label, depress=axis_id == active
            )
            operator.axis = axis_id
    return True


def mirror_confirmation(layout: Any, project: Any) -> None:
    """Ask which preview matches the face UV when symmetry is ambiguous."""

    if not bool(getattr(project, "symmetry_requires_confirmation", False)):
        return
    box = layout.box()
    box.alert = True
    box.label(text=tr("Choose the preview that matches the face UV"), icon="MOD_MIRROR")
    row = box.row(align=True)
    for mode, label in (
        ("TEXTURE_MIRROR", "Whole Texture"),
        ("ISLAND_PAIR", "Paired Islands"),
        ("OVERLAPPED_UV", "Shared UV"),
    ):
        operator = row.operator("quicksdf.symmetry_choose", text=tr(label))
        operator.mode = mode


def aux_edit_status(layout: Any, context: Any, project: Any) -> bool:
    """Draw the mask editing notice.  Return True while a mask is edited."""

    mask_uuid = editing_aux_mask_uuid(context, project)
    if not mask_uuid:
        return False
    mask = aux_mask(project, mask_uuid)
    name = str(getattr(mask, "name", "")) or tr("Additional Mask")
    box = layout.box()
    box.label(text=tr("Editing Mask: %s") % name, icon="IMAGE_DATA")
    box.label(text=tr("These masks are shared by every angle."), icon="INFO")
    paint_value_row(box, project, mask=True)
    back = box.operator(
        "quicksdf.aux_mask_back",
        text=tr("Back to Face Shadow"),
        icon="LOOP_BACK",
    )
    back.mask_uuid = mask_uuid
    return True

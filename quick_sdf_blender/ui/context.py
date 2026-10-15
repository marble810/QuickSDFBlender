# SPDX-License-Identifier: GPL-3.0-or-later
"""One place that resolves the project and the editing state a UI sees.

Three entry points exist because the surfaces see different states.  They share
the same building blocks instead of copying them:

``sidebar_project()``
    The 3D View sidebar tab.
``studio_workspace_project()``
    The Studio timeline and the bottom overlay, gated to their own workspace.
``viewport_project()``
    The viewport overlay, additionally gated to the gizmo and to face painting.
"""

from __future__ import annotations

from typing import Any

from .. import runtime


def session(context: Any) -> Any | None:
    """Return the active Studio session, or None when there is none."""

    try:
        from ..studio import active_session

        return active_session(context)
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        return None


def session_project(context: Any) -> Any | None:
    """Return the project owned by the active Studio session."""

    try:
        from ..studio import resolve_session_project

        return resolve_session_project(session(context))
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        return None


def is_studio_active(context: Any, project: Any | None) -> bool:
    """Return True while the Studio workspace hosts ``project``."""

    if project is None:
        return False
    try:
        from ..studio import is_studio_active as studio_active

        return studio_active(context, str(project.uuid))
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        return False


def curve_edit_active(context: Any) -> bool:
    """Return True while the Terminator curve editor owns the viewport."""

    try:
        from ..curve_session import is_curve_edit_active

        return bool(is_curve_edit_active(context))
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        return False


def editing_aux_mask_uuid(context: Any, project: Any | None) -> str:
    """Return the mask the current session edits, or an empty string."""

    if project is None:
        return ""
    active = session(context)
    if active is None or str(getattr(active, "project_uuid", "")) != str(
        getattr(project, "uuid", "")
    ):
        return ""
    return str(getattr(active, "editing_aux_mask_uuid", ""))


def aux_mask(project: Any, mask_uuid: str) -> Any | None:
    """Read-only mask lookup by UUID."""

    return runtime.aux_mask_for_uuid(project, mask_uuid)


def active_aux_mask(project: Any) -> Any | None:
    """Read-only lookup of the mask selected in the sidebar list.

    The read is deliberately side-effect free: panel draw callbacks must never
    repair ID references.
    """

    items = getattr(project, "aux_masks", ())
    index = int(getattr(project, "active_aux_mask_index", -1))
    if 0 <= index < len(items):
        return items[index]
    return None


def front_axis_id(context: Any, project: Any | None) -> str | None:
    """Return the front axis to highlight in the axis rows."""

    if project is None:
        from ..operators import _scene_front_axis

        return _scene_front_axis(context.scene)
    return runtime.matching_world_front_axis(
        getattr(project, "target_object", None), project.forward_vector
    )


def object_project(context: Any) -> Any | None:
    """Return the project whose target object is the active object."""

    active = getattr(context, "active_object", None)
    return next(
        (
            project
            for project in getattr(context.scene, "quick_sdf_projects", ())
            if project.target_object == active
        ),
        None,
    )


def sidebar_project(context: Any) -> Any | None:
    """Return the project the 3D View sidebar tab shows."""

    active = getattr(context, "active_object", None)
    try:
        from ..curve_session import is_terminator_object

        if curve_edit_active(context) or is_terminator_object(active):
            project = session_project(context)
            if project is not None:
                return project
    except (ImportError, AttributeError, ReferenceError, RuntimeError):
        pass
    project = object_project(context)
    if project is not None:
        return project
    # Selecting another mesh is the artist's intent to create/open that face,
    # not a reason to keep showing controls for an unrelated prior project.
    return runtime.active_project(context.scene) if active is None else None


def studio_workspace_project(context: Any) -> Any | None:
    """Return the Studio project, but only inside its own workspace."""

    from ..studio import WORKSPACE_PROJECT_TAG

    active = session(context)
    workspace = getattr(context, "workspace", None)
    if (
        active is None
        or workspace is None
        or workspace.get(WORKSPACE_PROJECT_TAG) != active.project_uuid
    ):
        return None
    return session_project(context)


def viewport_project(context: Any) -> Any | None:
    """Return the project for the bottom viewport overlay."""

    if getattr(getattr(context, "area", None), "type", "") != "VIEW_3D":
        return None
    if getattr(getattr(context, "region", None), "type", "") != "WINDOW":
        return None
    if not context.space_data.show_gizmo:
        return None
    project = studio_workspace_project(context)
    active = session(context)
    if active is not None and active.editing_aux_mask_uuid:
        return None
    return project

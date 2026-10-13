# Archived tests

Files here are **not** run by `scripts/build_extension.ps1` or by
`python -m unittest discover -s tests`. They are kept only as a reference for
behaviour that no live test covers yet.

## blender_studio_smoke.py

Archived with the Studio workspace redesign (commit `8416b09`): the angle
timeline moved out of a dedicated `NODE_EDITOR` area into a self-drawn overlay
at the bottom of the 3D Viewport, and the default workspace became a Canvas/3D
two-column layout. The file still drives the removed three-area layout, so it
fails on `assert len(bpy.context.window.screen.areas) == 3` as soon as Studio
opens.

Removing it from the gate also removed gate steps 8 and 15 (see below). Restore
that coverage in a replacement Studio smoke test before the next release.

### Coverage that is currently missing

- Interactive Studio lifecycle: enter, workspace restore on exit, selection and
  mode restore, paint-settings restore.
- Viewport shading recovery: Solid back to Material Preview when painting
  resumes. `tests/blender_preview_render_probe.py` cannot cover it because
  background Blender has no `VIEW_3D`.
- Timeline interaction through simulated mouse events (drag, snap, release).
- Export review and adjustment-image lifecycle.
- Auxiliary-mask editing, Undo/Redo fencing, autosave and save/reload of an
  active Studio session. `tests/blender_saved_state_smoke.py` stays in `tests/`
  but has no producer until a replacement test saves that `.blend` again.

The two-column layout and the viewport timeline overlay themselves are covered
by `tests/blender_icosphere_paint_smoke.py` and `tests/blender_studio_switch_smoke.py`.

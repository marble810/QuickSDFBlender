# bl_ui_widgets (local fork)

Source: https://github.com/jayanam/bl_ui_widgets

Version: 0.6.4.2, commit `37b9313c278dba208ec95f954b48ff536eee8afd`.
Author: Jayanam. Upstream distributes these files under GPL v3;
its unmodified license is included in `LICENSE.txt`.

Only `BL_UI_Widget` and `BL_UI_Slider` are imported. Quick SDF adaptations:
remove bgl, use UNIFORM_COLOR and current blf.size, use WINDOW-region height
instead of area height. Scale rail/handle geometry, recompute position on
resize, fix position-to-value conversion, and propagate callback errors.
GPU blending is owned and restored by the overlay.
The host provides full-rail hit testing, UI-scaled layout, drag throttling,
cancellation and settle callbacks via a Blender gizmo instead of the upstream
long-lived modal drawing operator. No upstream image/textbox/button code is
included. The slider displays lighting progress (0–100), not internal angles.

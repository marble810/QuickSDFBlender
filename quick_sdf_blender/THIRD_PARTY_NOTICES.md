# Third-Party Notices

Quick SDF Paint is distributed under GPL-3.0-or-later. The following notice
applies to third-party work used by the project.

## Felzenszwalb-Huttenlocher distance transform

The exact Euclidean distance-transform implementations in
`native/quicksdf_core.cpp` and `quick_sdf_blender/core.py` are adaptations of
the implementation accompanying *Distance Transforms of Sampled Functions* by
Pedro F. Felzenszwalb and Daniel P. Huttenlocher.

- Original implementation copyright (C) 2006 Pedro Felzenszwalb
- Original license: GNU General Public License, version 2 or any later version
- Upstream source: https://cs.brown.edu/people/pfelzens/dt/
- Paper: https://theoryofcomputing.org/articles/v008a019/

Quick SDF's adaptations and modifications are distributed under
GPL-3.0-or-later. The complete GPL-3.0-or-later text is included in `LICENSE`.

## bl_ui_widgets

`quick_sdf_blender/bl_ui_widgets` contains a minimal fork of Jayanam's
`BL_UI_Widget` and `BL_UI_Slider`, version 0.6.4.2, commit
`37b9313c278dba208ec95f954b48ff536eee8afd`.

- Author: Jayanam
- Upstream: https://github.com/jayanam/bl_ui_widgets
- License: GNU GPL v3 (included in `bl_ui_widgets/LICENSE.txt`)
- Local adaptations are recorded in `bl_ui_widgets/README.md`.

## NumPy

Quick SDF uses the NumPy installation provided by a supported Blender version.
NumPy is not redistributed in the Quick SDF Extension ZIP. NumPy is available
under the BSD-3-Clause license: https://numpy.org/doc/stable/license.html

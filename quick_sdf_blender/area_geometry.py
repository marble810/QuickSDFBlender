# SPDX-License-Identifier: GPL-3.0-or-later
"""Area-pair geometry for rebuilding the Quick SDF Paint screen layout.

Blender insets every area rectangle by a divider whose width follows the
display scale, so two areas that visually touch never share an exact edge.
The divider is what Blender's screen geometry reserves, and the same layout
therefore leaves a different gap per machine: 4px on a 1x Windows display,
7px on the 2x backing scale macOS reports here.

Adjacency is consequently not a threshold test against a constant. Pairs are
ranked by how cleanly they line up, and ``bpy.ops.screen.area_join`` remains
the authority on whether the best candidate really shares an edge. This module
stays free of ``bpy`` so the geometry is unit-testable without Blender.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = ("AREA_ALIGNMENT_SLACK", "join_candidates", "shared_edge")

#: Rects that share an edge sit on Blender's area grid, so only rounding has
#: to be absorbed here. The divider gap is ranked, never bounded, because it
#: is display dependent.
AREA_ALIGNMENT_SLACK = 4


def _edge_option(misalignment: int, gap: int, thickness: int) -> tuple[int, int] | None:
    """Validate one candidate edge: ``(misalignment, gap)`` or ``None``.

    ``thickness`` is the extent of the thinner rect along the gap axis. The
    divider that separates two touching areas is far thinner than the areas it
    separates, so a gap approaching their own thickness means a third area sits
    between them; Blender refuses to join such a pair anyway.
    """

    if misalignment > AREA_ALIGNMENT_SLACK:
        return None
    if gap > thickness // 2:
        return None
    return misalignment, gap


def shared_edge(first: Any, second: Any) -> tuple[int, int] | None:
    """Return ``(misalignment, gap)`` for two stacked or side-by-side rects.

    ``misalignment`` is how far the rects deviate from one clean column or row
    and ``gap`` is the distance between their facing edges. Lower is better on
    both counts. ``None`` means the rects do not share an edge, which is what
    keeps a nearby but unrelated area out of the candidate list.
    """

    stacked = _edge_option(
        misalignment=abs(first.x - second.x) + abs(first.width - second.width),
        gap=min(
            abs(first.y + first.height - second.y),
            abs(second.y + second.height - first.y),
        ),
        thickness=min(first.height, second.height),
    )
    side_by_side = _edge_option(
        misalignment=abs(first.y - second.y) + abs(first.height - second.height),
        gap=min(
            abs(first.x + first.width - second.x),
            abs(second.x + second.width - first.x),
        ),
        thickness=min(first.width, second.width),
    )
    options = [option for option in (stacked, side_by_side) if option is not None]
    if not options:
        return None
    return min(options)


def join_candidates(areas: Iterable[Any]) -> list[tuple[Any, Any]]:
    """Rank ``(source, target)`` rect pairs worth offering to Blender.

    The cleanest shared edge comes first, and a position tie-break keeps the
    sequence stable for a given screen. The operator still decides: a listed
    pair may turn out not to be joinable, in which case the next one is used.
    """

    ordered = list(areas)
    ranked: list[tuple[tuple[int, int], tuple[int, int], Any, Any]] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            edge = shared_edge(first, second)
            if edge is None:
                continue
            source, target = sorted((first, second), key=lambda area: area.as_pointer())
            ranked.append((edge, (source.x, source.y), source, target))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(source, target) for _edge, _origin, source, target in ranked]

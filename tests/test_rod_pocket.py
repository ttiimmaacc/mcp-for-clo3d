"""Pure parts of the rod-pocket recipe (no CLO needed)."""

import pytest

from clo3d_mcp.rod_pocket import find_top_edge, placements


def _line(i, a, b, curved=False):
    return {"line_index": i, "start": list(a), "end": list(b), "curved": curved, "length": 0}


def test_top_edge_of_rectangle():
    piece = {"lines": [_line(0, (0, 0), (1000, 0)), _line(1, (1000, 0), (1000, 1500)),
                       _line(2, (1000, 1500), (0, 1500)), _line(3, (0, 1500), (0, 0))]}
    assert find_top_edge(piece) == (2, 0, 1000, 1500, False)


def test_top_edge_requires_straight_horizontal_line():
    piece = {"lines": [_line(0, (0, 0), (1000, 0)), _line(1, (1000, 0), (0, 1500), curved=True)]}
    assert find_top_edge(piece)[0] == 0  # the only straight horizontal line
    with pytest.raises(ValueError):
        find_top_edge({"lines": [_line(0, (0, 0), (0, 100))]})


def test_rod_in_front_of_the_strip_above_the_top_edge():
    rod_y, rod_z = placements(top_y=1500, fold_y=1440, plane_z=200, pocket_depth=150, rod_diameter=30)
    assert rod_y == 1525 and rod_z == 218


def test_pocket_too_small_is_rejected_with_a_suggestion():
    with pytest.raises(ValueError, match="pocket_depth >="):
        placements(top_y=1500, fold_y=1460, plane_z=200, pocket_depth=60, rod_diameter=40)

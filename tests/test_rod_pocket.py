"""Pure parts of the rod-pocket recipe (no CLO needed)."""

import pytest

from clo3d_mcp.rod_pocket import find_top_edge, tube_center


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


def _slice(top, far_z):
    return {"min": [0, 1400, min(203, far_z)], "max": [0, top, max(203, far_z)]}


def test_rod_centre_between_fold_and_tube_top_on_the_tube_side():
    y, z, clearance = tube_center([_slice(1560, 260), _slice(1550, 250), _slice(1560, 260)],
                                  plane_z=200, fold_y=1440, rod_diameter=30)
    assert 1490 < y < 1505 and 225 < z < 230
    assert clearance == pytest.approx(50)


def test_rod_that_does_not_fit_is_rejected():
    with pytest.raises(ValueError, match="does not fit"):
        tube_center([_slice(1470, 220)], plane_z=200, fold_y=1440, rod_diameter=30)

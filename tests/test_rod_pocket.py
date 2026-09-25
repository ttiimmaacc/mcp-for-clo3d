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


def test_rod_above_the_top_edge_on_the_pocket_side():
    back = placements(top_y=1500, fold_y=1440, plane_z=200, pocket_depth=150, rod_diameter=30)
    front = placements(top_y=1500, fold_y=1440, plane_z=200, pocket_depth=150, rod_diameter=30, side=1)
    assert back == (1525, 182) and front == (1525, 218)


def test_pocket_too_small_is_rejected_with_a_suggestion():
    with pytest.raises(ValueError, match="pocket_depth >="):
        placements(top_y=1500, fold_y=1460, plane_z=200, pocket_depth=60, rod_diameter=40)


def test_retry_placements_move_the_rod():
    first = placements(1500, 1440, 200, 150, 30, side=1, gap=3.0, lift=10.0)
    second = placements(1500, 1440, 200, 150, 30, side=1, gap=6.0, lift=16.0)
    assert first == (1525, 218) and second == (1531, 221)


def test_assemblies_follow_seams_but_not_across_panels():
    from clo3d_mcp.rod_pocket import assemblies
    seams = [{"sides": [{"pattern_index": 0}, {"pattern_index": 1}]},      # body L - band L
             {"sides": [{"pattern_index": 1}, {"pattern_index": 2}]},      # band L - strip L
             {"sides": [{"pattern_index": 6}, {"pattern_index": 0, "internal_shape": 3}]},  # patch on body L
             {"sides": [{"pattern_index": 3}, {"pattern_index": 4}]}]      # body R - band R
    assert assemblies({"seams": seams}, [0, 3]) == [[0, 1, 2, 6], [3, 4]]


def test_wrap_needs_cloth_over_the_rod_and_round_its_far_side():
    from clo3d_mcp.rod_pocket import wrapped
    over_and_round = {"vertex_count": 40, "min": [0, 1990, 200], "max": [0, 2048, 236]}
    only_over = {"vertex_count": 40, "min": [0, 1990, 200], "max": [0, 2048, 215]}
    assert wrapped(over_and_round, rod_y=2030, rod_z=219, radius=16, side=1)
    assert not wrapped(only_over, rod_y=2030, rod_z=219, radius=16, side=1)
    assert not wrapped({"vertex_count": 0}, rod_y=2030, rod_z=219, radius=16, side=1)


def test_band_rod_sits_mid_band_just_clear_of_its_face():
    from clo3d_mcp.rod_pocket import band_rod
    assert band_rod(top_y=1960, plane_z=200, band_height=60, rod_diameter=32, side=1) == (1990, 217)
    assert band_rod(top_y=1960, plane_z=200, band_height=60, rod_diameter=32, side=-1) == (1990, 183)
    with pytest.raises(ValueError, match="band_height >= 42"):
        band_rod(top_y=1960, plane_z=200, band_height=40, rod_diameter=32, side=1)

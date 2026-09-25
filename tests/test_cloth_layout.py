"""Mapping the flat cloth vertex list onto patterns."""

import pytest

from clo3d_mcp.cloth_layout import vertex_count, vertex_ranges


def test_vertex_count_finds_the_vertex_key():
    assert vertex_count({"Face Count": "120", "Vertex Count": "75", "Mesh Type": "Triangle"}) == 75
    with pytest.raises(ValueError):
        vertex_count({"Face Count": "120"})


def test_ranges_are_contiguous_when_counts_add_up():
    counts = {"patterns": [{"vertex": "10"}, {"vertex": "5"}, {"vertex": "7"}], "total_vertices": 22}
    assert vertex_ranges(counts) == [(0, 10), (10, 5), (15, 7)]


def test_mismatch_is_refused_instead_of_guessed():
    with pytest.raises(ValueError, match="add up to 10 but CLO reports 12"):
        vertex_ranges({"patterns": [{"vertex": "10"}], "total_vertices": 12})


def test_segment_finds_real_boundaries_shorter_than_clo_counts():
    from clo3d_mcp.cloth_layout import segment
    # two flat squares side by side; CLO "counts" 60 + 60 but lists 50 + 45 vertices
    left = [(0, 0), (100, 0), (100, 100), (0, 100)]
    right = [(200, 0), (300, 0), (300, 100), (200, 100)]
    verts = [[10 + (k % 8) * 10, 10 + (k // 8) * 10, 200] for k in range(50)]
    verts += [[210 + (k % 8) * 10, 10 + (k // 8) * 10, 200] for k in range(45)]
    ranges = segment(lambda k: verts[k], [left, right], [0, 0], [60, 60], len(verts), 200.0, False)
    assert ranges == [(0, 50), (50, 45)]

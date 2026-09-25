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
    with pytest.raises(ValueError, match="not available"):
        vertex_ranges({"patterns": [{"vertex": "10"}], "total_vertices": 12})

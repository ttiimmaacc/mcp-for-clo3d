"""Tests for mapping CLO's pattern export onto line indices (synthetic data)."""

from clo3d_mcp.geometry import summarize


def _line(line_id, a, b):
    return {"ID": line_id, "PointList": [
        {"ID": line_id + "p0", "PointType": "Straight", "Position": {"x": a[0], "y": a[1]}},
        {"ID": line_id + "p1", "PointType": "Straight", "Position": {"x": b[0], "y": b[1]}},
    ]}


def _square(pattern_id, name):
    corners = [(0, 0), (100, 0), (100, 100), (0, 100)]
    lines = [_line("%s%d" % (pattern_id, i), corners[i], corners[(i + 1) % 4]) for i in range(4)]
    return {
        "ID": pattern_id, "Name": name, "ShapeInfo": {"LineList": lines},
        "InternalLineList": [{"ID": pattern_id + "-fold", "ShapeType": "Line", "IsClosed": False,
                              "LineList": [_line(pattern_id + "f", (50, 0), (50, 100))]}],
        "NotchList": [],
    }


def _side(shape_id, start, end, forward):
    return {"ShapeID": shape_id, "LengthParam": {"fStart": start, "fEnd": end}, "Direction": forward}


def _result(seams):
    groups = [{"Name": "Seam%d" % i, "bIsTurned": False, "FoldData": {"iAngle": 180, "iStrength": 5},
               "PairList": [{"First": first, "Second": second}]} for i, (first, second) in enumerate(seams)]
    return {
        "export": {"PatternList": [_square("A", "front"), _square("B", "back")], "SeamLinePairGroupList": groups},
        "line_lengths": [{"outline": [100.0] * 4, "internal_shapes": [[100.0]]}] * 2,
        "seam_names": [g["Name"] for g in groups],
    }


def _lines(side):
    return [(l["line_index"], l["coverage"]) for l in side["lines"]]


def test_pieces_are_indexed_in_export_order():
    pieces = summarize(_result([]))["pieces"]
    assert [p["name"] for p in pieces] == ["front", "back"]
    assert [l["line_index"] for l in pieces[0]["lines"]] == [0, 1, 2, 3]
    assert pieces[0]["perimeter"] == 400.0
    assert pieces[0]["lines"][1]["start"] == [100, 0]


def test_forward_and_backward_sides_map_to_whole_lines():
    seam = summarize(_result([(_side("A", 0.25, 0.5, True), _side("B", 0.75, 0.5, False))]))["seams"][0]
    assert _lines(seam["sides"][0]) == [(1, 1.0)]
    assert _lines(seam["sides"][1]) == [(2, 1.0)]


def test_seam_wrapping_past_the_start_point():
    seam = summarize(_result([(_side("A", 0.875, 0.125, True), _side("B", 0.125, 0.875, False))]))["seams"][0]
    assert _lines(seam["sides"][0]) == [(0, 0.5), (3, 0.5)]
    assert _lines(seam["sides"][1]) == [(0, 0.5), (3, 0.5)]


def test_internal_shape_seam_and_pattern_filter():
    result = _result([(_side("A-fold", 0.0, 1.0, True), _side("B", 0.0, 0.25, True)),
                      (_side("B", 0.5, 0.75, True), _side("B", 0.25, 0.5, True))])
    summary = summarize(result, pattern_index=0)
    assert [p["name"] for p in summary["pieces"]] == ["front"]
    assert len(summary["seams"]) == 1
    side = summary["seams"][0]["sides"][0]
    assert side["pattern_index"] == 0 and side["internal_shape"] == 0
    assert _lines(side) == [(0, 1.0)]


def test_slivers_at_line_boundaries_are_ignored():
    seam = summarize(_result([(_side("A", 0.2499, 0.5001, True), _side("B", 0.0, 0.25, True))]))["seams"][0]
    assert _lines(seam["sides"][0]) == [(1, 1.0)]


def test_seam_end_points_and_what_is_sewn_together():
    # A: forward over line 1 ((100,0) -> (100,100)); B: backward from 0.75 to 0.5, i.e. line 2
    # walked from (0,100) to (100,100)
    seam = summarize(_result([(_side("A", 0.25, 0.5, True), _side("B", 0.75, 0.5, False))]))["seams"][0]
    a, b = seam["sides"]
    assert a["start_point"] == [100, 0] and a["end_point"] == [100, 100]
    assert b["start_point"] == [0, 100] and b["end_point"] == [100, 100]
    assert seam["sewn_together"] == [{"a": [100, 0], "b": [0, 100]}, {"a": [100, 100], "b": [100, 100]}]


def test_end_points_on_internal_shape_and_across_the_start():
    seam = summarize(_result([(_side("A-fold", 0.0, 1.0, True), _side("B", 0.875, 0.125, True))]))["seams"][0]
    fold, wrap = seam["sides"]
    assert fold["start_point"] == [50, 0] and fold["end_point"] == [50, 100]
    assert wrap["start_point"] == [0, 50] and wrap["end_point"] == [50, 0]

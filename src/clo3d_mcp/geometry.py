"""Turn CLO's pattern export into line-indexed pieces and seams.

CLO's API addresses lines by index ("line 3 of pattern 1"), but its export only lists points
and describes seams as fractions of a shape's total outline length. This module joins the two
so tools can say which lines a seam uses and where each line sits.

Verified against CLO 2025.2.236:
- PatternList order is the pattern index; ShapeInfo.LineList order is the line index.
- A seam side's LengthParam fStart/fEnd are fractions (0-1) of the shape's perimeter. With
  Direction true the seam runs forward from fStart to fEnd, otherwise backward, and it
  wraps past 0/1 when needed.
"""

EPS = 1e-4
MIN_COVERAGE = 0.01  # ignore rounding slivers where a seam ends right at a line boundary


def _pt(point):
    return [round(point["Position"]["x"], 2), round(point["Position"]["y"], 2)]


def _lines(line_list, lengths, include_points):
    lines = []
    for index, line in enumerate(line_list):
        points = line.get("PointList", [])
        entry = {
            "line_index": index,
            "length": round(lengths[index], 2) if index < len(lengths) else None,
            "start": _pt(points[0]) if points else None,
            "end": _pt(points[-1]) if points else None,
            "curved": any(p.get("PointType") != "Straight" for p in points),
        }
        if include_points:
            entry["points"] = [_pt(p) + [p.get("PointType")] for p in points]
        lines.append(entry)
    return lines


def _covered_ranges(start, end, forward):
    """Perimeter fraction ranges a seam side covers, handling wrap-around."""
    lo, hi = (start, end) if forward else (end, start)
    if lo <= hi + EPS:
        return [(min(lo, hi), max(lo, hi))]
    return [(lo, 1.0), (0.0, hi)]


def _lines_in_ranges(lengths, ranges):
    total = sum(lengths)
    if total <= 0:
        return []
    used, position = [], 0.0
    for index, length in enumerate(lengths):
        a, b = position / total, (position + length) / total
        position += length
        overlap = sum(max(0.0, min(b, hi) - max(a, lo)) for lo, hi in ranges)
        fraction = overlap / ((b - a) or 1)
        if fraction >= MIN_COVERAGE:
            used.append({"line_index": index, "coverage": round(min(fraction, 1.0), 3)})
    return used


def summarize(result, pattern_index=None, include_points=False):
    """Build pieces and seams from the plug-in's get_pattern_geometry result."""
    export = result.get("export", {})
    lengths = result.get("line_lengths", [])
    patterns = export.get("PatternList", [])

    shapes = {}  # shape ID -> (pattern index, internal shape index or None, line lengths)
    pieces = []
    for i, pattern in enumerate(patterns):
        piece_lengths = lengths[i] if i < len(lengths) else {"outline": [], "internal_shapes": []}
        shapes[pattern.get("ID")] = (i, None, piece_lengths.get("outline", []))
        internal = []
        for k, shape in enumerate(pattern.get("InternalLineList", [])):
            child_lengths = piece_lengths.get("internal_shapes", [])
            child_lengths = child_lengths[k] if k < len(child_lengths) else []
            shapes[shape.get("ID")] = (i, k, child_lengths)
            internal.append({
                "internal_shape": k,
                "closed": shape.get("IsClosed"),
                "type": shape.get("ShapeType"),
                "lines": _lines(shape.get("LineList", []), child_lengths, include_points),
            })
        outline = pattern.get("ShapeInfo", {}).get("LineList", [])
        pieces.append({
            "pattern_index": i,
            "name": pattern.get("Name"),
            "half_symmetric": pattern.get("IsHalfSymmetric"),
            "grain_angle": pattern.get("fGrainlineAngle"),
            "perimeter": round(sum(piece_lengths.get("outline", [])), 2),
            "lines": _lines(outline, piece_lengths.get("outline", []), include_points),
            "internal_shapes": internal,
            "notch_count": len(pattern.get("NotchList", [])),
        })

    names = result.get("seam_names", [])
    seams = []
    for s, group in enumerate(export.get("SeamLinePairGroupList", [])):
        name = group.get("Name")
        sides = []
        for pair in group.get("PairList", []):
            for key in ("First", "Second"):
                side = pair.get(key, {})
                owner = shapes.get(side.get("ShapeID"))
                params = side.get("LengthParam", {})
                start, end = params.get("fStart", 0.0), params.get("fEnd", 0.0)
                forward = bool(side.get("Direction"))
                entry = {"side": "a" if key == "First" else "b", "forward": forward,
                         "start_fraction": round(start, 4), "end_fraction": round(end, 4)}
                if owner:
                    entry["pattern_index"] = owner[0]
                    if owner[1] is not None:
                        entry["internal_shape"] = owner[1]
                    entry["lines"] = _lines_in_ranges(owner[2], _covered_ranges(start, end, forward))
                else:
                    entry["shape_id"] = side.get("ShapeID")
                sides.append(entry)
        seams.append({
            "seam_index": names.index(name) if name in names else s,
            "name": name,
            "fold_angle": group.get("FoldData", {}).get("iAngle"),
            "fold_strength": group.get("FoldData", {}).get("iStrength"),
            "turned": group.get("bIsTurned"),
            "sides": sides,
        })

    if pattern_index is not None:
        pieces = [p for p in pieces if p["pattern_index"] == pattern_index]
        seams = [s for s in seams if any(side.get("pattern_index") == pattern_index for side in s["sides"])]
    return {"pieces": pieces, "seams": seams}

"""Rod pocket: sew a pocket along a panel's top edge and hang the panel on a rod.

Recipe worked out by simulating a curtain in CLO 2025.2.236:
- A rod placed before the pocket forms pushes the folding cloth away, so the pocket is
  formed first (panel frozen, no rod) and the rod is added inside the finished tube.
- The pocket is a separate strip so the panel can stay frozen while the strip folds.
- Cloth passes through a rod unless its mesh is finer than the rod, so both pieces get a
  small particle distance before the tube forms.
- The rod goes where the tube actually is, measured in several slices across the width
  (the tube is not uniform; the ends bulge more than the middle).
A new flat piece lies at its 2D (x, y) in 3D, in a plane of constant z.
"""

from clo3d_mcp.geometry import summarize

PLANE_TOLERANCE = 3.0  # mm: vertices closer than this to the panel plane belong to the panel


def find_top_edge(piece):
    """The piece's straight, horizontal top line: (line_index, x0, x1, y, runs_left_to_right)."""
    best = None
    for line in piece["lines"]:
        (ax, ay), (bx, by) = line["start"], line["end"]
        if line["curved"] or abs(ay - by) > 1.0 or abs(ax - bx) < 1.0:
            continue
        if best is None or ay > best[3]:
            best = (line["line_index"], min(ax, bx), max(ax, bx), (ay + by) / 2.0, bx > ax)
    if best is None:
        raise ValueError("the panel needs a straight, horizontal top edge for a rod pocket")
    return best


def tube_center(slices, plane_z, fold_y, rod_diameter):
    """Rod centre from tube slices [{"min": [...], "max": [...]}, ...] on one side of the panel.

    Returns (y, z, smallest_clearance). Raises if the rod would not fit.
    """
    ys, zs, sizes = [], [], []
    for s in slices:
        top = s["max"][1]
        far = s["max"][2] if s["max"][2] - plane_z > plane_z - s["min"][2] else s["min"][2]
        height, depth = top - fold_y, abs(far - plane_z)
        ys.append((fold_y + top) / 2.0)
        zs.append((plane_z + far) / 2.0)
        sizes.append(min(height, depth))
    smallest = min(sizes)
    if rod_diameter > 0.8 * smallest:
        raise ValueError("the pocket formed only %.0f mm across; a %.0f mm rod does not fit. "
                         "Use a larger pocket_depth or a thinner rod." % (smallest, rod_diameter))
    return sum(ys) / len(ys), sum(zs) / len(zs), smallest


def make_rod_pocket(send, pattern_index, pocket_depth=150.0, rod_diameter=30.0,
                    rod_overhang=200.0, settle_steps=150, log=None):
    say = log or (lambda msg: None)
    piece = summarize(send("get_pattern_geometry"), pattern_index)["pieces"][0]
    top_line, x0, x1, top_y, left_to_right = find_top_edge(piece)
    fold_offset = pocket_depth * 0.4
    fold_y = top_y - fold_offset
    width, mid_x = x1 - x0, (x0 + x1) / 2.0

    # 1. pocket strip directly above the panel, fold line on the panel
    count = send("get_pattern_count")["count"]
    send("create_pattern", {"points": [[x0, top_y], [x1, top_y], [x1, top_y + pocket_depth], [x0, top_y + pocket_depth]]})
    strip = count
    if send("get_pattern_count")["count"] != count + 1:
        raise RuntimeError("CLO did not create the pocket strip")
    shapes_before = len(piece["internal_shapes"])
    send("add_internal_shape", {"pattern_index": pattern_index, "points": [[x0, fold_y], [x1, fold_y]], "closed": False})
    fold_shape = shapes_before
    for p in (pattern_index, strip):
        send("place_pattern", {"pattern_index": p, "shape_style": "Flat"})

    # 2. sew: strip bottom (x0 -> x1) to panel top, strip top (x1 -> x0) to the fold line (x0 -> x1)
    seam_top = send("sew_lines", {"pattern_a": strip, "line_a": 0, "pattern_b": pattern_index, "line_b": top_line,
                                  "direction_a": True, "direction_b": left_to_right})
    seam_fold = send("sew_lines", {"pattern_a": strip, "line_a": 2, "pattern_b": pattern_index, "line_b": 0,
                                   "direction_a": True, "direction_b": False, "internal_shape_b": fold_shape})

    # 3. fine mesh, then form the tube with the panel held still
    strip_mesh, panel_mesh = max(3.0, rod_diameter / 5.0), max(5.0, rod_diameter / 3.0)
    send("set_pattern_state", {"pattern_index": strip, "particle_distance": strip_mesh})
    send("set_pattern_state", {"pattern_index": pattern_index, "particle_distance": panel_mesh, "frozen": True})
    for _ in range(3):
        send("simulate", {"steps": 20})
    say("tube formed")

    # 4. measure the panel plane and the tube in three slices across the width
    below = send("get_cloth_bounds", {"min": [x0 + 50, fold_y - 400, -1e6], "max": [x1 - 50, fold_y - 100, 1e6]})
    if not below.get("vertex_count"):
        raise RuntimeError("CLO reported no cloth below the pocket; is the panel simulated?")
    plane_z = (below["min"][2] + below["max"][2]) / 2.0
    region_y = [fold_y - 50, top_y + pocket_depth + 50]
    sides = {}
    for side, (zlo, zhi) in {1: (plane_z + PLANE_TOLERANCE, plane_z + 1000), -1: (plane_z - 1000, plane_z - PLANE_TOLERANCE)}.items():
        sides[side] = send("get_cloth_bounds", {"min": [x0, region_y[0], zlo], "max": [x1, region_y[1], zhi]}).get("vertex_count", 0)
    side = 1 if sides[1] >= sides[-1] else -1
    zlo, zhi = (plane_z + PLANE_TOLERANCE, plane_z + 1000) if side > 0 else (plane_z - 1000, plane_z - PLANE_TOLERANCE)
    slices = []
    for fraction in (0.25, 0.5, 0.75):
        x = x0 + width * fraction
        s = send("get_cloth_bounds", {"min": [x - 40, region_y[0], zlo], "max": [x + 40, region_y[1], zhi]})
        if not s.get("vertex_count"):
            raise RuntimeError("the pocket did not form at x = %.0f; check the seams with view_patterns" % x)
        slices.append(s)
    rod_y, rod_z, clearance = tube_center(slices, plane_z, fold_y, rod_diameter)
    say("tube measured: clearance %.0f mm" % clearance)

    # 5. rod inside the tube, then let the panel hang
    rod = {"center": [mid_x, rod_y, rod_z], "length": width + 2 * rod_overhang, "diameter": rod_diameter}
    send("__add_rod__", rod)
    send("simulate", {"steps": 20})
    send("set_pattern_state", {"pattern_index": pattern_index, "frozen": False})
    remaining = settle_steps
    while remaining > 0:
        send("simulate", {"steps": min(50, remaining)})
        remaining -= 50

    # 6. check it hangs across the whole width (catches a pocket that holds only at the ends)
    whole = send("get_cloth_bounds", {})
    middle = send("get_cloth_bounds", {"min": [mid_x - 50, -1e6, -1e6], "max": [mid_x + 50, 1e6, 1e6]})
    top, middle_top = whole["max"][1], middle.get("max", [0, 0, 0])[1]
    hangs = top > rod_y - 100 and middle_top > rod_y - 100
    return {
        "hangs": hangs,
        "strip_pattern_index": strip, "fold_internal_shape": fold_shape,
        "seams": [seam_top.get("seam_index"), seam_fold.get("seam_index")],
        "rod": rod, "pocket_clearance_mm": round(clearance, 1),
        "panel_top_y": round(top, 1), "panel_middle_top_y": round(middle_top, 1),
        "sag_mm": round(max(0.0, rod_y - middle_top), 1),
        "next": "Look at it with capture_3d(['front', 'right', 'three_quarter_left'])." if hangs else
                "The panel did not stay up across its width; inspect with capture_3d and view_patterns.",
    }

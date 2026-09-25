"""Rod pocket: sew a pocket along a panel's top edge and hang the panel on a rod.

Recipe worked out by simulating a curtain in CLO 2025.2.236:
- The pocket is a separate strip, so the panel can stay frozen while the strip folds.
- A strip that starts in the panel's plane folds either way (it buckled both ways across
  the width), and a tube formed without a rod collapses flat, so the rod must be there
  while the pocket forms.
- A rod below the panel's top edge pushes the folding strip away; a rod just in front of
  the strip and above the top edge is wrapped by it. So the rod goes there.
- Cloth passes through a rod unless its mesh is finer than the rod, so both pieces get a
  small particle distance first.
A new flat piece lies at its 2D (x, y) in 3D, in a plane of constant z.
"""

import math

from clo3d_mcp.geometry import summarize

ROD_GAP = 12.0     # mm between the panel and the rod's surface at the start


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


def placements(top_y, fold_y, plane_z, pocket_depth, rod_diameter):
    """Where the rod goes: (rod_y, rod_z). Raises if the pocket is too small to wrap the rod:
    the loop (strip plus the panel between fold line and top) must go around it.
    """
    radius = rod_diameter / 2.0
    loop = pocket_depth + (top_y - fold_y)
    needed = math.pi * (rod_diameter + 2 * ROD_GAP) * 1.1
    if loop < needed:
        raise ValueError("a %.0f mm pocket is too small to wrap a %.0f mm rod; use pocket_depth >= %.0f"
                         % (pocket_depth, rod_diameter, math.ceil(needed / 1.4)))
    # Live test: a rod just in front of the strip, above the panel's top edge, is wrapped by
    # the strip as it folds; a rod below the top edge pushes it away.
    rod = (top_y + radius + 10.0, plane_z + radius + 3.0)
    return rod


def make_rod_pocket(send, pattern_index, pocket_depth=150.0, rod_diameter=30.0,
                    rod_overhang=200.0, settle_steps=150, log=None):
    say = log or (lambda msg: None)
    piece = summarize(send("get_pattern_geometry"), pattern_index)["pieces"][0]
    top_line, x0, x1, top_y, left_to_right = find_top_edge(piece)
    fold_y = top_y - pocket_depth * 0.4
    width, mid_x = x1 - x0, (x0 + x1) / 2.0
    placements(top_y, fold_y, 0.0, pocket_depth, rod_diameter)  # size check before changing anything

    # 1. pocket strip directly above the panel, fold line on the panel
    count = send("get_pattern_count")["count"]
    send("create_pattern", {"points": [[x0, top_y], [x1, top_y], [x1, top_y + pocket_depth], [x0, top_y + pocket_depth]]})
    strip = count
    if send("get_pattern_count")["count"] != count + 1:
        raise RuntimeError("CLO did not create the pocket strip")
    fold_shape = len(piece["internal_shapes"])
    send("add_internal_shape", {"pattern_index": pattern_index, "points": [[x0, fold_y], [x1, fold_y]], "closed": False})
    for p in (pattern_index, strip):
        send("place_pattern", {"pattern_index": p, "shape_style": "Flat"})

    # 2. sew: strip bottom (x0 -> x1) to panel top, strip top (x1 -> x0) to the fold line (x0 -> x1)
    seam_top = send("sew_lines", {"pattern_a": strip, "line_a": 0, "pattern_b": pattern_index, "line_b": top_line,
                                  "direction_a": True, "direction_b": left_to_right})
    seam_fold = send("sew_lines", {"pattern_a": strip, "line_a": 2, "pattern_b": pattern_index, "line_b": 0,
                                   "direction_a": True, "direction_b": False, "internal_shape_b": fold_shape})

    # 3. fine mesh; hold the panel still; a few steps until CLO reports where the panel is
    send("set_pattern_state", {"pattern_index": strip, "particle_distance": max(3.0, rod_diameter / 5.0)})
    send("set_pattern_state", {"pattern_index": pattern_index, "particle_distance": max(5.0, rod_diameter / 3.0), "frozen": True})
    below = {}
    for _ in range(10):
        send("simulate", {"steps": 2})
        below = send("get_cloth_bounds", {"min": [x0 + 50, fold_y - 400, -1e6], "max": [x1 - 50, fold_y - 100, 1e6]})
        if below.get("vertex_count"):
            break
    if not below.get("vertex_count"):
        raise RuntimeError("CLO reported no cloth below the pocket after 20 steps")
    plane_z = (below["min"][2] + below["max"][2]) / 2.0
    rod_y, rod_z = placements(top_y, fold_y, plane_z, pocket_depth, rod_diameter)

    # 4. rod just in front of the strip, above the panel's top: the strip wraps it as it folds
    length = width + 2 * rod_overhang
    rod = {"center": [mid_x, rod_y, rod_z], "length": length, "diameter": rod_diameter}
    objects = send("__add_rod__", rod)["objects"]
    for _ in range(6):
        send("simulate", {"steps": 20})
    say("pocket formed around the rod")

    # 5. let the panel hang
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
        "rod": rod, "rod_object_index": len(objects) - 1,
        "panel_top_y": round(top, 1), "panel_middle_top_y": round(middle_top, 1),
        "sag_mm": round(max(0.0, rod_y - middle_top), 1),
        "next": "Look at it with capture_3d(['front', 'right', 'three_quarter_left'])." if hangs else
                "The panel did not stay up across its width; inspect with capture_3d and view_patterns.",
    }

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


def placements(top_y, fold_y, plane_z, pocket_depth, rod_diameter, side=-1, gap=3.0, lift=10.0):
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
    # side -1: pocket on the back (-z, away from the front camera), +1: on the front
    rod = (top_y + radius + lift, plane_z + side * (radius + gap))
    return rod


# Rod placements tried in turn when a pocket does not wrap (the simulation is deterministic,
# so a retry needs a different start): (gap between cloth and rod surface, height above the edge)
ATTEMPTS = ((3.0, 10.0), (6.0, 16.0), (1.5, 6.0))


def assemblies(geometry, panels):
    """Every piece joined to each panel through seams (bands, strips, patches...)."""
    links = {}
    for seam in geometry["seams"]:
        owners = {side.get("pattern_index") for side in seam["sides"] if side.get("pattern_index") is not None}
        for a in owners:
            links.setdefault(a, set()).update(owners - {a})
    groups = []
    for panel in panels:
        seen, todo = {panel}, [panel]
        while todo:
            for other in links.get(todo.pop(), ()):
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        groups.append(sorted(seen))
    return groups


def wrapped(slice_bounds, rod_y, rod_z, radius, side):
    """A pocket slice wraps the rod when the strip goes over its top and round its far side."""
    if not slice_bounds.get("vertex_count"):
        return False
    over = slice_bounds["max"][1] >= rod_y + radius - 3.0
    if side > 0:
        far = slice_bounds["max"][2] >= rod_z + radius - 3.0
    else:
        far = slice_bounds["min"][2] <= rod_z - radius + 3.0
    return over and far


def make_rod_pocket(send, pattern_index, pocket_depth=150.0, rod_diameter=30.0,
                    rod_overhang=200.0, settle_steps=150, log=None, pocket_side="front"):
    """One pattern index or a list of panels (e.g. the two halves of a noren); all panels share
    one rod and need their straight top edges at the same height."""
    say = log or (lambda msg: None)
    if pocket_side == "back":
        # Tested in CLO 2025.2.236: with the rod behind the panel the strip still folded to the
        # front, away from the rod, and the panels fell. Build the item facing the "back" camera
        # instead, so the pocket (+z) ends up on its back.
        raise ValueError("a pocket on the -z side did not form in testing; build the panel so its "
                         "decorative face points at the 'back' camera (-z) and keep pocket_side='front'")
    if pocket_side != "front":
        raise ValueError('pocket_side must be "front"')
    side = 1
    indices = list(pattern_index) if isinstance(pattern_index, (list, tuple)) else [pattern_index]
    geometry = summarize(send("get_pattern_geometry"))
    panels = []
    for index in indices:
        pieces = [p for p in geometry["pieces"] if p["pattern_index"] == index]
        if not pieces:
            raise ValueError("no pattern %d" % index)
        top_line, x0, x1, top_y, left_to_right = find_top_edge(pieces[0])
        panels.append({"index": index, "piece": pieces[0], "top_line": top_line, "x0": x0, "x1": x1,
                       "top_y": top_y, "left_to_right": left_to_right})
    top_y = panels[0]["top_y"]
    if any(abs(p["top_y"] - top_y) > 1.0 for p in panels):
        raise ValueError("all panels need their top edges at the same height")
    fold_y = top_y - pocket_depth * 0.4
    x0, x1 = min(p["x0"] for p in panels), max(p["x1"] for p in panels)
    width, mid_x = x1 - x0, (x0 + x1) / 2.0
    radius = rod_diameter / 2.0
    placements(top_y, fold_y, 0.0, pocket_depth, rod_diameter)  # size check before changing anything
    held = sorted({i for group in assemblies(geometry, indices) for i in group})

    for panel in panels:
        index, px0, px1 = panel["index"], panel["x0"], panel["x1"]
        # 1. pocket strip directly above the panel, fold line on the panel
        count = send("get_pattern_count")["count"]
        send("create_pattern", {"points": [[px0, top_y], [px1, top_y], [px1, top_y + pocket_depth],
                                           [px0, top_y + pocket_depth]]})
        strip = count
        if send("get_pattern_count")["count"] != count + 1:
            raise RuntimeError("CLO did not create the pocket strip")
        fold_shape = len(panel["piece"]["internal_shapes"])
        send("add_internal_shape", {"pattern_index": index, "points": [[px0, fold_y], [px1, fold_y]], "closed": False})
        for p in (index, strip):
            send("place_pattern", {"pattern_index": p, "shape_style": "Flat"})
        # 2. sew: strip bottom (x0 -> x1) to panel top, strip top (x1 -> x0) to the fold line (x0 -> x1)
        seam_top = send("sew_lines", {"pattern_a": strip, "line_a": 0, "pattern_b": index,
                                      "line_b": panel["top_line"], "direction_a": True,
                                      "direction_b": panel["left_to_right"]})
        seam_fold = send("sew_lines", {"pattern_a": strip, "line_a": 2, "pattern_b": index, "line_b": 0,
                                       "direction_a": True, "direction_b": False, "internal_shape_b": fold_shape})
        send("set_pattern_state", {"pattern_index": strip, "particle_distance": max(3.0, rod_diameter / 5.0)})
        send("set_pattern_state", {"pattern_index": index, "particle_distance": max(5.0, rod_diameter / 3.0)})
        panel.update({"strip": strip, "fold_shape": fold_shape,
                      "seams": [seam_top.get("seam_index"), seam_fold.get("seam_index")]})

    # 3. hold every joined piece still (not just the panels) while the pockets form
    for index in held:
        send("set_pattern_state", {"pattern_index": index, "frozen": True})
    below = {}
    for _ in range(10):
        send("simulate", {"steps": 2})
        below = send("get_cloth_bounds", {"min": [x0 + 50, fold_y - 400, -1e6], "max": [x1 - 50, fold_y - 100, 1e6]})
        if below.get("vertex_count"):
            break
    if not below.get("vertex_count"):
        raise RuntimeError("CLO reported no cloth below the pocket after 20 steps")
    plane_z = (below["min"][2] + below["max"][2]) / 2.0

    def pocket_report(rod_y, rod_z):
        report = []
        for panel in panels:
            slices = []
            for fraction in (0.2, 0.5, 0.8):
                x = panel["x0"] + (panel["x1"] - panel["x0"]) * fraction
                b = send("__pattern_bounds__", {"pattern_index": panel["strip"],
                                                "min": [x - 30, -1e6, -1e6], "max": [x + 30, 1e6, 1e6]})
                slices.append(wrapped(b, rod_y, rod_z, radius, side))
            report.append(slices)
        return report

    # 4. one rod just past the strips, above the panels' top: the strips wrap it as they fold.
    #    Check each pocket in three places; if any missed, reset the cloth and move the rod a little.
    rod_part, attempts = None, []
    for attempt, (gap, lift) in enumerate(ATTEMPTS):
        if attempt:
            send("reset_arrangement")
            send("__remove_object__", {"index": rod_part})
        rod_y, rod_z = placements(top_y, fold_y, plane_z, pocket_depth, rod_diameter, side, gap, lift)
        rod = {"center": [mid_x, rod_y, rod_z], "length": width + 2 * rod_overhang, "diameter": rod_diameter}
        rod_part = send("__add_rod__", rod)["added_index"]
        for _ in range(6):
            send("simulate", {"steps": 20})
        report = pocket_report(rod_y, rod_z)
        attempts.append({"gap": gap, "lift": lift, "wrapped": report})
        say("attempt %d: %s" % (attempt + 1, report))
        if all(all(r) for r in report):
            break

    # 5. let the panels hang
    for index in held:
        send("set_pattern_state", {"pattern_index": index, "frozen": False})
    remaining = settle_steps
    while remaining > 0:
        send("simulate", {"steps": min(50, remaining)})
        remaining -= 50

    # 6. check each panel on its own: its top must be at the rod and its pocket still wrapped
    final = pocket_report(rod_y, rod_z)
    results = []
    for panel, slices in zip(panels, final):
        b = send("__pattern_bounds__", {"pattern_index": panel["index"]})
        top = b["max"][1] if b.get("vertex_count") else float("-inf")
        results.append({"pattern_index": panel["index"], "strip_pattern_index": panel["strip"],
                        "fold_internal_shape": panel["fold_shape"], "seams": panel["seams"],
                        "panel_top_y": round(top, 1), "drop_mm": round(max(0.0, rod_y - radius - top), 1),
                        "pocket_wrapped": slices, "hangs": all(slices) and top > rod_y - radius - 60.0})
    hangs = all(r["hangs"] for r in results)
    return {
        "hangs": hangs, "pocket_side": pocket_side, "fold_y": fold_y, "panels": results, "rod": rod,
        "rod_object_index": rod_part, "attempts": attempts, "frozen_while_forming": held,
        "next": "Look at it with capture_3d(['front', 'right', 'three_quarter_left'])." if hangs else
                "A pocket did not hold the rod everywhere; see 'pocket_wrapped' (20/50/80% across each "
                "panel) and inspect with capture_3d.",
    }

"""Build a noren doorway curtain in CLO from the MCP tools (reference: split linen curtain with a
blue header patch, a 10-point star split across the panels and L-shaped blue bottom blocks).

Finished size 1050 x 2020 mm, two separate panels on a 32 mm rod. 2D pattern coordinates are
millimetres with y up; the finished bottom edge is y = 0 and the top edge y = 2020.

Run with CLO open, an empty scene and the MCP listener running:
    uv run python examples/noren_curtain.py
"""

import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import clo3d_mcp.server as s  # noqa: E402

W, H, SPLIT = 1050.0, 2020.0, 525.0
ROD_DIAMETER = 32.0
HEM_OUTER, HEM_CENTRE, HEM_BOTTOM = 30.0, 20.0, 70.0
LINEN = r"C:\Users\Public\Documents\CLO\CLO Assets\Fabric\01_LINEN.ZFAB"
COLOURS = {"brown": (92, 62, 45), "blue": (122, 138, 160), "light": (165, 176, 192)}

# Proportions read from the reference photo (1 px ~ 1.02 mm across, 1.15 mm down)
STAR_CENTRE, STAR_R, STAR_r, STAR_POINTS = (SPLIT, H - 877.0), 205.0, 110.0, 10
HEADER = (174.0, 845.0, H - 240.0, H - 30.0)          # x0, x1, y0, y1
# The rod pocket forms on the +z side, so the curtain's decorative face points at CLO's "back"
# camera (-z). Seen from there, left and right swap: 2D x 0..525 is the viewer's right panel.
LEFT = {"band_top": 220.0, "strip_x": 149.0, "strip_top": 530.0, "square": (149.0, 318.0, 220.0, 375.0)}
RIGHT = {"band_top": 231.0, "strip_x": 875.0, "strip_top": 531.0, "square": (712.0, 875.0, 369.0, 531.0)}
FACE, REVERSE = -1, 1   # pattern layers: -1 = towards the back camera = the curtain's face

log = []


def step(msg):
    print(msg, flush=True)
    log.append(msg)


def piece(points, fabric, name):
    index = s.get_pattern_count()["count"]
    s.create_pattern(points)
    s.assign_fabric_to_pattern(fabrics[fabric], index)
    s.set_pattern_name(index, name)
    s.place_pattern(index, shape_style="Flat")
    return index


def sew(a, la, b, lb, flip_b=True, shape_b=None):
    return s.sew_lines(a, la, b, lb, direction_a=True, direction_b=not flip_b, internal_shape_b=shape_b)


def star_half(side):
    """Half of a 10-point star, from the top point round one side to the bottom point."""
    cx, cy = STAR_CENTRE
    step_deg = 180.0 / STAR_POINTS
    pts = []
    for k in range(STAR_POINTS + 1):
        angle = math.radians(90 + side * k * step_deg)   # side -1: via the right, +1: via the left
        r = STAR_R if k % 2 == 0 else STAR_r
        pts.append([round(cx + r * math.cos(angle), 2), round(cy + r * math.sin(angle), 2)])
    pts[0][0] = pts[-1][0] = cx  # top and bottom points exactly on the split
    return pts


POCKET_DEPTH = 150.0
FOLD_Y = H - POCKET_DEPTH * 0.4   # the rod pocket's fold line; its back is the pocket


def applique(base, outline, sewn_lines, fabric, name, reverse=True, reverse_outline=None):
    """Patch on the front (layer 1) and, for 'front and reverse patches', a copy behind (layer -1).
    The base gets an open internal line along the patch's sewn edges; each sewn edge of the patch
    is stitched to it, so the patch stays attached where it sits."""
    made = []
    shapes = {}
    for layer, label in ((FACE, "front"), (REVERSE, "reverse")) if reverse else ((FACE, "front"),):
        shape_points = reverse_outline if (layer == REVERSE and reverse_outline) else outline
        key = tuple(map(tuple, shape_points[:sewn_lines + 1]))
        if key not in shapes:  # one internal line per distinct outline, shared by front and back
            shapes[key] = len(s.get_pattern_geometry(base)["pieces"][0]["internal_shapes"])
            s.add_internal_shape(base, shape_points[:sewn_lines + 1], closed=False)
        shape = shapes[key]
        patch = piece(shape_points, fabric, "%s %s" % (name, label))
        s.set_pattern_state(patch, layer=layer)
        for line in range(sewn_lines):
            sew(patch, line, base, line, flip_b=False, shape_b=shape)
        made.append(patch)
    return made


def hem_line(pattern, a, b):
    s.add_internal_shape(pattern, [a, b], closed=False)


if __name__ == "__main__":
    t0 = time.time()
    if "--reset" in sys.argv:  # clear pieces and collision objects of a previous run
        for i in reversed(range(s.get_pattern_count()["count"])):
            s.delete_pattern(i)
        if s.get_avatars()["count"]:
            s._send("delete_objects", {"indices": [0]})
        s._apply_parts([])
    info = s.get_project_info()
    if info["pattern_count"] or s.get_avatars()["count"]:
        sys.exit("open an empty scene first (or pass --reset to clear a previous run)")

    # Fabrics: CLO's linen in three colours (60/40 cotton-linen content cannot be set through the API yet)
    fabrics = {}
    for name, rgb in COLOURS.items():
        fabrics[name] = s.add_fabric(LINEN)["fabric_index"]
        s.set_fabric_color(fabrics[name], *rgb)
    step("fabrics: %s" % fabrics)

    # --- left panel: brown body with a notch for the blue side strip, blue bottom band ---
    bt, sx, st = LEFT["band_top"], LEFT["strip_x"], LEFT["strip_top"]
    body_l = piece([[sx, bt], [SPLIT, bt], [SPLIT, H], [0, H], [0, st], [sx, st]], "brown", "Left body")
    band_l = piece([[0, 0], [SPLIT, 0], [SPLIT, bt], [sx, bt], [0, bt]], "blue", "Left bottom band")
    strip_l = piece([[0, bt], [sx, bt], [sx, st], [0, st]], "blue", "Left side strip")
    sew(body_l, 0, band_l, 2)     # body bottom (x 175 -> 525) to band top-right (525 -> 175)
    sew(body_l, 4, strip_l, 2)    # body notch top (0 -> 175) to strip top (175 -> 0)
    sew(body_l, 5, strip_l, 1)    # body notch side (down) to strip right side (up)
    sew(strip_l, 0, band_l, 3)    # strip bottom (0 -> 175) to band top-left (175 -> 0)

    # --- right panel ---
    bt, sx, st = RIGHT["band_top"], RIGHT["strip_x"], RIGHT["strip_top"]
    body_r = piece([[SPLIT, bt], [sx, bt], [sx, st], [W, st], [W, H], [SPLIT, H]], "brown", "Right body")
    band_r = piece([[SPLIT, 0], [W, 0], [W, bt], [sx, bt], [SPLIT, bt]], "blue", "Right bottom band")
    strip_r = piece([[sx, bt], [W, bt], [W, st], [sx, st]], "blue", "Right side strip")
    sew(body_r, 0, band_r, 3)     # body bottom (525 -> 901) to band top-left (901 -> 525)
    sew(body_r, 1, strip_r, 3)    # body notch side (up) to strip left side (down)
    sew(body_r, 2, strip_r, 2)    # body notch top (901 -> 1050) to strip top (1050 -> 901)
    sew(strip_r, 0, band_r, 2)    # strip bottom (901 -> 1050) to band top-right (1050 -> 901)
    step("panels and 15 mm joins: %d pieces, %d seams" % (s.get_pattern_count()["count"], len(s.get_pattern_geometry()["seams"])))

    # --- hems as hem lines at the finished widths (CLO's API cannot set fold angles) ---
    for p, x, y0, y1 in ((body_l, HEM_OUTER, LEFT["strip_top"], H), (strip_l, HEM_OUTER, LEFT["band_top"], LEFT["strip_top"]),
                         (band_l, HEM_OUTER, 0, LEFT["band_top"]), (body_l, SPLIT - HEM_CENTRE, LEFT["band_top"], H),
                         (band_l, SPLIT - HEM_CENTRE, 0, LEFT["band_top"]),
                         (body_r, W - HEM_OUTER, RIGHT["strip_top"], H), (strip_r, W - HEM_OUTER, RIGHT["band_top"], RIGHT["strip_top"]),
                         (band_r, W - HEM_OUTER, 0, RIGHT["band_top"]), (body_r, SPLIT + HEM_CENTRE, RIGHT["band_top"], H),
                         (band_r, SPLIT + HEM_CENTRE, 0, RIGHT["band_top"])):
        hem_line(p, [x, y0], [x, y1])
    hem_line(band_l, [0, HEM_BOTTOM], [SPLIT, HEM_BOTTOM])
    hem_line(band_r, [SPLIT, HEM_BOTTOM], [W, HEM_BOTTOM])
    step("hem lines: outer 30, centre 20, bottom 70 mm")

    # --- appliqué: front and reverse, sewn along their inner edges ---
    x0, x1, y0, y1 = HEADER
    ry1 = min(y1, FOLD_Y - 20.0)   # the reverse header stops below the rod pocket (+z side)
    applique(body_l, [[SPLIT, y1], [x0, y1], [x0, y0], [SPLIT, y0]], 3, "light", "Header L",
             reverse_outline=[[SPLIT, ry1], [x0, ry1], [x0, y0], [SPLIT, y0]])
    applique(body_r, [[SPLIT, y1], [x1, y1], [x1, y0], [SPLIT, y0]], 3, "light", "Header R",
             reverse_outline=[[SPLIT, ry1], [x1, ry1], [x1, y0], [SPLIT, y0]])
    applique(body_l, star_half(+1), STAR_POINTS, "light", "Star L")
    applique(body_r, star_half(-1), STAR_POINTS, "light", "Star R")
    qx0, qx1, qy0, qy1 = LEFT["square"]    # beside the strip, on top of the band: 2 free edges
    applique(body_l, [[qx0, qy1], [qx1, qy1], [qx1, qy0], [qx0, qy0]], 2, "light", "Square L")
    qx0, qx1, qy0, qy1 = RIGHT["square"]   # beside the strip, level with its top: 3 free edges
    applique(body_r, [[qx1, qy1], [qx0, qy1], [qx0, qy0], [qx1, qy0]], 3, "light", "Square R")
    step("appliqué: header, star and squares, front and reverse (%d pieces)" % s.get_pattern_count()["count"])

    # --- rod pocket on both panels, one 32 mm rod ---
    s.save_checkpoint("noren_flat")
    result = s.make_rod_pocket([body_l, body_r], pocket_depth=POCKET_DEPTH, rod_diameter=ROD_DIAMETER,
                               pocket_side="front")
    step("rod pocket: hangs=%s %s" % (result["hangs"], [(p["pattern_index"], p["sag_mm"]) for p in result["panels"]]))
    s.simulate(100)
    step("done in %.0f s" % (time.time() - t0))

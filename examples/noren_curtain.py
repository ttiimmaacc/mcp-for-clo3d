"""Build a noren doorway curtain in CLO from the MCP tools (reference: split linen curtain with a
blue header patch, a 10-point star split across the panels and L-shaped blue bottom blocks).

Finished size 1050 x 2020 mm, two separate panels on a 32 mm rod. 2D pattern coordinates are
millimetres with y up; the finished bottom edge is y = 0 and the top edge y = 2020. The panels
end at TOP; hang_on_rod adds the 60 mm header band the rod lies along above them.

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
BAND = 60.0                  # header band hang_on_rod adds for the rod
TOP = H - BAND               # the panels' top edge
HEADER = (174.0, 845.0, TOP - 220.0, TOP - 10.0)      # x0, x1, y0, y1
# The rod lies on the +z side of the band, so the curtain's decorative face points at CLO's
# "back" camera (-z). Seen from there, left and right swap: 2D x 0..525 is the viewer's right panel.
# The two panels mirror each other in height: one band top, one side-strip top and one square
# size for both (the photo gave each side its own values, up to 11 mm apart).
BAND_TOP, STRIP_TOP = 225.0, 530.0
SQUARE_W, SQUARE_H = 165.0, 160.0
LEFT = {"band_top": BAND_TOP, "strip_x": 149.0, "strip_top": STRIP_TOP,
        "square": (149.0, 149.0 + SQUARE_W, BAND_TOP, BAND_TOP + SQUARE_H)}           # on the band
RIGHT = {"band_top": BAND_TOP, "strip_x": 875.0, "strip_top": STRIP_TOP,
         "square": (875.0 - SQUARE_W, 875.0, STRIP_TOP - SQUARE_H, STRIP_TOP)}         # level with the strip top
FACE = -1               # pattern layer of the face patches: -1 = towards the back camera
# On the back (+z): bottom hems on layer 1, side hems crossing them on 2, reverse patches on 3
HEM_BOTTOM_LAYER, HEM_SIDE_LAYER, REVERSE_PATCH = 1, 2, 3
CONTENT = "60% Cotton, 40% Linen"

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


def applique(base, outline, sewn_lines, fabric, name):
    """'Front and reverse patches': the patch on the face and a copy on the back, sewn along the
    same stitch line on the base; the edges on the panel's outline or a seam stay free."""
    return s.add_applique(base, outline, sewn_lines=sewn_lines, layer=FACE, reverse=True,
                          reverse_layer=REVERSE_PATCH, fabric_index=fabrics[fabric], name=name)


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

    # Fabrics: CLO's linen in three colours, labelled 60/40 cotton-linen (the label is metadata;
    # the drape comes from the linen's physical properties)
    fabrics = {}
    for name, rgb in COLOURS.items():
        fabrics[name] = s.add_fabric(LINEN)["fabric_index"]
        s.set_fabric_color(fabrics[name], *rgb)
        s.set_fabric_information(fabrics[name], {"Content": CONTENT}, name="Cotton-linen %s" % name)
    step("fabrics: %s" % fabrics)

    # --- left panel: brown body with a notch for the blue side strip, blue bottom band ---
    bt, sx, st = LEFT["band_top"], LEFT["strip_x"], LEFT["strip_top"]
    body_l = piece([[sx, bt], [SPLIT, bt], [SPLIT, TOP], [0, TOP], [0, st], [sx, st]], "brown", "Left body")
    band_l = piece([[0, 0], [SPLIT, 0], [SPLIT, bt], [sx, bt], [0, bt]], "blue", "Left bottom band")
    strip_l = piece([[0, bt], [sx, bt], [sx, st], [0, st]], "blue", "Left side strip")
    sew(body_l, 0, band_l, 2)     # body bottom (x 175 -> 525) to band top-right (525 -> 175)
    sew(body_l, 4, strip_l, 2)    # body notch top (0 -> 175) to strip top (175 -> 0)
    sew(body_l, 5, strip_l, 1)    # body notch side (down) to strip right side (up)
    sew(strip_l, 0, band_l, 3)    # strip bottom (0 -> 175) to band top-left (175 -> 0)

    # --- right panel ---
    bt, sx, st = RIGHT["band_top"], RIGHT["strip_x"], RIGHT["strip_top"]
    body_r = piece([[SPLIT, bt], [sx, bt], [sx, st], [W, st], [W, TOP], [SPLIT, TOP]], "brown", "Right body")
    band_r = piece([[SPLIT, 0], [W, 0], [W, bt], [sx, bt], [SPLIT, bt]], "blue", "Right bottom band")
    strip_r = piece([[sx, bt], [W, bt], [W, st], [sx, st]], "blue", "Right side strip")
    sew(body_r, 0, band_r, 3)     # body bottom (525 -> 901) to band top-left (901 -> 525)
    sew(body_r, 1, strip_r, 3)    # body notch side (up) to strip left side (down)
    sew(body_r, 2, strip_r, 2)    # body notch top (901 -> 1050) to strip top (1050 -> 901)
    sew(strip_r, 0, band_r, 2)    # strip bottom (901 -> 1050) to band top-right (1050 -> 901)
    step("panels and 15 mm joins: %d pieces, %d seams" % (s.get_pattern_count()["count"], len(s.get_pattern_geometry()["seams"])))

    # --- hems: the turned-back layer on the back, sewn at the edge and the hem line ---
    for p, line, width, layer in ((band_l, 0, HEM_BOTTOM, HEM_BOTTOM_LAYER), (band_r, 0, HEM_BOTTOM, HEM_BOTTOM_LAYER),
                                  (body_l, 3, HEM_OUTER, HEM_SIDE_LAYER), (strip_l, 3, HEM_OUTER, HEM_SIDE_LAYER),
                                  (band_l, 4, HEM_OUTER, HEM_SIDE_LAYER), (body_l, 1, HEM_CENTRE, HEM_SIDE_LAYER),
                                  (band_l, 1, HEM_CENTRE, HEM_SIDE_LAYER),
                                  (body_r, 3, HEM_OUTER, HEM_SIDE_LAYER), (strip_r, 1, HEM_OUTER, HEM_SIDE_LAYER),
                                  (band_r, 1, HEM_OUTER, HEM_SIDE_LAYER), (body_r, 5, HEM_CENTRE, HEM_SIDE_LAYER),
                                  (band_r, 4, HEM_CENTRE, HEM_SIDE_LAYER)):
        s.add_hem(p, line, width, layer=layer)
    step("hems: outer 30, centre 20, bottom 70 mm (%d pieces)" % s.get_pattern_count()["count"])

    # --- appliqué: front and reverse, sewn along their inner edges ---
    x0, x1, y0, y1 = HEADER
    patches = []
    patches.append(applique(body_l, [[SPLIT, y1], [x0, y1], [x0, y0], [SPLIT, y0]], 3, "light", "Header L"))
    patches.append(applique(body_r, [[SPLIT, y1], [x1, y1], [x1, y0], [SPLIT, y0]], 3, "light", "Header R"))
    patches.append(applique(body_l, star_half(+1), STAR_POINTS, "light", "Star L"))
    patches.append(applique(body_r, star_half(-1), STAR_POINTS, "light", "Star R"))
    qx0, qx1, qy0, qy1 = LEFT["square"]    # beside the strip, on top of the band: 2 free edges
    patches.append(applique(body_l, [[qx0, qy1], [qx1, qy1], [qx1, qy0], [qx0, qy0]], 2, "light", "Square L"))
    qx0, qx1, qy0, qy1 = RIGHT["square"]   # beside the strip, level with its top: 3 free edges
    patches.append(applique(body_r, [[qx1, qy1], [qx0, qy1], [qx0, qy0], [qx1, qy0]], 3, "light", "Square R"))
    step("appliqué: header, star and squares, front and reverse (%d pieces)" % s.get_pattern_count()["count"])

    # --- appliqué stitching: 0.4 mm thread, 4 mm in from each sewn patch edge (the 4 mm return) ---
    style = s.create_topstitch_style("Noren appliqué 4 mm", thread_thickness_mm=0.4, offset_mm=4.0)
    seams = [seam for made in patches for patch in made["patches"] for seam in patch["seams"]]
    for seam in seams:
        s.add_topstitch(style["style_index"], seam_index=seam)
    step("appliqué stitching: %d seams, %s" % (len(seams), {k: style[k] for k in ("thread_tex", "offset_mm")}))

    if "--flat-only" in sys.argv:  # stop before the rod pocket (e.g. to inspect the flat layout)
        step("flat build done in %.0f s" % (time.time() - t0))
        sys.exit(0)

    # --- both panels on one 32 mm rod, from frozen header bands ---
    s.save_checkpoint("noren_flat")
    result = s.hang_on_rod([body_l, body_r], band_height=BAND, rod_diameter=ROD_DIAMETER, rod_side="front")
    for p in result["panels"]:
        step("  panel %d: cloth just below its band at 20/50/80%% of its width: %s" % (p["pattern_index"], p["below_band"]))
    step("hang on rod: hangs=%s, finished top y=%.0f" % (result["hangs"], result["finished_top_y"]))
    s.simulate(100)
    step("done in %.0f s" % (time.time() - t0))

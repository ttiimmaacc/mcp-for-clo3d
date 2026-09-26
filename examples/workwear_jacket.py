"""Build a reversible wool workwear jacket on CLO's male avatar from the MCP tools.

Reference: boxy, cropped jacket in double-faced wool (plaid / solid charcoal), very dropped
shoulders, stand collar, five-button front, turn-back cuffs, side slits.
Size M, the brand's measuring guide (laid flat): shoulders 80 cm seam to seam, chest 54 cm
straight across at the narrowest point, back 64 cm from below the collar to the hem, sleeves
76 cm from the collar seam to the wrist (so the shoulder line counts towards the sleeve).

2D pattern coordinates are millimetres with y up; the hem is y = 0. Pieces are drafted as seen
from the outside of the garment, so on the fronts the avatar's right is on the left of the
drawing, and on the back (seen from behind) it is on the right: a front's seams go to the
mirrored side of the back (sewing them to the same x crosses the garment through itself).

Run with CLO open and the MCP listener running:
    uv run python examples/workwear_jacket.py [--reset]
"""

import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import clo3d_mcp.server as s  # noqa: E402
from clo3d_mcp.geometry import find_edge, summarize  # noqa: E402

AVATAR = r"C:\Users\Public\Documents\CLO\CLO Assets\Avatar\Male\MV2.1_Luka.avt"
WOOL = r"C:\Users\Public\Documents\CLO\CLO Assets\Fabric\V2_Woven_Melton_Boiled_1.zfab"

# Grey/black workwear check, one 120 mm repeat, and the plain charcoal back face
PLAID = [["#1b1b1d", 45], ["#5d5d60", 6], ["#1b1b1d", 6], ["#5d5d60", 35], ["#8e8e90", 3], ["#5d5d60", 25]]
CHARCOAL = [92, 92, 96]

# Measurements (mm)
HALF_SHOULDER = 400.0        # centre to the dropped shoulder seam
HALF_CHEST = 270.0           # chest 54 cm laid flat: each front and half the back
LENGTH = 640.0               # back length, centre back neck to hem
SLEEVE_FROM_COLLAR = 760.0   # collar seam (neck point) along the shoulder to the wrist
CUFF_H = 70.0
COLLAR_H = 65.0
NECK_HALF = 95.0             # half the neck opening across
FRONT_NECK_DROP = 85.0
EXTENSION = 25.0             # button extension past centre front
SHOULDER_Y = LENGTH - 25.0   # height of the dropped shoulder point
# deep, boxy armhole: straight down from the dropped shoulder, then curving in to the side seam
# (in the brand's diagram the chest line sits about halfway down the body)
ARMHOLE_DEPTH = 240.0       # verified: with the low-cap two-piece sleeve this drapes cleanly once the
                            # sleeves are nudged onto the arms with CLO's Move tool (see the note at the end)
UNDERARM_Y = SHOULDER_Y - ARMHOLE_DEPTH           # side seam top
ARM_CURVE = (HALF_SHOULDER - 15.0, UNDERARM_Y + 110.0)   # curve point: near vertical above it
NECK_Y = LENGTH + 20.0       # height of the neck point (shoulder rises to it)
SLIT = 100.0                 # side slits above the hem
# kangaroo pocket half, in front coordinates (x outwards from centre front, y up from the hem)
POCKET_BOTTOM, POCKET_TOP, POCKET_SLANT_Y = 60.0, 300.0, 170.0
POCKET_CF = 18.0             # the halves stop just short of the front edges
POCKET_OUT, POCKET_TOP_OUT = 230.0, 120.0
SHOULDER_LINE = math.hypot(HALF_SHOULDER - NECK_HALF, NECK_Y - SHOULDER_Y)
SLEEVE = SLEEVE_FROM_COLLAR - SHOULDER_LINE   # dropped shoulder seam to the cuff edge
CUFF_HALF = 165.0            # half the cuff width (33 cm round)
CAP_HEIGHT = 60.0            # low sleeve cap for a dropped shoulder

log = []


def step(msg):
    print(msg, flush=True)
    log.append(msg)


def piece(points, name):
    result = s.create_pattern(points)
    s.set_pattern_name(result["pattern_index"], name)
    s.assign_fabric_to_pattern(fabric, result["pattern_index"])
    return result["pattern_index"], result["lines"]


def arrangement(name):
    for p in points_by_name:
        if p["ArrangementName"] == name:
            return p["arrangement_index"]
    raise ValueError("no arrangement point %s" % name)


if __name__ == "__main__":
    t0 = time.time()
    if "--reset" in sys.argv:
        for i in reversed(range(s.get_pattern_count()["count"])):
            s.delete_pattern(i)
        s._apply_parts([])
    if not s.get_avatars()["count"]:
        s.import_avatar(AVATAR)
    points_by_name = s.get_arrangement_points()["arrangement_points"]
    fabric = s.add_fabric(WOOL)["fabric_index"]
    s.set_fabric_information(fabric, {"Content": "100% Wool, double-faced"}, name="Double-faced wool")
    # double-faced: grey/black plaid outside, plain charcoal inside
    s.apply_plaid(fabric, PLAID, back_color=CHARCOAL)
    step("avatar and fabric ready (fabric %d)" % fabric)

    # --- body: back and two fronts, dropped shoulders, side slits ---
    sx, sy = HALF_SHOULDER, SHOULDER_Y
    cx, cy = ARM_CURVE
    back, back_lines = piece([[-HALF_CHEST, 0], [HALF_CHEST, 0], [HALF_CHEST, SLIT], [HALF_CHEST, UNDERARM_Y],
                              [cx, cy, 2], [sx, sy], [NECK_HALF, NECK_Y], [0, LENGTH, 2], [-NECK_HALF, NECK_Y],
                              [-sx, sy], [-cx, cy, 2], [-HALF_CHEST, UNDERARM_Y], [-HALF_CHEST, SLIT]], "Back")
    neck_front_y = NECK_Y - FRONT_NECK_DROP - 20
    front_r, fr_lines = piece([[-HALF_CHEST, 0], [EXTENSION, 0], [EXTENSION, neck_front_y],
                               [-NECK_HALF * 0.55, neck_front_y + 18, 2], [-NECK_HALF, NECK_Y],
                               [-sx, sy], [-cx, cy, 2], [-HALF_CHEST, UNDERARM_Y], [-HALF_CHEST, SLIT]], "Front right")
    front_l, fl_lines = piece([[-EXTENSION, 0], [HALF_CHEST, 0], [HALF_CHEST, SLIT], [HALF_CHEST, UNDERARM_Y],
                               [cx, cy, 2], [sx, sy], [NECK_HALF, NECK_Y], [NECK_HALF * 0.55, neck_front_y + 18, 2],
                               [-EXTENSION, neck_front_y]], "Front left")
    # shoulders (neck point to shoulder point) and sides (underarm to slit top), right then left
    for front, sign in ((front_r, -1), (front_l, 1)):
        neck, shoulder = [sign * NECK_HALF, NECK_Y], [sign * sx, sy]
        underarm, slit = [sign * HALF_CHEST, UNDERARM_Y], [sign * HALF_CHEST, SLIT]
        m = lambda p: [-p[0], p[1]]      # the same side of the body on the back
        s.sew_edges(back, m(neck), m(shoulder), front, neck, shoulder)
        s.sew_edges(back, m(underarm), m(slit), front, underarm, slit)
    step("body: back and fronts, shoulder and side seams, %d mm slits" % SLIT)

    # --- front closure: CLO's API cannot place buttons, so the overlap is sewn along centre
    #     front, as if buttoned; without it the unbuttoned fronts spread and the collar flops ---
    cf = [[0, 40], [0, neck_front_y - 15]]
    closure = []
    for front in (front_r, front_l):
        closure.append(len(summarize(s._send("get_pattern_geometry"), front)["pieces"][0]["internal_shapes"]))
        s.add_internal_shape(front, cf, closed=False)
    s.sew_lines(front_r, 0, front_l, 0, direction_a=True, direction_b=True,
                internal_shape_a=closure[0], internal_shape_b=closure[1])
    # no layer: a layered piece shows tinted green in CLO, and the closure seam holds the overlap
    step("front closed along centre front (buttons stand-in)")

    # --- sleeves: flat dropped-shoulder sleeve; the top's midpoint is the shoulder point ---
    # armhole seam length on each body piece (curved), read back from CLO
    pz = summarize(s._send("get_pattern_geometry"), back)["pieces"][0]
    li, _ = find_edge(pz, [sx, sy], [HALF_CHEST, UNDERARM_Y])
    arm = next(l["length"] for l in pz["lines"] if l["line_index"] == li)
    bs = SLEEVE - CUFF_H
    # dropped shoulder: a low, flat cap (the body already covers the shoulder); a tall set-in
    # style cap collapsed into wrinkles. The sleeve's width follows from the armhole length.
    cap = CAP_HEIGHT
    half = math.sqrt(arm ** 2 - cap ** 2)
    low = bs - cap
    sleeves = {}
    # two-piece sleeve: a front and a back panel, seamed over the top of the arm (from the
    # shoulder point to the cuff) and underneath. CLO places a flat front or back panel reliably;
    # a one-piece sleeve wrapped round the arm landed rotated, seams across the front and back.
    # Halves of the capped sleeve outline, split along x = 0:
    plus = [[0, 0], [CUFF_HALF, 0], [half, low], [0, bs]]           # over-arm edge on its left
    minus = [[-CUFF_HALF, 0], [0, 0], [0, bs], [-half, low]]        # over-arm edge on its right
    for side, sign in (("right", -1), ("left", 1)):
        front, sfx = (front_r, "R") if side == "right" else (front_l, "L")
        # drawn as seen from the side each panel faces, the over-arm edge towards the arm's outer
        # side: right arm front (+x half), right arm back (-x half), and the reverse on the left
        fx = 1 if side == "right" else -1
        fp, _ = piece(plus if fx > 0 else minus, "Sleeve %s front" % side)
        bp, _ = piece(minus if fx > 0 else plus, "Sleeve %s back" % side)
        s.sew_edges(fp, [0, bs], [0, 0], bp, [0, bs], [0, 0])                                       # over-arm
        s.sew_edges(fp, [fx * half, low], [fx * CUFF_HALF, 0], bp, [-fx * half, low], [-fx * CUFF_HALF, 0])  # underarm
        shoulder, underarm = [sign * sx, sy], [sign * HALF_CHEST, UNDERARM_Y]
        s.sew_edges(fp, [0, bs], [fx * half, low], front, shoulder, underarm)
        s.sew_edges(bp, [0, bs], [-fx * half, low], back, [-shoulder[0], shoulder[1]], [-underarm[0], underarm[1]])
        # cuff: top edge split at its middle, one half to each panel, closed into a tube
        cf, _ = piece([[-CUFF_HALF, -CUFF_H], [CUFF_HALF, -CUFF_H], [CUFF_HALF, 0], [0, 0], [-CUFF_HALF, 0]],
                      "Cuff %s" % side)
        s.sew_edges(cf, [0, 0], [fx * CUFF_HALF, 0], fp, [0, 0], [fx * CUFF_HALF, 0])
        s.sew_edges(cf, [0, 0], [-fx * CUFF_HALF, 0], bp, [0, 0], [-fx * CUFF_HALF, 0])
        s.sew_edges(cf, [CUFF_HALF, -CUFF_H], [CUFF_HALF, 0], cf, [-CUFF_HALF, -CUFF_H], [-CUFF_HALF, 0])
        sleeves[side] = (fp, bp, cf, sfx)
    step("two-piece sleeves: %.0f mm armhole seams, %.0f mm cap, %.0f mm round at the top, %d mm to the cuff"
         % (arm, cap, half * 2, SLEEVE))

    # --- stand collar: bottom edge split to match right front, back and left front necklines ---
    neck_len = {}
    for pc, p0, p1 in ((front_r, [EXTENSION, neck_front_y], [-NECK_HALF, NECK_Y]),
                       (back, [-NECK_HALF, NECK_Y], [NECK_HALF, NECK_Y])):
        pz = summarize(s._send("get_pattern_geometry"), pc)["pieces"][0]
        li, _ = find_edge(pz, p0, p1)
        neck_len[pc] = next(l["length"] for l in pz["lines"] if l["line_index"] == li)
    a, b = neck_len[front_r], neck_len[back]
    total = a + b + a
    collar, _ = piece([[0, 0], [a, 0], [a + b, 0], [total, 0], [total, COLLAR_H], [0, COLLAR_H]], "Stand collar")
    # drawn as seen from outside at the back of the neck, where CLO's Neck_Collar point wraps it:
    # left front first (drawn from the right front, CLO put it on face-in)
    s.sew_edges(collar, [0, 0], [a, 0], front_l, [-EXTENSION, neck_front_y], [NECK_HALF, NECK_Y])
    s.sew_edges(collar, [a, 0], [a + b, 0], back, [-NECK_HALF, NECK_Y], [NECK_HALF, NECK_Y])
    s.sew_edges(collar, [a + b, 0], [total, 0], front_r, [-NECK_HALF, NECK_Y], [EXTENSION, neck_front_y])
    s.set_pattern_state(collar, strengthened=True)   # stands like an interfaced stand collar
    step("stand collar %.0f x %d mm, strengthened" % (total, COLLAR_H))

    # --- dress the avatar ---
    # arrangement points are centred on each piece; the _3 points (55 % up the torso) put a
    # 64 cm body piece's neckline at the base of the neck, the _2 points put it at the face
    for p, name, *start in ((back, "Body_Back_Center_3"), (front_r, "Body_Front_3_R"), (front_l, "Body_Front_3_L"),
                            (sleeves["right"][0], "Arm_Front_1_R"), (sleeves["right"][1], "Arm_Back_1_R"),
                            (sleeves["left"][0], "Arm_Front_1_L"), (sleeves["left"][1], "Arm_Back_1_L"),
                            (sleeves["right"][2], "Wrist_Outside_R"), (sleeves["left"][2], "Wrist_Outside_L"),
                            (collar, "Neck_Collar")):
        if start:   # position on the point's surface (%) and distance from the body
            s.place_pattern(p, arrangement_index=arrangement(name), position_x=start[0][0],
                            position_y=start[0][1], offset=start[0][2])
        else:
            s.place_pattern(p, arrangement_index=arrangement(name))
    step("placed on the avatar")

    # --- split kangaroo pocket: one half on each front, meeting at centre front; sewn at the
    #     top, outer side, bottom and centre front, the slanted side left open as the opening ---
    for front, sign, point in ((front_r, -1, "Body_Front_4_R"), (front_l, 1, "Body_Front_4_L"))             if "--no-pocket" not in sys.argv else ():
        x = lambda v: sign * v
        half = [[x(POCKET_OUT), POCKET_SLANT_Y], [x(POCKET_OUT), POCKET_BOTTOM], [x(-POCKET_CF), POCKET_BOTTOM],
                [x(-POCKET_CF), POCKET_TOP], [x(POCKET_TOP_OUT), POCKET_TOP]]
        made = s.add_applique(front, half, sewn_lines=4, layer=1, fabric_index=fabric,
                              name="Pocket %s" % ("right" if sign < 0 else "left"), place_flat=False)
        s.place_pattern(made["patches"][0]["pattern_index"], arrangement_index=arrangement(point))
    step("kangaroo pocket: two halves, slanted openings")

    # --- contrast edge stitching on every seam and raw edge, as on the reversible original ---
    # overlock shape and grey thread come from a style saved from CLO (Shape: Overlock); pass it
    # once as template_path, later runs reuse the stored "overlock" template
    saved = os.path.join(os.path.dirname(__file__), "..", "overlock.sst")
    style = s.create_topstitch_style("Overlock edge", thread_thickness_mm=1.0, stitch_length_mm=2.0, offset_mm=1.5,
                                     template="overlock",
                                     template_path=saved if os.path.exists(saved) else None)
    done = s.topstitch_all(style["style_index"])
    step("contrast stitching: %d seams, %d edges%s" % (done["seams"], done["edges"],
                                                         ", failed %s" % done["failed"] if done["failed"] else ""))
    s.save_checkpoint("jacket_arranged")
    for _ in range(6):
        s.simulate(50)
    step("simulated; done in %.0f s" % (time.time() - t0))
    # Known limit: CLO's API only places pieces on arrangement points (no free 3D move), and the
    # sleeves can start partly off the arms, leaving the arm through the armhole seam at the back
    # of the upper arm. A nudge with CLO's Move tool before simulating fixes it.

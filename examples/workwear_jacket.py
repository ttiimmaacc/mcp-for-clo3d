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
UNDERARM_Y = 380.0           # side seam top
SHOULDER_Y = LENGTH - 25.0   # height of the dropped shoulder point
NECK_Y = LENGTH + 20.0       # height of the neck point (shoulder rises to it)
SLIT = 100.0                 # side slits above the hem
SHOULDER_LINE = math.hypot(HALF_SHOULDER - NECK_HALF, NECK_Y - SHOULDER_Y)
SLEEVE = SLEEVE_FROM_COLLAR - SHOULDER_LINE   # dropped shoulder seam to the cuff edge
CUFF_HALF = 165.0            # half the cuff width (33 cm round)

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
    back, back_lines = piece([[-HALF_CHEST, 0], [HALF_CHEST, 0], [HALF_CHEST, SLIT], [HALF_CHEST, UNDERARM_Y],
                              [sx, sy], [NECK_HALF, NECK_Y], [0, LENGTH, 2], [-NECK_HALF, NECK_Y],
                              [-sx, sy], [-HALF_CHEST, UNDERARM_Y], [-HALF_CHEST, SLIT]], "Back")
    neck_front_y = NECK_Y - FRONT_NECK_DROP - 20
    front_r, fr_lines = piece([[-HALF_CHEST, 0], [EXTENSION, 0], [EXTENSION, neck_front_y],
                               [-NECK_HALF * 0.55, neck_front_y + 18, 2], [-NECK_HALF, NECK_Y],
                               [-sx, sy], [-HALF_CHEST, UNDERARM_Y], [-HALF_CHEST, SLIT]], "Front right")
    front_l, fl_lines = piece([[-EXTENSION, 0], [HALF_CHEST, 0], [HALF_CHEST, SLIT], [HALF_CHEST, UNDERARM_Y],
                               [sx, sy], [NECK_HALF, NECK_Y], [NECK_HALF * 0.55, neck_front_y + 18, 2],
                               [-EXTENSION, neck_front_y]], "Front left")
    # shoulders (neck point to shoulder point) and sides (underarm to slit top), right then left
    for front, sign in ((front_r, -1), (front_l, 1)):
        neck, shoulder = [sign * NECK_HALF, NECK_Y], [sign * sx, sy]
        underarm, slit = [sign * HALF_CHEST, UNDERARM_Y], [sign * HALF_CHEST, SLIT]
        m = lambda p: [-p[0], p[1]]      # the same side of the body on the back
        s.sew_edges(back, m(neck), m(shoulder), front, neck, shoulder)
        s.sew_edges(back, m(underarm), m(slit), front, underarm, slit)
    step("body: back and fronts, shoulder and side seams, %d mm slits" % SLIT)

    # --- sleeves: flat dropped-shoulder sleeve; the top's midpoint is the shoulder point ---
    arm = math.hypot(sx - HALF_CHEST, sy - UNDERARM_Y)      # armhole seam length on each body piece
    bs = SLEEVE - CUFF_H
    sleeves = {}
    for side, sign in (("right", -1), ("left", 1)):
        sl, _ = piece([[-CUFF_HALF, 0], [CUFF_HALF, 0], [arm, bs], [0, bs], [-arm, bs]], "Sleeve %s" % side)
        s.sew_edges(sl, [arm, bs], [CUFF_HALF, 0], sl, [-arm, bs], [-CUFF_HALF, 0])      # underarm seam
        # seen from outside the arm, the front of the body is on +x for the right arm, -x for the left
        front = front_r if side == "right" else front_l
        shoulder, underarm = [sign * sx, sy], [sign * HALF_CHEST, UNDERARM_Y]
        s.sew_edges(sl, [0, bs], [-sign * arm, bs], front, shoulder, underarm)
        s.sew_edges(sl, [0, bs], [sign * arm, bs], back, [-shoulder[0], shoulder[1]], [-underarm[0], underarm[1]])
        cf, _ = piece([[-CUFF_HALF, -CUFF_H], [CUFF_HALF, -CUFF_H], [CUFF_HALF, 0], [-CUFF_HALF, 0]], "Cuff %s" % side)
        s.sew_edges(cf, [-CUFF_HALF, 0], [CUFF_HALF, 0], sl, [-CUFF_HALF, 0], [CUFF_HALF, 0])
        s.sew_edges(cf, [CUFF_HALF, -CUFF_H], [CUFF_HALF, 0], cf, [-CUFF_HALF, -CUFF_H], [-CUFF_HALF, 0])
        sleeves[side] = (sl, cf)
    step("sleeves: %.0f mm armhole seams, %d mm to the cuff, %d mm cuffs" % (arm, SLEEVE, CUFF_H))

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
    s.sew_edges(collar, [0, 0], [a, 0], front_r, [EXTENSION, neck_front_y], [-NECK_HALF, NECK_Y])
    s.sew_edges(collar, [a, 0], [a + b, 0], back, [NECK_HALF, NECK_Y], [-NECK_HALF, NECK_Y])
    s.sew_edges(collar, [a + b, 0], [total, 0], front_l, [NECK_HALF, NECK_Y], [-EXTENSION, neck_front_y])
    step("stand collar %.0f x %d mm" % (total, COLLAR_H))

    # --- dress the avatar ---
    # arrangement points are centred on each piece; the _3 points (55 % up the torso) put a
    # 64 cm body piece's neckline at the base of the neck, the _2 points put it at the face
    for p, name in ((back, "Body_Back_Center_3"), (front_r, "Body_Front_3_R"), (front_l, "Body_Front_3_L"),
                    (sleeves["right"][0], "Arm_Outside_1_R"), (sleeves["left"][0], "Arm_Outside_1_L"),
                    (sleeves["right"][1], "Wrist_Outside_R"), (sleeves["left"][1], "Wrist_Outside_L"),
                    (collar, "Neck_Collar")):
        s.place_pattern(p, arrangement_index=arrangement(name))
    step("placed on the avatar")
    s.save_checkpoint("jacket_arranged")
    for _ in range(6):
        s.simulate(50)
    step("simulated; done in %.0f s" % (time.time() - t0))

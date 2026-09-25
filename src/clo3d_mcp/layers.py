"""Pieces that lie on top of another piece and are sewn to it where they sit: hems and appliqué.

CLO's API cannot fold cloth: fold angles on internal lines are stored (pattern JSON) but the
simulation ignores them (tested in CLO 2025.2.236 at 0, 90 and 360 degrees, strength 5-100).
So a hem is modelled as its turned-back layer: a strip covering the hem area on the reverse
layer, sewn along the edge and along the hem line. Nothing has to move into place, so it is
stable from the first step, the same way appliqué patches are.
"""


def signed_area(points):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])) / 2.0


def outline(piece):
    """The piece's outline points in order (the start of each line)."""
    return [tuple(line["start"]) for line in piece["lines"]]


def hem_strip(piece, line_index, width):
    """The hem strip for one straight outline line: its 4 corners (line 0 runs along the edge
    in the edge's direction, line 2 along the hem line in the opposite direction) and the hem
    line's two ends, running the same way as the edge."""
    line = next((l for l in piece["lines"] if l["line_index"] == line_index), None)
    if line is None:
        raise ValueError("no line %d on this piece" % line_index)
    if line["curved"]:
        raise ValueError("line %d is curved; hems need a straight edge" % line_index)
    if width <= 0:
        raise ValueError("width must be positive")
    (ax, ay), (bx, by) = line["start"], line["end"]
    length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if length < 1.0:
        raise ValueError("line %d is too short" % line_index)
    dx, dy = (bx - ax) / length, (by - ay) / length
    # the inside is to the left of each line on a counter-clockwise outline
    nx, ny = (-dy, dx) if signed_area(outline(piece)) > 0 else (dy, -dx)
    a2 = (ax + nx * width, ay + ny * width)
    b2 = (bx + nx * width, by + ny * width)
    corners = [(ax, ay), (bx, by), b2, a2]
    return [[round(x, 3), round(y, 3)] for x, y in corners], [[round(v, 3) for v in a2], [round(v, 3) for v in b2]]

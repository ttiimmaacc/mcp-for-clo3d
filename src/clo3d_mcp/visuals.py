"""Draw the 2D pattern layout from get_pattern_geometry, labelled for line-based tools.

CLO's own 2D snapshot call is undocumented (and its 3D sibling opens a dialog), and a plain
screenshot would not show line indices anyway. This drawing labels every outline line with
its index and colours each seam, so an assistant can pick lines for sewing, elastic etc.
"""

import io

from PIL import Image, ImageDraw, ImageFont

SEAM_COLOURS = [(214, 39, 40), (31, 119, 180), (44, 160, 44), (148, 103, 189), (255, 127, 14),
                (23, 190, 207), (227, 119, 194), (140, 86, 75), (188, 189, 34), (127, 127, 127)]


def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _pieces_points(piece):
    """All drawable polylines of a piece: outline lines and internal-shape lines."""
    lines = [(l, None) for l in piece["lines"]]
    for shape in piece.get("internal_shapes", []):
        lines += [(l, shape["internal_shape"]) for l in shape["lines"]]
    return lines


def _polyline(line):
    if line.get("points"):
        return [(p[0], p[1]) for p in line["points"]]
    return [tuple(line["start"]), tuple(line["end"])]


def render_patterns(summary, size=1400, margin=40):
    """Return PNG bytes of the pieces in `summary` (from geometry.summarize(include_points=True))."""
    pieces = summary["pieces"]
    xs, ys = [], []
    for piece in pieces:
        for line, _ in _pieces_points(piece):
            for x, y in _polyline(line):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError("no pattern pieces to draw")
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    scale = (size - 2 * margin) / span
    width = int((max_x - min_x) * scale + 2 * margin)
    height = int((max_y - min_y) * scale + 2 * margin) + 30

    def to_px(point):  # CLO's 2D y axis points up; images point down
        return (margin + (point[0] - min_x) * scale, margin + (max_y - point[1]) * scale)

    image = Image.new("RGB", (max(width, 300), max(height, 200)), "white")
    draw = ImageDraw.Draw(image)
    small, label = _font(12), _font(16)

    # Seam colour per (pattern, internal shape or None, line)
    seam_of = {}
    for seam in summary.get("seams", []):
        colour = SEAM_COLOURS[seam["seam_index"] % len(SEAM_COLOURS)]
        for side in seam["sides"]:
            for line in side.get("lines", []):
                key = (side.get("pattern_index"), side.get("internal_shape"), line["line_index"])
                seam_of.setdefault(key, (seam["seam_index"], colour))

    for piece in pieces:
        index = piece["pattern_index"]
        outline_px = [to_px(p) for l in piece["lines"] for p in _polyline(l)]
        for line, shape in _pieces_points(piece):
            points = [to_px(p) for p in _polyline(line)]
            seam = seam_of.get((index, shape, line["line_index"]))
            if shape is None:
                colour, width_px = (seam[1], 4) if seam else ((40, 40, 40), 2)
            else:
                colour, width_px = (seam[1], 3) if seam else ((150, 150, 150), 1)
            draw.line(points, fill=colour, width=width_px)
            if shape is None:
                mid = points[len(points) // 2] if len(points) > 2 else (
                    (points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2)
                text = str(line["line_index"]) + ("  s%d" % seam[0] if seam else "")
                draw.text((mid[0] + 3, mid[1] - 7), text, fill=(0, 0, 160), font=small)
        if outline_px:  # name above the piece, clear of the line labels
            left = min(x for x, _ in outline_px)
            top = min(y for _, y in outline_px)
            draw.text((left, max(top - 34, 2)), "#%d %s" % (index, piece.get("name") or ""),
                      fill=(0, 0, 0), font=label)

    draw.text((margin, image.height - 26),
              "Blue numbers = line_index; sN = seam_index; coloured lines are sewn; grey = internal lines",
              fill=(90, 90, 90), font=small)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()

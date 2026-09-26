"""Reading CLO's plot outputs back, to report and check them.

Tested in CLO 2025.2.236:
- ExportSnapshot2D writes a PDF or PNG by the file's extension. The PDF is vector and exactly
  1:1: coordinates are millimetres scaled by 72 / 25.4 points. From the print layout (mode 1)
  the page is the fabric width by the marker length and each piece is one closed path.
- GetFabricLength did not match the laid-out marker (70 mm for a 240 mm marker), so the
  marker length is measured from that PDF instead.
- ExportDXF writes AAMA/ASTM DXF (R12): one block per piece named "<piece>_<size>" holding
  its outline polylines, plus a "BoundingBox_*" block.
"""

import re
import zlib

PT_PER_MM = 72.0 / 25.4


def _streams(pdf):
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        yield raw


def pdf_page_mm(pdf):
    """(width, height) of the first page in mm."""
    m = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", pdf)
    if not m:
        raise ValueError("no page size in the PDF")
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return round((x1 - x0) / PT_PER_MM, 2), round((y1 - y0) / PT_PER_MM, 2)


def pdf_paths(pdf):
    """Closed paths drawn in the PDF (piece outlines), in mm from the page's bottom-left corner.
    CLO draws each piece as "1 0 0 1 tx ty cm" then "x y m", "x y l" ... "h" inside a page-wide
    scale to mm; open paths (grain line arrows, internal lines) are left out."""
    paths = []
    for raw in _streams(pdf):
        text = raw.decode("latin-1").replace("\r", "\n")
        tx = ty = 0.0
        current = []
        for line in text.split("\n"):
            parts = line.split()
            if len(parts) == 7 and parts[-1] == "cm" and parts[:4] == ["1", "0", "0", "1"]:
                tx, ty = float(parts[4]), float(parts[5])
            elif len(parts) == 3 and parts[-1] in ("m", "l"):
                if parts[-1] == "m":
                    current = []
                current.append((float(parts[0]) + tx, float(parts[1]) + ty))
            elif parts[:1] == ["h"] and len(current) >= 3:
                paths.append(current)
                current = []
    return paths


def polygon_area(points):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2.0


def marker_report(pdf, fabric_width_mm=None):
    """Marker size and use from a print-layout PDF: length along the fabric, width, each
    piece's box, and how much of the marker the pieces cover."""
    page_w, page_h = pdf_page_mm(pdf)
    paths = pdf_paths(pdf)
    if not paths:
        raise ValueError("no pieces in the print layout")
    xs = [x for p in paths for x, _ in p]
    ys = [y for p in paths for _, y in p]
    width = fabric_width_mm or page_w
    length = page_h
    area = sum(polygon_area(p) for p in paths)
    return {
        "marker_length_mm": round(length, 1),
        "fabric_width_mm": round(width, 1),
        "pieces": len(paths),
        "pieces_extent_mm": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
        "pieces_area_m2": round(area / 1e6, 4),
        "utilization_percent": round(100.0 * area / (width * length), 1) if width and length else None,
        "outside_fabric_width": max(xs) > width + 0.5 or min(xs) < -0.5,
    }


def dxf_pieces(text):
    """Pieces in an AAMA/ASTM DXF: {block name: {"name", "size", "quantity", "layers", "bbox"}}.
    "layers" maps each AAMA layer number to its polylines (1 = the cut outline, 7 = grain line,
    8 = internal lines, 14 = sewing lines); "bbox" is the outline's box in mm."""
    lines = [l.strip() for l in text.splitlines()]
    pairs = list(zip(lines[0::2], lines[1::2]))
    pieces, block, section = {}, None, None
    entity, layer, poly, x = None, None, None, None
    for code, value in pairs:
        if code == "2" and section is None:
            section = value
            continue
        if code == "0" and value == "ENDSEC":
            section = None
            continue
        if section != "BLOCKS":
            continue
        if code == "0":
            if value == "SEQEND" and poly is not None:
                pieces[block]["layers"].setdefault(poly[0], []).append(poly[1])
                poly = None
            entity, x = value, None
            if value == "BLOCK":
                block = "?"
            elif value == "ENDBLK":
                block = None
            elif value == "POLYLINE" and block not in (None, "?"):
                poly = (layer, [])
            continue
        if code == "2" and block == "?":
            block = value
            pieces[block] = {"layers": {}}
        elif block in (None, "?"):
            continue
        elif code == "8":
            layer = value
            if entity == "POLYLINE" and poly is not None:
                poly = (value, poly[1])
        elif code == "1" and ":" in value:
            key, _, val = value.partition(":")
            key = key.strip().lower()
            if key == "piece name":
                pieces[block]["name"] = val.strip()
            elif key == "size":
                pieces[block]["size"] = val.strip()
            elif key == "quantity":
                pieces[block]["quantity"] = int(val.strip() or 0)
        elif code == "10" and entity == "VERTEX":
            x = float(value)
        elif code == "20" and entity == "VERTEX" and x is not None and poly is not None:
            poly[1].append((x, float(value)))
            x = None
    for entry in pieces.values():
        outline = [pt for poly in entry["layers"].get("1", []) for pt in poly]
        if outline:
            entry["bbox"] = [min(x for x, _ in outline), min(y for _, y in outline),
                             max(x for x, _ in outline), max(y for _, y in outline)]
    return {k: v for k, v in pieces.items() if not k.startswith("BoundingBox")}

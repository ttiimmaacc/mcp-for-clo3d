"""Reading CLO's print-layout PDF and DXF output (synthetic files in CLO's format)."""

import zlib

from clo3d_mcp.plotting import dxf_pieces, marker_report, pdf_page_mm


def _pdf(width_mm, length_mm, pieces):
    pt = 72 / 25.4
    streams = []
    for piece in pieces:
        path = "q\r/G0 gs\r1 0 0 1 0 %s cm\r0.2 0.2 0.2 RG\r" % length_mm
        path += "%s %s m\r" % piece[0] + "".join("%s %s l\r" % p for p in piece[1:]) + "h\rS\r"
        path += "50 -200 m\r50 -150 l\r45 -160 l\rS\rQ\r"  # grain line arrow: open, not a piece
        streams.append(zlib.compress(path.encode()))
    body = b"%%PDF-1.4\n/MediaBox[0 0 %.3f %.3f]\n" % (width_mm * pt, length_mm * pt)
    for data in streams:
        body += b"stream\r\n" + data + b"\r\nendstream\n"
    return body


def test_marker_from_print_layout_pdf():
    # CLO's layout: pieces drawn relative to the top edge (y down from length_mm)
    pdf = _pdf(250, 240, [[(10, -230), (110, -230), (110, -130), (10, -130)],
                          [(10, -110), (210, -110), (210, -10), (10, -10)]])
    assert pdf_page_mm(pdf) == (250.0, 240.0)
    report = marker_report(pdf, 250)
    assert report["marker_length_mm"] == 240.0 and report["pieces"] == 2
    assert report["utilization_percent"] == 50.0 and report["pieces_extent_mm"] == [10.0, 10.0, 210.0, 230.0]
    assert not report["outside_fabric_width"]


def test_dxf_pieces_use_outline_polylines_only():
    rows = ["0", "SECTION", "2", "HEADER", "0", "ENDSEC", "0", "SECTION", "2", "BLOCKS",
            "0", "BLOCK", "8", "1", "2", "Square_M", "70", "0", "10", "0.0", "20", "0.0",
            "0", "POLYLINE", "8", "1", "66", "1"]
    for x, y in ((0, 0), (100, 0), (100, 100), (0, 100)):
        rows += ["0", "VERTEX", "8", "1", "10", str(x), "20", str(y)]
    rows += ["0", "SEQEND", "8", "1", "0", "TEXT", "8", "1", "10", "0.0", "20", "-40.0", "1", "PIECE NAME: Square",
             "0", "TEXT", "8", "1", "10", "0.0", "20", "-40.0", "1", "QUANTITY: 2", "0", "ENDBLK",
             "0", "ENDSEC", "0", "EOF"]
    pieces = dxf_pieces("\n".join(rows))
    assert pieces["Square_M"]["name"] == "Square" and pieces["Square_M"]["quantity"] == 2
    assert pieces["Square_M"]["bbox"] == [0.0, 0.0, 100.0, 100.0]

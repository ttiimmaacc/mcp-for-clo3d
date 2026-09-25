"""view_patterns drawing (synthetic geometry, no CLO needed)."""

import io

from PIL import Image

from clo3d_mcp.geometry import summarize
from clo3d_mcp.visuals import render_patterns
from test_geometry import _result, _side  # pytest puts tests/ on sys.path


def test_renders_pieces_and_seams_as_png():
    summary = summarize(_result([(_side("A", 0.25, 0.5, True), _side("B", 0.75, 0.5, False))]),
                        include_points=True)
    png = render_patterns(summary)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width >= 300 and image.height >= 200
    # sewn lines are drawn in colour, not only black/grey
    colours = {c for _, c in image.convert("RGB").getcolors(1 << 20)}
    assert any(abs(r - g) > 60 or abs(g - b) > 60 for r, g, b in colours)


def test_single_piece():
    summary = summarize(_result([]), pattern_index=1, include_points=True)
    assert Image.open(io.BytesIO(render_patterns(summary))).format == "PNG"

"""Plaid texture images."""

import pytest

from clo3d_mcp.textures import plaid, save_with_size


def test_plaid_repeat_size_and_colours():
    image = plaid([("#000000", 10), ("#ffffff", 5)], px_per_mm=2)
    assert image.size == (30, 30)
    assert image.getpixel((0, 0)) == (0, 0, 0)          # black crosses black
    assert image.getpixel((25, 25)) == (255, 255, 255)  # white crosses white
    mixed = {image.getpixel((x, 25)) for x in range(20)}
    assert mixed == {(0, 0, 0), (255, 255, 255)}        # twill: both colours where they cross


def test_average_mixing_and_bad_input():
    image = plaid([("#000000", 1), ("#ffffff", 1)], px_per_mm=1, twill=False)
    assert image.getpixel((1, 0)) == (127, 127, 127)
    with pytest.raises(ValueError):
        plaid([])
    with pytest.raises(ValueError):
        plaid([("#000000", 0)])


def test_dpi_sets_the_repeat(tmp_path):
    from PIL import Image
    path = tmp_path / "p.png"
    dpi = save_with_size(plaid([("#000000", 10), ("#ffffff", 10)], px_per_mm=4), path, 20)
    assert round(dpi, 1) == 101.6  # 80 px for 20 mm
    assert round(Image.open(path).info["dpi"][0], 1) == 101.6

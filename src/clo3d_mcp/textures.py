"""Fabric texture images made from a description, such as plaids and stripes.

A plaid (tartan) is a sett: a sequence of coloured stripes, woven the same way in both
directions. Where a warp stripe crosses a weft stripe of the same colour the colour is solid;
where two different colours cross, a twill weave shows them mixed, drawn here as fine diagonal
lines so the checks read like woven cloth rather than flat blocks.
"""

from PIL import Image


def _hex(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def plaid(sett, px_per_mm=4.0, twill=True):
    """An RGB image of one repeat of a plaid.

    Args:
        sett: [(colour, width_mm), ...] stripes across one repeat, e.g.
            [("#1c1c1e", 40), ("#6b6b6e", 30), ("#9a9a9c", 2), ("#6b6b6e", 30)].
        px_per_mm: Resolution; the image is sum(widths) * px_per_mm pixels square.
        twill: Mix crossing colours along diagonals (as woven), or average them.
    """
    if not sett:
        raise ValueError("a plaid needs at least one stripe")
    stripes = []
    for colour, width in sett:
        if width <= 0:
            raise ValueError("stripe widths must be positive")
        stripes += [_hex(colour)] * max(1, round(width * px_per_mm))
    size = len(stripes)
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        weft = stripes[y]
        for x in range(size):
            warp = stripes[x]
            if warp == weft:
                pixels[x, y] = warp
            elif twill:
                pixels[x, y] = warp if (x + y) // 2 % 2 == 0 else weft
            else:
                pixels[x, y] = tuple((a + b) // 2 for a, b in zip(warp, weft))
    return image


def save_with_size(image, path, repeat_mm):
    """Save the image with its DPI set so one image equals repeat_mm."""
    dpi = image.width / (repeat_mm / 25.4)
    image.save(path, dpi=(dpi, dpi))
    return dpi

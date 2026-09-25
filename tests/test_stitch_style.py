"""Topstitch style files: patching values in a synthetic .sst (header + zip)."""

import io
import struct
import zipfile

import pytest

from clo3d_mcp.stitch_style import make_style, read_values


def _template():
    blob = (b"xx" + b"fStitchLength" + struct.pack("<f", 1.8) + b"fThreadThickness" + struct.pack("<f", 0.2)
            + b"fStitchLength" + struct.pack("<f", 3.0) + b"fAddtionalOffset" + struct.pack("<f", 1.5875)
            + b"iStitchOffsetIndexVer100" + struct.pack("<i", 3))
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Style.sst.tmp", blob)
        z.writestr("clofiles.json", "{}")
    header = bytearray(64)
    struct.pack_into("<II", header, 32, len(header), len(zipped.getvalue()))
    return bytes(header) + zipped.getvalue()


def test_patches_first_stitch_and_offset_and_fixes_the_header():
    new = make_style(_template(), stitch_length=4.0, thread_thickness=0.4, offset=4.0)
    assert read_values(new) == (4.0, 0.4, 4.0)
    start, length = struct.unpack_from("<II", new, 32)
    assert start + length == len(new)
    data = zipfile.ZipFile(io.BytesIO(new)).read("Style.sst.tmp")
    assert struct.unpack_from("<i", data, data.find(b"iStitchOffsetIndexVer100") + 24)[0] == 0
    assert data.count(struct.pack("<f", 3.0)) == 1  # the unused second stitch is untouched


def test_unchanged_values_stay_and_bad_input_is_refused():
    assert read_values(make_style(_template(), thread_thickness=0.4)) == (1.8, 0.4, 1.5875)
    with pytest.raises(ValueError):
        make_style(_template(), stitch_length=0)
    with pytest.raises(ValueError, match="saved from CLO"):
        make_style(b"not a style" * 10, stitch_length=2)

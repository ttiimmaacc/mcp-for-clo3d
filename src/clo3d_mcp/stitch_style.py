"""Topstitch styles with a given stitch length and thread thickness.

CLO's API can import a topstitch style (.sst) but cannot set its stitch length or thread
thickness. An .sst saved from CLO's Property Editor is a CLO header followed by a zip; the
zip holds a binary key/value file in which the first stitch's values follow the names
"fStitchLength" and "fThreadThickness" as little-endian floats (mm; CLO shows thread
thickness as Tex, 200 Tex per mm), and the style's offset from the line follows
"fAddtionalOffset" (sic) with its preset in "iStitchOffsetIndexVer100" (3 = 1/16"). A new
style is that template with those values patched, the zip rebuilt and the header's zip
length updated.
"""

import io
import struct
import zipfile

ZIP_START, ZIP_LENGTH = 32, 36  # header offsets of the zip's start and length (uint32)


def _patch_first(data, key, value, fmt="<f"):
    at = data.find(key)
    if at < 0:
        raise ValueError("the template has no %s value" % key.decode())
    at += len(key)
    return data[:at] + struct.pack(fmt, value) + data[at + 4:], struct.unpack(fmt, data[at:at + 4])[0]


def read_values(sst):
    """(stitch_length, thread_thickness, offset) of an .sst file's bytes (first stitch)."""
    with zipfile.ZipFile(io.BytesIO(sst)) as z:
        name = next(n for n in z.namelist() if n.endswith(".sst.tmp"))
        data = z.read(name)
    values = []
    for key in (b"fStitchLength", b"fThreadThickness", b"fAddtionalOffset"):
        at = data.find(key) + len(key)
        values.append(round(struct.unpack("<f", data[at:at + 4])[0], 4))
    return tuple(values)


def make_style(template, stitch_length=None, thread_thickness=None, offset=None):
    """Bytes of a new .sst: the template with the first stitch's length and thread thickness
    and the style's offset (all mm) replaced; None keeps the template's value."""
    if any(v is not None and v <= 0 for v in (stitch_length, thread_thickness)) or (offset is not None and offset < 0):
        raise ValueError("stitch length and thread thickness must be positive, offset not negative")
    start, length = struct.unpack_from("<II", template, ZIP_START)
    if start + length != len(template) or template[start:start + 4] != b"PK\x03\x04":
        raise ValueError("not a topstitch style (.sst) saved from CLO")
    out = io.BytesIO()
    out.write(template[:start])
    with zipfile.ZipFile(io.BytesIO(template[start:])) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(".sst.tmp"):
                if stitch_length is not None:
                    data, _ = _patch_first(data, b"fStitchLength", stitch_length)
                if thread_thickness is not None:
                    data, _ = _patch_first(data, b"fThreadThickness", thread_thickness)
                if offset is not None:
                    data, _ = _patch_first(data, b"fAddtionalOffset", offset)
                    data, _ = _patch_first(data, b"iStitchOffsetIndexVer100", 0, "<i")  # not a preset
            dst.writestr(info, data)
    result = bytearray(out.getvalue())
    struct.pack_into("<I", result, ZIP_LENGTH, len(result) - start)
    return bytes(result)

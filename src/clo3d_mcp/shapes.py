"""Generate simple collision meshes (OBJ, millimetres, CLO's 3D axes: Y up, Z towards the viewer)."""

import math

AXES = {"x": 0, "y": 1, "z": 2}


def cylinder_obj(center, length, diameter, axis="x", segments=32):
    """OBJ text for a capped cylinder centred on `center`, running along `axis`."""
    if axis not in AXES:
        raise ValueError("axis must be x, y or z")
    if length <= 0 or diameter <= 0:
        raise ValueError("length and diameter must be positive")
    a = AXES[axis]
    u, v = [i for i in range(3) if i != a]  # the two axes across the rod
    radius = diameter / 2.0
    verts = []
    for end in (-0.5, 0.5):
        for s in range(segments):
            t = 2 * math.pi * s / segments
            p = list(center)
            p[a] += end * length
            p[u] += radius * math.cos(t)
            p[v] += radius * math.sin(t)
            verts.append(p)
    for end in (-0.5, 0.5):  # cap centres
        p = list(center)
        p[a] += end * length
        verts.append(p)
    lines = ["# rod: length %g mm, diameter %g mm, axis %s" % (length, diameter, axis)]
    lines += ["v %.4f %.4f %.4f" % tuple(p) for p in verts]
    n = segments
    c0, c1 = 2 * n + 1, 2 * n + 2  # 1-based cap centre indices
    for s in range(n):
        a0, a1 = s + 1, (s + 1) % n + 1
        b0, b1 = a0 + n, a1 + n
        lines.append("f %d %d %d %d" % (a0, a1, b1, b0))
        lines.append("f %d %d %d" % (c0, a1, a0))
        lines.append("f %d %d %d" % (c1, b0, b1))
    return "\n".join(lines) + "\n"


def combine_obj(parts):
    """One OBJ holding several meshes (CLO keeps a single avatar, so objects share one mesh)."""
    out, offset = ["# combined collision objects"], 0
    for text in parts:
        verts = [l for l in text.splitlines() if l.startswith("v ")]
        out += verts
        for line in text.splitlines():
            if line.startswith("f "):
                out.append("f " + " ".join(str(int(i) + offset) for i in line.split()[1:]))
        offset += len(verts)
    return "\n".join(out) + "\n"


def part_obj(part):
    """OBJ text for a stored part: {"kind": "rod"|"box", ...}."""
    if part["kind"] == "rod":
        return cylinder_obj(part["center"], part["length"], part["diameter"], part.get("axis", "x"))
    if part["kind"] == "box":
        return box_obj(part["center"], part["size"])
    raise ValueError("unknown part kind %r" % part["kind"])


def box_obj(center, size):
    """OBJ text for an axis-aligned box with the given [x, y, z] size."""
    if any(s <= 0 for s in size):
        raise ValueError("box size must be positive")
    h = [s / 2.0 for s in size]
    verts = [[center[0] + sx * h[0], center[1] + sy * h[1], center[2] + sz * h[2]]
             for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    faces = [(1, 3, 4, 2), (5, 6, 8, 7), (1, 2, 6, 5), (3, 7, 8, 4), (1, 5, 7, 3), (2, 4, 8, 6)]
    lines = ["# box %g x %g x %g mm" % tuple(size)]
    lines += ["v %.4f %.4f %.4f" % tuple(p) for p in verts]
    lines += ["f %d %d %d %d" % f for f in faces]
    return "\n".join(lines) + "\n"

"""Generated collision meshes are valid, closed and where they were asked to be."""

import pytest

from clo3d_mcp.shapes import box_obj, cylinder_obj


def _parse(text):
    verts = [tuple(map(float, l.split()[1:])) for l in text.splitlines() if l.startswith("v ")]
    faces = [tuple(int(i) for i in l.split()[1:]) for l in text.splitlines() if l.startswith("f ")]
    return verts, faces


def _closed(faces):
    edges = {}
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[(a, b)] = edges.get((a, b), 0) + 1
    # every directed edge appears once and its reverse once: watertight and consistently wound
    return all(edges.get((b, a)) == 1 for (a, b) in edges)


def test_cylinder_extent_and_closed():
    verts, faces = _parse(cylinder_obj([10, 1500, -40], 1200, 25, "x", segments=16))
    xs, ys, zs = zip(*verts)
    assert min(xs) == pytest.approx(10 - 600) and max(xs) == pytest.approx(10 + 600)
    assert max(ys) - min(ys) == pytest.approx(25, abs=0.01)
    assert max(zs) - min(zs) == pytest.approx(25, abs=0.2)
    assert all(1 <= i <= len(verts) for f in faces for i in f)
    assert _closed(faces)


def test_box_extent_and_closed():
    verts, faces = _parse(box_obj([0, 400, 0], [800, 20, 500]))
    xs, ys, zs = zip(*verts)
    assert (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)) == (-400, 400, 390, 410, -250, 250)
    assert _closed(faces)


def test_bad_input():
    with pytest.raises(ValueError):
        cylinder_obj([0, 0, 0], 100, 10, axis="w")
    with pytest.raises(ValueError):
        box_obj([0, 0, 0], [1, 0, 1])


def test_combined_mesh_keeps_each_part_closed_and_indexed():
    from clo3d_mcp.shapes import combine_obj, part_obj
    rod = {"kind": "rod", "center": [0, 1500, 180], "length": 1000, "diameter": 30}
    box = {"kind": "box", "center": [0, 400, 0], "size": [800, 20, 500]}
    verts, faces = _parse(combine_obj([part_obj(rod), part_obj(box)]))
    n_rod = len(_parse(part_obj(rod))[0])
    assert len(verts) == n_rod + 8
    assert all(1 <= i <= len(verts) for f in faces for i in f)
    assert _closed(faces)
    assert max(i for f in faces[-6:] for i in f) == len(verts)  # box faces point at box vertices

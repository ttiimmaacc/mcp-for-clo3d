"""Map CLO's flat cloth vertex list (GetClothPositions) onto patterns.

GetClothPositions returns every cloth vertex in one list. Probing CLO 2025.2.236 showed the
list is ordered pattern by pattern, but each pattern's slice is slightly shorter than the
vertex count GetMeshCountByType reports (12,231 vertices listed vs 12,343 counted for 18
pieces). So the real slice boundaries are found while the cloth is still flat: a vertex then
lies inside its own piece's 2D outline, on the side of the panel given by its layer.
"""

EDGE = 15.0  # mm; vertices this close to an outline count as on it (seams pull edges a little)


def vertex_count(mesh_count):
    """The vertex count from one GetMeshCountByType map (key names vary; match 'vertex')."""
    for key, value in mesh_count.items():
        if "vert" in key.lower():
            try:
                return int(float(value))
            except (TypeError, ValueError):
                pass
    raise ValueError("no vertex count in CLO's mesh info %r" % (mesh_count,))


def vertex_ranges(counts):
    """[(first, count), ...] if CLO's counts add up exactly (then the slices follow directly)."""
    sizes = [vertex_count(m) for m in counts["patterns"]]
    total = int(counts["total_vertices"])
    if sum(sizes) != total:
        raise ValueError("pattern vertex counts add up to %d but CLO reports %d cloth vertices"
                         % (sum(sizes), total))
    ranges, first = [], 0
    for size in sizes:
        ranges.append((first, size))
        first += size
    return ranges


def _inside(point, polygon):
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def _edge_distance(point, polygon):
    x, y = point
    best = float("inf")
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ((dx * dx + dy * dy) or 1.0)))
        best = min(best, ((x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2) ** 0.5)
    return best


def _z_matches(z, layer, plane_z, separated):
    if not separated:
        return True
    if layer < 0:
        return z < plane_z - 1.0
    if layer > 0:
        return z > plane_z + 1.0
    return abs(z - plane_z) <= 2.5


def belongs(vertex, polygon, layer, plane_z, separated, strict=False):
    """Is this (flat-state) vertex part of the piece with this outline and layer?"""
    xy = (vertex[0], vertex[1])
    if not _z_matches(vertex[2], layer, plane_z, separated):
        return False
    if strict:  # clearly inside, not on a shared edge
        return _inside(xy, polygon) and _edge_distance(xy, polygon) > EDGE
    return _inside(xy, polygon) or _edge_distance(xy, polygon) <= EDGE


def segment(vertex_at, polygons, layers, estimates, total, plane_z, separated):
    """Contiguous (first, count) slices, one per pattern, found by binary search around the
    boundaries CLO's mesh counts suggest. vertex_at(k) returns the k-th cloth vertex [x, y, z]."""
    n = len(polygons)
    starts = [0]
    for i in range(n - 1):
        lo = starts[-1] + 1
        hi = min(total, starts[-1] + estimates[i] + 400)

        def still_i(k, i=i):
            v = vertex_at(k)
            return (belongs(v, polygons[i], layers[i], plane_z, separated)
                    and not belongs(v, polygons[i + 1], layers[i + 1], plane_z, separated, strict=True))

        # smallest k in [lo, hi) where the vertex no longer belongs to pattern i
        while lo < hi:
            mid = (lo + hi) // 2
            if still_i(mid):
                lo = mid + 1
            else:
                hi = mid
        starts.append(lo)
    starts.append(total)
    ranges = [(starts[i], starts[i + 1] - starts[i]) for i in range(n)]
    for i, (_, count) in enumerate(ranges):
        if count <= 0 or abs(count - estimates[i]) > max(40, 0.25 * estimates[i]):
            raise ValueError("could not map cloth vertices to pattern %d (found %d, CLO counts %d); "
                             "run this while the cloth is still flat" % (i, count, estimates[i]))
    return ranges

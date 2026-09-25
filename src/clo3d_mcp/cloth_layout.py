"""Map CLO's flat cloth vertex list (GetClothPositions) onto patterns.

GetClothPositions returns every cloth vertex in one list. Assuming CLO lists patterns in order,
each pattern's vertices are a contiguous slice whose length is its mesh vertex count
(GetMeshCountByType). That assumption is only used when the counts add up exactly to the
total; it was checked in CLO 2025.2.236 against each piece's 2D outline on a flat layout.
"""


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
    """[(first, count), ...] per pattern from the plug-in's get_mesh_counts result."""
    sizes = [vertex_count(m) for m in counts["patterns"]]
    total = int(counts["total_vertices"])
    if sum(sizes) != total:
        raise ValueError("pattern vertex counts add up to %d but CLO reports %d cloth vertices; "
                         "per-pattern bounds are not available for this scene" % (sum(sizes), total))
    ranges, first = [], 0
    for size in sizes:
        ranges.append((first, size))
        first += size
    return ranges

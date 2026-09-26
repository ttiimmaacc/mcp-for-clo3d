"""
CLO3D MCP Server: FastMCP server exposing CLO3D tools to LLMs.

Bridges Claude/Cursor and CLO3D via the Model Context Protocol.
Communicates with the native CLO plug-in (cpp_plugin/) through a shared file directory.
"""

import json
import os
import re
import time

from mcp.server.fastmcp import FastMCP, Image
from clo3d_mcp.connection import get_connection, CLO3DConnectionError
from clo3d_mcp.geometry import summarize
from clo3d_mcp.visuals import render_patterns

mcp = FastMCP(
    "clo3d",
    instructions="Control CLO3D — the industry-standard 3D garment design software. "
    "Create patterns, manage fabrics, run simulations, export 3D models, and more. "
    "Lines are addressed by index: call get_pattern_geometry (or view_patterns for a labelled "
    "picture) to see each piece's numbered lines, internal shapes and seams before sewing, "
    "adding elastic, topstitch or seam taping. Pattern coordinates are millimetres in the 2D "
    "window. CLO has no undo: call save_checkpoint before risky edits. After simulating, "
    "look at the result with capture_3d (optionally with fit_map='strain') before continuing.",
)


def _send(command: str, params: dict | None = None) -> dict:
    """Send a command to CLO3D and return the result."""
    conn = get_connection()
    return conn.send_command(command, params)


def _given(**params) -> dict:
    """Drop parameters the caller left unset."""
    return {k: v for k, v in params.items() if v is not None}


def _work_dir(name: str) -> str:
    """A folder next to the request files (CLO and the server both see it)."""
    path = os.path.join(get_connection().comm_dir, name)
    os.makedirs(path, exist_ok=True)
    return path


# CLO's camera presets (SDK: SetCamViewPoint)
CAMERA_VIEWS = {
    "front": 2, "back": 8, "left": 6, "right": 4, "three_quarter_left": 3,
    "three_quarter_right": 1, "top": 5, "bottom": 0, "zoom_all": 9, "current": -1,
}


# ─── See the Result ────────────────────────────────────────────────────────


@mcp.tool()
def capture_3d(views: list[str] | None = None, fit_map: str | None = None) -> list:
    """Look at the garment: capture CLO's 3D window from one or more camera views.

    Use after simulating or editing to check the result (e.g. twisted seams, pieces in the
    wrong place, fabric through the avatar). Moves CLO's 3D camera.

    Args:
        views: Any of front, back, left, right, three_quarter_left, three_quarter_right, top,
            bottom, zoom_all, current (default: ["front"]).
        fit_map: "strain" or "stress" to show CLO's fit map in the capture (red = tight or
            overstretched), "off" to hide it, or omit to leave the display as it is.
    """
    views = views or ["front"]
    unknown = [v for v in views if v not in CAMERA_VIEWS]
    if unknown:
        raise ValueError("unknown view(s) %s; use %s" % (unknown, ", ".join(CAMERA_VIEWS)))
    if fit_map is not None:
        _send("set_fit_map", {"mode": fit_map})
    folder = _work_dir("captures")
    content = []
    for view in views:
        path = os.path.join(folder, "%s_%d.png" % (view, int(time.time() * 1000)))
        result = _send("capture_3d", {"camera": CAMERA_VIEWS[view], "file_path": path})
        written = result["file_path"]
        with open(written, "rb") as f:
            data = f.read()
        content.append(Image(data=data, format=os.path.splitext(written)[1].lstrip(".").lower() or "png"))
        content.append("view: %s" % view)
    return content


@mcp.tool()
def set_fit_map(mode: str) -> dict:
    """Show CLO's fit map in the 3D window: "strain" (stretch %), "stress" (pressure) or "off"."""
    return _send("set_fit_map", {"mode": mode})


@mcp.tool()
def view_patterns(pattern_index: int | None = None) -> list:
    """Picture of the 2D pattern pieces with every outline line numbered and seams coloured.

    Blue numbers are line_index values for sewing, elastic, topstitch and seam taping; "sN"
    marks lines sewn by seam N; grey lines are internal lines. Pieces on another layer
    (appliqué, hem strips) lie on their base piece and are labelled with their layer, +z or -z.
    Pair it with get_pattern_geometry for exact lengths and coordinates.

    Args:
        pattern_index: Draw only this piece (default: all pieces).
    """
    summary = summarize(_send("get_pattern_geometry"), pattern_index, include_points=True)
    for piece in summary["pieces"]:
        piece["layer"] = _send("get_pattern_state", {"pattern_index": piece["pattern_index"]}).get("layer", 0)
    return [Image(data=render_patterns(summary), format="png"),
            "%d pieces, %d seams" % (len(summary["pieces"]), len(summary["seams"]))]


# ─── Collision Objects (rods, rails, props) ────────────────────────────────


@mcp.tool()
def make_rod_pocket(
    pattern_index: int | list[int],
    pocket_depth: float = 150.0,
    rod_diameter: float = 30.0,
    rod_overhang: float = 200.0,
    settle_steps: int = 150,
    pocket_side: str = "front",
) -> dict:
    """Make a rod pocket along a panel's top edge and hang the panel on a rod (curtains,
    banners, flags). The panel needs a straight, horizontal top edge. Verified with one panel;
    with several panels a pocket often fails to wrap, so prefer hang_on_rod there.

    Adds a pocket strip above the panel, sews it to the top edge and to a fold line, holds the
    panel still, places a rod just in front of the strip above the top edge (the folding strip
    wraps it; verified in CLO 2025.2) and lets the panel hang. Returns whether the cloth stays
    up at the ends and in the middle; the pocket can be fuller in places, so look at the result
    with capture_3d. Save a checkpoint first.

    Args:
        pattern_index: The panel, or a list of panels sharing one rod (e.g. the two halves of a
            noren); their top edges must be at the same height.
        pocket_depth: Height of the pocket strip in mm (the tube is roughly 1.4x this around).
        rod_diameter: Rod diameter in mm.
        rod_overhang: How far the rod extends past each side of the panel, in mm.
        settle_steps: Simulation steps after releasing the panel.
        pocket_side: Only "front" (+z, towards the front camera) forms reliably in CLO 2025.2.
            For a curtain whose pocket belongs on the back, build it facing the "back" camera
            (decorative patches on layer -1). Keep patches on the pocket side below fold_y.
    """
    from clo3d_mcp.rod_pocket import make_rod_pocket as build

    def send(command, params=None):
        if command == "__add_rod__":
            return add_rod(params["center"], params["length"], params["diameter"], "x")
        if command == "__remove_object__":
            return remove_collision_object(params["index"])
        if command == "__map_cloth__":
            return map_cloth_to_patterns()
        if command == "__pattern_bounds__":
            return get_cloth_bounds(params.get("min"), params.get("max"), params["pattern_index"])
        return _send(command, params)

    return build(send, pattern_index, pocket_depth, rod_diameter, rod_overhang, settle_steps,
                 pocket_side=pocket_side)


@mcp.tool()
def hang_on_rod(
    pattern_index: int | list[int],
    band_height: float = 60.0,
    rod_diameter: float = 30.0,
    rod_overhang: float = 200.0,
    settle_steps: int = 150,
    rod_side: str = "front",
    fabric_index: int | None = None,
) -> dict:
    """Hang panels on a rod from a frozen header band: the reliable way to hang curtains,
    noren and banners (make_rod_pocket's folded pocket is not reliable with several panels).

    Adds a band above each panel's straight top edge, sews it on, freezes it flat (it acts like
    a pin line) and lays a rod along it; the panels drape from the bands. The pocket does not
    wrap the rod in 3D: the rod lies against the band's rod_side face, so from the other side it
    looks like a rod pocket with the rod ends showing. Save a checkpoint first.

    Args:
        pattern_index: The panel, or a list of panels sharing one rod; their top edges must be
            at the same height.
        band_height: Height of the band in mm, added above the top edge (so make the panels
            that much shorter for a given finished height). At least rod_diameter + 10.
        rod_diameter: Rod diameter in mm.
        rod_overhang: How far the rod extends past each side, in mm.
        settle_steps: Simulation steps to let the panels hang.
        rod_side: "front" (+z, towards the front camera) or "back" (-z).
        fabric_index: Fabric for the bands (default: each panel's own fabric).
    """
    from clo3d_mcp.rod_pocket import hang_on_rod as build

    def send(command, params=None):
        if command == "__add_rod__":
            return add_rod(params["center"], params["length"], params["diameter"], "x")
        return _send(command, params)

    return build(send, pattern_index, band_height, rod_diameter, rod_overhang, settle_steps,
                 rod_side=rod_side, fabric_index=fabric_index)


@mcp.tool()
def get_cloth_bounds(region_min: list[float] | None = None, region_max: list[float] | None = None,
                     pattern_index: int | None = None) -> dict:
    """3D bounding box (mm) of the cloth: min/max [x, y, z]. CLO's 3D axes: Y is up, Z points
    towards the front view camera, X to the avatar's left. Use it to place rods and props and
    to check where a piece ended up (e.g. is this panel still on the rod?).

    CLO only reports cloth once it has been simulated, and reports nothing while every piece
    is frozen: run simulate(1) with at least one piece unfrozen first. A flat new piece lies
    at its 2D (x, y) coordinates in the plane z = 200.

    Args:
        region_min: Only count vertices inside this box: [x, y, z] lower corner.
        region_max: Upper corner [x, y, z] of that box.
        pattern_index: Only this pattern's vertices.
    """
    params = _given(min=region_min, max=region_max)
    if pattern_index is not None:
        params["vertex_range"] = list(_pattern_vertex_range(pattern_index))
    return _send("get_cloth_bounds", params)


# Pattern -> cloth vertex slice, found while the cloth was flat; valid while CLO's mesh is
# unchanged (same per-pattern counts and total).
_layout = {"key": None, "ranges": None}


def _mesh_key(counts):
    from clo3d_mcp.cloth_layout import vertex_count
    return (tuple(vertex_count(c) for c in counts["patterns"]), int(counts["total_vertices"]))


@mcp.tool()
def map_cloth_to_patterns() -> dict:
    """Work out which cloth vertices belong to which pattern, for get_cloth_bounds(pattern_index).
    Run while the pieces are still flat (after a few simulation steps, before draping).

    CLO lists cloth vertices pattern by pattern, but its per-pattern counts are slightly off, so
    the boundaries are found from the flat layout. Freezing or unfreezing pieces reorders CLO's
    list (unfrozen pieces first) and changes the mesh; the map is then refused, so map again in
    the new state."""
    from clo3d_mcp.cloth_layout import segment, vertex_count, vertex_ranges
    counts = _send("get_mesh_counts")
    for _ in range(10):  # CLO reports positions only after a few steps (also after a reset)
        if counts["total_vertices"]:
            break
        _send("simulate", {"steps": 2})
        counts = _send("get_mesh_counts")
    key = _mesh_key(counts)
    try:
        ranges = vertex_ranges(counts)
    except ValueError:
        pieces = summarize(_send("get_pattern_geometry"), include_points=True)["pieces"]
        polygons = [[tuple(pt[:2]) for line in p["lines"] for pt in line["points"][:-1]] for p in pieces]
        layers = [_send("get_pattern_state", {"pattern_index": p["pattern_index"]})["layer"] for p in pieces]
        cache = {}

        def vertex_at(k):
            if k not in cache:
                cache[k] = _send("get_cloth_bounds", {"vertex_range": [k, 1]})["min"]
            return cache[k]

        base = [i for i, layer in enumerate(layers) if layer == 0]
        plane_z = vertex_at(0)[2] if base else 200.0
        # layers only separate in depth once CLO has pushed them apart; else ignore depth
        separated = any(abs(vertex_at(k)[2] - plane_z) > 1.0 for k in range(0, key[1], max(1, key[1] // 40)))
        ranges = segment(vertex_at, polygons, layers, [vertex_count(c) for c in counts["patterns"]],
                         key[1], plane_z, separated)
    _layout.update(key=key, ranges=ranges)
    return {"patterns": len(ranges), "vertices": key[1], "ranges": ranges}


def _pattern_vertex_range(pattern_index: int):
    from clo3d_mcp.cloth_layout import vertex_ranges
    counts = _send("get_mesh_counts")
    if _layout["key"] == _mesh_key(counts):
        ranges = _layout["ranges"]
    else:
        try:
            ranges = vertex_ranges(counts)
        except ValueError:
            raise ValueError("CLO's cloth vertices are not mapped to patterns for the current mesh; "
                             "call map_cloth_to_patterns while the pieces are still flat") from None
    if not 0 <= pattern_index < len(ranges):
        raise ValueError("no pattern %d" % pattern_index)
    return ranges[pattern_index]


# CLO keeps one avatar: importing an OBJ as an avatar replaces the previous one (verified in
# 2025.2.236). So all rods and boxes live in one mesh, re-imported whenever a part changes.
OBJECTS_MESH = "mcp_objects"


def _objects_file(name):
    return os.path.join(_work_dir("objects"), name)


def _load_parts(replace_avatar: bool) -> list:
    """The current parts, checked against what is actually in CLO's scene."""
    avatars = _send("get_avatars")
    if avatars["count"] == 0:
        return []
    names = [a.get("name") for a in avatars["avatars"]]
    if names == [OBJECTS_MESH]:
        try:
            with open(_objects_file("parts.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []
    if replace_avatar:
        return []
    raise ValueError("the scene has the avatar %s. CLO keeps one avatar, so adding a collision "
                     "object would replace it; pass replace_avatar=True to do that." % names)


def _apply_parts(parts: list) -> dict:
    from clo3d_mcp.shapes import combine_obj, part_obj
    with open(_objects_file("parts.json"), "w", encoding="utf-8") as f:
        json.dump(parts, f, indent=1)
    if not parts:
        if _send("get_avatars")["count"]:
            _send("delete_objects", {"indices": [0]})
        return {"objects": []}
    path = _objects_file(OBJECTS_MESH + ".obj")
    with open(path, "w") as f:
        f.write(combine_obj([part_obj(p) for p in parts]))
    result = _send("import_obj", {"file_path": path, "object_type": 0})
    if not result.get("imported"):
        raise RuntimeError("CLO did not import the collision mesh")
    return {"objects": [dict(p, index=i) for i, p in enumerate(parts)]}


def _add_part(part: dict, replace_avatar: bool) -> dict:
    parts = _load_parts(replace_avatar) + [part]
    result = _apply_parts(parts)
    result["added_index"] = len(parts) - 1
    return result


@mcp.tool()
def add_rod(center: list[float], length: float, diameter: float = 25.0, axis: str = "x",
            replace_avatar: bool = False) -> dict:
    """Add a cylinder (curtain rod, rail, hanger bar...) that cloth collides with.

    All rods and boxes form one collision object (CLO keeps a single avatar), placed exactly
    at the given coordinates. For curtains, make_rod_pocket does the whole job. Tested in CLO
    2025.2: cloth passes through a thin rod unless its mesh is finer than the rod, so set the
    pieces' particle_distance to about a third of the diameter and use >= 30 mm.

    Args:
        center: [x, y, z] centre in mm (see get_cloth_bounds for where the cloth is).
        length: Length in mm.
        diameter: Diameter in mm.
        axis: Direction of the rod: "x" (left-right), "y" (up-down) or "z" (front-back).
        replace_avatar: Allow replacing a human avatar in the scene.
    """
    return _add_part({"kind": "rod", "center": center, "length": length, "diameter": diameter,
                      "axis": axis}, replace_avatar)


@mcp.tool()
def add_box(center: list[float], size: list[float], replace_avatar: bool = False) -> dict:
    """Add a box that cloth collides with (a table, shelf, bed or window sill...).

    Args:
        center: [x, y, z] centre in mm.
        size: [width_x, height_y, depth_z] in mm.
        replace_avatar: Allow replacing a human avatar in the scene.
    """
    return _add_part({"kind": "box", "center": center, "size": size}, replace_avatar)


@mcp.tool()
def list_collision_objects() -> dict:
    """The rods and boxes currently in the scene, with the index remove_collision_object takes."""
    return {"objects": [dict(p, index=i) for i, p in enumerate(_load_parts(False))]}


@mcp.tool()
def remove_collision_object(index: int) -> dict:
    """Remove one rod or box (index from list_collision_objects); the others stay."""
    parts = _load_parts(False)
    if not 0 <= index < len(parts):
        raise ValueError("no collision object %d; there are %d" % (index, len(parts)))
    del parts[index]
    return _apply_parts(parts)


@mcp.tool()
def import_collision_object(file_path: str, keep_position: bool = True, scale: float = 1.0) -> dict:
    """Import your own OBJ (e.g. a modelled curtain rail or furniture) as an object cloth
    collides with. Units are mm unless scale is set (e.g. 10 for a model in cm). It replaces
    the current avatar and any rods or boxes (CLO keeps a single avatar).

    Args:
        file_path: Absolute path to the .obj file.
        keep_position: Keep the file's coordinates (False: CLO drops it onto the ground).
        scale: Scale factor applied on import.
    """
    with open(_objects_file("parts.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    return _send("import_obj", {"file_path": file_path, "object_type": 0,
                                "align_to_ground": not keep_position, "scale": scale})


# ─── Checkpoints (CLO's API has no undo) ───────────────────────────────────


def _checkpoint_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "checkpoint"
    return os.path.join(_work_dir("checkpoints"), safe + ".zprj")


@mcp.tool()
def save_checkpoint(name: str = "checkpoint") -> dict:
    """Save the whole scene so it can be restored later. CLO's API has no undo, so save one
    before risky edits (sewing, deleting lines, simulating). Reusing a name overwrites it.

    CLO saves the checkpoint like "Save As", so its open file becomes the checkpoint copy.
    The result's original_path is the real file: save there with save_project when done.
    """
    before = _send("get_project_info")
    path = _checkpoint_path(name)
    result = _send("save_file", {"file_path": path})
    after = _send("get_project_info")
    meta = {"name": name, "file": result.get("file_path") or path, "saved_at": time.time(),
            "project_name": before.get("project_name"), "original_path": before.get("project_path")}
    with open(os.path.splitext(path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    out = {"saved": result.get("saved", False), "checkpoint": name, "file": meta["file"]}
    if _same_path(after.get("project_path"), meta["file"]) and not _same_path(before.get("project_path"), meta["file"]):
        # CLO's ExportZPrj acts like "Save As": the open project is now the checkpoint copy.
        out["note"] = ("CLO's open file is now the checkpoint copy, so CLO's own Save would write "
                       "there. When finished, save the work with save_project(%r)."
                       % before.get("project_path"))
        out["original_path"] = before.get("project_path")
    return out


def _same_path(a, b) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@mcp.tool()
def list_checkpoints() -> dict:
    """List saved checkpoints, newest first."""
    folder = _work_dir("checkpoints")
    items = []
    for entry in os.listdir(folder):
        if entry.endswith(".json"):
            with open(os.path.join(folder, entry), encoding="utf-8") as f:
                meta = json.load(f)
            meta["saved"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta.get("saved_at", 0)))
            items.append(meta)
    items.sort(key=lambda m: m.get("saved_at", 0), reverse=True)
    return {"checkpoints": items}


@mcp.tool()
def restore_checkpoint(name: str) -> dict:
    """Reopen a checkpoint, replacing the current scene (unsaved changes are lost).

    Afterwards CLO's open file is the checkpoint copy; save with save_project to the
    original_path returned here to keep working on the real file.
    """
    path = _checkpoint_path(name)
    if not os.path.exists(path):
        raise ValueError("no checkpoint named %r; see list_checkpoints" % name)
    meta_file = os.path.splitext(path)[0] + ".json"
    meta = {}
    if os.path.exists(meta_file):
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
    result = _send("open_file", {"file_path": path})
    return {"restored": result.get("opened", False), "checkpoint": name,
            "original_path": meta.get("original_path"), "project": _send("get_project_info")}


# ─── Scene Tools ───────────────────────────────────────────────────────────


@mcp.tool()
def get_project_info() -> dict:
    """Get information about the current CLO3D project including name, path, version, and counts for patterns, fabrics, and colorways."""
    return _send("get_project_info")


@mcp.tool()
def new_project() -> dict:
    """Create a new empty CLO3D project, clearing the current scene."""
    return _send("new_project")


@mcp.tool()
def open_file(file_path: str) -> dict:
    """Open a file in CLO3D. Supports .zprj, .zpac, .avt, .obj, .fbx formats.

    Args:
        file_path: Absolute path to the file to open.
    """
    return _send("open_file", {"file_path": file_path})


@mcp.tool()
def save_project(file_path: str) -> dict:
    """Save the current CLO3D project as a .zprj file.

    Args:
        file_path: Absolute path for the saved .zprj file.
    """
    return _send("save_file", {"file_path": file_path})


@mcp.tool()
def get_garment_info() -> dict:
    """Export and retrieve garment metadata as JSON, including pattern details, fabric assignments, and measurements."""
    return _send("get_garment_info")


# ─── Pattern Tools ─────────────────────────────────────────────────────────


@mcp.tool()
def get_pattern_count() -> dict:
    """Get the total number of pattern pieces in the current project."""
    return _send("get_pattern_count")


@mcp.tool()
def get_pattern_list() -> dict:
    """Get a list of all pattern pieces with their indices and names."""
    return _send("get_pattern_list")


@mcp.tool()
def get_pattern_info(pattern_index: int) -> dict:
    """Get detailed information about a specific pattern piece.

    Args:
        pattern_index: Zero-based index of the pattern piece.
    """
    return _send("get_pattern_info", {"pattern_index": pattern_index})


@mcp.tool()
def get_pattern_bounding_box(pattern_index: int) -> dict:
    """Get the bounding box (width/height) of a pattern piece.

    Args:
        pattern_index: Zero-based index of the pattern piece.
    """
    return _send("get_bounding_box", {"pattern_index": pattern_index})


@mcp.tool()
def set_pattern_name(pattern_index: int, name: str) -> dict:
    """Rename a pattern piece.

    Args:
        pattern_index: Zero-based index of the pattern piece.
        name: New name for the pattern piece.
    """
    return _send("set_pattern_name", {"pattern_index": pattern_index, "name": name})


@mcp.tool()
def copy_pattern(pattern_index: int, x: float = 0, y: float = 0) -> dict:
    """Duplicate a pattern piece at a given position.

    Args:
        pattern_index: Zero-based index of the pattern piece to copy.
        x: X position offset for the copy (mm).
        y: Y position offset for the copy (mm).
    """
    return _send("copy_pattern", {"pattern_index": pattern_index, "x": x, "y": y})


@mcp.tool()
def delete_pattern(pattern_index: int) -> dict:
    """Delete a pattern piece from the project.

    Args:
        pattern_index: Zero-based index of the pattern piece to delete.
    """
    return _send("delete_pattern", {"pattern_index": pattern_index})


@mcp.tool()
def flip_pattern(pattern_index: int, horizontal: bool = True) -> dict:
    """Flip a pattern piece horizontally or vertically.

    Args:
        pattern_index: Zero-based index of the pattern piece to flip.
        horizontal: True for horizontal flip, False for vertical flip.
    """
    return _send("flip_pattern", {"pattern_index": pattern_index, "horizontal": horizontal})


@mcp.tool()
def create_pattern(points: list[list[float]]) -> dict:
    """Create a new pattern piece from vertex points.

    Args:
        points: List of [x, y] or [x, y, type] coordinates in mm.
                Type: 0=straight (default), 2=spline, 3=bezier.
                Example: [[0,0], [100,0], [100,200], [0,200]]

    Returns the new pattern_index and its outline lines (line_index, start, end, length), so
    you can sew, hem or topstitch without looking the piece up.
    """
    index = _send("get_pattern_count")["count"]
    result = _send("create_pattern", {"points": points})
    if _send("get_pattern_count")["count"] != index + 1:
        return result
    pieces = [p for p in summarize(_send("get_pattern_geometry"))["pieces"] if p["pattern_index"] == index]
    lines = [{"line_index": l["line_index"], "start": l["start"], "end": l["end"], "length": l["length"]}
             for l in pieces[0]["lines"]] if pieces else []
    return dict(result, pattern_index=index, lines=lines)


@mcp.tool()
def get_arrangement_list() -> dict:
    """Get the list of arrangement points on the avatar."""
    return _send("get_arrangement_list")


# ─── Fabric Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def get_fabric_list() -> dict:
    """Get a list of all fabrics in the current project with their indices and names."""
    return _send("get_fabric_list")


@mcp.tool()
def get_fabric_info(fabric_index: int) -> dict:
    """A fabric's name, its information fields (content, classification, supplier...) and
    CLO's fabric info JSON."""
    return _send("get_fabric_info", {"fabric_index": fabric_index})


@mcp.tool()
def set_fabric_information(fabric_index: int, information: dict[str, str] | None = None,
                           name: str | None = None) -> dict:
    """Set a fabric's name and information fields, e.g. {"Content": "60% Cotton, 40% Linen"}.
    Field names are CLO's (see get_fabric_info). This is metadata only; the drape comes from
    the fabric's physical properties.
    """
    return _send("set_fabric_information", _given(fabric_index=fabric_index, information=information, name=name))


@mcp.tool()
def export_fabric(fabric_index: int, file_path: str) -> dict:
    """Save a fabric as a .zfab file (CLO 2025.2 refuses .jfab here; the .fab inside is binary).

    Args:
        fabric_index: Fabric to export.
        file_path: Absolute output path ending in .zfab.
    """
    if file_path.lower().endswith(".jfab"):
        raise ValueError("CLO exports fabrics only as .zfab; use set_fabric_physics to change physical properties")
    return _send("export_fabric", {"fabric_index": fabric_index, "file_path": file_path})


@mcp.tool()
def apply_fabric_json(fabric_index: int, file_path: str) -> dict:
    """Apply a .jfab file (CLO's JSON fabric format, see JFABSpec.json in the CLO SDK) to a
    fabric. Keys the file leaves out at the top level keep their values (colour, texture,
    content), but a "mapPhysical" block replaces the whole physical set: its missing keys reset
    to CLO's defaults, and a missing "qsFabricName" clears the name. set_fabric_physics
    handles this for you."""
    return _send("change_fabric_with_json", {"fabric_index": fabric_index, "file_path": file_path})


@mcp.tool()
def set_fabric_physics(
    fabric_index: int,
    weight_gsm: float = 300.0,
    thickness_mm: float = 0.5,
    stretch_weft: float = 100000.0,
    stretch_warp: float = 100000.0,
    shear: float = 10000.0,
    bending_weft: float = 2000.0,
    bending_warp: float = 2000.0,
    bending_bias: float = 2000.0,
    buckling_ratio: float = 0.8,
    buckling_stiffness: float = 0.8,
    friction: float = 0.03,
    internal_damping: float = 0.0001,
    content: str | None = None,
    property_name: str = "Custom",
) -> dict:
    """Set all of a fabric's physical properties (how it stretches, bends and hangs), keeping
    its colour, texture and name. CLO cannot report a fabric's current values (only weight
    and thickness, via get_fabric_info), so every value is written: the defaults are CLO's
    documented defaults, not the fabric's own. Pass real values for a specific cloth, or keep
    a library fabric (add_fabric) whose physics were measured.

    Args:
        fabric_index: Fabric to change.
        weight_gsm: Weight in g/m2.
        thickness_mm: Thickness in mm.
        stretch_weft: Stretch stiffness across the grain (weft); higher = less stretch.
        stretch_warp: Stretch stiffness along the grain (warp).
        shear: Shear stiffness (bias stretch).
        bending_weft: Bending stiffness across the grain; higher = stiffer, fewer folds.
        bending_warp: Bending stiffness along the grain.
        bending_bias: Bending stiffness on the bias.
        buckling_ratio: Buckling ratio (all directions).
        buckling_stiffness: Buckling stiffness (all directions).
        friction: Friction.
        internal_damping: Internal damping.
        content: Optionally also set the content label, e.g. "60% Cotton, 40% Linen".
        property_name: Name for this set of physical properties.
    """
    name = _send("get_fabric_info", {"fabric_index": fabric_index}).get("name") or "Fabric %d" % fabric_index
    physical = {
        "enFabricType": 0, "fBhK": bending_bias, "fBuK": bending_weft, "fBvK": bending_warp,
        "fBhLR": buckling_ratio, "fBuLR": buckling_ratio, "fBvLR": buckling_ratio,
        "fBucklingStiffnessH": buckling_stiffness, "fBucklingStiffnessU": buckling_stiffness,
        "fBucklingStiffnessV": buckling_stiffness, "fDensity": weight_gsm / 1e6, "fFriction": friction,
        "fHK": shear, "fIDS": internal_damping, "fSuK": stretch_weft, "fSvK": stretch_warp,
        "fThickness": thickness_mm, "listNonlinearHK": [], "listNonlinearSuK": [], "listNonlinearSvK": [],
        "qsPhysicalPropertyName": property_name, "qsPhysicalPropertyNameUTF8": property_name,
    }
    jfab = {"qsFabricName": name, "qsFabricNameUTF8": name, "uiFabricVersion": 100, "uiVersion": 100,
            "mapPhysical": physical}
    if content is not None:
        jfab.update(fabricContent=content, fabricContentUTF8=content)
    path = os.path.join(_work_dir("fabrics"), "fabric_%d_%d.jfab" % (fabric_index, int(time.time() * 1000)))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jfab, f, indent=1, ensure_ascii=False)
    changed = _send("change_fabric_with_json", {"fabric_index": fabric_index, "file_path": path}).get("changed")
    info = _send("get_fabric_info", {"fabric_index": fabric_index})
    return {"changed": changed, "fabric_index": fabric_index, "name": info.get("name"),
            "weight": info["information"].get("Weight"), "thickness": info["information"].get("Thickness"),
            "content": info["information"].get("Content"), "jfab": path}


@mcp.tool()
def add_fabric(file_path: str) -> dict:
    """Add a new fabric to the project from a .zfab or .jfab file.

    Args:
        file_path: Absolute path to the fabric file (.zfab or .jfab).
    """
    return _send("add_fabric", {"file_path": file_path})


@mcp.tool()
def replace_fabric(fabric_index: int, file_path: str) -> dict:
    """Replace an existing fabric with a new one from file.

    Args:
        fabric_index: Zero-based index of the fabric to replace.
        file_path: Absolute path to the replacement fabric file (.zfab).
    """
    return _send("replace_fabric", {"fabric_index": fabric_index, "file_path": file_path})


@mcp.tool()
def assign_fabric_to_pattern(
    fabric_index: int, pattern_index: int, assign_option: int = 1
) -> dict:
    """Assign a fabric to a pattern piece.

    Args:
        fabric_index: Zero-based index of the fabric.
        pattern_index: Zero-based index of the pattern piece.
        assign_option: 1=current colorway only, 2=all colorways (unlinked), 3=all colorways (linked).
    """
    return _send(
        "assign_fabric",
        {
            "fabric_index": fabric_index,
            "pattern_index": pattern_index,
            "assign_option": assign_option,
        },
    )


@mcp.tool()
def set_fabric_color(fabric_index: int, r: int = 255, g: int = 255, b: int = 255) -> dict:
    """Set the PBR base color of a fabric.

    Args:
        fabric_index: Zero-based index of the fabric.
        r: Red channel (0-255).
        g: Green channel (0-255).
        b: Blue channel (0-255).
    """
    return _send("set_fabric_color", {"fabric_index": fabric_index, "r": r, "g": g, "b": b})


@mcp.tool()
def get_fabric_for_pattern(pattern_index: int) -> dict:
    """Get which fabric is assigned to a pattern piece.

    Args:
        pattern_index: Zero-based index of the pattern piece.
    """
    return _send("get_fabric_for_pattern", {"pattern_index": pattern_index})


# ─── Export Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def export_obj(file_path: str, options: dict | None = None) -> dict:
    """Export the garment as an OBJ file.

    Args:
        file_path: Absolute path for the exported .obj file.
        options: Optional export options (bExportGarment, bExportAvatar, bThin, scale, etc.).
    """
    params = {"file_path": file_path}
    if options:
        params["options"] = options
    return _send("export_obj", params)


@mcp.tool()
def export_fbx(file_path: str) -> dict:
    """Export the garment as an FBX file.

    Args:
        file_path: Absolute path for the exported .fbx file.
    """
    return _send("export_fbx", {"file_path": file_path})


@mcp.tool()
def export_glb(file_path: str, options: dict | None = None) -> dict:
    """Export the garment as a GLB (binary glTF) file.

    Args:
        file_path: Absolute path for the exported .glb file.
        options: Optional export options.
    """
    params = {"file_path": file_path}
    if options:
        params["options"] = options
    return _send("export_glb", params)


@mcp.tool()
def export_gltf(file_path: str, options: dict | None = None) -> dict:
    """Export the garment as a glTF file.

    Args:
        file_path: Absolute path for the exported .gltf file.
        options: Optional export options.
    """
    params = {"file_path": file_path}
    if options:
        params["options"] = options
    return _send("export_gltf", params)


@mcp.tool()
def export_thumbnail(file_path: str, width: int = 512, height: int = 512) -> dict:
    """Export a 3D viewport screenshot/thumbnail.

    Args:
        file_path: Absolute path for the exported image file.
        width: Image width in pixels (default 512).
        height: Image height in pixels (default 512).
    """
    return _send("export_thumbnail", {"file_path": file_path, "width": width, "height": height})


@mcp.tool()
def export_snapshot(file_path: str) -> dict:
    """Save front, back, left and right images of the 3D window as PNG files.

    To look at the garment yourself, use capture_3d instead (returns the images).

    Args:
        file_path: Base path; files are saved as <base>_front.png, <base>_back.png, ...
    """
    return _send("export_snapshot", {"file_path": file_path})


@mcp.tool()
def export_turntable(file_path: str) -> dict:
    """Export a 360-degree turntable image sequence.

    Args:
        file_path: Absolute path (directory or base name) for turntable images.
    """
    return _send("export_turntable", {"file_path": file_path})


@mcp.tool()
def export_tech_pack(file_path: str) -> dict:
    """Export a tech pack with JSON metadata and images.

    Args:
        file_path: Absolute path for the tech pack output.
    """
    return _send("export_tech_pack", {"file_path": file_path})


# ─── Plotting, Print Layout & DXF ─────────────────────────────────────────

PAPER = {"a4": 0, "a3": 1, "b5": 2, "b4": 3, "letter": 4, "legal": 5, "tabloid": 6, "wide_24in": 7,
         "wide_36in": 8, "wide_44in": 9, "one_to_one": 10, "custom": 11}
SHEET_SOURCE = {"pattern": 0, "print_layout": 1}
GRAIN_MODES = {"1-way": 0, "2-way": 1, "4-way": 2}


@mcp.tool()
def get_print_options(source: str = "print_layout") -> dict:
    """Current 2D export settings (paper, orientation, resolution and what is shown) for the
    pattern window ("pattern") or the print layout ("print_layout")."""
    return _send("get_print_options", {"mode": SHEET_SOURCE[source]})


@mcp.tool()
def export_pattern_sheet(file_path: str, source: str = "print_layout", paper: str | None = None,
                         landscape: bool | None = None, width_mm: float | None = None,
                         height_mm: float | None = None, resolution_dpi: int | None = None,
                         show: dict[str, bool] | None = None) -> dict:
    """Export the 2D pattern as a PDF (vector, exactly 1:1: verified in CLO 2025.2) or PNG, by
    the file's extension, for plotting or printing.

    "print_layout" exports the marker of the fabric shown in CLO's Print Layout Editor (see
    marker_report): the page is the fabric width by the marker length. It needs the layout set
    up once in CLO (Printing Layout mode, Auto Nesting). "pattern" exports the pattern window.

    Args:
        file_path: Absolute path ending in .pdf or .png.
        source: "print_layout" or "pattern".
        paper: a4, a3, b5, b4, letter, legal, tabloid, wide_24in, wide_36in, wide_44in,
            one_to_one or custom (then give width_mm and height_mm). Default: CLO's setting.
            The PDF stays one 1:1 page whatever the paper (tested with a4: no tiling), so
            tile it with your plotter or PDF software for small paper.
        landscape: Paper orientation.
        width_mm: Paper width for custom paper.
        height_mm: Paper height for custom paper.
        resolution_dpi: Resolution for PNG output.
        show: What to draw, e.g. {"seam_allowance": true, "notches": true, "grain_line": true,
            "pattern_name": true, "annotations": true}. Keys: outline, internal_lines,
            graphic_outline, baseline, notches, seam_allowance, symmetry_line, reference_line,
            pattern_name, annotations, line_length, grain_line, button_holes, seam_taping,
            measurements_2d, grading_size_name, nest_information, fabric_texture,
            pattern_texture, graphic_texture.
    """
    from clo3d_mcp.plotting import pdf_page_mm
    if paper is not None and paper not in PAPER:
        raise ValueError("paper must be one of %s" % ", ".join(PAPER))
    params = _given(file_path=file_path, mode=SHEET_SOURCE[source], paper_preset=PAPER.get(paper),
                    orientation=None if landscape is None else int(landscape), width_mm=width_mm,
                    height_mm=height_mm, resolution=resolution_dpi, show=show)
    if width_mm is not None or height_mm is not None:
        params["unit"] = 2  # millimetres
    result = _send("export_pattern_sheet", params)
    if not os.path.exists(file_path):  # CLO returns no file list; check the file itself
        raise RuntimeError(LAYOUT_SETUP if source == "print_layout" else "CLO did not write %s" % file_path)
    out = {"file_path": file_path, "bytes": os.path.getsize(file_path), "options": result["options"]}
    if file_path.lower().endswith(".pdf"):
        with open(file_path, "rb") as f:
            out["page_mm"] = pdf_page_mm(f.read())
    return out


@mcp.tool()
def export_dxf(file_path: str, format: str = "aama", metric: bool = True, box_per_piece: bool = False,
               curves_to_straight: bool = False, optimize_curve_points: bool = False,
               without_grading: bool = False, without_baselines: bool = False,
               remove_duplicate_notches: bool = False, scale: float = 1.0, rotate_degrees: float = 0.0) -> dict:
    """Export the pattern as a DXF for CAD, plotters and cutters (no dialog). Returns the
    pieces read back from the file (name, size, quantity, outline box in mm) as a check.

    Args:
        file_path: Absolute path ending in .dxf.
        format: "aama", "astm" or "gerber" (Gerber-flavoured AAMA).
        metric: Millimetres (True) or inches.
        box_per_piece: One bounding box per piece instead of one around all pieces.
        curves_to_straight: Convert curve points to straight points.
        optimize_curve_points: Drop curve points very close to others.
        without_grading: Leave grading out.
        without_baselines: Only the outlines, without base lines.
        remove_duplicate_notches: Remove duplicate notches.
        scale: Scale factor.
        rotate_degrees: Rotate the pieces.
    """
    from clo3d_mcp.plotting import dxf_pieces
    formats = {"aama": 0, "astm": 1, "gerber": 2}
    result = _send("export_dxf", {"file_path": file_path, "format": formats[format], "metric": metric,
                                  "bounding_box": 2 if box_per_piece else 1, "scale": scale,
                                  "rotate": rotate_degrees, "curves_to_straight": curves_to_straight,
                                  "optimize_curve_points": optimize_curve_points, "without_grading": without_grading,
                                  "without_baselines": without_baselines,
                                  "remove_duplicate_notches": remove_duplicate_notches})
    path = result.get("file_path") or file_path
    if not result.get("exported") or not os.path.exists(path):
        raise RuntimeError("CLO did not write %s" % file_path)
    with open(path, encoding="latin-1") as f:
        pieces = dxf_pieces(f.read())
    return {"file_path": path, "pieces": [{"block": k, "name": v.get("name"), "size": v.get("size"),
                                           "quantity": v.get("quantity"), "outline_box_mm": v.get("bbox")}
                                          for k, v in pieces.items()]}


@mcp.tool()
def set_fabric_width(fabric_index: int, width_mm: float) -> dict:
    """Set a fabric's usable width (the marker width in the print layout), in mm."""
    return _send("set_fabric_width", {"fabric_index": fabric_index, "width_mm": width_mm})


@mcp.tool()
def set_nesting(buffer_spacing_mm: float | None = None, colorways: list[int] | None = None,
                pattern_index: int | None = None, grain: str | None = None,
                fixed_position: list[int] | None = None) -> dict:
    """Nesting settings for the print layout: spacing between pieces, which colourways get the
    result, and per piece how it may turn ("1-way": as cut, "2-way": also turned 180 degrees,
    "4-way": also 90 degrees) or a fixed [x, y] position."""
    if grain is not None and grain not in GRAIN_MODES:
        raise ValueError("grain must be 1-way, 2-way or 4-way")
    params = _given(buffer_spacing_mm=buffer_spacing_mm, target_colorways=colorways, pattern_index=pattern_index,
                    grain_mode=GRAIN_MODES.get(grain))
    if fixed_position is not None:
        params.update(fixed_x=fixed_position[0], fixed_y=fixed_position[1])
    _send("set_nesting", params)
    return _send("get_nesting")


LAYOUT_SETUP = ("CLO's print layout is empty: switch CLO to Printing Layout mode once in this session "
                "(mode dropdown at the top right) and call nest_patterns again; if it stays empty, use "
                "nest_patterns(allow_ui_click=True) or clo_ui_nest_all_fabrics, which click CLO's Nest All "
                "Fabrics button")


def _markers():
    """Measure the marker shown in CLO's Print Layout Editor. Tested in CLO 2025.2.236:
    - The export covers only the fabric shown there; the API cannot choose it (selecting a
      fabric only changes the Object Browser), so the fabric is identified by matching the
      laid-out outlines' areas to each fabric's pieces.
    - The API's nesting does nothing until the layout has been set up once in CLO (Auto Nesting
      from the Printing Layout toolbar); an empty layout exports nothing, and the first time CLO
      shows a dialog that waits for OK."""
    from clo3d_mcp.plotting import marker_report, pdf_paths, polygon_area
    path = os.path.join(_work_dir("plots"), "marker_%d.pdf" % int(time.time() * 1000))
    _send("export_pattern_sheet", {"file_path": path, "mode": 1})
    if not os.path.exists(path):
        raise RuntimeError(LAYOUT_SETUP)
    with open(path, "rb") as f:
        pdf = f.read()
    laid_out = sorted(polygon_area(p) for p in pdf_paths(pdf))
    pieces = summarize(_send("get_pattern_geometry"), include_points=True)["pieces"]
    by_fabric = {}
    for piece in pieces:
        points = [tuple(pt[:2]) for line in piece["lines"] for pt in line["points"][:-1]]
        fabric = _send("get_fabric_for_pattern", {"pattern_index": piece["pattern_index"]})["fabric_index"]
        by_fabric.setdefault(fabric, []).append(polygon_area(points))

    def matches(areas):
        left, hits = list(areas), 0
        for area in laid_out:
            best = min(left, key=lambda a: abs(a - area), default=None)
            if best is not None and abs(best - area) <= max(1.0, 0.01 * area):
                left.remove(best)
                hits += 1
        return hits

    scores = {f: matches(areas) for f, areas in by_fabric.items()}
    fabric = max(scores, key=scores.get) if scores else None
    info = {f["fabric_index"]: f for f in _send("get_fabric_layout")["fabrics"]}.get(fabric, {})
    report = dict(marker_report(pdf, info.get("width_mm")), pdf=path, fabric_index=fabric, fabric=info.get("name"))
    report["pieces_matched_to_fabric"] = "%d of %d" % (scores.get(fabric, 0), len(laid_out))
    report["fabric_pieces"] = len(by_fabric.get(fabric, []))
    others = sorted(set(by_fabric) - {fabric})
    if others:
        report["other_fabrics"] = others
        report["note"] = ("CLO exports one fabric's marker at a time: switch the fabric in CLO's Print "
                          "Layout Editor and call marker_report again for fabrics %s" % others)
    return report


@mcp.tool()
def nest_patterns(buffer_spacing_mm: float | None = None, fabric_index: int | None = None,
                  fabric_width_mm: float | None = None, timeout_s: float = 60.0,
                  allow_ui_click: bool = False) -> dict:
    """Auto-nest the pieces in CLO's print layout and report the marker shown there: length
    along the fabric, width, pieces and utilisation (measured from the exported 1:1 layout;
    CLO's own fabric length value did not match the layout in testing).

    In a fresh CLO session the API's nesting leaves the layout empty until CLO's Printing Layout
    mode has been opened once (tested: empty before the switch, working right after it and for
    the rest of the session, also back in Simulation mode). If it is still empty,
    allow_ui_click clicks "Nest All Fabrics" in CLO (needs Printing Layout mode); otherwise the
    error says what to do.

    Args:
        buffer_spacing_mm: Space between pieces.
        fabric_index: With fabric_width_mm, set this fabric's width first.
        fabric_width_mm: Usable fabric width in mm.
        timeout_s: How long to wait for CLO's nesting to finish.
        allow_ui_click: Click CLO's "Nest All Fabrics" button when the layout is empty.
    """
    if fabric_width_mm is not None:
        if fabric_index is None:
            raise ValueError("give fabric_index with fabric_width_mm")
        _send("set_fabric_width", {"fabric_index": fabric_index, "width_mm": fabric_width_mm})
    if buffer_spacing_mm is not None:
        _send("set_nesting", {"buffer_spacing_mm": buffer_spacing_mm})
    _send("start_nesting")  # returns at once; CLO nests in the background and then reports its time
    deadline, last = time.time() + timeout_s, None
    time.sleep(1.0)
    while time.time() < deadline:
        ms = _send("get_nesting")["last_nesting_ms"]
        if ms == last and 0 <= ms < timeout_s * 1000:
            break
        last = ms
        time.sleep(0.5)
    else:
        _send("stop_nesting")
        raise RuntimeError("nesting did not finish within %.0f s (stopped)" % timeout_s)
    try:
        report = _markers()
    except RuntimeError as error:
        if str(error) != LAYOUT_SETUP or not allow_ui_click:
            raise
        from clo3d_mcp.clo_ui import nest_all_fabrics
        nest_all_fabrics()  # CLO's own nesting sets the layout up, with the same spacing
        time.sleep(2.0)
        report = _markers()
        report["set_up_by_ui_click"] = True
    report["nesting_ms"] = last
    return report


@mcp.tool()
def clo_ui_nest_all_fabrics() -> dict:
    """Click "Nest All Fabrics" in CLO's Print Layout Editor (through Windows UI Automation),
    for when the print layout is empty: the API's nesting only works once CLO's own nesting has
    run. CLO must be in Printing Layout mode (the user switches it; CLO's mode menu cannot be
    driven this way). Returns the marker it produced."""
    from clo3d_mcp.clo_ui import nest_all_fabrics
    nest_all_fabrics()
    time.sleep(2.0)
    return _markers()


@mcp.tool()
def marker_report() -> dict:
    """Measure the marker shown in CLO's Print Layout Editor: which fabric it is, its length
    along the fabric, width, pieces, area and utilisation, from a 1:1 PDF export (path included
    for plotting). CLO exports one fabric at a time and the API cannot switch it; the result
    lists the other fabrics to switch to in CLO."""
    return _markers()


@mcp.tool()
def add_pattern_annotation(pattern_index: int, text: str, x: float, y: float,
                           annotation_index: int | None = None) -> dict:
    """Write a note on a piece (cut quantity, fabric, size, "place on fold"...) at 2D (x, y)
    in the piece's coordinates; it prints on pattern sheets with show={"annotations": true}.
    With annotation_index, replace that note instead."""
    _send("add_pattern_annotation", _given(pattern_index=pattern_index, text=text, x=x, y=y,
                                           annotation_index=annotation_index))
    return _send("get_pattern_annotations", {"pattern_index": pattern_index})


@mcp.tool()
def get_pattern_annotations(pattern_index: int) -> dict:
    """The notes written on a piece, with their positions."""
    return _send("get_pattern_annotations", {"pattern_index": pattern_index})


# ─── Import Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def import_file(file_path: str) -> dict:
    """Import a file into CLO3D. Auto-detects type from extension (.zprj, .zpac, .obj, .fbx, .avt, etc.).

    Args:
        file_path: Absolute path to the file to import.
    """
    return _send("import_file", {"file_path": file_path})


# ─── Simulation Tools ─────────────────────────────────────────────────────


@mcp.tool()
def simulate(steps: int = 100) -> dict:
    """Run cloth simulation for a number of steps.

    Args:
        steps: Number of simulation steps to run (default 100).
    """
    return _send("simulate", {"steps": steps})


# ─── Colorway Tools ───────────────────────────────────────────────────────


@mcp.tool()
def get_colorways() -> dict:
    """Get a list of all colorways in the current project with names and which is active."""
    return _send("get_colorways")


@mcp.tool()
def set_current_colorway(colorway_index: int) -> dict:
    """Switch to a different colorway.

    Args:
        colorway_index: Zero-based index of the colorway to activate.
    """
    return _send("set_current_colorway", {"colorway_index": colorway_index})


# ─── Geometry & Seams ─────────────────────────────────────────────────────


@mcp.tool()
def get_pattern_geometry(pattern_index: int | None = None, include_points: bool = False) -> dict:
    """Get pattern pieces with numbered outline lines, internal shapes and all seams.

    Use this before sewing or editing lines: every line-based tool takes the `line_index`
    values listed here. Each line has its length (mm) and start/end points in 2D pattern
    coordinates. Each seam lists, for both sides, the pattern, optional internal shape, the
    lines it covers (coverage 1.0 = whole line) and where stitching starts and ends.
    "sewn_together" pairs those ends (side a's start is sewn to side b's start, end to end):
    if a left end is paired with a right end the seam is twisted; sew it again with one
    direction flipped. A pocket's fold-over seam deliberately pairs the strip's top edge with
    a lower line.

    Args:
        pattern_index: Only return this piece and the seams touching it (default: all).
        include_points: Also return every point of every line (larger output).
    """
    return summarize(_send("get_pattern_geometry"), pattern_index, include_points)


@mcp.tool()
def sew_lines(
    pattern_a: int,
    line_a: int,
    pattern_b: int,
    line_b: int,
    direction_a: bool = True,
    direction_b: bool = True,
    internal_shape_a: int | None = None,
    internal_shape_b: int | None = None,
) -> dict:
    """Sew one line to another (segment sewing). Get line indices from get_pattern_geometry.

    If the seam comes out twisted, sew it again with one direction flipped.

    Args:
        pattern_a: Pattern index of side A.
        line_a: Line index on side A (outline line, or line of internal_shape_a).
        pattern_b: Pattern index of side B.
        line_b: Line index on side B (outline line, or line of internal_shape_b).
        direction_a: Stitch direction along line A (True = forward).
        direction_b: Stitch direction along line B (True = forward).
        internal_shape_a: Sew a line of this internal shape on A instead of the outline.
        internal_shape_b: Sew a line of this internal shape on B instead of the outline.
    """
    return _send("sew_lines", _given(
        pattern_a=pattern_a, line_a=line_a, pattern_b=pattern_b, line_b=line_b,
        direction_a=direction_a, direction_b=direction_b,
        internal_shape_a=internal_shape_a, internal_shape_b=internal_shape_b))


@mcp.tool()
def list_topstitch_styles() -> dict:
    """List the topstitch styles in the project, for add_topstitch's style_index."""
    return _send("list_topstitch_styles")


# A topstitch style saved from CLO once, kept outside the temp folder and the repo (it holds
# CLO's stitch textures); create_topstitch_style patches copies of it.
STITCH_TEMPLATE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "clo3d_mcp",
                               "topstitch_template.sst")


@mcp.tool()
def create_topstitch_style(name: str, stitch_length_mm: float | None = None,
                           thread_thickness_mm: float | None = None, offset_mm: float | None = None,
                           template_path: str | None = None) -> dict:
    """Create a topstitch style with a given stitch length, thread thickness and offset from
    the stitched line (CLO's API cannot set these on a style, so a saved style file is copied
    with the values changed and imported). Returns its style_index for add_topstitch.

    Needs a template once: in CLO select any topstitch in the Object Browser, click the save
    icon in the Property Editor's Topstitch row and pass that .sst as template_path; it is
    kept for later calls.

    Args:
        name: Name for the new style.
        stitch_length_mm: Length of each stitch in mm (default: the template's).
        thread_thickness_mm: Thread thickness in mm (CLO shows it as Tex: 0.2 mm = 40 Tex).
        offset_mm: Distance of the stitching from the line or seam it follows, in mm.
        template_path: A .sst saved from CLO (only needed the first time).
    """
    import shutil
    from clo3d_mcp.stitch_style import make_style, read_values
    if template_path:
        os.makedirs(os.path.dirname(STITCH_TEMPLATE), exist_ok=True)
        if os.path.abspath(template_path) != os.path.abspath(STITCH_TEMPLATE):
            shutil.copyfile(template_path, STITCH_TEMPLATE)
    if not os.path.exists(STITCH_TEMPLATE):
        raise ValueError("no topstitch template yet: save any topstitch style from CLO's Property Editor "
                         "(save icon in the Topstitch row) and pass the .sst as template_path")
    with open(STITCH_TEMPLATE, "rb") as f:
        style = make_style(f.read(), stitch_length_mm, thread_thickness_mm, offset_mm)
    path = os.path.join(_work_dir("stitches"), "style_%d.sst" % int(time.time() * 1000))
    with open(path, "wb") as f:
        f.write(style)
    result = _send("import_topstitch_style", {"file_path": path})
    if "style_index" not in result:
        raise RuntimeError("CLO did not add the style: %s" % result)
    _send("set_topstitch_style", {"style_index": result["style_index"], "name": name})
    length, thickness, offset = read_values(style)
    return {"style_index": result["style_index"], "name": name, "stitch_length_mm": length,
            "thread_thickness_mm": thickness, "thread_tex": round(thickness * 200), "offset_mm": offset}


@mcp.tool()
def get_topstitch_style(style_index: int) -> dict:
    """A topstitch style's name, offset and per-line values as CLO's API reports them
    (stitch length and thread thickness are not readable through the API)."""
    return _send("get_topstitch_style", {"style_index": style_index})


@mcp.tool()
def set_topstitch_style(style_index: int, name: str | None = None, color: list[int] | None = None,
                        number_of_lines: int | None = None) -> dict:
    """Rename a topstitch style, set its thread colour ([r, g, b] 0-255) or its number of
    parallel lines. For stitch length, thread thickness and offset use create_topstitch_style."""
    return _send("set_topstitch_style", _given(style_index=style_index, name=name, color=color,
                                               number_of_lines=number_of_lines))


@mcp.tool()
def add_topstitch(
    style_index: int,
    seam_index: int | None = None,
    start_ratio: float = 0.0,
    end_ratio: float = 1.0,
    pattern_index: int | None = None,
    line_index: int | None = None,
) -> dict:
    """Add topstitching either along a seam or along one pattern line.

    Args:
        style_index: Topstitch style from list_topstitch_styles.
        seam_index: Topstitch this seam (from get_pattern_geometry).
        start_ratio: Where along the seam to start (0-1), with seam_index.
        end_ratio: Where along the seam to end (0-1), with seam_index.
        pattern_index: Topstitch a line of this pattern instead of a seam.
        line_index: The line to topstitch, with pattern_index.
    """
    return _send("add_topstitch", _given(
        style_index=style_index, seam_index=seam_index, start_ratio=start_ratio,
        end_ratio=end_ratio, pattern_index=pattern_index, line_index=line_index))


@mcp.tool()
def set_seam_taping(pattern_index: int, line_index: int, enabled: bool = True) -> dict:
    """Turn seam taping on or off for one outline line of a pattern."""
    return _send("set_seam_taping", {"pattern_index": pattern_index, "line_index": line_index, "enabled": enabled})


# ─── Piece State ───────────────────────────────────────────────────────────


@mcp.tool()
def set_pattern_state(
    pattern_index: int,
    frozen: bool | None = None,
    strengthened: bool | None = None,
    solidified: bool | None = None,
    solidify_strength: float | None = None,
    hidden_3d: bool | None = None,
    layer: int | None = None,
    particle_distance: float | None = None,
    grain_degrees: float | None = None,
) -> dict:
    """Set simulation state of a pattern piece. Only the values you pass are changed.

    CLO's API cannot place pins; freeze or strengthen a piece to hold it in place instead.

    Args:
        pattern_index: Pattern to change.
        frozen: Freeze (excluded from simulation, stays where it is).
        strengthened: Strengthen (stiffer, holds shape).
        solidified: Solidify.
        solidify_strength: Solidify strength.
        hidden_3d: Hide the piece in the 3D window.
        layer: Layer number (higher layers sit on top in collisions).
        particle_distance: Mesh particle distance in mm (smaller = finer mesh, slower).
        grain_degrees: Grainline direction in degrees.
    """
    return _send("set_pattern_state", _given(
        pattern_index=pattern_index, frozen=frozen, strengthened=strengthened, solidified=solidified,
        solidify_strength=solidify_strength, hidden_3d=hidden_3d, layer=layer,
        particle_distance=particle_distance, grain_degrees=grain_degrees))


@mcp.tool()
def get_pattern_state(pattern_index: int) -> dict:
    """Get a piece's layer, solidify state, grain direction, shrinkage and 3D arrangement."""
    return _send("get_pattern_state", {"pattern_index": pattern_index})


@mcp.tool()
def remove_all_pins() -> dict:
    """Remove every pin in the scene. (CLO's API can remove pins but not create them.)"""
    return _send("remove_all_pins")


# ─── 3D Placement ──────────────────────────────────────────────────────────


@mcp.tool()
def get_arrangement_points() -> dict:
    """List the avatar's arrangement points (needs an avatar in the scene) for place_pattern."""
    return _send("get_arrangement_points")


@mcp.tool()
def place_pattern(
    pattern_index: int,
    arrangement_index: int | None = None,
    orientation: int | None = None,
    position_x: int | None = None,
    position_y: int | None = None,
    offset: int | None = None,
    shape_style: str | None = None,
) -> dict:
    """Place a pattern piece in 3D on an avatar arrangement point.

    CLO's API has no free XYZ move/rotate; placement goes through arrangement points.
    Run simulate afterwards to drape.

    Args:
        pattern_index: Pattern to place.
        arrangement_index: Arrangement point from get_arrangement_points.
        orientation: Orientation value for the piece on the point.
        position_x: Position on the arrangement surface (set together with position_y/offset).
        position_y: Position on the arrangement surface.
        offset: Distance from the arrangement surface.
        shape_style: "Flat" or "Curved" (wrap around the body).
    """
    return _send("place_pattern", _given(
        pattern_index=pattern_index, arrangement_index=arrangement_index, orientation=orientation,
        position_x=position_x, position_y=position_y, offset=offset, shape_style=shape_style))


@mcp.tool()
def reset_arrangement() -> dict:
    """Reset all pieces to their arrangement positions (undo draping)."""
    return _send("reset_arrangement")


@mcp.tool()
def move_pattern_2d(
    pattern_index: int,
    x: float | None = None,
    y: float | None = None,
    dx: float | None = None,
    dy: float | None = None,
) -> dict:
    """Move a piece in the 2D pattern window: to (x, y), or by (dx, dy)."""
    return _send("move_pattern_2d", _given(pattern_index=pattern_index, x=x, y=y, dx=dx, dy=dy))


# ─── Lines & Shapes ────────────────────────────────────────────────────────


@mcp.tool()
def add_internal_shape(pattern_index: int, points: list[list[float]], closed: bool = False) -> dict:
    """Draw an internal line or shape on a pattern (fold lines, darts, pocket placement...).

    Args:
        pattern_index: Pattern to draw on.
        points: [[x, y], ...] or [[x, y, type], ...] in pattern coordinates (mm), inside the
            piece (CLO also accepts points outside it); type 0 = straight, 2 = spline curve,
            3 = bezier curve. Use get_pattern_geometry for the piece's coordinates.
        closed: Connect the last point back to the first.
    """
    return _send("add_internal_shape", {"pattern_index": pattern_index, "points": points, "closed": closed})


@mcp.tool()
def offset_internal_line(
    pattern_index: int, line_index: int, distance: float, count: int = 1,
    reverse: bool = False, extend: bool = False,
) -> dict:
    """Create internal lines parallel to an outline line (e.g. hem fold or pleat lines).

    The offset must fit inside the piece; if CLO creates nothing, the tool returns an error
    (try reverse=True, a smaller distance, or a longer line).

    Args:
        pattern_index: Pattern to draw on.
        line_index: Outline line to offset from.
        distance: Distance between lines in mm.
        count: Number of lines to create.
        reverse: Offset to the other side.
        extend: Extend the new lines to the piece outline.
    """
    return _send("offset_internal_line", {
        "pattern_index": pattern_index, "line_index": line_index, "distance": distance,
        "count": count, "reverse": reverse, "extend": extend})


@mcp.tool()
def convert_shape(pattern_index: int, internal_shape: int, to: str) -> dict:
    """Convert an internal shape to a base line ("base") or back ("internal")."""
    return _send("convert_shape", {"pattern_index": pattern_index, "internal_shape": internal_shape, "to": to})


@mcp.tool()
def delete_point(pattern_index: int, point_index: int) -> dict:
    """Delete an outline point (point n is the start point of line n; see get_pattern_geometry)."""
    return _send("delete_point", {"pattern_index": pattern_index, "point_index": point_index})


@mcp.tool()
def delete_line(pattern_index: int, line_index: int) -> dict:
    """Delete an outline line of a pattern."""
    return _send("delete_line", {"pattern_index": pattern_index, "line_index": line_index})


@mcp.tool()
def mirror_pattern(pattern_index: int, with_sewing: bool = True) -> dict:
    """Create a symmetric (mirrored, linked) copy of a piece, optionally copying its sewing."""
    return _send("mirror_pattern", {"pattern_index": pattern_index, "with_sewing": with_sewing})


@mcp.tool()
def unfold_pattern(pattern_index: int, line_index: int, half_symmetry: bool = False) -> dict:
    """Unfold a half pattern across one of its lines (e.g. the centre-front fold line)."""
    return _send("unfold_pattern", {"pattern_index": pattern_index, "line_index": line_index, "half_symmetry": half_symmetry})


# ─── Hems & Appliqué ───────────────────────────────────────────────────────


def _piece(pattern_index: int) -> dict:
    pieces = [p for p in summarize(_send("get_pattern_geometry"))["pieces"] if p["pattern_index"] == pattern_index]
    if not pieces:
        raise ValueError("no pattern %d" % pattern_index)
    return pieces[0]


def _layered_piece(base: int, points, layer: int, fabric_index, name: str, place_flat: bool) -> int:
    """Create a piece lying on the base piece, in its fabric, on a layer."""
    index = _send("get_pattern_count")["count"]
    _send("create_pattern", {"points": points})
    if _send("get_pattern_count")["count"] != index + 1:
        raise RuntimeError("CLO did not create the piece")
    fabric = fabric_index if fabric_index is not None else _send("get_fabric_for_pattern", {"pattern_index": base}).get("fabric_index")
    if fabric is not None:
        _send("assign_fabric", {"fabric_index": fabric, "pattern_index": index, "assign_option": 1})
    _send("set_pattern_name", {"pattern_index": index, "name": name})
    _send("set_pattern_state", {"pattern_index": index, "layer": layer})
    if place_flat:
        _send("place_pattern", {"pattern_index": index, "shape_style": "Flat"})
    return index


@mcp.tool()
def add_hem(pattern_index: int, line_index: int, width: float, layer: int = -1,
            fabric_index: int | None = None, place_flat: bool = True) -> dict:
    """Hem a straight edge: adds the turned-back layer as a strip covering the hem area on the
    reverse side, sewn along the edge and along a new hem line (CLO's API cannot fold cloth;
    fold angles are ignored by the simulation). Gives the doubled fabric at the edge. The
    pattern's edge is the finished edge, so draw pieces at their finished size.

    Args:
        pattern_index: Piece to hem.
        line_index: The straight outline line to hem (from get_pattern_geometry / view_patterns).
        width: Finished hem width in mm.
        layer: Layer for the hem strip: -1 = the -z side (towards the back camera), 1 = the +z
            side. Use the side opposite the garment's face. Where hems cross (a bottom hem and
            the side hems), put the later ones one layer further out (e.g. -2), like the real
            corner; on the same layer the overlapping strips crumple.
        fabric_index: Fabric for the strip (default: the piece's fabric).
        place_flat: Place the strip Flat, like a piece laid out without an avatar; set False
            and place it yourself for pieces on an avatar.
    """
    from clo3d_mcp.layers import hem_strip
    piece = _piece(pattern_index)
    corners, hem_line = hem_strip(piece, line_index, width)
    shape = len(piece["internal_shapes"])
    _send("add_internal_shape", {"pattern_index": pattern_index, "points": hem_line, "closed": False})
    strip = _layered_piece(pattern_index, corners, layer, fabric_index,
                           "%s hem" % (piece.get("name") or "Pattern %d" % pattern_index), place_flat)
    edge = _send("sew_lines", {"pattern_a": strip, "line_a": 0, "pattern_b": pattern_index, "line_b": line_index,
                               "direction_a": True, "direction_b": True})
    fold = _send("sew_lines", {"pattern_a": strip, "line_a": 2, "pattern_b": pattern_index, "line_b": 0,
                               "direction_a": True, "direction_b": False, "internal_shape_b": shape})
    return {"hem_pattern_index": strip, "hem_line_internal_shape": shape, "hem_line": hem_line,
            "seams": [edge.get("seam_index"), fold.get("seam_index")], "layer": layer}


@mcp.tool()
def add_applique(pattern_index: int, points: list[list[float]], sewn_lines: int | None = None,
                 layer: int = 1, reverse: bool = False, fabric_index: int | None = None,
                 name: str = "Applique", place_flat: bool = True, reverse_layer: int | None = None) -> dict:
    """Add an appliqué patch on a piece, sewn to it where it sits (a new internal line on the
    base marks each sewn edge). Optionally the matching patch on the other side ("front and
    reverse patches"), sharing the same stitch line.

    Args:
        pattern_index: Base piece.
        points: The patch outline in the base's 2D coordinates (mm), inside the piece.
        sewn_lines: Sew only the first N edges (points[0]->points[1] is edge 0); edges on the
            base's outline or a seam can stay free. Default: all edges.
        layer: Layer for the patch: 1 = the +z side, -1 = the -z side.
        reverse: Also add the same patch on the opposite layer.
        fabric_index: Fabric for the patch (default: the base's fabric).
        name: Name for the patch piece(s); " front"/" reverse" is added with reverse=True.
        place_flat: Place the patch Flat; set False and place it yourself on an avatar.
        reverse_layer: Layer for the reverse patch (default: -layer). Put it outside any hem
            strips it crosses (e.g. 3 over hems on 1 and 2).
    """
    n = len(points)
    if n < 3:
        raise ValueError("an appliqué needs at least 3 points")
    sewn = n if sewn_lines is None else sewn_lines
    if not 1 <= sewn <= n:
        raise ValueError("sewn_lines must be between 1 and %d" % n)
    piece = _piece(pattern_index)
    shape = len(piece["internal_shapes"])
    closed = sewn == n
    _send("add_internal_shape", {"pattern_index": pattern_index, "points": points if closed else points[:sewn + 1],
                                 "closed": closed})
    made = []
    back = -layer if reverse_layer is None else reverse_layer
    for side in ((layer, " front"), (back, " reverse")) if reverse else ((layer, ""),):
        patch = _layered_piece(pattern_index, points, side[0], fabric_index, name + side[1], place_flat)
        seams = [_send("sew_lines", {"pattern_a": patch, "line_a": k, "pattern_b": pattern_index, "line_b": k,
                                     "direction_a": True, "direction_b": True, "internal_shape_b": shape}).get("seam_index")
                 for k in range(sewn)]
        made.append({"pattern_index": patch, "layer": side[0], "seams": seams})
    return {"patches": made, "stitch_line_internal_shape": shape}


# ─── Elastic & Shrinkage ───────────────────────────────────────────────────


@mcp.tool()
def set_elastic(
    pattern_index: int,
    line_index: int = -1,
    enabled: bool | None = None,
    strength: float | None = None,
    ratio: int | None = None,
    segment_length: float | None = None,
    total_length: float | None = None,
) -> dict:
    """Set elastic on a pattern line (or all lines with line_index=-1). Only passed values change.

    Args:
        pattern_index: Pattern to change.
        line_index: Outline line, or -1 for every line.
        enabled: Elastic on/off.
        strength: Elastic strength.
        ratio: Elastic strength ratio in percent.
        segment_length: Elastic segment length in mm.
        total_length: Elastic total length in mm.
    """
    return _send("set_elastic", _given(
        pattern_index=pattern_index, line_index=line_index, enabled=enabled, strength=strength,
        ratio=ratio, segment_length=segment_length, total_length=total_length))


@mcp.tool()
def set_shrinkage(pattern_index: int, width_percent: float | None = None, height_percent: float | None = None) -> dict:
    """Set a piece's fabric shrinkage as its size in percent: 100 = no shrinkage, 97 = shrinks 3 %.

    Args:
        pattern_index: Pattern to change.
        width_percent: Width (weft) size in percent, 50-150.
        height_percent: Height (warp) size in percent, 50-150.
    """
    return _send("set_shrinkage", _given(
        pattern_index=pattern_index, width_percent=width_percent, height_percent=height_percent))


# ─── Simulation, Avatar & Housekeeping ─────────────────────────────────────


@mcp.tool()
def set_simulation_quality(quality: int, simulation_mode: int | None = None) -> dict:
    """Set the simulation preset: 0 Normal, 1 Animation (stable), 2 Fitting (accurate), 3 FAST (GPU).

    Args:
        quality: Preset index.
        simulation_mode: 0 CPU, 1 GPU (defaults to GPU for preset 3, CPU otherwise).
    """
    return _send("set_simulation_quality", _given(quality=quality, simulation_mode=simulation_mode))


@mcp.tool()
def get_avatars() -> dict:
    """List avatars in the scene with names and genders."""
    return _send("get_avatars")


@mcp.tool()
def import_avatar(file_path: str, apf_path: str = "") -> dict:
    """Load an avatar (.avt, .avac) into the scene, optionally with an arrangement file (.apf)."""
    return _send("import_avatar", {"file_path": file_path, "apf_path": apf_path})


@mcp.tool()
def show_avatar(show: bool = True) -> dict:
    """Show or hide the avatar."""
    return _send("show_hide_avatar", {"show": show})


@mcp.tool()
def delete_fabric(fabric_index: int) -> dict:
    """Delete a fabric from the project."""
    return _send("delete_fabric", {"fabric_index": fabric_index})

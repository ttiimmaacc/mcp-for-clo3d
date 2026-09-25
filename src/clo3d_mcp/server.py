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
    marks lines sewn by seam N; grey lines are internal lines. Pair it with
    get_pattern_geometry for exact lengths and coordinates.

    Args:
        pattern_index: Draw only this piece (default: all pieces).
    """
    summary = summarize(_send("get_pattern_geometry"), pattern_index, include_points=True)
    return [Image(data=render_patterns(summary), format="png"),
            "%d pieces, %d seams" % (len(summary["pieces"]), len(summary["seams"]))]


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
    """
    return _send("create_pattern", {"points": points})


@mcp.tool()
def get_arrangement_list() -> dict:
    """Get the list of arrangement points on the avatar."""
    return _send("get_arrangement_list")


# ─── Fabric Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def get_fabric_list() -> dict:
    """Get a list of all fabrics in the current project with their indices."""
    return _send("get_fabric_list")


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
    coordinates. Each seam lists, for both sides, the pattern, optional internal shape and the
    lines it covers (coverage 1.0 = whole line).

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

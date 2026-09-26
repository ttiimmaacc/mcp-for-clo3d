# MCP for CLO3D

Control CLO3D from an AI assistant such as Claude, Codex or Cursor: ask in plain language and it
inspects your project, builds and sews pattern pieces, manages fabrics, runs the simulation,
takes pictures of the result and exports models, pattern PDFs and DXF files.

```
AI assistant  <-- MCP -->  MCP server (Python)  <-- files -->  CLO plug-in (native DLL)  -->  CLO3D
```

CLO stays fully usable while the plug-in listens in the background.

## Before you start

You need:

| What | How to check or get it |
|------|------------------------|
| **Windows 10 or 11** (64-bit) | The plug-in is a Windows DLL; macOS is not supported. |
| **CLO3D 2025.2.236**, exactly this version | CLO shows its version at the bottom left of its window (*Version: 2025.2.236*). The plug-in only works with the CLO build it was made for; for another version, [build it yourself](#or-build-the-plug-in-yourself). |
| **An MCP client** | [Claude Desktop](https://claude.ai/download), [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex), Cursor or any other app that supports MCP servers. |
| **uv** (runs the Python server and installs Python for you) | In PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"`, then open a new terminal. See [docs.astral.sh/uv](https://docs.astral.sh/uv/). |
| **Git** (uv uses it to fetch the server) | [git-scm.com/download/win](https://git-scm.com/download/win), or `winget install Git.Git`. |

You do **not** need Visual Studio or CLO's SDK unless you build the plug-in yourself.

## Setup

### 1. Download the plug-in

Download `CloMcpPlugin.dll` from the
[latest release](https://github.com/ttiimmaacc/mcp-for-clo3d/releases/latest). The release notes
say which CLO version it is for (currently **2025.2.236**). GitHub builds every release from this
repository with CLO's official SDK, and each release lists the DLL's SHA-256 checksum.

### 2. Install it so CLO loads it at startup

1. **Close CLO.**
2. Copy `CloMcpPlugin.dll` into `C:\Users\Public\Documents\CLO\Plugins\` (create the `Plugins`
   folder if it doesn't exist) and **rename the copy to `CloLibraryAPI_Plugin.dll`**. CLO loads a
   plug-in with exactly that name every time it starts.
   - If you cloned this repository, `cpp_plugin\install_autostart.bat path\to\CloMcpPlugin.dll`
     does the copy and rename for you (`install_autostart.bat /remove` undoes it).
   - If a `CloLibraryAPI_Plugin.dll` is already there, it belongs to another plug-in: keep a copy
     of it, because this replaces it.
3. **Start CLO.** Nothing visible changes: the listener starts quietly in the background.

<details>
<summary>Optional: an on/off switch in CLO's Plugins menu</summary>

Open **Plugins → Plug-in Manager → + ADD**, choose a copy of `CloMcpPlugin.dll` that is *outside*
the Plugins folder, give it a name and click **OK**. **Plugins → Plug-in → &lt;your name&gt;** then
stops or starts the listener and shows its new state. Without the autostart copy, this menu entry
alone also works, but CLO only loads it when you click it, so click it once per CLO session.

</details>

### 3. Check that it is running

Paste `%TEMP%\clo3d_mcp` into the File Explorer address bar and open `status.json`. It should say
`"state": "listening"`, and its `ticks` number should grow each time you reopen the file. If the
folder or file is missing, see [Troubleshooting](#troubleshooting).

### 4. Connect your AI assistant

Add the server to your MCP client. `uvx` downloads and runs it; there is nothing else to install.

**Claude Code**
```bash
claude mcp add clo3d -- uvx --from git+https://github.com/ttiimmaacc/mcp-for-clo3d.git mcp-for-clo3d
```

**Claude Desktop**: open *Settings → Developer → Edit Config*, add this to
`claude_desktop_config.json`, then restart Claude Desktop:
```json
{
  "mcpServers": {
    "clo3d": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ttiimmaacc/mcp-for-clo3d.git", "mcp-for-clo3d"]
    }
  }
}
```

**Codex** (`~/.codex/config.toml`)
```toml
[mcp_servers.clo3d]
command = "uvx"
args = ["--from", "git+https://github.com/ttiimmaacc/mcp-for-clo3d.git", "mcp-for-clo3d"]
tool_timeout_sec = 200   # simulations and exports can take up to 180 s
```

Connect only one client to CLO at a time, because they share the same request file.

### 5. Try it

With CLO open, describe the job the way you would to a colleague. The assistant works through
the steps in CLO, looks at the result (it can capture the 3D window and draw the 2D pieces), and
reports back. Some examples:

**Check a garment**
- *"Simulate this, then show me the front, back and side with the strain map on. Where is it
  pulling?"*
- *"Check the sewing: are any seams twisted, with one side sewn in the opposite direction?"*

**Get a style ready for production**
- *"Write 'CUT 1' and the piece name on every piece, nest them on 1400 mm wide fabric with 10 mm
  spacing, and tell me how long the marker is and how well the fabric is used."*
  (CLO lays out one fabric at a time: switch fabrics in its Print Layout Editor to measure the next.)
- *"Export that marker as a 1:1 PDF for the plotter and the whole pattern as an AAMA DXF for our
  CAD system."*

**Build something from a spec**
- *"Make a two-panel noren curtain, 1050 × 2020 mm finished, with 30 mm side hems, a 70 mm bottom
  hem and a star appliqué on front and back, hanging from a 32 mm rod."*
  ([`examples/noren_curtain.py`](examples/noren_curtain.py) is this build as a script.)
- *"Topstitch every appliqué patch 4 mm in from its edge with 0.4 mm thread."*

**Fabrics and presentation**
- *"What fabrics does this project use, and what are their weights and thicknesses? Label them
  all 60% cotton / 40% linen."*
- *"Export a GLB for the web shop and turntable images of the finished garment."*

Good to know:
- CLO's API has no undo, so ask the assistant to **save a checkpoint** before bigger changes.
- For nesting, switch CLO to **Printing Layout** mode once per session (mode dropdown at the top
  right); the assistant will tell you when it needs this.
- Some things CLO's API can't do at all, like folding cloth or placing pins; see
  [Tools](#tools) for what's covered and what isn't.

### Or build the plug-in yourself

Needed for any CLO version other than the one the release was built for.

1. Install **Visual Studio Build Tools** with the *Desktop development with C++* workload.
2. Download the CLO SDK that matches your CLO version from
   [developer.clo3d.com/download.html](https://developer.clo3d.com/download.html)
   (CLO 2025.2.236 → SDK v9.1.0, `CLO_SDK_v2025.2.236_WIN.zip`) and unzip it. CLO's SDK can't be
   redistributed, so it is not included here.
3. Clone this repository, point `CLO_SDK_DIR` at the unzipped folder (the one containing
   `CLOAPIInterface\`) and build:
   ```bat
   set CLO_SDK_DIR=C:\path\to\CLO_SDK_v2025.2.236_WIN
   cpp_plugin\build.bat
   python cpp_plugin\check_runtime.py
   ```
   `check_runtime.py` confirms that every C++ runtime function the DLL imports exists in the
   runtime DLLs your CLO install ships, because CLO loads its own copies.
4. Install `cpp_plugin\dist\CloMcpPlugin.dll` as in step 2.

> **Use the SDK for your exact CLO version.** The plug-in calls CLO through C++ vtables. Headers
> from another CLO version put functions at different slots and call the wrong ones.

## Tools

| Area | Tools |
|------|-------|
| Scene | `get_project_info`, `new_project`, `open_file`, `save_project`, `get_garment_info`, `import_file` |
| See the result | `capture_3d`: the 3D window from front, back, sides, 3/4 or top, returned to the assistant as images, optionally with CLO's `strain` or `stress` fit map. `view_patterns`: picture of the 2D pieces with every line numbered and seams coloured. `set_fit_map` |
| Collision objects | `add_rod`, `add_box`, `list_collision_objects`, `remove_collision_object`, `import_collision_object`, `get_cloth_bounds`. CLO keeps one avatar, so rods and boxes share one collision mesh, and they won't replace a human avatar unless asked |
| Hems & appliqué | `add_hem`: the turned-back layer of a hem as a strip on the reverse, sewn at the edge and the hem line (cloth cannot be folded this way: a fold angle only bends it about 20° even at full strength, however it is set). `add_applique`: a patch sewn where it sits, optionally with its reverse copy |
| Plotting & PDF | `export_pattern_sheet` (vector PDF at exactly 1:1, or PNG; pattern window or print layout; seam allowance, notches, grain lines, names, annotations...), `nest_patterns` and `marker_report` (marker length, width, utilisation per fabric, measured from the 1:1 layout), `set_fabric_width`, `set_nesting` (spacing, 1/2/4-way grain, fixed pieces), `add_pattern_annotation`, `get_pattern_annotations`, `get_print_options`. CLO's print layout exports one fabric at a time, and in a fresh CLO session the API's nesting only works after Printing Layout mode has been opened once; `clo_ui_nest_all_fabrics` / `nest_patterns(allow_ui_click=True)` click CLO's "Nest All Fabrics" through Windows UI Automation as a fallback |
| CAD / DXF | `export_dxf`: AAMA, ASTM or Gerber DXF without a dialog, read back as a check (piece names, sizes, quantities, outline boxes) |
| Topstitch styles | `create_topstitch_style` (stitch length, thread thickness, offset: CLO's API cannot set these, so a style saved from CLO once is copied with the values changed and imported), `get_topstitch_style`, `set_topstitch_style` (name, colour, lines) |
| Fabric details | `get_fabric_info`, `set_fabric_information` (name, content such as 60% cotton / 40% linen), `set_fabric_physics` (weight, thickness, stretch, shear, bending...; keeps colour, texture and name), `export_fabric` (.zfab), `apply_fabric_json` |
| Hanging on a rod | `hang_on_rod`: sews a header band to each panel's top edge, freezes it and lays a rod along it, so curtains, noren and banners hang reliably (the band stands in for the pocket; the rod lies against it). `make_rod_pocket`: a real folded pocket around the rod. Verified with a single 1000 × 1500 mm panel; with several panels it often fails to wrap |
| Checkpoints | `save_checkpoint`, `list_checkpoints`, `restore_checkpoint`: CLO's API has no undo, so save the scene before risky edits and reopen it if something goes wrong |
| Geometry | `get_pattern_geometry`: pieces with numbered outline lines (length, endpoints), internal shapes, and every seam mapped to the lines it uses |
| Patterns | `get_pattern_count`, `get_pattern_list`, `get_pattern_info`, `get_pattern_bounding_box`, `set_pattern_name`, `copy_pattern`, `delete_pattern`, `flip_pattern`, `create_pattern`, `mirror_pattern`, `unfold_pattern`, `move_pattern_2d` |
| Sewing | `sew_lines` (outline or internal-shape lines), `add_topstitch`, `list_topstitch_styles`, `set_seam_taping` |
| Lines and shapes | `add_internal_shape`, `offset_internal_line`, `convert_shape` (internal ↔ base line), `delete_point`, `delete_line` |
| Piece state | `set_pattern_state` (freeze, strengthen, solidify, hide in 3D, layer, particle distance, grain), `get_pattern_state`, `remove_all_pins` |
| Elastic and shrinkage | `set_elastic` (on/off, strength, ratio, segment and total length), `set_shrinkage` |
| 3D placement | `get_arrangement_points`, `place_pattern` (arrangement point, orientation, position, Flat/Curved), `reset_arrangement`, `get_arrangement_list` |
| Fabrics | `get_fabric_list`, `add_fabric`, `replace_fabric`, `assign_fabric_to_pattern`, `set_fabric_color`, `get_fabric_for_pattern`, `delete_fabric` |
| Avatar | `get_avatars`, `import_avatar`, `show_avatar` |
| Export | `export_obj`, `export_fbx`, `export_glb`, `export_gltf`, `export_thumbnail`, `export_snapshot`, `export_turntable`, `export_tech_pack` |
| Simulation | `simulate`, `set_simulation_quality` |
| Colorways | `get_colorways`, `set_current_colorway` |

Line-based tools take the line indices that `get_pattern_geometry` (or the `view_patterns` picture) shows. Checkpoints are saved next to the request files. CLO saves them like "Save As", so its open file becomes the checkpoint copy; `save_checkpoint` returns the original path to save back to. Restoring a large scene (avatar plus simulation data) can take a few minutes. The plug-in checks
every pattern and line index before calling CLO, because CLO doesn't check them. Line-creating
tools report how many internal shapes CLO actually created, because CLO creates nothing, without
an error, for requests it can't do.

**Not possible through CLO's API (2025.2):** creating pins (they can only be removed), folding
cloth or pleats (a fold angle on a line only bends the cloth about 20° even at full strength,
whether set through the API, CLO's Property Editor or its Fold Arrangement tool), free 3D move or rotate of a piece (placement goes through avatar arrangement points),
and editing or removing an existing seam. Two SDK functions exist but did nothing in CLO 2025.2 testing, so they aren't exposed: `MovePatternPoint` (points never move) and `DistribueInternalLinesbetweenSegments` (created nothing on any pair of lines). To hold pieces in place, freeze or strengthen them. For
turned-back layers such as hems, use `add_hem`, which models the fold as a second layer.

## How it works

The CLO side is a small C++ plug-in built on CLO's official SDK. It runs a timer on CLO's UI
thread, so every request runs on the main thread between UI events, in roughly 50 ms.

- The **MCP server** (`src/clo3d_mcp`) writes `request.json` to `%TEMP%\clo3d_mcp` (override
  with `CLO3D_MCP_DIR`) and waits up to 180 s for `response.json`. Both sides write to a temp
  file and rename it into place.
- The **plug-in** (`cpp_plugin/CloMcpPlugin.cpp`) creates a hidden message-only window with a
  50 ms timer. CLO's event loop dispatches the timer, so the plug-in reads the request, calls the
  CLO API on the main thread and writes the response. It never blocks CLO.
- The plug-in pins itself in memory, because CLO loads the DLL to read its menu name and unloads it
  again during registration. Only one listener runs per CLO process.
- Native faults inside a command are caught (built with `/EHa`) and returned as an error
  instead of taking CLO down.
- To stop the listener without the menu, create an empty file named `stop` in the request folder.

## Development

```bash
uv run --with pytest pytest          # server tests (mock plug-in)
cpp_plugin\test\build_tests.bat      # builds two stand-in hosts that load the real DLL outside CLO
python cpp_plugin\test\smoke_test.py # DLL + stand-in host + MCP client, end to end
```

`host.exe` loads the plug-in and runs a Win32 event loop. `host_add.exe` reproduces the
Plug-in Manager's load → read name → unload sequence. Both use CLO's real `CLOAPIInterface.dll`
with fake API objects, so you can test the plug-in and the MCP client without CLO.

### Releases

[`.github/workflows/build.yml`](.github/workflows/build.yml) runs on every push and pull request.
It downloads CLO's SDK from CLO's official link (checksum-pinned, never committed), builds the DLL,
checks its imports against CLO's runtime export lists in `cpp_plugin/clo_exports/<version>/`, and
runs the tests and smoke test. Pushing a tag publishes a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

To support a new CLO version, update `CLO_VERSION`, `CLO_SDK_URL` and `CLO_SDK_SHA256` in the
workflow. Then, on a machine with that CLO installed, run
`python cpp_plugin\check_runtime.py --write-exports cpp_plugin\clo_exports\<version>` and commit
the result.

## Troubleshooting

- **`%TEMP%\clo3d_mcp\status.json` is missing:** CLO didn't load the plug-in. Check that the
  file in `C:\Users\Public\Documents\CLO\Plugins\` is named exactly `CloLibraryAPI_Plugin.dll`,
  that it matches your CLO version, and that you restarted CLO after copying it.
- **The client times out:** check `status.json`. If `ticks` isn't rising, start the listener
  from **Plugins → Plug-in** (with the optional menu entry), or restart CLO.
- **`uvx` is not recognised:** open a new terminal after installing uv, or restart your MCP
  client so it picks up the new PATH.
- **Rebuilding fails with `LNK1104: cannot open file ...CloMcpPlugin.dll`:** CLO has the DLL
  loaded. Close CLO, or rename the loaded DLL (Windows allows renaming a file that's in use) and
  rebuild. CLO keeps using the old copy until it restarts. With autostart, run
  `install_autostart.bat` again after rebuilding, with CLO closed.
- **CLO crashes when adding the plug-in, or calls do odd things:** the SDK doesn't match your CLO
  version.

## Credits and licence

Licensed under MIT; see [LICENSE](LICENSE).

The Python MCP server began as [Ubani-Studio/clo3d-mcp](https://github.com/Ubani-Studio/clo3d-mcp)
by Violet Sphinx (MIT). This project has since grown into a separate codebase: its Python CLO
plug-in was replaced by a new native C++ plug-in, and most tools were added or rewritten.

CLO's SDK is **not** included and is not covered by this licence. See [NOTICE](NOTICE).

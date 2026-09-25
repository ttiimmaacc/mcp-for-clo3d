# MCP for CLO3D

Control CLO3D from Claude, Codex, Cursor or any MCP client: inspect projects, build and edit
pattern pieces, manage fabrics and colorways, run simulations and export models, all from a chat.

```
AI assistant  <-- MCP -->  MCP server (Python)  <-- files -->  CLO plug-in (native DLL)  -->  CLO3D
                           src/clo3d_mcp          %TEMP%\clo3d_mcp   cpp_plugin/
```

CLO stays fully usable while the plug-in listens. Requests are answered in roughly 50 ms.

Tested with **CLO 2025.2.236 on Windows**.

## Why a native plug-in

CLO's embedded Python cannot serve requests in the background:

- background threads stop running once a script returns,
- a polling loop on the main thread freezes CLO,
- CLO ships no Qt bindings for Python, so there is no timer to hook, and
- `rest_api.CallbackRestRequest`, the only asynchronous call, cannot accept a Python callback
  in this build.

So the CLO side is a small C++ plug-in built on CLO's official SDK. It runs a timer on CLO's UI
thread, so every request is executed on the main thread between UI events.

## Setup

### 1. Get the plug-in DLL

Download `CloMcpPlugin.dll` from the
[latest release](https://github.com/ttiimmaacc/mcp-for-clo3d/releases/latest). Each release says
which CLO version it's for, and the DLL only works with that exact CLO build (currently
**2025.2.236**). GitHub Actions builds every release from this repo against CLO's official SDK,
and each release includes the DLL's SHA-256 checksum.

<details>
<summary>Or build it yourself (needed for any other CLO version)</summary>

CLO's SDK files can't be redistributed, so you download the SDK yourself.

1. Install **Visual Studio Build Tools** with the *Desktop development with C++* workload.
2. Download the CLO SDK that matches your CLO version from
   [developer.clo3d.com/download.html](https://developer.clo3d.com/download.html)
   (CLO 2025.2.236 → SDK v9.1.0, `CLO_SDK_v2025.2.236_WIN.zip`) and unzip it.
3. Point `CLO_SDK_DIR` at the unzipped folder (the one containing `CLOAPIInterface\`) and build:
   ```bat
   set CLO_SDK_DIR=C:\path\to\CLO_SDK_v2025.2.236_WIN
   cpp_plugin\build.bat
   python cpp_plugin\check_runtime.py
   ```
   `check_runtime.py` confirms that every C++ runtime function the DLL imports exists in the
   runtime DLLs your CLO install ships, because CLO loads its own copies.

> **Use the SDK for your exact CLO version.** The plug-in calls CLO through C++ vtables. Headers
> from another CLO version put functions at different slots and will call the wrong ones.

</details>

### 2. Load it in CLO

**Recommended: autostart plus a menu toggle.**

1. Close CLO and run `cpp_plugin\install_autostart.bat path\to\CloMcpPlugin.dll`. This copies
   the DLL to `C:\Users\Public\Documents\CLO\Plugins\CloLibraryAPI_Plugin.dll`, which CLO loads at
   startup, so the listener runs as soon as CLO opens. `install_autostart.bat /remove` undoes it.
2. Optionally, for an on/off switch: open **Plugins → Plug-in Manager → + ADD**, choose
   `CloMcpPlugin.dll` (a copy outside the Plugins folder), name it, and click **OK**. This adds
   **Plugins → Plug-in → <your name>**, which stops or starts the listener and shows a message box
   with the new state. The autostart copy adds no menu entry of its own, so the listener is listed
   once.

Without autostart, the Plug-in Manager entry alone also works. CLO only loads it when you click
the menu item, so click it once per CLO session.

To check that it is running, open `%TEMP%\clo3d_mcp\status.json`. It should show
`"state": "listening"` and a `ticks` count that keeps rising.

### 3. Connect your MCP client

The server runs with [uv](https://docs.astral.sh/uv/).

**Claude Code**
```bash
claude mcp add clo3d -- uvx --from git+https://github.com/ttiimmaacc/mcp-for-clo3d.git mcp-for-clo3d
```

**Claude Desktop** (`claude_desktop_config.json`)
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

Only connect one client to CLO at a time, because they share the same request file.

Then ask things like *"What's in this project?"*, *"Create a rectangle pattern 400 × 600 mm"*,
*"Run 100 simulation steps and export a GLB to my desktop"*.

## Tools

| Area | Tools |
|------|-------|
| Scene | `get_project_info`, `new_project`, `open_file`, `save_project`, `get_garment_info`, `import_file` |
| Geometry | `get_pattern_geometry`: pieces with numbered outline lines (length, endpoints), internal shapes, and every seam mapped to the lines it uses |
| Patterns | `get_pattern_count`, `get_pattern_list`, `get_pattern_info`, `get_pattern_bounding_box`, `set_pattern_name`, `copy_pattern`, `delete_pattern`, `flip_pattern`, `create_pattern`, `mirror_pattern`, `unfold_pattern`, `move_pattern_2d` |
| Sewing | `sew_lines` (outline or internal-shape lines), `add_topstitch`, `list_topstitch_styles`, `set_seam_taping` |
| Lines and shapes | `add_internal_shape`, `offset_internal_line`, `distribute_internal_lines`, `convert_shape`, `move_point`, `delete_point`, `delete_line` |
| Piece state | `set_pattern_state` (freeze, strengthen, solidify, hide in 3D, layer, particle distance, grain), `get_pattern_state`, `remove_all_pins` |
| Elastic and shrinkage | `set_elastic` (on/off, strength, ratio, segment and total length), `set_shrinkage` |
| 3D placement | `get_arrangement_points`, `place_pattern` (arrangement point, orientation, position, Flat/Curved), `reset_arrangement`, `get_arrangement_list` |
| Fabrics | `get_fabric_list`, `add_fabric`, `replace_fabric`, `assign_fabric_to_pattern`, `set_fabric_color`, `get_fabric_for_pattern`, `delete_fabric` |
| Avatar | `get_avatars`, `import_avatar`, `show_avatar` |
| Export | `export_obj`, `export_fbx`, `export_glb`, `export_gltf`, `export_thumbnail`, `export_snapshot`, `export_turntable`, `export_tech_pack` |
| Simulation | `simulate`, `set_simulation_quality` |
| Colorways | `get_colorways`, `set_current_colorway` |

Line-based tools take the line indices that `get_pattern_geometry` returns. The plug-in checks
every pattern and line index before calling CLO, because CLO doesn't check them. Line-creating
tools report how many internal shapes CLO actually created, because CLO creates nothing, without
an error, for requests it can't do.

**Not possible through CLO's API (2025.2):** creating pins (they can only be removed), pleats or
fold angles, free 3D move or rotate of a piece (placement goes through avatar arrangement points),
and editing or removing an existing seam. To hold pieces in place, freeze or strengthen them. For
pleats, draw fold lines with the internal-line tools and let the simulation fold them.

## How it works

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

- **The client times out:** check `status.json`. If it's missing or `ticks` isn't rising, start
  the listener from **Plugins → Plug-in**.
- **Rebuilding fails with `LNK1104: cannot open file ...CloMcpPlugin.dll`:** CLO has the DLL
  loaded. Close CLO, or rename the loaded DLL (Windows allows renaming a file that's in use) and
  rebuild. CLO keeps using the old copy until it restarts. With autostart, run
  `install_autostart.bat` again after rebuilding, with CLO closed.
- **CLO crashes when adding the plug-in, or calls do odd things:** the SDK doesn't match your CLO
  version.

## Credits and licence

MIT, see [LICENSE](LICENSE). The Python MCP server started from
[Ubani-Studio/clo3d-mcp](https://github.com/Ubani-Studio/clo3d-mcp) by Violet Sphinx (MIT).
This project replaces its Python CLO plug-in with the native plug-in.

CLO's SDK is **not** included and is not covered by this licence. See [NOTICE](NOTICE).

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

### 2. Register it in CLO

1. **Plugins → Plug-in Manager → + ADD**, choose `CloMcpPlugin.dll`, name it, **OK**.
2. **Plugins → Plug-in → CLO MCP Listener (start/stop)** now appears. The listener starts
   when CLO loads the plug-in. Clicking the item stops or starts it and shows a message box with
   the new state.

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
| Patterns | `get_pattern_count`, `get_pattern_list`, `get_pattern_info`, `get_pattern_bounding_box`, `set_pattern_name`, `copy_pattern`, `delete_pattern`, `flip_pattern`, `create_pattern`, `get_arrangement_list` |
| Fabrics | `get_fabric_list`, `add_fabric`, `replace_fabric`, `assign_fabric_to_pattern`, `set_fabric_color`, `get_fabric_for_pattern` |
| Export | `export_obj`, `export_fbx`, `export_glb`, `export_gltf`, `export_thumbnail`, `export_snapshot`, `export_turntable`, `export_tech_pack` |
| Simulation | `simulate` |
| Colorways | `get_colorways`, `set_current_colorway` |

The plug-in also implements `ping`, fabric/colorway deletion, colorway copy/rename, avatar
queries and simulation quality. These aren't exposed as MCP tools yet.

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
  loaded. Close CLO, then rebuild.
- **CLO crashes when adding the plug-in, or calls do odd things:** the SDK doesn't match your CLO
  version.

## Credits and licence

MIT, see [LICENSE](LICENSE). The Python MCP server started from
[Ubani-Studio/clo3d-mcp](https://github.com/Ubani-Studio/clo3d-mcp) by Violet Sphinx (MIT).
This project replaces its Python CLO plug-in with the native plug-in.

CLO's SDK is **not** included and is not covered by this licence. See [NOTICE](NOTICE).

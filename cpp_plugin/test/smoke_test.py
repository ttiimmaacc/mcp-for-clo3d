"""End-to-end smoke test of CloMcpPlugin.dll without CLO.

Runs host_add.exe (CLO's Plug-in Manager load -> read name -> unload sequence, then a second
copy toggled via DoFunction) and talks to the plug-in through the MCP server's own client.
Build first with build.bat and test/build_tests.bat.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))

DLL = os.path.join(ROOT, "cpp_plugin", "dist", "CloMcpPlugin.dll")
HOST = os.path.join(HERE, "host_add.exe")


def main():
    work = tempfile.mkdtemp(prefix="clo_mcp_smoke_")
    comm = os.path.join(work, "comm")
    copy = os.path.join(work, "copy", "CloMcpPlugin.dll")
    os.makedirs(os.path.dirname(copy))
    shutil.copy(DLL, copy)
    os.environ["CLO3D_MCP_DIR"] = comm

    host = subprocess.Popen([HOST, DLL, copy, "12"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 10
    while not os.path.exists(os.path.join(comm, "status.json")):
        if time.time() > deadline or host.poll() is not None:
            print(host.communicate()[0])
            sys.exit("FAIL: listener never started")
        time.sleep(0.1)

    from clo3d_mcp.connection import CLO3DConnectionError, get_connection
    conn = get_connection()
    failures = []

    def check(name, got, expected):
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append(name)
        print("%s %-18s %s" % (status, name, got))

    check("ping", conn.send_command("ping", retries=1).get("pong"), True)
    patterns = conn.send_command("get_pattern_list", retries=1)
    check("get_pattern_list", [p["name"] for p in patterns["patterns"]], ["Piece 0", "Piece 1"])
    check("set_pattern_name", conn.send_command("set_pattern_name", {"pattern_index": 0, "name": "Front"}, retries=1),
          {"index": 0, "name": "Front"})
    check("set_pattern_state", conn.send_command("set_pattern_state", {"pattern_index": 1, "frozen": True}, retries=1),
          {"pattern_index": 1, "frozen": True})
    # Invalid input must come back as a clear error before anything reaches CLO's API.
    for label, cmd, params, expected in [
        ("bogus_command", "bogus_command", None, "Unknown command"),
        ("get_project_info", "get_project_info", None, "native exception"),
        ("bad pattern index", "set_pattern_state", {"pattern_index": 5, "frozen": True}, "out of range (2 patterns)"),
        ("bad line index", "sew_lines", {"pattern_a": 0, "line_a": 3, "pattern_b": 1, "line_b": 0}, "out of range"),
        ("nothing to set", "set_pattern_state", {"pattern_index": 0}, "no state given"),
        ("bad shape style", "place_pattern", {"pattern_index": 0, "shape_style": "Round"}, "Flat"),
        ("bad fit map", "set_fit_map", {"mode": "heat"}, "strain"),
        ("bad camera", "capture_3d", {"camera": 12, "file_path": "x.png"}, "camera must be 0-9"),
    ]:
        try:
            conn.send_command(cmd, params, retries=1)
            check(label, "no error", expected)
        except CLO3DConnectionError as e:
            check(label, expected if expected in str(e) else str(e), expected)

    output = host.communicate(timeout=30)[0]
    print(output.strip())
    for line in ("module still mapped: yes", "UI loop survived, listeners in process: 1",
                 "DoFunction (stop) -> listeners: 0", "DoFunction (start) -> listeners: 1", "done"):
        check("host: " + line.split(",")[0][:30], line in output, True)
    check("host exit code", host.returncode, 0)

    shutil.rmtree(work, ignore_errors=True)
    if failures:
        sys.exit("FAIL: " + ", ".join(failures))
    print("OK: smoke test passed")


if __name__ == "__main__":
    main()

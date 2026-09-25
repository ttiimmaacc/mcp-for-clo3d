"""Verify every import of CloMcpPlugin.dll exists in the DLLs CLO itself ships.

CLO loads its own copies of the C++ runtime (and CLOAPIInterface.dll) from its install
folder, so the plug-in may only import symbols those exact files export.

    python check_runtime.py                        check against the local CLO install
    python check_runtime.py --exports clo_exports/2025.2.236 --clo-api-dll <SDK>/CLOAPIInterface/Lib/CLOAPIInterface.dll
                                                   check against saved runtime export lists (CI)
    python check_runtime.py --write-exports clo_exports/2025.2.236
                                                   save the local CLO install's runtime export lists

CLOAPIInterface's exports are CLO's function names, so they are not saved here; in CI they are
read from the SDK's CLOAPIInterface.dll, which exports the same set as the installed one.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

CLO_DIR = r"C:\Program Files\CLO Standalone OnlineAuth"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "dist", "CloMcpPlugin.dll")
RUNTIME = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
CHECKED = RUNTIME + ("cloapiinterface.dll",)


def find_dumpbin():
    pattern = r"C:\Program Files*\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe"
    found = sorted(glob.glob(pattern))
    if not found:
        sys.exit("dumpbin.exe not found; install the MSVC build tools")
    return found[-1]


DUMPBIN = find_dumpbin()


def dumpbin(*args):
    return subprocess.run([DUMPBIN, "/nologo", *args], capture_output=True, text=True).stdout


def imports(dll):
    current, result = None, {}
    for line in dumpbin("/imports", dll).splitlines():
        m = re.match(r"^\s{4}(\S+\.dll)\s*$", line, re.I)
        if m:
            current = m.group(1).lower()
            result[current] = set()
            continue
        m = re.match(r"^\s+[0-9A-F]+\s+(\S+)\s*$", line)
        if current and m:
            result[current].add(m.group(1))
    return result


def exports(dll):
    names = set()
    for line in dumpbin("/exports", dll).splitlines():
        m = re.match(r"^\s+\d+\s+[0-9A-F]+\s+[0-9A-F]{8}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def load_exports(dll, exports_dir, clo_api_dll):
    if dll == "cloapiinterface.dll" and clo_api_dll:
        return exports(clo_api_dll)
    if exports_dir:
        with open(os.path.join(exports_dir, dll + ".txt"), encoding="ascii") as f:
            return {line.strip() for line in f if line.strip()}
    return exports(os.path.join(CLO_DIR, dll))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exports", help="folder of saved export lists instead of the CLO install")
    parser.add_argument("--write-exports", help="save the CLO install's export lists to this folder")
    parser.add_argument("--clo-api-dll", help="CLOAPIInterface.dll to check against (e.g. the SDK's copy)")
    parser.add_argument("--dll", default=PLUGIN)
    args = parser.parse_args()

    if args.write_exports:
        os.makedirs(args.write_exports, exist_ok=True)
        for dll in RUNTIME:
            names = sorted(exports(os.path.join(CLO_DIR, dll)))
            with open(os.path.join(args.write_exports, dll + ".txt"), "w", encoding="ascii", newline="\n") as f:
                f.write("\n".join(names) + "\n")
            print("%-22s %5d exports saved" % (dll, len(names)))
        return 0

    ok = True
    for dll, syms in sorted(imports(args.dll).items()):
        if dll not in CHECKED:
            print("%-22s %3d imports (system DLL, not checked)" % (dll, len(syms)))
            continue
        missing = sorted(syms - load_exports(dll, args.exports, args.clo_api_dll))
        print("%-22s %3d imports, %d missing from CLO's copy" % (dll, len(syms), len(missing)))
        for s in missing:
            print("    MISSING", s)
        ok = ok and not missing
    print("OK: all runtime imports exist in CLO's DLLs" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

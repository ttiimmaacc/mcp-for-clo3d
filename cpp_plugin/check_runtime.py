"""Verify every import of CloMcpPlugin.dll exists in the DLLs CLO itself ships.

CLO loads its own copies of the C++ runtime (and CLOAPIInterface.dll) from its install
folder, so the plug-in may only import symbols those exact files export.
"""
import glob
import os
import re
import subprocess
import sys

CLO_DIR = r"C:\Program Files\CLO Standalone OnlineAuth"
HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "dist", "CloMcpPlugin.dll")
DUMPBIN = sorted(glob.glob(r"C:\Program Files (x86)\Microsoft Visual Studio\*\BuildTools\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe"))[-1]
CHECKED = ("msvcp140", "vcruntime140", "vcruntime140_1", "cloapiinterface")


def dumpbin(*args):
    return subprocess.run([DUMPBIN, "/nologo", *args], capture_output=True, text=True).stdout


def imports():
    out, current, result = dumpbin("/imports", PLUGIN), None, {}
    for line in out.splitlines():
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


def main():
    ok = True
    for dll, syms in sorted(imports().items()):
        stem = dll[:-4]
        if stem not in CHECKED:
            print("%-22s %3d imports (system DLL, not checked)" % (dll, len(syms)))
            continue
        path = os.path.join(CLO_DIR, dll)
        missing = sorted(syms - exports(path))
        print("%-22s %3d imports, %d missing from CLO's copy" % (dll, len(syms), len(missing)))
        for s in missing:
            print("    MISSING", s)
        ok = ok and not missing
    print("OK: all runtime imports exist in CLO's DLLs" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

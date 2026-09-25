"""Read-only probe: run once in CLO's Python Editor with a garment open.

Saves what CLO's API reports about pattern geometry, lines and seams to
%TEMP%/clo3d_mcp/probe/ so the MCP tools can be designed around the real data format.
It only reads and exports; the scene is not modified. Finishes in a second or two.
"""
import json
import os

import pattern_api

OUT = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "clo3d_mcp", "probe")
if not os.path.exists(OUT):
    os.makedirs(OUT)
report = {}


def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else json.dumps(text, indent=1))


def attempt(label, fn):
    try:
        value = fn()
        report[label] = "ok"
        return value
    except Exception as e:
        report[label] = "%s: %s" % (type(e).__name__, e)
        return None


count = attempt("GetPatternCount", pattern_api.GetPatternCount) or 0
report["pattern_count"] = count

attempt("ExportPatternJSON", lambda: pattern_api.ExportPatternJSON(os.path.join(OUT, "export_pattern.json")))
save("stitches.json", attempt("GetAllStitchProperty", pattern_api.GetAllStitchProperty) or "")
save("input_info_all.json", attempt("GetPatternInputInformation()", pattern_api.GetPatternInputInformation) or "")
for i in range(min(count, 3)):
    save("input_info_%d.json" % i, attempt("GetPatternInputInformation(%d)" % i,
                                           lambda i=i: pattern_api.GetPatternInputInformation(i)) or "")

# Line lengths of pattern 0, to see how line indices behave past the last line
lengths = []
for line in range(40):
    lengths.append(attempt("GetLineLength(0,%d)" % line, lambda line=line: pattern_api.GetLineLength(0, line)))
report["pattern0_line_lengths"] = lengths
report["seam_count"] = attempt("GetSeamlinePairGroupCount", pattern_api.GetSeamlinePairGroupCount)

save("report.json", report)
print("[probe] wrote " + OUT)

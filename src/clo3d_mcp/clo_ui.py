"""Opt-in clicks in CLO's own UI, for steps its API cannot do, through Windows UI Automation.

Probed in CLO 2025.2.236 (Qt 5): toolbar buttons are exposed with their visible names. The one
used here is the Print Layout Editor's "Nest All Fabrics", which sets up an empty print layout
(the API's nesting does nothing until CLO's own nesting has run once). Pop-up menus are not
usable this way: while one is open CLO stops answering UI Automation (the mode switcher's list
timed out), so the mode must still be switched by the user.
"""

import subprocess

_NEST_ALL = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$proc = Get-Process | Where-Object { $_.ProcessName -like "CLO*" -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { "NO_CLO"; exit }
$root = $A::FromHandle($proc.MainWindowHandle)
$cond = New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, "Nest All Fabrics")
$button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if (-not $button) { "NOT_FOUND"; exit }
$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
"CLICKED"
"""


def nest_all_fabrics(timeout_s=30.0):
    """Click "Nest All Fabrics" in CLO's Print Layout Editor. Returns "CLICKED", or raises
    with what to do when CLO is not running or not in Printing Layout mode."""
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _NEST_ALL],
                                capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError("CLO's UI did not answer within %.0f s (is a menu or dialog open in CLO?)" % timeout_s)
    status = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()
    if status == "NO_CLO":
        raise RuntimeError("CLO is not running")
    if status == "NOT_FOUND":
        raise RuntimeError("CLO's 'Nest All Fabrics' button is not showing: switch CLO to Printing Layout "
                           "mode (mode dropdown at the top right of CLO's window) and retry")
    if status != "CLICKED":
        raise RuntimeError("clicking 'Nest All Fabrics' failed: %s" % status)
    return status

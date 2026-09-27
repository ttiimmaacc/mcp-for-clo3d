"""Opt-in clicks in CLO's own UI, for steps its API cannot do, through Windows UI Automation.

Probed in CLO 2025.2.236 (Qt 5): toolbar buttons are exposed with their visible names. The one
used here is the Print Layout Editor's "Nest All Fabrics", which sets up an empty print layout
(the API's nesting does nothing until CLO's own nesting has run once). Pop-up menus are not
usable this way: while one is open CLO stops answering UI Automation (the mode switcher's list
timed out), so the mode must still be switched by the user.

Dialogs: CLO's message boxes (Qt) are top-level windows owned by CLO's main window, which is
disabled while one is open. Finding one is a cheap window listing (no PowerShell); reading its
title, message (a QTextBrowser, readable through the Text pattern) and buttons, and clicking a
button, go through UI Automation. A dialog opened by an API call blocks that call inside CLO until
it is answered, so the connection checks for one while it waits for a reply.
"""

import ctypes
import json
import os
import subprocess
import sys
import time

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


# ─── Dialogs ───────────────────────────────────────────────────────────────


def _user32():
    return ctypes.windll.user32 if sys.platform == "win32" else None


def find_dialog(pid):
    """Handle of an open modal dialog of CLO process pid, or None: a visible, enabled window
    owned by a window that is disabled (CLO's main window while the dialog is up). CLO's
    progress windows are owned too, but disabled, so they are skipped."""
    user32 = _user32()
    if not user32 or not pid:
        return None
    from ctypes import wintypes
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
            owner = user32.GetWindow(hwnd, 4)   # GW_OWNER
            if owner and not user32.IsWindowEnabled(owner):
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


_READ = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$e = $A::FromHandle([IntPtr]%d)
$title = $e.Current.Name; $texts = @(); $buttons = @()
foreach ($d in $e.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
  $type = $d.Current.ControlType.ProgrammaticName; $name = $d.Current.Name
  if ($type -eq "ControlType.Button" -and $name) { $buttons += $name }
  elseif ($type -eq "ControlType.Text") {
    if ($d.Current.AutomationId -like "*titleLabel") { $title = $name }
    elseif ($name) { $texts += $name }
    else {
      try { $t = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(4000); if ($t) { $texts += $t } } catch {}
    }
  }
}
@{ title = $title; message = ($texts -join "`n"); buttons = $buttons } | ConvertTo-Json -Compress
"""

_CLICK = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$e = $A::FromHandle([IntPtr]%d)
$c = New-Object System.Windows.Automation.AndCondition(
  (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)),
  (New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, "%s")))
$b = $e.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
if (-not $b) { "NOT_FOUND"; exit }
$b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
"CLICKED"
"""


def _powershell(script, timeout_s=30.0):
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=timeout_s)
    return result.stdout.strip()


def read_dialog(hwnd):
    """A dialog's title, message and button names."""
    out = _powershell(_READ % hwnd)
    try:
        info = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return {"title": "", "message": "", "buttons": [], "error": out[-300:]}
    buttons = info.get("buttons") or []
    info["buttons"] = [buttons] if isinstance(buttons, str) else buttons
    info["title"] = (info.get("title") or "").replace(" - CLO Standalone OnlineAuth", "")
    return info


def click_dialog(hwnd, button):
    """Click a dialog's button by its visible name. True when clicked."""
    if '"' in button or "`" in button or "$" in button:
        raise ValueError("unexpected characters in the button name")
    return _powershell(_CLICK % (hwnd, button)).endswith("CLICKED")


# dialogs with only these buttons just report something; they are answered with OK or Close
_ACK_ONLY = {"OK", "Close", "Cancel"}


def default_answer(info):
    """The button to click without asking, or None: only dialogs whose buttons merely acknowledge
    (OK / Close, with or without Cancel) are answered automatically."""
    buttons = info.get("buttons") or []
    if buttons and set(buttons) <= _ACK_ONLY:
        for name in ("OK", "Close"):
            if name in buttons:
                return name
    return None


def is_failure(info):
    message = (info.get("message") or "").lower()
    return message.startswith("failed") or " error" in message or message.startswith("error") \
        or (info.get("title") or "").lower().startswith("error")


# ─── Restart ───────────────────────────────────────────────────────────────

DEFAULT_EXE = r"C:\Program Files\CLO Standalone OnlineAuth\CLO_Standalone_OnlineAuth_x64.exe"
PLUGIN_PATH = r"C:\Users\Public\Documents\CLO\Plugins\CloLibraryAPI_Plugin.dll"


def clo_process():
    """(pid, exe path) of the running CLO, or (None, None)."""
    out = _powershell('Get-Process | Where-Object { $_.ProcessName -like "CLO*" -and $_.MainWindowHandle -ne 0 } '
                      '| Select-Object -First 1 | ForEach-Object { "$($_.Id)|$($_.Path)" }')
    if "|" not in out:
        return None, None
    pid, path = out.splitlines()[-1].split("|", 1)
    return int(pid), path or None


def kill(pid, timeout_s=60.0):
    """End CLO without saving (no dialogs)."""
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
    end = time.time() + timeout_s
    while time.time() < end:
        if clo_process()[0] != pid:
            return True
        time.sleep(1)
    return False


def start(exe):
    """Start CLO detached from this process."""
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

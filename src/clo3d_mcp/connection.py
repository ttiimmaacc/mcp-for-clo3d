"""
CLO3D file-based connection client.

Communicates with the CLO3D plugin via a shared directory.
The MCP server writes request.json, the plugin reads it, processes,
and writes response.json. Both sides use atomic writes (temp + rename).

On WSL, auto-detects the Windows temp directory for the shared path.
Override with CLO3D_MCP_DIR environment variable if needed.
"""

import json
import os
import time
import uuid

TIMEOUT = 180  # seconds to wait for a reply while CLO is not reporting work on this request
MAX_BUSY = 3600  # seconds to keep waiting while CLO reports it is still running this request
POLL_INTERVAL = 0.05  # seconds between file checks
MAX_RETRIES = 3
RETRY_DELAY = 1.0
DIALOG_CHECK = 1.0  # seconds between checks for a CLO dialog while waiting for a reply


class CLO3DConnectionError(Exception):
    """Base class for all errors talking to CLO3D."""


class CLO3DNotRunningError(CLO3DConnectionError):
    """The request could not be delivered (nothing was sent), so it is safe to retry."""


class CLO3DCommandError(CLO3DConnectionError):
    """CLO ran the command and reported an error."""


class CLO3DTimeoutError(CLO3DConnectionError):
    """No reply in time. The command may still run or have run, so it is never re-sent."""


class CLO3DDialogError(CLO3DConnectionError):
    """CLO opened a dialog that needs a decision; the command waits inside CLO until it is
    answered (answer_dialog), which then returns the command's reply."""

    def __init__(self, dialog):
        self.dialog = dialog
        super().__init__("CLO is waiting on a dialog: %r %r, buttons %s. The command is paused inside CLO "
                         "until a button is clicked (answer_clo_dialog)."
                         % (dialog.get("title"), dialog.get("message"), dialog.get("buttons")))


def _find_comm_dir():
    """Determine the shared communication directory.

    Priority:
    1. CLO3D_MCP_DIR env var (explicit override)
    2. Windows %TEMP%/clo3d_mcp via WSL mount (auto-detect)
    3. System temp dir fallback
    """
    # 1. Explicit override
    env_dir = os.environ.get("CLO3D_MCP_DIR")
    if env_dir:
        return env_dir

    # 2. Auto-detect WSL: look for Windows user temp via /mnt/c
    # CLO3D runs on Windows, so the plugin writes to Windows %TEMP%
    if os.path.isdir("/mnt/c/Users"):
        # Try to find the Windows user from /mnt/c/Users
        try:
            users = [
                d
                for d in os.listdir("/mnt/c/Users")
                if d not in ("Public", "Default", "Default User", "All Users")
                and os.path.isdir(os.path.join("/mnt/c/Users", d))
            ]
            for user in users:
                temp_dir = os.path.join(
                    "/mnt/c/Users", user, "AppData", "Local", "Temp", "clo3d_mcp"
                )
                # If the dir already exists (plugin is running), use it
                if os.path.isdir(temp_dir):
                    return temp_dir
            # If none found yet, use the first real user
            if users:
                return os.path.join(
                    "/mnt/c/Users", users[0], "AppData", "Local", "Temp", "clo3d_mcp"
                )
        except OSError:
            pass

    # 3. Fallback: local temp
    return os.path.join(os.environ.get("TEMP", "/tmp"), "clo3d_mcp")


class CLO3DConnection:
    """File-based IPC client for the CLO3D plugin."""

    _instance = None

    def __new__(cls, comm_dir=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, comm_dir=None):
        if self._initialized:
            return
        self.comm_dir = comm_dir or _find_comm_dir()
        self.request_file = os.path.join(self.comm_dir, "request.json")
        self.response_file = os.path.join(self.comm_dir, "response.json")
        self.pending = None      # a request paused on a dialog: (request, answers)
        self._initialized = True

    @property
    def connected(self):
        """Check if the communication directory exists (plugin is likely running)."""
        return os.path.isdir(self.comm_dir)

    def connect(self):
        """Ensure the communication directory exists."""
        if not os.path.isdir(self.comm_dir):
            raise CLO3DConnectionError(
                "Cannot find CLO3D communication directory at: " + self.comm_dir + ". "
                "Is CLO3D running with the MCP plugin loaded?"
            )

    def disconnect(self):
        """No-op for file-based connection (kept for API compatibility)."""
        pass

    def send_command(self, command_type, params=None, retries=MAX_RETRIES, answers=None):
        """
        Send a command to CLO3D and return the result.

        Writes request.json, waits for response.json, returns the parsed result. answers maps
        text found in a dialog's title or message to the button to click if CLO asks during this command.
        """
        request = {
            "id": str(uuid.uuid4()),
            "type": command_type,
            "params": params or {},
        }

        # Only retry when nothing was delivered. Re-sending after a timeout or an error reply
        # could run a command twice (e.g. a second simulation after a slow first one).
        for attempt in range(retries):
            try:
                return self._do_send(request, answers)
            except CLO3DNotRunningError:
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                raise

    def _busy_with(self, request_id):
        """True while the plug-in reports it is still executing this request."""
        try:
            with open(os.path.join(self.comm_dir, "status.json"), "r") as f:
                status = json.load(f)
        except (OSError, ValueError):
            return False
        return status.get("state") == "busy" and status.get("request_id") == request_id

    def _clo_pid(self):
        try:
            with open(os.path.join(self.comm_dir, "status.json"), "r") as f:
                return json.load(f).get("pid")
        except (OSError, ValueError):
            return None

    def _handle_dialog(self, request, answers, seen):
        """Answer a dialog CLO opened during this request, or raise CLO3DDialogError."""
        from clo3d_mcp import clo_ui
        hwnd = clo_ui.find_dialog(self._clo_pid())
        if not hwnd:
            return
        info = clo_ui.read_dialog(hwnd)
        if not info.get("buttons"):
            return   # still being built, or a transient window: look again on the next check
        choice = None
        shown = ((info.get("title") or "") + " " + (info.get("message") or "")).lower()
        for text, button in (answers or {}).items():
            if text.lower() in shown and button in info.get("buttons", []):
                choice = button
                break
        choice = choice or clo_ui.default_answer(info)
        if not choice:
            self.pending = (request, answers, seen)
            raise CLO3DDialogError(info)
        info["clicked"] = choice if clo_ui.click_dialog(hwnd, choice) else None
        seen.append(info)

    def answer_dialog(self, button):
        """Click a button on CLO's open dialog; if a command was paused on it, wait for its reply."""
        from clo3d_mcp import clo_ui
        hwnd = clo_ui.find_dialog(self._clo_pid())
        if not hwnd:
            raise CLO3DConnectionError("CLO has no dialog open")
        info = clo_ui.read_dialog(hwnd)
        if button not in info.get("buttons", []):
            raise CLO3DConnectionError("the dialog has no %r button; it has %s" % (button, info.get("buttons")))
        if not clo_ui.click_dialog(hwnd, button):
            raise CLO3DConnectionError("clicking %r failed" % button)
        info["clicked"] = button
        if not self.pending:
            return {"dialog": info}
        request, answers, seen = self.pending
        self.pending = None
        seen.append(info)
        return self._wait(request, answers, seen)

    def _do_send(self, request, answers=None):
        """Write request, poll for response, return result."""
        # Ensure comm dir exists
        if not os.path.isdir(self.comm_dir):
            raise CLO3DNotRunningError(
                "CLO3D communication directory not found: " + self.comm_dir + ". "
                "Is CLO3D running with the MCP plugin loaded?"
            )

        # Clean up any stale response file
        if os.path.exists(self.response_file):
            try:
                os.remove(self.response_file)
            except OSError:
                pass

        # Write request atomically
        payload = json.dumps(request)
        tmp_file = self.request_file + ".tmp"
        with open(tmp_file, "w") as f:
            f.write(payload)

        # Atomic rename
        if os.path.exists(self.request_file):
            os.remove(self.request_file)
        os.rename(tmp_file, self.request_file)
        self.pending = None
        return self._wait(request, answers, [])

    def _wait(self, request, answers, seen):
        """Poll for the reply to request, answering CLO dialogs on the way."""
        start_time = time.time()
        next_check = start_time + DIALOG_CHECK
        while True:
            if time.time() > next_check:
                self._handle_dialog(request, answers, seen)
                next_check = time.time() + DIALOG_CHECK
            elapsed = time.time() - start_time
            # The plug-in writes the reply before clearing "busy", so check for a reply first.
            if elapsed > TIMEOUT and not os.path.exists(self.response_file):
                busy = self._busy_with(request["id"])
                if not busy or elapsed > MAX_BUSY:
                    raise CLO3DTimeoutError(
                        "Timed out waiting for CLO3D response (" + str(int(elapsed)) + "s). "
                        + ("CLO is still running it; it was not re-sent." if busy else
                           "Is the CLO MCP listener running? The command was not re-sent.")
                    )

            if os.path.exists(self.response_file):
                try:
                    with open(self.response_file, "r") as f:
                        data = f.read()

                    # Delete response file
                    try:
                        os.remove(self.response_file)
                    except OSError:
                        pass

                    if not data.strip():
                        time.sleep(POLL_INTERVAL)
                        continue

                    response = json.loads(data)

                    # Verify this response matches our request
                    if response.get("id") != request["id"]:
                        # Stale response from a previous request, keep waiting
                        time.sleep(POLL_INTERVAL)
                        continue

                    if response.get("status") == "error":
                        error_msg = response.get("message", "Unknown error from CLO3D")
                        raise CLO3DCommandError("CLO3D error: " + error_msg)

                    result = response.get("result", {})
                    if seen:
                        from clo3d_mcp import clo_ui
                        failed = [d for d in seen if clo_ui.is_failure(d)]
                        if failed:   # CLO's API often reports success while its dialog says it failed
                            raise CLO3DCommandError("CLO3D error (from its dialog): " + failed[0].get("message", ""))
                        if isinstance(result, dict):
                            result["clo_dialogs"] = seen
                    return result

                except (json.JSONDecodeError, ValueError):
                    # File might be partially written, wait and retry
                    time.sleep(POLL_INTERVAL)
                    continue

            time.sleep(POLL_INTERVAL)

    def ping(self):
        """Test the connection. Returns True if CLO3D responds."""
        try:
            result = self.send_command("ping", retries=1)
            return result.get("pong", False)
        except CLO3DConnectionError:
            return False


# Module-level singleton
_connection = None


def get_connection(comm_dir=None):
    """Get or create the global CLO3D connection."""
    global _connection
    if _connection is None:
        _connection = CLO3DConnection(comm_dir)
    return _connection

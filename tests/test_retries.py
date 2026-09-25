"""The client must never re-send a command that CLO may already have run."""

import json
import os
import threading
import time

import pytest

import clo3d_mcp.connection as connection
from clo3d_mcp.connection import (CLO3DCommandError, CLO3DConnection, CLO3DNotRunningError,
                                  CLO3DTimeoutError)


class FakePlugin:
    """Counts requests; replies with an error, never, or after reporting 'busy'."""

    def __init__(self, comm_dir, mode, work_seconds=0.0):
        self.comm_dir, self.mode, self.work = comm_dir, mode, work_seconds
        self.received = 0
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _write(self, name, data):
        tmp = os.path.join(self.comm_dir, name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, os.path.join(self.comm_dir, name))

    def _loop(self):
        path = os.path.join(self.comm_dir, "request.json")
        while not self._stop:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        request = json.load(f)
                    os.remove(path)
                except (OSError, ValueError):
                    time.sleep(0.01)
                    continue
                self.received += 1
                if self.mode == "error":
                    self._write("response.json", {"id": request["id"], "status": "error", "message": "boom"})
                elif self.mode == "busy":
                    self._write("status.json", {"state": "busy", "request_id": request["id"]})
                    time.sleep(self.work)
                    self._write("response.json", {"id": request["id"], "status": "success", "result": {"done": True}})
                    self._write("status.json", {"state": "listening"})
                # mode "silent": never answer
            time.sleep(0.01)

    def stop(self):
        self._stop = True
        self._thread.join(timeout=2)


def _connection(comm_dir):
    conn = object.__new__(CLO3DConnection)
    conn.comm_dir = str(comm_dir)
    conn.request_file = os.path.join(conn.comm_dir, "request.json")
    conn.response_file = os.path.join(conn.comm_dir, "response.json")
    conn._initialized = True
    return conn


def test_error_reply_is_not_retried(tmp_path):
    plugin = FakePlugin(str(tmp_path), "error")
    try:
        with pytest.raises(CLO3DCommandError):
            _connection(tmp_path).send_command("sew_lines", {})
        time.sleep(0.2)
        assert plugin.received == 1
    finally:
        plugin.stop()


def test_timeout_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "TIMEOUT", 0.3)
    plugin = FakePlugin(str(tmp_path), "silent")
    try:
        with pytest.raises(CLO3DTimeoutError):
            _connection(tmp_path).send_command("simulate", {})
        time.sleep(0.3)
        assert plugin.received == 1
    finally:
        plugin.stop()


def test_keeps_waiting_while_clo_reports_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "TIMEOUT", 0.3)
    plugin = FakePlugin(str(tmp_path), "busy", work_seconds=1.0)
    try:
        assert _connection(tmp_path).send_command("simulate", {}) == {"done": True}
        assert plugin.received == 1
    finally:
        plugin.stop()


def test_missing_comm_dir_is_retried_then_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "RETRY_DELAY", 0.01)
    with pytest.raises(CLO3DNotRunningError):
        _connection(tmp_path / "missing").send_command("ping", {})

"""Decisions on CLO's dialogs, with the window and UI Automation calls faked (no CLO needed)."""

import pytest

from clo3d_mcp import clo_ui
from clo3d_mcp.connection import CLO3DConnection, CLO3DDialogError

UNSAVED = {"title": "CLO Standalone OnlineAuth", "message": "File not saved. Do you want to save?",
           "buttons": ["Save As", "No", "Cancel"]}
FAILED = {"title": "Information", "message": "Failed to read the Project file : Unzip error",
          "buttons": ["OK", "Cancel"]}
OPEN_PROJECT = {"title": "Open Project", "message": "Object\nEnvironment", "buttons": ["Reset", "OK", "Cancel"]}


def test_acknowledge_only_dialogs_are_answered():
    assert clo_ui.default_answer({"buttons": ["OK"]}) == "OK"
    assert clo_ui.default_answer({"buttons": ["OK", "Cancel"]}) == "OK"
    assert clo_ui.default_answer({"buttons": ["Close"]}) == "Close"


def test_decisions_are_not_guessed():
    assert clo_ui.default_answer(UNSAVED) is None
    assert clo_ui.default_answer({"buttons": ["Yes", "No"]}) is None
    assert clo_ui.default_answer(OPEN_PROJECT) is None
    assert clo_ui.default_answer({"buttons": []}) is None


def test_failure_messages():
    assert clo_ui.is_failure(FAILED)
    assert not clo_ui.is_failure(UNSAVED)
    assert not clo_ui.is_failure(OPEN_PROJECT)


@pytest.fixture
def clo(monkeypatch, tmp_path):
    """One fake dialog at a time; records the buttons clicked."""
    state = {"dialog": None, "clicked": []}
    monkeypatch.setattr(clo_ui, "find_dialog", lambda pid: 1 if state["dialog"] else None)
    monkeypatch.setattr(clo_ui, "read_dialog", lambda hwnd: dict(state["dialog"]))

    def click(hwnd, button):
        state["clicked"].append(button)
        state["dialog"] = None
        return True

    monkeypatch.setattr(clo_ui, "click_dialog", click)
    CLO3DConnection._instance = None
    conn = CLO3DConnection(str(tmp_path))
    yield conn, state
    CLO3DConnection._instance = None


def test_answers_match_title_or_message(clo):
    conn, state = clo
    for dialog, answers, expected in ((UNSAVED, {"File not saved": "No"}, "No"),
                                      (OPEN_PROJECT, {"Open Project": "OK"}, "OK"),
                                      (FAILED, {}, "OK")):
        state["dialog"] = dialog
        seen = []
        conn._handle_dialog({"id": "x"}, answers, seen)
        assert state["clicked"][-1] == expected and seen[-1]["clicked"] == expected


def test_a_decision_pauses_the_command(clo):
    conn, state = clo
    state["dialog"] = UNSAVED
    with pytest.raises(CLO3DDialogError) as error:
        conn._handle_dialog({"id": "x"}, {}, [])
    assert error.value.dialog["buttons"] == UNSAVED["buttons"]
    assert conn.pending[0]["id"] == "x" and not state["clicked"]


def test_an_answer_naming_a_missing_button_is_not_clicked(clo):
    conn, state = clo
    state["dialog"] = UNSAVED
    with pytest.raises(CLO3DDialogError):
        conn._handle_dialog({"id": "x"}, {"File not saved": "Discard"}, [])
    assert not state["clicked"]


def test_windows_being_built_are_skipped(clo):
    conn, state = clo
    state["dialog"] = {"title": "", "message": "", "buttons": []}
    conn._handle_dialog({"id": "x"}, {}, [])   # no error, no click: looked at again next time
    assert not state["clicked"] and conn.pending is None

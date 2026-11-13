"""#68: native commandExecution/fileChange items keep the #23 shell display shape (api-contracts/issue68-native-shell-history.md)."""
import pytest

from app.conversations import display_native_item


def command(**fields):
    return {"id": "cmd", "type": "commandExecution", "command": "python -m unittest", "commandActions": [],
            "cwd": "/state/workspace", "status": "completed", "exitCode": 0, "aggregatedOutput": "OK\n",
            "durationMs": 1200, **fields}


def test_a_completed_command_is_a_shell_tool_call_with_combined_output():
    assert display_native_item(command()) == [{
        "id": "cmd", "type": "tool_call", "name": "shell",
        "input": {"command": "python -m unittest", "cwd": "."},
        "output": {"status": "completed", "stdout": "OK\n", "stderr": None, "combinedOutput": True,
                   "exitCode": 0, "durationMs": 1200},
        "status": "completed"}]


@pytest.mark.parametrize("native,shown", [("inProgress", "inProgress"), ("failed", "failed"), ("declined", "rejected")])
def test_statuses_map_and_only_a_declined_command_carries_shell_declined(native, shown):
    projected = display_native_item(command(status=native, exitCode=None, aggregatedOutput=None))[0]
    assert projected["status"] == projected["output"]["status"] == shown
    assert projected.get("error") == ({"code": "shell_declined"} if shown == "rejected" else None)
    assert projected["output"]["stdout"] is None and projected["output"]["exitCode"] is None


@pytest.mark.parametrize("cwd,shown", [("/state/workspace/pkg/sub", "pkg/sub"), ("/tmp", "/tmp"),
                                       ("/state/workspace-other", "/state/workspace-other"), (None, None)])
def test_cwd_is_workspace_relative_and_anything_outside_is_shown_as_given(cwd, shown):
    assert display_native_item(command(cwd=cwd))[0]["input"]["cwd"] == shown


def test_a_file_change_is_an_apply_patch_call_with_one_diff_per_change():
    item = {"id": "patch", "type": "fileChange", "status": "completed", "changes": [
        {"path": "/state/workspace/ledger/__init__.py", "kind": {"type": "update", "move_path": None}, "diff": "@@ -1 +1 @@\n-a\n+b\n"},
        {"path": "/state/workspace/new.py", "kind": {"type": "add"}, "diff": "x = 1\n"},
        {"path": "/state/workspace/old.py", "kind": {"type": "update", "move_path": "/state/workspace/moved.py"}, "diff": ""}]}
    assert display_native_item(item) == [{
        "id": "patch", "type": "tool_call", "name": "apply_patch",
        "input": {"changes": [
            {"path": "ledger/__init__.py", "kind": "update", "movePath": None, "diff": "@@ -1 +1 @@\n-a\n+b\n"},
            {"path": "new.py", "kind": "add", "movePath": None, "diff": "x = 1\n"},
            {"path": "old.py", "kind": "update", "movePath": "moved.py", "diff": ""}]},
        "output": {"status": "completed"}, "status": "completed"}]


def test_a_declined_file_change_is_rejected_and_malformed_changes_are_skipped():
    item = {"id": "patch", "type": "fileChange", "status": "declined", "changes": ["bad", {"path": "/x", "kind": "add", "diff": "d"}]}
    projected = display_native_item(item)[0]
    assert projected["status"] == "rejected" and projected["error"] == {"code": "shell_declined"}
    assert projected["input"]["changes"] == [{"path": "/x", "kind": None, "movePath": None, "diff": "d"}]


def test_model_mismatch_is_kept_on_native_shell_items():
    assert display_native_item(command(modelMismatch=True))[0]["modelMismatch"] is True

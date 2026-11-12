"""Workspace path-jail and allowlisted test-runner tests (no mocks for I/O)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from synvio.autodev.runner import ALLOWED_RUNNERS
from synvio.autodev.runner import TestCommandNotAllowed as RunnerRejected
from synvio.autodev.runner import TestRunner as DevTestRunner
from synvio.autodev.workspace import Workspace, WorkspaceError, WorkspaceViolation

from support import BROKEN_PROJECT, FIXED_CALCULATOR


def test_workspace_creates_dedicated_temp_directory(tmp_path):
    workspace = Workspace()
    try:
        assert workspace.root.is_dir()
        assert workspace.root != tmp_path
        assert workspace.root.name.startswith("synvio-dev-")
    finally:
        root = workspace.root
        workspace.cleanup()
    assert not root.exists()


def test_workspace_does_not_delete_caller_directory(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.cleanup()
    assert (tmp_path / "project").is_dir()


@pytest.mark.parametrize(
    "path", ["../escape.txt", "..\\escape.txt", "sub/../../escape.txt", "C:\\Windows\\evil.txt", "/etc/passwd"]
)
def test_workspace_rejects_paths_outside_root(tmp_path, path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(WorkspaceViolation):
        workspace.write_text(path, "nope")
    with pytest.raises(WorkspaceViolation):
        workspace.read_text(path)


def test_workspace_rejects_empty_path(tmp_path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(WorkspaceViolation):
        workspace.write_text("", "x")


def test_workspace_records_created_and_modified_files(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("pkg/mod.py", "VALUE = 1\n")
    workspace.write_text("pkg/mod.py", "VALUE = 2\n")

    assert workspace.files_created == ["pkg/mod.py"]
    assert workspace.files_modified == ["pkg/mod.py"]
    assert workspace.read_text("pkg/mod.py") == "VALUE = 2\n"
    assert workspace.list_files("**/*.py") == ["pkg/mod.py"]


def test_workspace_read_missing_file_raises(tmp_path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(WorkspaceError):
        workspace.read_text("missing.py")


def test_workspace_rejects_escaping_glob(tmp_path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(WorkspaceViolation):
        workspace.list_files("../*")


def test_runner_rejects_unknown_runner(tmp_path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(RunnerRejected):
        DevTestRunner(workspace, runner="bash -c rm -rf /")


@pytest.mark.parametrize("target", ["../tests", "C:\\Windows\\System32", "missing_dir"])
def test_runner_rejects_targets_outside_workspace(tmp_path, target):
    workspace = Workspace(tmp_path / "project")
    runner = DevTestRunner(workspace)
    with pytest.raises(RunnerRejected):
        runner.run(target)


def test_runner_uses_allowlisted_argv_and_no_shell(tmp_path):
    workspace = Workspace.from_files({"test_ok.py": "def test_ok():\n    assert True\n"})
    runner = DevTestRunner(workspace)

    outcome = runner.run()

    assert outcome.passed, outcome.stdout + outcome.stderr
    assert outcome.command[:4] == ALLOWED_RUNNERS["pytest"][:4]
    assert outcome.command[0] == sys.executable
    assert outcome.target is None


def test_runner_env_excludes_secrets(tmp_path):
    workspace = Workspace(tmp_path / "project")
    runner = DevTestRunner(workspace)

    env = runner.build_env(
        {
            "PATH": "C:\\bin",
            "OPENAI_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "GITHUB_TOKEN": "ghp_secret",
            "SYSTEMROOT": "C:\\Windows",
        }
    )

    assert env["PATH"] == "C:\\bin"
    assert env["SYSTEMROOT"] == "C:\\Windows"
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_runner_detects_real_failure_and_real_success(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    runner = DevTestRunner(workspace)

    broken = runner.run()
    assert not broken.passed
    assert broken.returncode != 0
    assert "test_add_positive" in broken.stdout or "test_add_positive" in broken.stderr
    assert broken.duration_seconds > 0

    workspace.write_text("calculator.py", FIXED_CALCULATOR)
    fixed = runner.run()

    assert fixed.passed, fixed.stdout + fixed.stderr
    assert fixed.returncode == 0
    assert "passed" in fixed.stdout.lower()
    assert len(runner.outcomes) == 2


def test_runner_reports_timeout(tmp_path):
    workspace = Workspace.from_files(
        {"test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(5)\n"}
    )
    runner = DevTestRunner(workspace, timeout=0.5)

    outcome = runner.run()

    assert outcome.timed_out
    assert not outcome.passed
    assert outcome.returncode == -1


def test_workspace_snapshot_reflects_disk(tmp_path):
    workspace = Workspace.from_files({"a.py": "x = 1\n"})
    snapshot = workspace.snapshot("**/*.py")
    assert snapshot == {"a.py": b"x = 1\n"}
    assert Path(workspace.root, "a.py").read_text(encoding="utf-8") == "x = 1\n"

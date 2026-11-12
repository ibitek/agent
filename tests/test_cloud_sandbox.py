"""Validate the remote runner contract with the installed SDK, without allocating VMs."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from synvio.autodev.workspace import Workspace
from synvio.cloud.common import safe_path, validate_files
from synvio.cloud.sandbox_runner import PYTHON, VercelTestRunner, project_files


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "C:\\Windows\\evil", "a/../../secret", ".env", "a/.git/config"])
def test_cloud_file_paths_are_restricted(name):
    with pytest.raises(ValueError):
        safe_path(name)


def test_project_snapshot_excludes_logs_and_credentials(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.write_text("calculator.py", "def add(a,b): return a+b")
    workspace.write_text(".env", "secret-not-for-sandbox")
    workspace.write_text(".synvio/execution.jsonl", "private-log")
    workspace.write_text("private.key", "private-key-not-for-sandbox")
    assert project_files(workspace) == {"calculator.py": "def add(a,b): return a+b"}


def test_remote_runner_never_inherits_provider_credentials_or_network(tmp_path, monkeypatch):
    from vercel.sandbox import sync as sandbox

    monkeypatch.setenv("OPENAI_API_KEY", "test-provider-key-not-for-sandbox")
    monkeypatch.setenv("SYNVIO_SANDBOX_SNAPSHOT", "test-snapshot")
    workspace = Workspace(tmp_path)
    workspace.write_text("test_ok.py", "def test_ok(): assert True")
    created, written, commands = [], {}, []

    class Files:
        def mkdir(self, *args, **kwargs):
            pass

        @contextmanager
        def batch(self):
            yield self

        def write_text(self, name, content):
            written[name] = content

    class Box:
        fs = Files()

        def run_command(self, command, args, **kwargs):
            commands.append((command, args, kwargs))
            kwargs["stdout"].write("1 passed in 0.01s")
            return SimpleNamespace(returncode=0)

    @contextmanager
    def create(**kwargs):
        created.append(kwargs)
        yield Box()

    monkeypatch.setattr(sandbox, "create_sandbox", create)
    runner = VercelTestRunner(workspace, timeout=30)
    result = runner.run("test_ok.py")
    assert result.passed
    assert result.metadata["execution_backend"] == "vercel_sandbox"
    assert created[0]["network_policy"].mode == "deny-all"
    assert created[0]["env"] == {}
    assert created[0]["ports"] == []
    assert created[0]["source"].snapshot_id == "test-snapshot"
    assert commands[0][0] == PYTHON
    assert commands[0][1] == ["-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "test_ok.py"]
    assert commands[0][2]["kill_after"] == 30
    assert "OPENAI_API_KEY" not in commands[0][2]["env"]
    assert list(written) == ["/vercel/sandbox/project/test_ok.py"]


def test_missing_snapshot_never_runs_host_tests(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.delenv("SYNVIO_SANDBOX_SNAPSHOT", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Host subprocess must never run"))
    workspace = Workspace(tmp_path)
    workspace.write_text("test_ok.py", "def test_ok(): pass")
    with pytest.raises(RuntimeError, match="Nie wykonano testów"):
        VercelTestRunner(workspace).run()


def test_remote_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    from vercel.sandbox import sync as sandbox

    monkeypatch.setenv("SYNVIO_SANDBOX_SNAPSHOT", "bad-snapshot")
    monkeypatch.setattr(sandbox, "create_sandbox", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Sandbox unavailable")))
    workspace = Workspace(tmp_path)
    with pytest.raises(RuntimeError, match="Sandbox unavailable"):
        VercelTestRunner(workspace).run()


def test_project_size_is_bounded():
    with pytest.raises(ValueError):
        validate_files({"large.py": "x" * (256 * 1024 + 1)})


def test_event_redaction_preserves_numbers_and_handles_quoted_secrets(monkeypatch):
    from synvio.cloud.common import redact_data

    monkeypatch.setenv("OPENAI_API_KEY", 'test-"quoted-key"')
    result = redact_data({"used": 1, "error": 'Invalid test-"quoted-key"', "events": [{"message": 'test-"quoted-key"'}]})
    assert result == {"used": 1, "error": "Invalid [redacted]", "events": [{"message": "[redacted]"}]}

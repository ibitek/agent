"""Fixed pytest/unittest commands run only in a disposable Vercel microVM."""

import os
import time

from synvio.autodev.runner import MAX_CAPTURED_CHARS, TestOutcome, TestRunner

from .common import safe_path, validate_files

PYTHON = "/vercel/sandbox/.venv/bin/python"
REMOTE_WORKSPACE = "/vercel/sandbox/project"


class BoundedOutput:
    def __init__(self):
        self.value = ""

    def write(self, value):
        self.value = (self.value + value)[-MAX_CAPTURED_CHARS:]
        return len(value)

    def flush(self):
        pass


def project_files(workspace):
    files = {}
    for name in workspace.list_files("**/*", limit=200):
        # Never stage SYNVIO logs, credentials, caches or hidden runtime files.
        if any(part.startswith(".") or part == "__pycache__" for part in name.replace("\\", "/").split("/")):
            continue
        if name.endswith((".pyc", ".pem", ".key", ".p12", ".pfx")):
            continue
        files[safe_path(name)] = workspace.read_text(name)
    return validate_files(files)


class VercelTestRunner(TestRunner):
    def check_available(self):
        from vercel.sandbox import NetworkPolicy, SnapshotSource
        from vercel.sandbox import sync as sandbox

        snapshot = os.environ.get("SYNVIO_SANDBOX_SNAPSHOT", "")
        if not snapshot:
            raise RuntimeError("Brak przygotowanego środowiska testów; model nie został uruchomiony.")
        with sandbox.create_sandbox(source=SnapshotSource(snapshot_id=snapshot), network_policy=NetworkPolicy.deny_all(), execution_time_limit=30, env={}, ports=[]) as box:
            box.run_command(PYTHON, ["-c", "import pytest; print(pytest.__version__)"], capture_output=True, kill_after=10, check=True)

    @property
    def execution_metadata(self):
        return {"execution_backend": "vercel_sandbox", "sandbox_policy": {"network": "deny-all", "credentials": "none", "host_fallback": False, "timeout_seconds": self.timeout}}

    def run(self, target=None):
        safe_target = self._validate_target(target)
        files = project_files(self.workspace)
        snapshot = os.environ.get("SYNVIO_SANDBOX_SNAPSHOT", "")
        if not snapshot:
            raise RuntimeError("Brak przygotowanego środowiska testów. Nie wykonano testów na serwerze hosta.")
        from vercel.sandbox import NetworkPolicy, SandboxResources, SnapshotSource
        from vercel.sandbox import sync as sandbox

        args = ["-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"] if self.runner == "pytest" else ["-m", "unittest", "discover", "-v"]
        if safe_target:
            args.append(safe_target)
        started = time.monotonic()
        stdout, stderr = BoundedOutput(), BoundedOutput()
        # SDK credentials stay in the caller. env={} is the microVM environment.
        with sandbox.create_sandbox(source=SnapshotSource(snapshot_id=snapshot), network_policy=NetworkPolicy.deny_all(), execution_time_limit=self.timeout + 20, resources=SandboxResources(vcpus=1, memory=2048), persistent=False, ports=[], env={}, tags={"app": "synvio", "purpose": "tests"}) as box:
            box.fs.mkdir(REMOTE_WORKSPACE, recursive=True)
            with box.fs.batch() as batch:
                for name, content in files.items():
                    batch.write_text(f"{REMOTE_WORKSPACE}/{name}", content)
            result = box.run_command(PYTHON, args, cwd=REMOTE_WORKSPACE, env={"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, kill_after=self.timeout, stdout=stdout, stderr=stderr)
        timed_out = result.returncode in {-9, 124, 137}
        outcome = TestOutcome(command=[PYTHON, *args], target=safe_target, returncode=result.returncode, passed=result.returncode == 0 and not timed_out, timed_out=timed_out, duration_seconds=time.monotonic() - started, stdout=stdout.value, stderr=stderr.value, metadata=self.execution_metadata)
        self.outcomes.append(outcome)
        return outcome

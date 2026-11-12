"""Allowlisted test execution for the SYNVIO developer agent.

Security properties enforced here:

* no shell: ``subprocess.run(..., shell=False)`` with an argv list only;
* no arbitrary commands: only the fixed runners below can be executed;
* target paths must stay inside the workspace;
* the child environment is a sanitized allowlist, so API keys, tokens and
  other secrets from the parent process are never inherited;
* wall-clock timeout, so a hanging test cannot stall the agent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from synvio.autodev.workspace import Workspace, WorkspaceViolation

MAX_CAPTURED_CHARS = 20_000


class TestCommandNotAllowed(RuntimeError):
    """Raised when a caller asks for something outside the allowlist."""


# Only these exact argv prefixes may be executed. No shell, no user commands.
ALLOWED_RUNNERS: Dict[str, List[str]] = {
    "pytest": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ],
    "unittest": [sys.executable, "-m", "unittest", "discover", "-v"],
}

# Environment variables copied into the child process. Anything else is
# dropped, which keeps API keys, tokens and credentials out of the test run.
SAFE_ENV_KEYS: Sequence[str] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PYTHONIOENCODING",
)


@dataclass(frozen=True)
class TestOutcome:
    """Ground-truth result of one real test run."""

    command: List[str]
    target: Optional[str]
    returncode: int
    passed: bool
    timed_out: bool
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)

    def failure_excerpt(self, limit: int = 1500) -> str:
        text = (self.stdout or "").strip()
        if not text:
            text = (self.stderr or "").strip()
        return text[-limit:]

    @property
    def summary(self) -> str:
        state = "PASSED" if self.passed else ("TIMEOUT" if self.timed_out else "FAILED")
        return f"{state} (exit code {self.returncode}) in {self.duration_seconds:.2f}s"

    def to_dict(self) -> Dict[str, object]:
        return {
            "command": list(self.command),
            "target": self.target,
            "returncode": self.returncode,
            "passed": self.passed,
            "timed_out": self.timed_out,
            "duration_seconds": round(self.duration_seconds, 3),
            "stdout_tail": (self.stdout or "")[-4000:],
            "stderr_tail": (self.stderr or "")[-2000:],
            **self.metadata,
        }


@dataclass
class TestRunner:
    """Runs an allowlisted test command inside a workspace."""

    workspace: Workspace
    timeout: float = 60.0
    runner: str = "pytest"
    outcomes: List[TestOutcome] = field(default_factory=list)

    @property
    def execution_metadata(self) -> Dict[str, object]:
        return {"execution_backend": "host_trusted", "sandbox_policy": None}

    def __post_init__(self) -> None:
        if self.runner not in ALLOWED_RUNNERS:
            raise TestCommandNotAllowed(
                f"runner {self.runner!r} is not allowlisted; "
                f"allowed: {', '.join(sorted(ALLOWED_RUNNERS))}"
            )
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def build_env(self, parent: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Return the sanitized child environment (no secrets inherited)."""
        source = os.environ if parent is None else parent
        env = {key: source[key] for key in SAFE_ENV_KEYS if key in source}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _validate_target(self, target: Optional[str]) -> Optional[str]:
        if target is None:
            return None
        if Path(target).is_absolute():
            raise TestCommandNotAllowed(f"target must be relative: {target!r}")
        try:
            path = self.workspace._resolve(target)
        except WorkspaceViolation as exc:
            raise TestCommandNotAllowed(str(exc)) from exc
        if not path.exists():
            raise TestCommandNotAllowed(
                f"target does not exist in workspace: {target!r}"
            )
        return self.workspace.relative(path)

    def run(self, target: Optional[str] = None) -> TestOutcome:
        """Execute the allowlisted runner and capture its real output."""
        safe_target = self._validate_target(target)
        command = list(ALLOWED_RUNNERS[self.runner])
        if safe_target:
            command.append(safe_target)

        started = time.monotonic()
        timed_out = False
        stdout = stderr = ""
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace.root),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=self.build_env(),
            )
            returncode = completed.returncode
            stdout = (completed.stdout or "")[:MAX_CAPTURED_CHARS]
            stderr = (completed.stderr or "")[:MAX_CAPTURED_CHARS]
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = _as_text(exc.stdout)[:MAX_CAPTURED_CHARS]
            stderr = _as_text(exc.stderr)[:MAX_CAPTURED_CHARS]

        outcome = TestOutcome(
            command=command,
            target=safe_target,
            returncode=returncode,
            passed=(returncode == 0 and not timed_out),
            timed_out=timed_out,
            duration_seconds=time.monotonic() - started,
            stdout=stdout,
            stderr=stderr,
            metadata=self.execution_metadata,
        )
        self.outcomes.append(outcome)
        return outcome


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = [
    "ALLOWED_RUNNERS",
    "SAFE_ENV_KEYS",
    "TestCommandNotAllowed",
    "TestOutcome",
    "TestRunner",
]

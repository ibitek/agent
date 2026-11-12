"""Legacy work-in-progress Docker backend, not enabled by SYNVIO CLI/API.

The public deployment uses synvio.cloud.sandbox_runner, not this module.

Defense in depth, not a perfect sandbox against hostile code or a compromised
Docker daemon. No model-authored shell command is accepted by this module.
Container changes are confined to a disposable staged copy and discarded after
verification; SYNVIO's existing workspace file tools remain the only edit path.
"""

from __future__ import annotations

import math
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from synvio.autodev.runner import MAX_CAPTURED_CHARS, TestCommandNotAllowed, TestOutcome, TestRunner
from synvio.autodev.workspace import Workspace, WorkspaceViolation

SANDBOX_WORKDIR = "/workspace"
SANDBOX_USER = "10001:10001"
IMAGE_DIGEST_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._/:@-]{0,250}@sha256:[a-f0-9]{64}|sha256:[a-f0-9]{64})$")
SAFE_DOCKER_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
)
EXCLUDED_DIRS = {".git", ".synvio", ".venv", "venv", "node_modules", "__pycache__"}
EXCLUDED_NAMES = {".env", ".env.local", ".env.production", "credentials", "credentials.json", "secrets.json"}
EXCLUDED_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".secrets")
MAX_STAGE_FILES = 20_000
MAX_STAGE_BYTES = 256 * 1024 * 1024


class DockerSandboxUnavailable(RuntimeError):
    """Docker or the requested digest-pinned image cannot be used."""


class DockerCommandTimeout(DockerSandboxUnavailable):
    """A bounded host-side Docker CLI call timed out."""


def safe_docker_environment(parent: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    source = os.environ if parent is None else parent
    return {key: source[key] for key in SAFE_DOCKER_ENV if key in source}


def stage_workspace(workspace: Workspace) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Make a disposable bounded copy; never mount the caller's workspace tree."""
    temporary = tempfile.TemporaryDirectory(prefix="synvio-docker-workspace-")
    root = Path(temporary.name) / "workspace"
    root.mkdir()
    root.chmod(0o777)
    count = size_total = 0
    try:
        for source in workspace.root.rglob("*"):
            relative = source.relative_to(workspace.root)
            if any(part.lower() in EXCLUDED_DIRS for part in relative.parts) or source.is_symlink():
                continue
            name = source.name.lower()
            if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            destination = root / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(0o777)
            elif source.is_file():
                file_size = source.stat().st_size
                if count >= MAX_STAGE_FILES or size_total + file_size > MAX_STAGE_BYTES:
                    raise DockerSandboxUnavailable("Workspace exceeds Docker staging limits")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination, follow_symlinks=False)
                try:
                    # Test code runs under a non-root UID in this disposable copy.
                    destination.chmod(0o666)
                except OSError:
                    pass
                count += 1
                size_total += file_size
        return temporary, root
    except Exception:
        temporary.cleanup()
        raise


@dataclass
class DockerTestRunner(TestRunner):
    """Fresh constrained container per call; pytest/unittest only."""

    workspace: Workspace
    image: str = ""
    timeout: float = 60.0
    docker_executable: str = "docker"
    memory_limit: str = "1g"
    cpu_limit: float = 1.0
    pids_limit: int = 128
    outcomes: List[TestOutcome] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not IMAGE_DIGEST_RE.fullmatch(self.image):
            raise ValueError("Docker sandbox image must be pinned by sha256 digest")
        if self.timeout <= 0 or self.cpu_limit <= 0 or not math.isfinite(self.cpu_limit):
            raise ValueError("timeout and CPU limits must be positive finite values")
        if self.pids_limit < 1:
            raise ValueError("pids_limit must be positive")
        if not re.fullmatch(r"[1-9][0-9]*(?:[kKmMgG])?", self.memory_limit):
            raise ValueError("memory_limit must be a size such as 512m or 1g")

    @property
    def policy(self) -> Dict[str, object]:
        return {
            "backend": "docker", "image": self.image, "network": "none",
            "user": SANDBOX_USER, "read_only_rootfs": True,
            "cap_drop": ["ALL"], "no_new_privileges": True,
            "memory": self.memory_limit, "cpus": self.cpu_limit,
            "pids_limit": self.pids_limit, "timeout_seconds": self.timeout,
            "mount": "disposable-workspace-copy-only",
        }

    @property
    def execution_metadata(self) -> Dict[str, object]:
        return {"execution_backend": "docker", "sandbox_policy": self.policy}

    def _docker(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        executable = shutil.which(self.docker_executable)
        if not executable:
            raise DockerSandboxUnavailable("Docker CLI unavailable; execution did not fall back to the host")
        try:
            return subprocess.run(
                [executable, *args], shell=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                env=safe_docker_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerCommandTimeout(f"Docker command timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise DockerSandboxUnavailable(f"Docker CLI failure: {exc}") from exc

    def check_available(self) -> None:
        result = self._docker(["info", "--format", "{{.ServerVersion}}"], timeout=8)
        if result.returncode:
            raise DockerSandboxUnavailable((result.stderr or "Docker daemon unavailable")[-1000:])
        image = self._docker(["image", "inspect", "--format", "{{.Id}}", self.image], timeout=8)
        if image.returncode or not image.stdout.strip():
            raise DockerSandboxUnavailable("Pinned image is not local; Docker runner does not pull images implicitly")

    def _test_command(self, target: Optional[str]) -> List[str]:
        command = (["python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
                   if self.runner == "pytest" else ["python", "-m", "unittest", "discover", "-v"])
        if target:
            command.append(target)
        return command

    def _wrapper(self, target: Optional[str]) -> str:
        """Run only the selected fixed test argv and enforce an inner timeout."""
        command = self._command(target)
        result_path = "/tmp/synvio-result.json"
        return (
            "import json,subprocess,sys,time\n"
            f"cmd={command!r}\n"
            "started=time.monotonic()\n"
            "try:\n"
            f" result=subprocess.run(cmd,cwd={SANDBOX_WORKDIR!r},shell=False,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout={float(self.timeout)!r},env={{'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','TMPDIR':'/tmp','PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8'}})\n"
            " payload={'returncode':result.returncode,'timed_out':False,'stdout':(result.stdout or '')[-20000:],'stderr':(result.stderr or '')[-20000:],'duration_seconds':time.monotonic()-started}\n"
            "except subprocess.TimeoutExpired as exc:\n"
            " payload={'returncode':-1,'timed_out':True,'stdout':str(exc.stdout or '')[-20000:],'stderr':str(exc.stderr or '')[-20000:],'duration_seconds':time.monotonic()-started}\n"
            f"open({result_path!r},'w',encoding='utf-8').write(json.dumps(payload))\n"
            "sys.exit(0)\n"
        )

    def _target(self, target: Optional[str]) -> Optional[str]:
        if target is None:
            return None
        if Path(target).is_absolute():
            raise TestCommandNotAllowed(f"target must be relative: {target!r}")
        try:
            resolved = self.workspace._resolve(target)
        except WorkspaceViolation as exc:
            raise TestCommandNotAllowed(str(exc)) from exc
        if not resolved.exists():
            raise TestCommandNotAllowed(f"target does not exist in workspace: {target!r}")
        return self.workspace.relative(resolved)

    def run(self, target: Optional[str] = None) -> TestOutcome:
        safe_target = self._target(target)
        self.check_available()
        staged_temp, staged = stage_workspace(self.workspace)
        started = time.monotonic()
        name = f"synvio-test-{uuid.uuid4().hex}"
        args = [
            "create", "--name", name, "--network=none", "--read-only",
            "--user", SANDBOX_USER, "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--memory", self.memory_limit,
            "--cpus", str(self.cpu_limit), "--ulimit", "nproc=64:64",
            "--pids-limit", str(self.pids_limit),
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--mount", f"type=bind,source={staged.resolve()},target={SANDBOX_WORKDIR},rw",
            "--workdir", SANDBOX_WORKDIR,
            "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PATH=/usr/local/bin:/usr/bin:/bin", self.image,
            "--entrypoint", "python",
            *self._test_command(safe_target),
            "-c", self._wrapper(safe_target),
        ]
        container_id = ""
        created = False
        cleanup_ok = False
        timed_out = False
        returncode = -1
        stdout = stderr = ""
        create = subprocess.CompletedProcess([], -1, "", "")
        try:
            create_result = self._docker(args, timeout=15)
            if create_result.returncode:
                stderr = (create_result.stderr or create_result.stdout or "docker create failed")[-MAX_CAPTURED_CHARS:]
            else:
                created = True
                container_id = create_result.stdout.strip().splitlines()[-1]
                if not re.fullmatch(r"[a-fA-F0-9]{12,64}", container_id):
                    raise DockerSandboxUnavailable("Docker returned invalid container ID")
                started_result = self._docker(["start", "--attach", container_id], timeout=self.timeout + 20)
                returncode = started_result.returncode
                stdout = (started_result.stdout or "")[:MAX_CAPTURED_CHARS]
                stderr = (started_result.stderr or "")[:MAX_CAPTURED_CHARS]
                fetched = self._docker(
                    ["cp", f"{container_id}:/tmp/synvio-result.json", "-"], timeout=8
                )
                if fetched.returncode == 0:
                    try:
                        payload = json.loads(fetched.stdout)
                        returncode = int(payload.get("returncode", returncode))
                        timed_out = bool(payload.get("timed_out", False))
                        stdout = str(payload.get("stdout", ""))[-MAX_CAPTURED_CHARS:]
                        stderr = str(payload.get("stderr", ""))[-MAX_CAPTURED_CHARS:]
                    except (ValueError, TypeError, json.JSONDecodeError):
                        returncode = -1
                        stderr = (stderr + "\nMalformed sandbox result file.").strip()
                else:
                    returncode = -1
                    stderr = (stderr + "\nSandbox did not produce a structured test result.").strip()
        except DockerCommandTimeout as exc:
            timed_out = True
            returncode = -1
            stderr = str(exc)
        except DockerSandboxUnavailable as exc:
            stderr = str(exc)
        finally:
            try:
                if created:
                    try:
                        self._docker(["stop", "--time", "2", container_id or name], timeout=6)
                    except DockerSandboxUnavailable:
                        pass
                    try:
                        removed = self._docker(["rm", "--force", container_id or name], timeout=10)
                        cleanup_ok = removed.returncode == 0
                        if not cleanup_ok:
                            stderr = (stderr + "\nContainer cleanup failed: " + (removed.stderr or "unknown error")[-500:]).strip()
                    except DockerSandboxUnavailable as exc:
                        stderr = (stderr + f"\nContainer cleanup failed: {exc}").strip()
            finally:
                staged_temp.cleanup()

        outcome = TestOutcome(
            command=["docker", *args[1:]], target=safe_target, returncode=returncode,
            passed=(create_result.returncode == 0 and returncode == 0 and cleanup_ok and not timed_out),
            timed_out=timed_out, duration_seconds=time.monotonic() - started,
            stdout=stdout, stderr=stderr,
            metadata={**self.execution_metadata, "container_cleanup": cleanup_ok,
                      "container_id": container_id or None},
        )
        self.outcomes.append(outcome)
        return outcome


__all__ = ["DockerTestRunner", "DockerSandboxUnavailable", "IMAGE_DIGEST_RE", "SANDBOX_USER", "SANDBOX_WORKDIR", "safe_docker_environment", "stage_workspace"]
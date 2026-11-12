"""Workspace isolation for the SYNVIO developer agent.

This is a *path jail*, not an OS sandbox: every file operation is resolved
inside one dedicated project directory and anything that escapes it raises
``WorkspaceViolation``. It prevents accidental edits to the rest of the disk,
but it is explicitly **not** claimed to be container- or OS-level isolation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Union

MAX_READ_BYTES = 256 * 1024


class WorkspaceError(RuntimeError):
    """Base class for workspace failures."""


class WorkspaceViolation(WorkspaceError):
    """Raised when a path escapes the workspace root."""


@dataclass(frozen=True)
class FileMutation:
    path: str
    action: str  # "created" | "modified"

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "action": self.action}


class Workspace:
    """A dedicated project directory with path-jailed file helpers."""

    def __init__(
        self,
        root: Optional[Union[str, os.PathLike]] = None,
        *,
        prefix: str = "synvio-dev-",
    ) -> None:
        if root is None:
            self._owns_root = True
            self.root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        else:
            self._owns_root = False
            self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutations: List[FileMutation] = []

    # -- path handling -----------------------------------------------------

    @property
    def mutations(self) -> List[FileMutation]:
        return list(self._mutations)

    @property
    def files_created(self) -> List[str]:
        return [m.path for m in self._mutations if m.action == "created"]

    @property
    def files_modified(self) -> List[str]:
        return [m.path for m in self._mutations if m.action == "modified"]

    def _resolve(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise WorkspaceViolation("path must be a non-empty relative string")
        raw = relative.strip().strip('"')
        if PureWindowsPath(raw).drive:
            raise WorkspaceViolation(f"absolute paths are not allowed: {relative!r}")
        raw = raw.replace("\\", "/")
        candidate = Path(raw)
        if candidate.is_absolute() or candidate.drive or raw.startswith(("/", "\\")):
            raise WorkspaceViolation(f"absolute paths are not allowed: {relative!r}")
        resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceViolation(
                f"path escapes the workspace: {relative!r} -> {resolved}"
            )
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:  # pragma: no cover - defensive
            raise WorkspaceViolation(f"path is outside workspace: {path}") from exc

    # -- file operations ---------------------------------------------------

    def exists(self, relative: str) -> bool:
        return self._resolve(relative).exists()

    def read_text(self, relative: str, *, max_bytes: int = MAX_READ_BYTES) -> str:
        path = self._resolve(relative)
        if not path.exists():
            raise WorkspaceError(f"file not found in workspace: {relative}")
        if path.is_dir():
            raise WorkspaceError(f"path is a directory, not a file: {relative}")
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise WorkspaceError(
                f"file too large to read ({len(data)} bytes > {max_bytes}): {relative}"
            )
        return data.decode("utf-8", errors="replace")

    def write_text(self, relative: str, content: str) -> Path:
        path = self._resolve(relative)
        action = "modified" if path.exists() else "created"
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.root not in path.resolve().parents:
            raise WorkspaceViolation(f"path escapes the workspace: {relative!r}")
        path.write_text(content, encoding="utf-8", newline="\n")
        mutation = FileMutation(path=self.relative(path), action=action)
        # Keep one record per (path, action) so a file that is first created and
        # then rewritten shows up in both ``files_created`` and ``files_modified``.
        key = (mutation.path, mutation.action)
        if all((m.path, m.action) != key for m in self._mutations):
            self._mutations.append(mutation)
        return path

    def clear_mutations(self) -> None:
        """Forget recorded mutations (used to baseline a task's changes)."""
        self._mutations = []

    def list_files(self, pattern: str = "**/*.py", *, limit: int = 200) -> List[str]:
        if ".." in Path(pattern).parts:
            raise WorkspaceViolation(f"pattern must stay inside workspace: {pattern!r}")
        files: List[str] = []
        for path in sorted(self.root.glob(pattern)):
            if path.is_file():
                files.append(self.relative(path))
            if len(files) >= limit:
                break
        return files

    # -- lifecycle ---------------------------------------------------------

    def cleanup(self) -> None:
        """Remove the workspace if SYNVIO created it."""
        if self._owns_root and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Workspace(root={str(self.root)!r})"

    @classmethod
    def from_files(
        cls,
        files: Dict[str, str],
        *,
        root: Optional[Union[str, os.PathLike]] = None,
        prefix: str = "synvio-dev-",
    ) -> "Workspace":
        """Create a workspace pre-populated with ``{relative_path: content}``."""
        workspace = cls(root, prefix=prefix)
        for relative, content in files.items():
            workspace.write_text(relative, content)
        return workspace

    def snapshot(self, pattern: str = "**/*") -> Dict[str, bytes]:
        """Return in-memory file contents (read-only ground truth for reports)."""
        snap: Dict[str, bytes] = {}
        for path in self.root.glob(pattern):
            if path.is_file():
                snap[self.relative(path)] = path.read_bytes()
        return snap


__all__ = [
    "FileMutation",
    "Workspace",
    "WorkspaceError",
    "WorkspaceViolation",
]

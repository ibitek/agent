"""Workspace-bound tools exposed to the OpenJarvis ReAct agent.

Every tool implements ``openjarvis.tools._stubs.BaseTool`` so OpenJarvis'
``ToolExecutor`` can dispatch it. Tools never touch paths outside the
``Workspace`` and never run arbitrary commands: ``dev_run_tests`` can only
launch the allowlisted runners from :mod:`synvio.autodev.runner`.
"""

from __future__ import annotations

from typing import Any, List, Optional

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from synvio.autodev.runner import TestRunner
from synvio.autodev.workspace import Workspace, WorkspaceError, WorkspaceViolation


class _WorkspaceTool(BaseTool):
    """Shared plumbing: workspace binding and error-to-ToolResult conversion."""

    is_local = True

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self.tool_id = self.spec.name

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    @property
    def edit_happened(self) -> bool:
        """True once this task has written at least one file."""
        return bool(self._workspace.files_modified or self._workspace.files_created)

    def _ok(self, content: str, **metadata: Any) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            content=content,
            success=True,
            metadata=metadata,
        )

    def _error(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, content=content, success=False)


class DevListFilesTool(_WorkspaceTool):
    """List project files inside the workspace."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dev_list_files",
            description=(
                "List files in the project workspace. Use this first to discover "
                "the project layout and the location of tests."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py' (default).",
                    },
                },
                "required": [],
            },
            category="autodev",
        )

    def execute(self, **params: Any) -> ToolResult:
        pattern = params.get("pattern") or "**/*.py"
        try:
            files = self._workspace.list_files(str(pattern))
        except WorkspaceViolation as exc:
            return self._error(f"Invalid pattern: {exc}")
        # SYNVIO's own artifacts (execution log, report) are not project files:
        # showing them to the model only burns tokens on noise.
        files = [name for name in files if not name.startswith(".synvio/")]
        if not files:
            return self._ok("No files matched.", pattern=str(pattern), files=[])
        return self._ok("\n".join(files), pattern=str(pattern), files=files)


class DevReadFileTool(_WorkspaceTool):
    """Read a single project file."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dev_read_file",
            description=(
                "Read a file from the project workspace. Paths are relative to "
                "the workspace root; absolute paths and '..' are rejected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path, e.g. 'calculator.py'.",
                    },
                },
                "required": ["path"],
            },
            category="autodev",
        )

    def execute(self, **params: Any) -> ToolResult:
        path = params.get("path")
        if not isinstance(path, str) or not path:
            return self._error("Missing required parameter 'path'.")
        try:
            content = self._workspace.read_text(path)
        except (WorkspaceError, WorkspaceViolation) as exc:
            return self._error(f"Could not read {path!r}: {exc}")
        return self._ok(content, path=path)


class DevWriteFileTool(_WorkspaceTool):
    """Create or overwrite a project file (full content replacement)."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dev_write_file",
            description=(
                "Create or overwrite a file in the project workspace with the "
                "complete new content. Always pass the whole file, not a diff."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete new file content.",
                    },
                },
                "required": ["path", "content"],
            },
            category="autodev",
        )

    def execute(self, **params: Any) -> ToolResult:
        path = params.get("path")
        content = params.get("content")
        if not isinstance(path, str) or not path:
            return self._error("Missing required parameter 'path'.")
        if not isinstance(content, str):
            return self._error("Missing required parameter 'content' (string).")
        try:
            if self._workspace.exists(path) and self._workspace.read_text(path) == content:
                # A no-op write is not an edit: say so instead of letting the
                # model believe it changed something and spend a test run on it.
                return self._error(
                    f"No change: {path!r} already has exactly that content. "
                    "Edit the file so the code differs before running the tests."
                )
            written = self._workspace.write_text(path, content)
        except (WorkspaceError, WorkspaceViolation) as exc:
            return self._error(f"Could not write {path!r}: {exc}")
        relative = self._workspace.relative(written)
        return self._ok(
            f"wrote {relative} ({len(content)} chars)", path=relative
        )


class DevRunTestsTool(_WorkspaceTool):
    """Run the project's test suite through an allowlisted runner."""

    def __init__(
        self, workspace: Workspace, runner: Optional[TestRunner] = None,
        *, max_runs: Optional[int] = None,
    ) -> None:
        super().__init__(workspace)
        self._runner = runner or TestRunner(workspace)
        self._max_runs = max_runs
        self.runs = 0
        self.last_outcome = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="dev_run_tests",
            description=(
                "Run the project's automated tests (pytest) and return the real "
                "output. Exit code 0 means every test passed. The tests only run "
                "after you have changed the code with dev_write_file, so edit "
                "first and test afterwards."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Optional relative test path or directory.",
                    },
                },
                "required": [],
            },
            category="autodev",
        )

    def execute(self, **params: Any) -> ToolResult:
        target = params.get("target")
        if target is not None and not isinstance(target, str):
            return self._error("Parameter 'target' must be a string.")
        if not self.edit_happened:
            # Ground truth: the model already saw the failure it is meant to fix.
            # Running pytest again before touching the code can only reproduce it
            # while spending a tool call and one of the few allowed test runs.
            # This refusal does NOT consume a test run: nothing was executed.
            return self._error(
                "Refused: no code has been changed yet, so a test run now would "
                "only reproduce the failure you were given. Call dev_write_file "
                "with your fix first, then run the tests to verify it."
            )
        if self._max_runs is not None and self.runs >= self._max_runs:
            return self._error(
                f"Test-run budget exhausted: this task allows {self._max_runs} "
                "dev_run_tests execution(s). Use the output you already have and "
                "provide a Final Answer."
            )
        try:
            outcome = self._runner.run(target)
        except Exception as exc:  # allowlist violations and spawn failures
            return self._error(f"Test run refused: {type(exc).__name__}: {exc}")
        self.runs += 1
        self.last_outcome = outcome
        text = f"{outcome.summary}\n\n{outcome.failure_excerpt()}".strip()
        return ToolResult(
            tool_name=self.spec.name,
            content=text,
            success=True,
            metadata={"outcome": outcome.to_dict()},
        )


def build_dev_tools(
    workspace: Workspace,
    runner: Optional[TestRunner] = None,
    *,
    max_test_runs: Optional[int] = None,
) -> List[BaseTool]:
    """All tools a developer agent may use, in a stable order."""
    return [
        DevListFilesTool(workspace),
        DevReadFileTool(workspace),
        DevWriteFileTool(workspace),
        DevRunTestsTool(workspace, runner, max_runs=max_test_runs),
    ]


__all__ = [
    "DevListFilesTool",
    "DevReadFileTool",
    "DevRunTestsTool",
    "DevWriteFileTool",
    "build_dev_tools",
]

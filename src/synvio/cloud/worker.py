"""One bounded task worker. Invoked by an active /run request, never a detached thread."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from .common import estimate, limits, public_error, redact_data, validate_files
from .sandbox_runner import VercelTestRunner, project_files
from .store import Store


def run(task_id):
    store = Store()
    task = store.get(task_id)
    if not task or task["status"] != "running":
        return 1
    temporary = tempfile.TemporaryDirectory(prefix="synvio-cloud-")
    os.environ["OPENJARVIS_HOME"] = str(Path(temporary.name) / "sdk")
    try:
        from synvio.autodev.agent import DeveloperAgent
        from synvio.autodev.workspace import Workspace
        from synvio.config import SYNVIOConfig
        from synvio.runtime import engine_for

        baseline = validate_files(store.project())
        store.put_json("baseline", task_id, baseline)
        workspace = Workspace.from_files(baseline, root=Path(temporary.name) / "project")
        workspace.clear_mutations()
        policy = limits()
        config = SYNVIOConfig(provider="openai", engine="cloud", model_override=task["model"], reasoning_mode=task["model_id"].split(":")[1], agent_steps_limit=policy["steps_per_attempt"], tool_execution_limit=policy["tool_calls_per_task"], max_repair_attempts=1, total_step_limit=policy["steps_per_attempt"], total_tool_limit=policy["tool_calls_per_task"], max_test_runs=policy["model_test_runs"], test_timeout_seconds=policy["test_timeout_seconds"])
        # Test infrastructure must be ready before opening the paid model engine.
        runner = VercelTestRunner(workspace, timeout=policy["test_timeout_seconds"])
        runner.check_available()
        with engine_for(config) as engine:
            agent = DeveloperAgent(engine=engine, workspace=workspace, config=config, model=task["model"], test_runner=runner, on_event=lambda event: store.event(task_id, redact_data(event)))
            report = agent.fix(task["task"])
        content = project_files(workspace)
        store.put_json("files", task_id, content)
        store.put_json("project", "default", content, expiry=0)
        result = redact_data(report.to_dict())
        result["workspace_root"] = "project"
        result["log_path"] = f"tasks/{task_id}/events"
        result["estimated_cost_usd"] = estimate(task["model"], result.get("tokens") or {})
        status = "completed" if report.success else "failed"
        store.fields(task_id, status=status, report=result, updated_at=time.time())
        store.event(task_id, {"event": "task_finished", "status": status, "success": report.success})
        return 0
    except Exception as exc:
        store.fields(task_id, status="failed", error=public_error(exc), updated_at=time.time())
        store.event(task_id, {"event": "task_error", "error": public_error(exc)})
        return 1
    finally:
        store.release(task_id)
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))

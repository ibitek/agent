"""One bounded task worker. Invoked by an active /run request, never a detached thread."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from .common import estimate, reasoning_for, limits, public_error, redact_data, validate_files
from .sandbox_runner import VercelTestRunner, project_files
from .store import Store
from .metering import MeteredEngine
from .followups import prompt_for


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
        from .gateway import engine_for

        source = store.get_json("files", task["parent_id"]) if task.get("parent_id") else store.project()
        if source is None:
            raise RuntimeError("The previous file snapshot expired. No model was called.")
        baseline = validate_files(source)
        store.put_json("baseline", task_id, baseline)
        workspace = Workspace.from_files(baseline, root=Path(temporary.name) / "project")
        workspace.clear_mutations()
        policy = limits()
        config = SYNVIOConfig(provider="openai", engine="cloud", model_override=task["model"], reasoning_mode=reasoning_for(task["model_id"]), agent_steps_limit=policy["steps_per_attempt"], tool_execution_limit=policy["tool_calls_per_task"], max_repair_attempts=1, total_step_limit=policy["steps_per_attempt"], total_tool_limit=policy["tool_calls_per_task"], max_test_runs=policy["model_test_runs"], test_timeout_seconds=policy["test_timeout_seconds"])
        if task.get("runtime") in {"web", "app"} and task.get("operation") != "verify":
            from .web_builder import build
            with engine_for(config) as engine:
                def persist_web_cost(event):
                    store.fields(task_id, cost_record=event)
                    store.event(task_id, event)
                meter = MeteredEngine(engine, task["model"], task.get("model_budget_usd", 0.10), persist_web_cost)
                return build(store, task, baseline, meter)
        # Test infrastructure must be ready before opening the paid model engine.
        runner = VercelTestRunner(workspace, timeout=policy["test_timeout_seconds"])
        runner.check_available()
        if task.get("operation") == "verify":
            started = time.monotonic()
            outcome = runner.run()
            test = outcome.to_dict()
            content = project_files(workspace)
            # Verification is read-only: do not publish a global project update.
            store.put_json("files", task_id, content)
            store.put_json("thread-head", task.get("thread_id", task_id), task_id)
            cost = MeteredEngine(object(), task["model"], task.get("model_budget_usd", 0.10)).summary()
            result = {"task": task["task"], "success": outcome.passed,
                      "reason": "tests_passed" if outcome.passed else "tests_failed",
                      "operation": "verify", "files_created": [], "files_modified": [],
                      "tests": [test], "final_test": test, "tokens": {}, "steps_used": 0,
                      "tool_calls": 0, "attempts": 1, "duration_seconds": time.monotonic() - started,
                      "estimated_cost_usd": 0.0, "cost_record": cost,
                      "attempt_details": [{"final_answer": "The checks passed. No project files were changed." if outcome.passed else "The checks failed. No project files were changed. See the actual output."}]}
            status = "completed" if outcome.passed else "failed"
            store.fields(task_id, status=status, report=result, cost_record=cost, updated_at=time.time())
            store.event(task_id, {"event": "task_finished", "status": status, "success": outcome.passed})
            return 0
        with engine_for(config) as engine:
            def persist_cost(event):
                store.fields(task_id, cost_record=event)
                store.event(task_id, event)
            metered = MeteredEngine(engine, task["model"], task.get("model_budget_usd", 0.10), persist_cost)
            def live_event(event):
                store.event(task_id, redact_data(event))
                if event.get("event") == "tool_call" and event.get("tool") == "dev_write_file" and event.get("success"):
                    store.put_json("preview-files", task_id, project_files(workspace))
                    store.event(task_id, {"event": "preview_updated"})
            agent = DeveloperAgent(engine=metered, workspace=workspace, config=config, model=task["model"], test_runner=runner, on_event=live_event)
            report = agent.fix(prompt_for(task))
        content = project_files(workspace)
        store.put_json("files", task_id, content)
        store.put_json("project", "default", content, expiry=0)
        store.put_json("thread-head", task.get("thread_id", task_id), task_id)
        result = redact_data(report.to_dict())
        result["task"] = task["task"]
        result["workspace_root"] = "project"
        result["log_path"] = f"tasks/{task_id}/events"
        result["cost_record"] = metered.summary()
        recorded = [call for call in metered.calls if call["state"] == "recorded"]
        result["tokens"] = {
            "prompt_tokens": sum(call["input_tokens"] for call in recorded),
            "completion_tokens": sum(call["output_tokens"] for call in recorded),
            "total_tokens": sum(call["input_tokens"] + call["output_tokens"] for call in recorded),
        } if recorded else {}
        result["tokens_complete"] = not bool(metered.reserved_unknown)
        result["estimated_cost_usd"] = float(result["cost_record"]["estimated_cost_usd"])
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

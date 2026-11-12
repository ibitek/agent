"""Local API tests; inference is replaced with the existing repair mock."""

from __future__ import annotations

import json
import time
from concurrent.futures import Future
from contextlib import contextmanager

import pytest

from fastapi.testclient import TestClient

from synvio import web
from synvio.autodev.workspace import Workspace
from support import BROKEN_PROJECT, RepairBotEngine


@pytest.fixture(autouse=True)
def trusted_host_for_api_tests(monkeypatch):
    """The API tests run trusted fixture projects, explicitly opting in to host tests."""
    monkeypatch.setenv("SYNVIO_EXECUTION_BACKEND", "host")
    monkeypatch.setenv("SYNVIO_TRUST_HOST_TEST_EXECUTION", "1")


def test_api_runs_a_mocked_repair_and_persists_events(tmp_path, monkeypatch):
    project = tmp_path / "allowed-project"
    project.mkdir()
    workspace = Workspace.from_files(BROKEN_PROJECT, root=project)
    workspace.clear_mutations()
    monkeypatch.setenv("SYNVIO_WORKSPACES", str(project))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent-to-provider")
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")

    selected_configs = []

    @contextmanager
    def mocked_engine(_config):
        selected_configs.append(_config)
        yield RepairBotEngine()

    monkeypatch.setattr(web, "engine_for", mocked_engine)

    with TestClient(web.app) as client:
        assert client.get("/api/health").json()["host"] == "127.0.0.1"
        catalog = client.get("/api/models").json()["items"]
        assert len(catalog) == 3
        gpt4o = next(item for item in catalog if item["id"] == "openai:medium")
        assert (gpt4o["provider"], gpt4o["engine"], gpt4o["model"]) == (
            "openai", "cloud", "gpt-4o"
        )
        assert "test-key" not in str(catalog)
        assert client.get("/api/workspaces").json()["items"] == [str(project)]
        response = client.post(
            "/api/tasks",
            json={
                "workspace": str(project),
                "task": "Fix the failing calculator tests.",
                "model_id": "openai:low",
            },
        )
        assert response.status_code == 202
        task_id = response.json()["id"]

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] in web.TERMINAL:
                break
            time.sleep(0.025)

        assert task["status"] == "completed"
        assert task["report"]["success"] is True
        assert "return a + b" in (project / "calculator.py").read_text(encoding="utf-8")
        events = client.get(f"/api/tasks/{task_id}/events").json()["items"]
        assert any(event["event"] == "tool_call" for event in events)
        assert any(event["event"] == "task_finished" for event in events)
        history = client.get("/api/tasks").json()["items"]
        assert history[0]["id"] == task_id
        assert history[0]["model"] == "gpt-4o-mini"
        assert history[0]["cancellation_supported"] is False
        assert history[0]["report"]["limits"]["global_steps"] == 10
        assert history[0]["report"]["estimated_cost_usd"] is not None
        assert selected_configs[0].engine == "cloud"
        assert selected_configs[0].provider == "openai"
        assert selected_configs[0].model == "gpt-4o-mini"
        files = client.get(f"/api/tasks/{task_id}/files").json()["items"]
        assert {item["path"] for item in files} == {"calculator.py"}
        source = client.get(
            f"/api/tasks/{task_id}/files/content", params={"path": "calculator.py"}
        ).json()
        assert source["read_only"] is True
        assert "return a + b" in source["content"]
        diff = client.get(
            f"/api/tasks/{task_id}/files/diff", params={"path": "calculator.py"}
        ).json()
        assert diff["available"] is True
        assert "+    return a + b" in diff["diff"]


def test_api_refuses_workspaces_not_explicitly_allowlisted(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    blocked = tmp_path / "blocked"
    allowed.mkdir()
    blocked.mkdir()
    monkeypatch.setenv("SYNVIO_WORKSPACES", str(allowed))
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")

    with TestClient(web.app) as client:
        response = client.post(
            "/api/tasks", json={"workspace": str(blocked), "task": "Fix code", "model_id": "openai:medium"}
        )
        assert response.status_code == 403
        assert client.get("/api/tasks").json()["items"] == []


def test_requested_docker_backend_fails_closed_without_host_fallback(tmp_path, monkeypatch):
    project = tmp_path / "allowed"
    project.mkdir()
    monkeypatch.setenv("SYNVIO_WORKSPACES", str(project))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SYNVIO_EXECUTION_BACKEND", "docker")
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")

    with TestClient(web.app) as client:
        response = client.post(
            "/api/tasks",
            json={"workspace": str(project), "task": "Fix a bug", "model_id": "openai:medium"},
        )
        assert response.status_code == 503
        assert "not available" in response.json()["detail"].lower()
        assert "not run on the host" in response.json()["detail"].lower()
        assert client.get("/api/tasks").json()["items"] == []


def test_cancel_endpoint_only_cancels_a_task_before_execution_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        response = client.post("/api/tasks/missing/cancel")
        assert response.status_code == 404

        db = web._database()
        now = time.time()
        db.execute(
            "INSERT INTO tasks(id,task,workspace,model,status,created,updated) VALUES(?,?,?,?,?,?,?)",
            ("running", "example", str(tmp_path), "mock", "running", now, now),
        )
        db.commit()
        db.close()
        response = client.post("/api/tasks/running/cancel")
        assert response.status_code == 409
        assert "cannot safely interrupt" in response.json()["detail"].lower()


def test_cancel_endpoint_cancels_a_queued_task_before_any_worker_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        now = time.time()
        db = web._database()
        db.execute(
            "INSERT INTO tasks(id,task,workspace,model,status,created,updated) VALUES(?,?,?,?,?,?,?)",
            ("queued", "example", str(tmp_path), "mock", "queued", now, now),
        )
        db.commit()
        db.close()
        future = Future()
        with web.TASK_LOCK:
            web.ACTIVE["queued"] = future

        response = client.post("/api/tasks/queued/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert client.get("/api/tasks/queued").json()["status"] == "cancelled"
        assert any(
            event["event"] == "task_cancelled"
            for event in client.get("/api/tasks/queued/events").json()["items"]
        )


def test_limits_endpoint_reports_hard_configured_budgets(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        limits = client.get("/api/limits").json()
    assert limits["steps_per_attempt"] == 10
    assert limits["tool_calls_per_task"] == 8
    assert limits["attempts"] == 1
    assert limits["model_test_runs"] == 2
    assert limits["cancellation"] == "queued_tasks_only"


def test_cloud_models_are_unavailable_without_backend_key(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("SYNVIO_WORKSPACES", str(project))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        model = next(item for item in client.get("/api/models").json()["items"] if item["id"] == "openai:medium")
        assert model["available"] is False
        assert "OPENAI_API_KEY" in model["unavailable_reason"]
        response = client.post("/api/tasks", json={"workspace": str(project), "task": "Fix a bug", "model_id": model["id"]})
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]


def test_unknown_model_and_general_chat_are_rejected_clearly(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("SYNVIO_WORKSPACES", str(project))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        unknown = client.post("/api/tasks", json={"workspace": str(project), "task": "Fix a bug", "model_id": "openai:unknown"})
        assert unknown.status_code == 422
        assert "Unknown model" in unknown.json()["detail"]

        chat = client.post("/api/tasks", json={"workspace": str(project), "task": "hello", "model_id": "openai:medium"})
        assert chat.status_code == 422
        assert "general chat" in chat.json()["detail"]


def test_operator_catalog_supports_additional_providers_without_leaking_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "backend-only-test-secret")
    monkeypatch.setenv(
        "SYNVIO_MODEL_CATALOG",
        '[{"id":"anthropic:sonnet","provider":"anthropic","engine":"cloud","model":"claude-sonnet","reasoning_mode":"medium","api_key_env":"ANTHROPIC_API_KEY"}]',
    )
    item = next(item for item in web._model_catalog() if item["id"] == "anthropic:sonnet")
    assert (item["provider"], item["engine"], item["model"], item["available"]) == (
        "anthropic", "cloud", "claude-sonnet", True
    )
    assert "backend-only-test-secret" not in str(item)


def test_provider_errors_redact_backend_api_key_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "backend-secret-value")
    error = web._public_error(RuntimeError("request failed with backend-secret-value"), {
        "provider": "openai", "engine": "cloud"
    })
    assert "openai" in error and "cloud" in error
    assert "backend-secret-value" not in error
    assert "[redacted]" in error


def test_modified_file_diff_uses_recorded_pre_task_baseline(tmp_path, monkeypatch):
    project = tmp_path / "allowed"
    project.mkdir()
    (project / "module.py").write_text("value = 1\n", encoding="utf-8")
    (project / "module.py").write_text("value = 2\n", encoding="utf-8")
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        now = time.time()
        db = web._database()
        report = {"files_created": [], "files_modified": ["module.py"]}
        db.execute(
            "INSERT INTO tasks(id,task,workspace,model,status,created,updated,report) VALUES(?,?,?,?,?,?,?,?)",
            ("modified", "change module", str(project), "mock", "completed", now, now, json.dumps(report)),
        )
        db.execute(
            "INSERT INTO task_baselines(task_id,path,content) VALUES(?,?,?)",
            ("modified", "module.py", "value = 1\n"),
        )
        db.commit()
        db.close()
        response = client.get(
            "/api/tasks/modified/files/diff", params={"path": "module.py"}
        )
        assert response.status_code == 200
        diff = response.json()
        assert "-value = 1" in diff["diff"]
        assert "+value = 2" in diff["diff"]


def test_file_viewer_only_exposes_reported_workspace_files(tmp_path, monkeypatch):
    project = tmp_path / "allowed"
    project.mkdir()
    (project / "visible.py").write_text("print('visible')\n", encoding="utf-8")
    (project / "secret.py").write_text("secret = True\n", encoding="utf-8")
    monkeypatch.setattr(web, "DB_PATH", tmp_path / "tasks.sqlite3")
    with TestClient(web.app) as client:
        now = time.time()
        db = web._database()
        report = {"files_created": ["visible.py"], "files_modified": []}
        db.execute(
            "INSERT INTO tasks(id,task,workspace,model,status,created,updated,report) VALUES(?,?,?,?,?,?,?,?)",
            ("viewer", "create file", str(project), "mock", "completed", now, now, json.dumps(report)),
        )
        db.commit()
        db.close()
        denied = client.get("/api/tasks/viewer/files/content", params={"path": "secret.py"})
        assert denied.status_code == 404
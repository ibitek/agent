"""Public deployment tests: real Redis commands via fakeredis, no provider calls."""

import io
import json
import time
import zipfile
from contextlib import contextmanager

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from synvio.cloud import app as cloud
from synvio.cloud import auth
from synvio.cloud.common import local_reply

PASSWORD = "test-only-password-with-at-least-20-characters"
SECRET = "test-only-session-secret-with-at-least-32-characters"
ORIGIN = "https://agents.example.test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SYNVIO_PASSWORD", PASSWORD)
    monkeypatch.setenv("SYNVIO_SESSION_SECRET", SECRET)
    monkeypatch.delenv("SYNVIO_CLOUD_DEV", raising=False)
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://redis.example.test")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "test-redis-token")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("SYNVIO_SANDBOX_SNAPSHOT", "test-snapshot")
    monkeypatch.setenv("SYNVIO_ENABLE_TASKS", "1")
    monkeypatch.delenv("SYNVIO_ALLOW_EXPENSIVE_MODELS", raising=False)
    redis = fakeredis.FakeRedis(decode_responses=True)

    def redis_post(url, *, json, **kwargs):
        assert url == "https://redis.example.test", "Only the local Redis emulator is allowed"
        result = redis.execute_command(*json)
        return httpx.Response(200, json={"result": result}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", redis_post)
    with TestClient(cloud.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as value:
        value.redis = redis
        yield value


def login(client):
    response = client.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200
    return response


def create(client, text="Create calculator and tests"):
    return client.post("/api/tasks", json={"task": text, "workspace": "project", "model_id": "openai:low"})


def test_private_routes_require_login_before_models_or_files(client):
    for path in ["/api/tasks", "/api/models", "/api/workspaces", "/api/health"]:
        assert client.get(path).status_code == 401
    assert client.post("/api/chat", json={"message": "siema"}).status_code == 401


def test_session_has_secure_cookie_and_logout_revokes_browser_session(client):
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/session").json()["authenticated"]
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/tasks").status_code == 401


def test_login_fails_closed_without_configuration(client, monkeypatch):
    monkeypatch.delenv("SYNVIO_SESSION_SECRET")
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 503
    assert client.get("/api/health").status_code == 503


def test_wrong_password_and_login_throttling(client):
    for _ in range(10):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_cookie_tampering_and_expiry_are_rejected(client, monkeypatch):
    login(client)
    token = client.cookies.get(auth.COOKIE)
    assert not auth.valid(token[:-1] + ("a" if token[-1] != "a" else "b"))
    monkeypatch.setattr(auth.time, "time", lambda: 4_000_000_000)
    assert not auth.valid(token)


def test_cross_site_posts_rejected_with_valid_cookie(client):
    login(client)
    assert client.post("/api/chat", json={"message": "siema"}, headers={"Origin": "https://attacker.example"}).status_code == 403


def test_greeting_free_without_model_key_or_redis(client, monkeypatch):
    login(client)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN")
    response = client.post("/api/chat", json={"message": "SIEMA!!!"})
    assert response.json() == local_reply()
    assert client.get("/api/tasks").json() == {"items": []}


def test_greeting_cannot_be_sent_to_programming_agent(client):
    login(client)
    assert create(client, "siema").status_code == 422
    assert client.get("/api/tasks").json() == {"items": []}
    assert client.redis.get("synvio:v1:project-lock:") is None


def test_tasks_fail_closed_without_remote_test_environment(client, monkeypatch):
    login(client)
    monkeypatch.delenv("SYNVIO_SANDBOX_SNAPSHOT")
    assert create(client).status_code == 503
    assert client.get("/api/workspaces").json()["items"] == []


def test_public_deployment_rejects_local_windows_workspace_and_expensive_model(client):
    login(client)
    assert client.post("/api/tasks", json={"task": "Fix calculator", "workspace": "C:\\Users\\User\\Documents\\SYNVIO"}).status_code == 403
    assert client.post("/api/chat", json={"message": "Explain a function", "model_id": "openai:medium"}).status_code == 403


def test_task_execution_idempotent_and_events_persist(client, monkeypatch):
    login(client)
    response = create(client)
    assert response.status_code == 202
    value = response.json()["id"]
    assert response.json()["run_required"]
    assert create(client).status_code == 429
    called = []

    async def mocked_worker(store, task_id, token):
        called.append(task_id)
        store.event(task_id, {"event": "tool_call", "tool": "dev_write_file"})
        store.fields(task_id, status="completed", report={"success": True, "files_created": ["calculator.py"], "files_modified": []}, updated_at=time.time())
        store.put_json("files", task_id, {"calculator.py": "def add(a,b): return a+b\n"})
        store.put_json("baseline", task_id, {})
        store.event(task_id, {"event": "task_finished", "success": True})
        store.release(task_id)

    monkeypatch.setattr(cloud, "run_worker", mocked_worker)
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "completed"
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "completed"
    assert called == [value]
    assert client.get(f"/api/tasks/{value}").json()["download_available"]
    events = client.get(f"/api/tasks/{value}/events?after=1").json()["items"]
    assert [event["sequence"] for event in events] == [2, 3]
    assert events[-1]["event"] == "task_finished"
    assert client.get(f"/api/tasks/{value}/files/content", params={"path": "calculator.py"}).json()["read_only"]
    assert client.get(f"/api/tasks/{value}/files/content", params={"path": "../private"}).status_code == 400
    assert client.get(f"/api/tasks/{value}/files/content", params={"path": "other.py"}).status_code == 404
    assert "+def add" in client.get(f"/api/tasks/{value}/files/diff", params={"path": "calculator.py"}).json()["diff"]
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/tasks/{value}/download").content))
    assert archive.namelist() == ["calculator.py"]


def test_cancel_prevents_model_execution(client, monkeypatch):
    login(client)
    value = create(client).json()["id"]
    assert client.post(f"/api/tasks/{value}/cancel").status_code == 200

    async def forbidden(*args):
        pytest.fail("Cancelled task cannot start worker")

    monkeypatch.setattr(cloud, "run_worker", forbidden)
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "cancelled"


def test_expired_running_task_is_not_automatically_retried(client, monkeypatch):
    login(client)
    value = create(client).json()["id"]
    store = cloud.get_store()
    store.fields(value, status="running", deadline=time.time() - 1)
    assert client.get(f"/api/tasks/{value}").json()["status"] == "failed"
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "failed"
    assert client.get(f"/api/tasks/{value}/events").json()["items"][-1]["event"] == "task_error"


def test_daily_limit_blocks_chat_before_opening_model(client, monkeypatch):
    login(client)
    monkeypatch.setenv("SYNVIO_DAILY_REQUEST_LIMIT", "1")
    assert cloud.get_store().allow_paid_request()
    assert client.post("/api/chat", json={"message": "Explain a function"}).status_code == 429


def test_conversation_calls_model_once_without_tools(client, monkeypatch):
    login(client)
    from synvio import runtime
    calls = []

    class Engine:
        def generate(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return {"content": "Explanation", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    @contextmanager
    def engine(config):
        yield Engine()

    monkeypatch.setattr(runtime, "engine_for", engine)
    response = client.post("/api/chat", json={"message": "Explain a function"})
    assert response.status_code == 200
    assert response.json()["content"] == "Explanation"
    assert len(calls) == 1
    assert calls[0][1] == {"model": "gpt-4o-mini", "temperature": 0.4, "max_tokens": 512}
    assert client.get("/api/tasks").json()["items"] == []


def test_sandbox_setup_requires_authenticated_owner(client):
    assert client.post("/api/setup/sandbox").status_code == 401


def test_prepared_snapshot_activates_tasks_without_another_deployment(client, monkeypatch):
    login(client)
    monkeypatch.delenv("SYNVIO_SANDBOX_SNAPSHOT")
    assert client.get("/api/health").json()["can_prepare_tasks"]

    async def prepare(store):
        store.put_json("settings", "sandbox", {"snapshot_id": "prepared-snapshot"}, expiry=0)
        return {"ready": True}

    monkeypatch.setattr(cloud, "prepare_sandbox", prepare)
    assert client.post("/api/setup/sandbox").json()["ready"]
    assert client.get("/api/health").json()["tasks_enabled"]
    assert client.get("/api/workspaces").json()["items"] == ["project"]


def test_worker_timeout_records_failure_without_retry(client, monkeypatch):
    import subprocess

    login(client)
    value = create(client).json()["id"]
    calls = []

    def timeout(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "failed"
    assert client.post(f"/api/tasks/{value}/run").json()["status"] == "failed"
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["synvio.cloud.worker", value]
    assert calls[0][1]["timeout"] == 220
    assert "SYNVIO_PASSWORD" not in calls[0][1]["env"]

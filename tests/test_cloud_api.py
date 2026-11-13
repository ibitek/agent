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
    assert client.get("/api/tasks/" + "a" * 32 + "/preview").status_code == 401


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
    costs = client.get("/api/costs").json()
    assert len(costs["chat"]) == 1
    assert costs["chat"][0]["calls"][0]["state"] == "recorded"
    assert costs["chat"][0]["calls"][0]["cached_input_tokens"] is None
    response = client.post("/api/chat", json={"message": "Explain a function", "model_budget_usd": 0.000001})
    assert response.status_code == 409
    assert len(calls) == 1


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


def test_followup_uses_parent_version_and_rejects_active_or_stale_parent(client):
    login(client)
    value = create(client).json()["id"]
    payload = {"task": "Make it blue", "parent_id": value, "workspace": "project"}
    assert client.post("/api/tasks", json=payload).status_code == 409
    store = cloud.get_store()
    store.put_json("files", value, {"index.html": "<h1>Original</h1>"})
    store.fields(value, status="completed", report={"reason": "tests_passed"})
    store.release(value)
    child = client.post("/api/tasks", json=payload)
    assert child.status_code == 202
    child_id = child.json()["id"]
    item = store.get(child_id)
    assert item["parent_id"] == value and item["thread_id"] == value
    assert len(item["followup_context"]) == 1
    store.fields(child_id, status="completed")
    store.put_json("thread-head", value, child_id)
    store.release(child_id)
    assert client.post("/api/tasks", json=payload).status_code == 409


def test_preview_returns_json_document_from_saved_task_not_host_files(client):
    login(client)
    value = create(client).json()["id"]
    store = cloud.get_store()
    store.put_json("files", value, {"index.html": "<h1>Preview</h1>"})
    store.fields(value, status="completed")
    response = client.get(f"/api/tasks/{value}/preview")
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["available"]
    assert "<h1>Preview</h1>" in response.json()["document"]


def test_followup_missing_snapshot_fails_before_provider(client):
    login(client)
    value = create(client).json()["id"]
    store = cloud.get_store()
    store.fields(value, status="failed")
    store.release(value)
    assert client.post("/api/tasks", json={"task": "Continue", "parent_id": value}).status_code == 409


def test_editor_steps_are_metered_and_duplicate_steps_do_not_charge_again(client, monkeypatch):
    login(client)
    from synvio import runtime
    calls = []
    class Engine:
        def generate(self, messages, **kwargs):
            calls.append(kwargs)
            return {"content": '{"action":"list"}', "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    @contextmanager
    def engine(config):
        yield Engine()
    monkeypatch.setattr(runtime, "engine_for", engine)
    response = client.post("/api/editor/sessions", json={"workspace_name": "demo"})
    assert response.status_code == 201
    value = response.json()["id"]
    payload = {"sequence": 0, "messages": [{"role": "user", "content": "List the project"}]}
    first = client.post(f"/api/editor/sessions/{value}/step", json=payload)
    assert first.status_code == 200
    assert first.json()["action"] == {"action": "list"}
    assert client.post(f"/api/editor/sessions/{value}/step", json=payload).json() == first.json()
    assert len(calls) == 1
    assert client.post(f"/api/editor/sessions/{value}/stop").status_code == 200
    payload["sequence"] = 1
    assert client.post(f"/api/editor/sessions/{value}/step", json=payload).status_code == 409
    assert len(calls) == 1


def test_editor_small_budget_prevents_inference(client, monkeypatch):
    login(client)
    from synvio import runtime
    class Engine:
        def generate(self, *args, **kwargs):
            raise AssertionError("Must never call provider")
    @contextmanager
    def engine(config):
        yield Engine()
    monkeypatch.setattr(runtime, "engine_for", engine)
    value = client.post("/api/editor/sessions", json={"workspace_name": "demo", "model_budget_usd": 0.000001}).json()["id"]
    response = client.post(f"/api/editor/sessions/{value}/step", json={"sequence": 0, "messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 409


def test_elongated_greeting_never_requires_redis_or_provider(client, monkeypatch):
    login(client)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN")
    assert client.post("/api/chat", json={"message": "yooo"}).json()["local_response"] is True


def assistant_engine(monkeypatch, actions):
    from synvio import runtime
    calls = []
    class Engine:
        def generate(self, messages, **kwargs):
            calls.append((messages, kwargs))
            action = actions.pop(0)
            return {"content": json.dumps(action), "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    @contextmanager
    def engine(config):
        yield Engine()
    monkeypatch.setattr(runtime, "engine_for", engine)
    return calls


def test_unified_greeting_is_free_english_durable_and_idempotent(client, monkeypatch):
    login(client)
    monkeypatch.delenv("OPENAI_API_KEY")
    payload = {"request_id": "b" * 32, "message": "siema"}
    response = client.post("/api/assistant", json=payload)
    assert response.status_code == 200
    assert response.json()["message"]["content"].startswith("Cze")
    assert response.json()["task"] is None
    assert response.json()["message"]["local_response"]
    assert client.post("/api/assistant", json=payload).json() == response.json()
    conversation = client.get("/api/conversations/" + "b" * 32).json()
    assert len(conversation["messages"]) == 2
    assert len(client.get("/api/conversations").json()["items"]) == 1
    payload["message"] = "different"
    assert client.post("/api/assistant", json=payload).status_code == 409


def test_unified_inspection_reads_real_files_without_writes_or_tests(client, monkeypatch):
    login(client)
    store = cloud.get_store()
    store.put_json("project", "default", {"calculator.py": "def add(a, b): return a + b"})
    calls = assistant_engine(monkeypatch, [{"action": "list"}, {"action": "read", "path": "calculator.py"}, {"action": "reply", "text": "The project adds two numbers."}])
    response = client.post("/api/assistant", json={"request_id": "c" * 32, "message": "Check what this project does without changing it"})
    assert response.status_code == 200
    assert response.json()["message"]["inspection"] == ["Listed project files", "Read calculator.py"]
    assert response.json()["task"] is None
    assert len(calls) == 3
    assert "def add" in calls[-1][0][-1].content
    assert "Default to" in calls[0][0][0].content and "English" in calls[0][0][0].content
    assert store.project() == {"calculator.py": "def add(a, b): return a + b"}
    assert store.history(50) == []


@pytest.mark.parametrize("approval", ["yes", "to dawaj", "yeah", "Go ahead!"])
def test_question_cannot_start_implementation_even_if_model_misroutes(client, monkeypatch, approval):
    login(client)
    calls = assistant_engine(monkeypatch, [{"action": "execute", "task": "Rewrite the calculator"}])
    first = client.post("/api/assistant", json={"request_id": "d" * 32, "message": "How does the calculator work?"})
    assert first.json()["message"]["kind"] == "clarification"
    assert first.json()["task"] is None
    assert cloud.get_store().history(50) == []
    second = client.post("/api/assistant", json={"request_id": "e" * 32, "conversation_id": "d" * 32, "message": approval})
    assert second.status_code == 200
    assert second.json()["task"]["status"] == "queued"
    assert len(calls) == 1


def test_unified_execution_shares_budget_and_never_queues_twice(client, monkeypatch):
    login(client)
    calls = assistant_engine(monkeypatch, [{"action": "execute", "task": "Build a calculator and tests"}])
    payload = {"request_id": "f" * 32, "message": "Build a calculator", "model_budget_usd": 0.1}
    first = client.post("/api/assistant", json=payload)
    assert first.status_code == 200
    value = first.json()["task"]["id"]
    task = cloud.get_store().get(value)
    cost = float(first.json()["message"]["cost_record"]["estimated_cost_usd"])
    assert task["model_budget_usd"] == pytest.approx(0.1 - cost)
    assert client.post("/api/assistant", json=payload).json() == first.json()
    assert len(calls) == 1 and len(cloud.get_store().history(50)) == 1


def test_unified_execution_reports_missing_runtime_without_starting_work(client, monkeypatch):
    login(client)
    assistant_engine(monkeypatch, [{"action": "execute", "task": "Create a page"}])
    monkeypatch.setattr(cloud, "tasks_ready", lambda *args: False)
    result = client.post("/api/assistant", json={"request_id": "1" * 32, "message": "Create a page"})
    assert result.status_code == 200
    assert result.json()["message"]["kind"] == "setup_required"
    assert result.json()["task"] is None
    assert cloud.get_store().history(50) == []


def test_unified_requested_verification_queues_readonly_operation(client, monkeypatch):
    login(client)
    assistant_engine(monkeypatch, [{"action": "verify", "task": "Run the existing test suite"}])
    result = client.post("/api/assistant", json={"request_id": "2" * 32, "message": "Run the existing tests without changing files"})
    assert result.status_code == 200
    value = result.json()["task"]["id"]
    assert cloud.get_store().get(value)["operation"] == "verify"


def test_bare_approval_without_pending_work_is_free(client, monkeypatch):
    login(client)
    calls = assistant_engine(monkeypatch, [])
    result = client.post('/api/assistant', json={'request_id': '8' * 32, 'message': 'yeah'})
    assert result.status_code == 200
    assert result.json()['message']['local_response']
    assert result.json()['task'] is None and calls == []


def test_static_website_can_queue_without_execution_snapshot(client, monkeypatch):
    login(client)
    monkeypatch.setattr(cloud, 'saved_snapshot', lambda store: None)
    assistant_engine(monkeypatch, [{'action': 'execute', 'task': 'Build a simple website'}])
    result = client.post('/api/assistant', json={'request_id': '9' * 32, 'message': 'Build a simple website'})
    assert result.status_code == 200
    assert result.json()['task'] is not None
    assert cloud.get_store().get(result.json()['task']['id'])['runtime'] == 'web'
    assert not cloud.tasks_ready('python')


def test_verification_worker_never_opens_model_or_publishes_project_changes(client, monkeypatch):
    from types import SimpleNamespace
    from synvio.cloud import worker
    from synvio import runtime
    login(client)
    value = client.post("/api/tasks", json={"task": "Run existing tests", "operation": "verify"}).json()["id"]
    store = cloud.get_store()
    before = {"test_ok.py": "def test_ok(): pass"}
    store.put_json("project", "default", before)
    store.fields(value, status="running")
    monkeypatch.setattr(runtime, "engine_for", lambda *args: pytest.fail("Verification must not open a model"))
    class Runner:
        def __init__(self, workspace, timeout):
            self.workspace = workspace
        def check_available(self):
            pass
        def run(self):
            assert self.workspace.read_text("test_ok.py") == before["test_ok.py"]
            return SimpleNamespace(passed=True, to_dict=lambda: {"passed": True, "stdout_tail": "1 passed", "returncode": 0})
    monkeypatch.setattr(worker, "VercelTestRunner", Runner)
    assert worker.run(value) == 0
    result = store.get(value)
    assert result["status"] == "completed"
    assert result["report"]["files_modified"] == []
    assert result["report"]["estimated_cost_usd"] == 0
    assert store.project() == before

def test_static_worker_uses_real_file_loop_without_creating_sandbox(client, monkeypatch):
    from synvio.cloud import worker
    login(client)
    assistant_engine(monkeypatch, [
        {'action': 'write', 'path': 'index.html', 'content': '<h1>Website</h1>'},
        {'action': 'finish', 'text': 'Saved the website.'},
    ])
    value = client.post('/api/tasks', json={'task': 'Build a website', 'runtime': 'web'}).json()['id']
    store = cloud.get_store()
    store.fields(value, status='running')
    monkeypatch.setattr(worker, 'VercelTestRunner', lambda *args, **kwargs: pytest.fail('Static builder must not create a sandbox'))
    assert worker.run(value) == 0
    result = store.get(value)
    assert result['status'] == 'completed'
    assert result['report']['verification_level'] == 'static'
    assert result['report']['tests'] == []
    assert store.get_json('files', value)['index.html'] == '<h1>Website</h1>'


def test_routing_and_execution_share_one_daily_allowance(client, monkeypatch):
    login(client)
    monkeypatch.setenv('SYNVIO_DAILY_REQUEST_LIMIT', '1')
    calls = assistant_engine(monkeypatch, [{'action': 'execute', 'task': "clear implementation request preserving the user's requirements"}])
    async def run(store, value, oidc_token=None):
        store.fields(value, status='completed')
        store.release(value)
    monkeypatch.setattr(cloud, 'run_worker', run)
    original = 'Zrob nowoczesna strone SYNVIO z dwiema podstronami'
    response = client.post('/api/assistant', json={'request_id':'7'*32, 'message':original})
    assert response.status_code == 200
    value = response.json()['task']['id']
    assert cloud.get_store().get(value)['task'] == original
    assert cloud.get_store().get(value)['runtime'] == 'web'
    assert client.post(f'/api/tasks/{value}/run').json()['status'] == 'completed'
    assert len(calls) == 1
    assert not cloud.get_store().allow_paid_request()


def test_preview_explains_failure_before_first_write(client):
    login(client)
    value = create(client).json()['id']
    store = cloud.get_store()
    store.fields(value, status='failed', error='The daily request limit has been reached.')
    preview = client.get(f'/api/tasks/{value}/preview').json()
    assert not preview['available']
    assert 'daily request limit' in preview['reason']

def test_explicit_retry_reuses_original_request_and_skips_empty_failed_snapshot(client, monkeypatch):
    login(client)
    original = 'Build a modern SYNVIO website with two pages'
    failed = client.post('/api/tasks', json={'task': original, 'runtime': 'web'}).json()['id']
    store = cloud.get_store()
    store.fields(failed, status='failed', error='Daily limit')
    store.release(failed)
    conversation = '6'*32
    store.put_json('conversation', conversation, {'id': conversation, 'last_task_id': failed, 'messages': [{'id':'old', 'role':'user', 'content':original}]})
    calls = assistant_engine(monkeypatch, [])
    result = client.post('/api/assistant', json={'request_id':'5'*32,'conversation_id':conversation,'message':'sprobuj jeszcze raz'})
    assert result.status_code == 200
    created = store.get(result.json()['task']['id'])
    assert created['task'] == original and created['parent_id'] is None
    assert created['runtime'] == 'web' and calls == []


def test_stronger_catalog_is_distinct_priced_and_server_gated(client, monkeypatch):
    login(client)
    from synvio.cloud.common import resolve_model, reasoning_for
    assert reasoning_for('openai:gpt-4.1') == 'medium'
    assert reasoning_for('openai:gpt-4.1-mini') == 'medium'
    with pytest.raises(Exception) as blocked:
        resolve_model('openai:gpt-4.1')
    assert blocked.value.status_code == 403
    monkeypatch.setenv('SYNVIO_ALLOW_EXPENSIVE_MODELS', '1')
    all_models = client.get('/api/models').json()['items']
    models = [item for item in all_models if item['provider']=='openai']
    assert len({item['model'] for item in models}) == len(models)
    assert {item['model'] for item in models} == {'gpt-4o-mini','gpt-4o','gpt-4.1-mini','gpt-4.1'}
    assert all(item['available'] and item['pricing']['currency']=='USD' for item in models)
    assert resolve_model('openai:gpt-4.1') == 'gpt-4.1'


def test_application_build_is_owner_only_idempotent_and_keeps_model_quota(client, monkeypatch):
    assert client.get('/api/tasks/'+'a'*32+'/build-preview').status_code == 401
    login(client)
    from synvio.cloud import app_build
    value = client.post('/api/tasks', json={'task':'Build a React app','runtime':'app'}).json()['id']
    store = cloud.get_store()
    store.put_json('files',value,{'package.json':json.dumps({'devDependencies':{'vite':'6.4.6'},'scripts':{'build':'vite build'}}),'index.html':'<div>source</div>'})
    store.fields(value,status='completed')
    calls=[]
    async def fake_build(store, task, request, files):
        calls.append(task)
        state={'status':'ready','request_id':request,'browser_verified':False}
        store.put_json('application-build',task,state)
        store.put_json('built-files',task,{'dist/index.html':'<h1>Built</h1>'})
        return state
    monkeypatch.setattr(app_build,'build',fake_build)
    payload={'request_id':'3'*32}
    result=client.post(f'/api/tasks/{value}/build-preview',json=payload)
    assert result.status_code == 200 and result.json()['status']=='ready'
    assert client.post(f'/api/tasks/{value}/build-preview',json=payload).json()==result.json()
    assert len(calls)==1
    preview=client.get(f'/api/tasks/{value}/preview').json()
    assert preview['available'] and preview['kind']=='built_application' and preview['can_build']
    monkeypatch.setenv('SYNVIO_DAILY_REQUEST_LIMIT','1')
    assert store.allow_paid_request()


def test_react_routing_uses_original_source_request_without_snapshot(client, monkeypatch):
    login(client)
    monkeypatch.setattr(cloud,'saved_snapshot',lambda store:None)
    assistant_engine(monkeypatch,[{'action':'execute','task':'Build the React app'}])
    response=client.post('/api/assistant',json={'request_id':'4'*32,'message':'Build a React dashboard with Vite'})
    assert response.status_code==200
    assert cloud.get_store().get(response.json()['task']['id'])['runtime']=='app'


def test_filename_query_survives_vercel_path_capture(client):
    login(client)
    value=create(client).json()['id']
    store=cloud.get_store()
    store.fields(value,status='completed',report={'files_modified':['index.html']})
    store.put_json('files',value,{'index.html':'<h1>Actual source</h1>'})
    result=client.get(f'/api/tasks/{value}/files/content',params={'file':'index.html','path':f'tasks/{value}/files/content'})
    assert result.status_code==200 and result.json()['content']=='<h1>Actual source</h1>'
    assert client.get(f'/api/tasks/{value}/files/content',params={'file':'../secret'}).status_code==400


def test_wallet_does_not_invent_balance_or_activate_payments(client):
    assert client.get('/api/wallet').status_code==401
    login(client)
    result=client.get('/api/wallet').json()
    assert result['payments_enabled'] is False and result['balance_usd'] is None
    assert result['bonus_tiers'][0]=={'deposit_usd':'5','bonus_percent':2}
    assert result['bonus_tiers'][-1]=={'deposit_usd':'100','bonus_percent':20}
    assert result['compute_price_per_minute_usd']=='0.005'

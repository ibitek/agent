"""Browser API contracts; the model and Redis are simulated."""

import json
from contextlib import contextmanager

from synvio.cloud import gateway
from test_cloud_api import client, login

STATUS = "/api/local-browser/status"
STEP = "/api/local-browser/next"
BODY = {
    "request_id": "9" * 32,
    "goal": "Find our help page",
    "page": {"title": "Home", "address": "https://example.test/", "text": "Help for visitors.",
             "links": [{"index": 0, "label": "Help"}]},
    "can_follow": True,
}


def mock_model(monkeypatch, decision):
    calls = []

    class Engine:
        def generate(self, messages, **_):
            calls.append(messages[-1].content)
            return {"content": json.dumps(decision),
                    "usage": {"prompt_tokens": 45, "completion_tokens": 12, "total_tokens": 57}}

    @contextmanager
    def connect(_config):
        yield Engine()

    monkeypatch.setattr(gateway, "engine_for", connect)
    return calls


def test_missing_key_keeps_local_controls_available_without_consuming_quota(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SYNVIO_DAILY_REQUEST_LIMIT", "1")
    calls = mock_model(monkeypatch, {"action": "follow", "index": 0})
    assert client.get(STATUS).status_code == 401
    login(client)
    assert client.get(STATUS).json() == {"research_configured": False}
    assert client.post(STEP, json=BODY).status_code == 503
    assert not calls
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    assert client.get(STATUS).json() == {"research_configured": True}
    assert client.post(STEP, json=BODY).status_code == 200
    assert len(calls) == 1


def test_browser_model_action_is_idempotent_and_only_uses_available_links(client, monkeypatch):
    calls = mock_model(monkeypatch, {"action": "follow", "index": 0})
    login(client)
    first = client.post(STEP, json=BODY)
    assert first.status_code == 200, first.text
    assert first.json()["decision"] == {"action": "follow", "index": 0}
    assert first.json() == client.post(STEP, json=BODY).json()
    assert client.post(STEP, json={**BODY, "goal": "Different request"}).status_code == 409
    assert len(calls) == 1
    unavailable = {**BODY, "request_id": "8" * 32, "can_follow": False}
    assert client.post(STEP, json=unavailable).status_code == 502
    assert len(calls) == 2

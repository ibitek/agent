"""Image provider and conversation contracts. Only fake provider responses."""
import base64
import json

import httpx

from synvio.cloud import image_generation
from synvio.cloud.assistant import requested_images
from synvio.cloud.store import Store
from test_cloud_api import client, login

JPEG = b"\xff\xd8\xff\xe0test-image"
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nmask").decode()


def install_provider(monkeypatch, jpeg=JPEG, before_response=None):
    calls = []
    async_client = httpx.AsyncClient

    def respond(request):
        calls.append((request.url.path, json.loads(request.content)))
        if before_response:
            before_response()
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(jpeg).decode()} for _ in range(calls[-1][1]["n"])],
            "usage": {"input_tokens_details": {"text_tokens": 10, "image_tokens": 20}, "output_tokens": 100},
        })

    monkeypatch.setattr(image_generation.httpx, "AsyncClient",
                        lambda **kwargs: async_client(transport=httpx.MockTransport(respond), **kwargs))
    return calls


def test_plain_chat_image_request_saves_inline_assets_exactly_once(client, monkeypatch):
    calls = install_provider(monkeypatch)
    login(client)
    body = {"request_id": "1" * 32, "message": "Wygeneruj 2 obrazy kawiarni."}
    response = client.post("/api/assistant", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    message = result["message"]
    assert message["kind"] == "image" and result["task"] is None
    assert len(message["generated_images"]) == 2
    assert message["cost_record"]["service_estimated_cost_usd"] == "0.004173000"
    assert client.post("/api/assistant", json=body).json() == result
    assert [path for path, _ in calls] == ["/v1/images/generations"]
    saved = client.get("/api/conversations/" + result["conversation_id"]).json()["messages"][-1]
    assert saved == message
    url = "/api/media/images/" + saved["generated_images"][0]["id"]
    media = client.get(url)
    assert media.headers["content-type"] == "image/jpeg" and media.content == JPEG
    client.post("/api/logout")
    assert client.get(url).status_code == 401


def test_studio_passes_single_source_and_mask_with_requested_variant_count(client, monkeypatch):
    calls = install_provider(monkeypatch)
    login(client)
    response = client.post("/api/assistant", json={
        "request_id": "2" * 32, "message": "Change the marked area to blue.",
        "images": [PNG], "image_options": {"mask": PNG, "variants": 2, "size": "1536x1024", "quality": "low"},
    })
    assert response.status_code == 200, response.text
    path, payload = calls[0]
    assert path == "/v1/images/edits"
    assert payload["images"] == [{"image_url": PNG}] and payload["mask"] == {"image_url": PNG}
    assert payload["n"] == 2 and payload["size"] == "1536x1024"


def test_image_intent_respects_destination_and_project_write_negation():
    assert requested_images("Zaplanuj wygenerowanie obrazu.") is None
    assert requested_images("Wygeneruj grafikę i zapisz ją w projekcie.").save_to_project
    assert not requested_images("Wygeneruj grafikę. Nie zapisuj jej w projekcie.").save_to_project
    assert requested_images("Can you make an illustration of a cafe?").variants == 1
    assert requested_images("Wygeneruj do projektu nowe logo.").save_to_project
    assert not requested_images("Wygeneruj obraz projektanta pracującego w projekcie naukowym.").save_to_project
    assert requested_images("Create an image processing app.") is None


def app_project(client):
    login(client)
    response = client.post("/api/tasks", json={"task": "Build a cafe page", "runtime": "app"})
    assert response.status_code == 202, response.text
    store = Store()
    value = response.json()["id"]
    store.finish(store.get(value), {"index.html": "<h1>Cafe</h1>"}, "completed", {})
    store.release(value)
    return store, value


def test_chat_can_save_two_generated_images_in_one_project_revision(client, monkeypatch):
    store, parent = app_project(client)
    calls = install_provider(monkeypatch)
    body = {"request_id": "3" * 32, "parent_task_id": parent,
            "message": "Wygeneruj 2 obrazy przedstawiające kawiarnię i zapisz je w projekcie."}
    response = client.post("/api/assistant", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    message = result["message"]
    revision = message["task_id"]
    assert revision != parent and result["task"] is None
    assert store.get(revision)["operation"] == "generated_images"
    assert store.get(revision)["status"] == "completed"
    paths = message["project_images"]
    assert len(paths) == 2 and all(path.startswith("public/images/") for path in paths)
    saved = store.get_json("files", revision)
    assert set(saved) == {"index.html", *paths}
    assert all("data:image/jpeg;base64," in saved[path] for path in paths)
    assert store.get_json("files", parent) == {"index.html": "<h1>Cafe</h1>"}
    assert [event["path"] for event in store.events(revision) if event.get("generated")] == paths
    conversation = client.get("/api/conversations/" + result["conversation_id"]).json()
    assert conversation["last_task_id"] == revision
    assert client.post("/api/assistant", json=body).json() == result
    assert len(calls) == 1


def test_project_image_request_checks_destination_before_spending(client, monkeypatch):
    calls = install_provider(monkeypatch)
    login(client)
    response = client.post("/api/assistant", json={
        "request_id": "4" * 32, "message": "Generate an image and save it to the project.",
    })
    assert response.status_code == 409
    assert calls == []


def test_large_generated_image_is_kept_in_chat_if_project_save_fails(client, monkeypatch):
    store, parent = app_project(client)
    calls = install_provider(monkeypatch, jpeg=JPEG + b"x" * 200_000)
    response = client.post("/api/assistant", json={
        "request_id": "5" * 32, "parent_task_id": parent,
        "message": "Generate an image and save it to the project.",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    message = result["message"]
    assert message["task_id"] is None and "project_images" not in message
    assert "project save could not be completed" in message["content"]
    assert len(message["generated_images"]) == 1 and len(calls) == 1
    assert client.get("/api/conversations/" + result["conversation_id"]).json()["last_task_id"] == parent
    assert store.get_json("files", parent) == {"index.html": "<h1>Cafe</h1>"}


def test_image_generation_cannot_overwrite_newer_project_revision(client, monkeypatch):
    store, parent = app_project(client)
    thread = store.get(parent).get("thread_id", parent)
    newer = "a" * 32
    calls = install_provider(monkeypatch, before_response=lambda: store.put_json("thread-head", thread, newer))
    response = client.post("/api/assistant", json={
        "request_id": "6" * 32, "parent_task_id": parent,
        "message": "Generate an image and save it to the project.",
    })
    assert response.status_code == 200, response.text
    assert response.json()["message"]["generated_images"]
    assert store.get_json("thread-head", thread) == newer
    assert len(store.history(10)) == 1 and len(calls) == 1

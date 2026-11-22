"""Image provider and conversation contracts. Only fake provider responses."""
import base64
import json

import httpx

from synvio.cloud import image_generation
from synvio.cloud.assistant import requested_images
from test_cloud_api import client, login

JPEG = b"\xff\xd8\xff\xe0test-image"
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nmask").decode()


def install_provider(monkeypatch):
    calls = []
    async_client = httpx.AsyncClient

    def respond(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(JPEG).decode()} for _ in range(calls[-1][1]["n"])],
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


def test_plans_and_project_asset_requests_do_not_trigger_chat_image_generation():
    assert requested_images("Zaplanuj wygenerowanie obrazu.") is None
    assert requested_images("Wygeneruj grafikę i zapisz ją w projekcie.") is None
    assert requested_images("Can you make an illustration of a cafe?").variants == 1

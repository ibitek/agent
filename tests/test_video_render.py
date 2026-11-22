"""Background video contract tests with simulated Gateway responses."""
import hashlib
import json
from urllib.parse import urlsplit

import httpx

from synvio.cloud import video_render
from synvio.cloud.store import Store
from test_cloud_api import client, login
from test_video_studio import PNG, storyboard

MODEL = "alibaba/wan-v3.0-video"
BOARD = "/api/video-studio/standalone"
SCENE_ID = "b" * 32
RENDER = BOARD + "/scenes/" + SCENE_ID + "/render"


def save_scene(client, *, single_reference=False):
    initial = client.get(BOARD).json()
    draft = storyboard()
    if single_reference:
        draft["characters"][0]["reference"] = None
    draft["scenes"].insert(0, {"id": "d" * 32, "title": "Outside", "direction": "Track the street at dawn."})
    draft["characters"].append({"id": "e" * 32, "name": "Excluded character", "reference": PNG})
    response = client.put(BOARD, json={"revision": initial["revision"], "storyboard": draft})
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def gateway_stub(monkeypatch, *, unconfirmed=False):
    captured = []
    async_client = httpx.AsyncClient
    states = iter([
        {"status": "pending"},
        {"status": "completed", "videos": [{"type": "url", "url": "https://media.example.test/clip.mp4", "mediaType": "video/mp4"}]},
    ])
    monkeypatch.setenv("SYNVIO_ENABLE_GATEWAY", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "fake-gateway-token")

    def respond(request):
        assert request.url.host == "ai-gateway.vercel.sh"
        captured.append(request)
        if request.url.path == "/v1/models":
            models = []
            for model, rate, operations in (
                (MODEL, "0.05", ["text-to-video", "image-to-video", "reference-to-video"]),
                (video_render.GROK_LITE, "0.02", ["text-to-video", "image-to-video"]),
            ):
                models.append({
                    "id": model, "supported_specifications": ["v4"],
                    "video_capabilities": {
                        "supported_aspect_ratios": ["9:16"], "supported_durations_seconds": [5],
                        "supported_operations": operations,
                    },
                    "pricing": {"video_duration_pricing": [
                        {"resolution": "480p", "cost_per_second": rate},
                    ]},
                })
            return httpx.Response(200, json={"data": models})
        if request.url.path.endswith("/start"):
            if unconfirmed and sum(item.url.path.endswith("/start") for item in captured) == 1:
                raise httpx.ReadTimeout("Test timeout", request=request)
            return httpx.Response(200, json={"operation": {"id": "saved-gateway-operation"}})
        assert json.loads(request.content) == {"operation": {"id": "saved-gateway-operation"}}
        return httpx.Response(200, json=next(states))

    monkeypatch.setattr(video_render.httpx, "AsyncClient",
                        lambda **kwargs: async_client(transport=httpx.MockTransport(respond), **kwargs))
    return captured


def test_video_render_persists_job_for_later_poll_and_passes_only_selected_references(client, monkeypatch):
    requests = gateway_stub(monkeypatch)
    login(client)
    board_revision = save_scene(client)
    catalog = client.get("/api/video-renderers").json()
    assert catalog["connected"]
    assert {model["id"]: model["rates"]["480p"] for model in catalog["models"]}[MODEL] == "0.065000000"
    body = {"revision": board_revision, "request_id": "3" * 32, "model": MODEL, "estimate_usd": "0.325"}
    first = client.post(RENDER, json=body)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "pending"
    assert "operation" not in first.json()
    assert client.post(RENDER, json=body).json() == first.json()
    starts = [request for request in requests if request.url.path.endswith("/start")]
    assert len(starts) == 1 and len(starts[0].headers["idempotency-key"]) == 64
    payload = json.loads(starts[0].content)
    assert payload["aspectRatio"] == "9:16"
    assert payload["resolution"] == "854x480"
    assert "Navy coat" in payload["prompt"] and "Track the street at dawn." in payload["prompt"]
    assert "Excluded character" not in payload["prompt"]
    assert len(payload["inputReferences"]) == 2
    reference_url = urlsplit(payload["inputReferences"][0]["url"]).path
    client.cookies.clear()
    assert client.get(RENDER).status_code == 401
    assert client.get(reference_url).headers["content-type"] == "image/png"
    assert client.get("/api/video-input/" + "0" * 48).status_code == 404
    login(client)
    assert client.get(RENDER).json()["render"]["status"] == "pending"
    store = Store()
    key = store.get_json("video-latest", "standalone:" + SCENE_ID)
    client.redis.delete(store.key("video-poll", key))
    result = client.get(RENDER).json()["render"]
    assert result["status"] == "completed" and result["video_url"].endswith(".mp4")
    assert client.get(RENDER).json()["render"] == result
    assert store.get_json("chat-cost", hashlib.sha256(key.encode()).hexdigest())["service_estimated_cost_usd"] == "0.325000000"


def test_estimate_change_and_unconfirmed_submission_do_not_create_duplicate_jobs(client, monkeypatch):
    requests = gateway_stub(monkeypatch, unconfirmed=True)
    login(client)
    revision = save_scene(client)
    body = {"revision": revision, "request_id": "4" * 32, "model": MODEL, "estimate_usd": "0.10"}
    assert client.post(RENDER, json=body).status_code == 409
    assert not any(request.url.path.endswith("/start") for request in requests)
    body["estimate_usd"] = "0.325"
    result = client.post(RENDER, json=body).json()
    assert result["status"] == "unconfirmed"
    assert client.post(RENDER, json=body).json() == result
    assert client.post(RENDER, json={**body, "request_id": "5" * 32}).status_code == 409
    assert len([request for request in requests if request.url.path.endswith("/start")]) == 1
    resumed = client.post(RENDER + "/resume/" + body["request_id"])
    assert resumed.status_code == 202 and resumed.json()["status"] == "pending"
    starts = [request for request in requests if request.url.path.endswith("/start")]
    assert len(starts) == 2 and starts[0].content == starts[1].content
    assert starts[0].headers["idempotency-key"] == starts[1].headers["idempotency-key"]


def test_grok_lite_charges_one_opening_image_and_rejects_multiple_references(client, monkeypatch):
    requests = gateway_stub(monkeypatch)
    login(client)
    revision = save_scene(client)
    grok = client.get("/api/video-renderers").json()["models"][0]
    assert grok["id"] == video_render.GROK_LITE
    assert grok["rates"]["480p"] == "0.026000000"
    assert grok["image_input_rate_usd"] == "0.013000000"
    body = {"revision": revision, "request_id": "6" * 32, "model": grok["id"], "estimate_usd": "0.143"}
    assert client.post(RENDER, json=body).status_code == 422

    body["revision"] = save_scene(client, single_reference=True)
    assert client.post(RENDER, json={**body, "estimate_usd": "0.13"}).status_code == 409
    assert not any(request.url.path.endswith("/start") for request in requests)
    response = client.post(RENDER, json=body)
    assert response.status_code == 202, response.text
    assert response.json()["estimate_usd"] == "0.143000000"
    assert response.json()["image_input_cost_usd"] == "0.013000000"
    start = next(request for request in requests if request.url.path.endswith("/start"))
    payload = json.loads(start.content)
    assert payload["image"]["type"] == "url"
    assert "aspectRatio" not in payload and "inputReferences" not in payload
    assert payload["resolution"] == "854x480"


def test_storyboard_quote_confirms_whole_cost_and_submits_each_scene_once(client, monkeypatch):
    requests = gateway_stub(monkeypatch)
    login(client)
    save_scene(client)
    endpoint = BOARD + "/render-batch"
    quote = client.get(endpoint).json()
    assert quote["estimate_usd"] == "0.455000000"
    assert [scene["model"] for scene in quote["scenes"]] == [video_render.GROK_LITE, MODEL]
    assert client.post(endpoint, json={"quote": "1" * 64}).status_code == 409
    assert not any(request.url.path.endswith("/start") for request in requests)
    first = client.post(endpoint, json={"quote": quote["quote"]})
    assert first.status_code == 202
    assert [scene["status"] for scene in first.json()["scenes"]] == ["pending", "pending"]
    assert client.post(endpoint, json={"quote": quote["quote"]}).json() == first.json()
    starts = [request for request in requests if request.url.path.endswith("/start")]
    assert len(starts) == 2
    assert len({request.headers["idempotency-key"] for request in starts}) == 2

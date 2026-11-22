"""Background scene renders on the existing Vercel AI Gateway connection."""
import asyncio
import base64
import hashlib
import os
import secrets
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, Field

from . import gateway
from .retail import service_amount
from .store import Store
from .video_studio import BoardKey, Storyboard

router = APIRouter(prefix="/api")
BASE = "https://ai-gateway.vercel.sh"
GROK_LITE = "spacexai/grok-imagine-video-1.5-lite"
MODEL_NAMES = {
    GROK_LITE: "Grok Imagine Video 1.5 Lite",
    "alibaba/wan-v3.0-video": "Wan 3.0 Video",
    "alibaba/wan-v3.0-video-prime": "Wan 3.0 Video Prime",
}
# The public Gateway metadata lists duration rates. SpaceX documents the
# separate image input fee at https://docs.x.ai/developers/models/grok-imagine-video-1.5-lite.
IMAGE_INPUT_RATES = {GROK_LITE: Decimal("0.01")}
RESOLUTIONS = {"480p": "854x480", "720p": "1280x720", "1080p": "1920x1080"}
ACTIVE = {"starting", "pending", "unconfirmed"}
RESUME_SECONDS = 45 * 60
SceneKey = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
SCENE_PATH = "/video-studio/{value}/scenes/{scene_id}/render"


class RenderRequest(BaseModel):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1, max_length=80)
    resolution: Literal["480p", "720p", "1080p"] = "480p"
    estimate_usd: Decimal = Field(gt=0, le=100, max_digits=12, decimal_places=9)
    include_previous: bool = True


async def available_models(store):
    cached = store.get_json("video-models", "input-rates-v1")
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(BASE + "/v1/models")
        response.raise_for_status()
        models = {}
        for entry in response.json()["data"]:
            model = entry.get("id")
            if model not in MODEL_NAMES or "v4" not in entry.get("supported_specifications", []):
                continue
            rates = {item["resolution"]: service_amount(Decimal(item["cost_per_second"]))
                     for item in entry["pricing"]["video_duration_pricing"] if item.get("resolution") in RESOLUTIONS}
            if rates:
                caps = entry["video_capabilities"]
                operations = caps.get("supported_operations", [])
                models[model] = {
                    "id": model, "label": MODEL_NAMES[model], "rates": rates,
                    "ratios": caps["supported_aspect_ratios"], "durations": caps["supported_durations_seconds"],
                    "supports_multiple_images": model != GROK_LITE and "reference-to-video" in operations,
                    "image_input_rate_usd": service_amount(IMAGE_INPUT_RATES.get(model, Decimal(0))),
                    "follows_input_aspect_ratio": model == GROK_LITE,
                }
    except (httpx.HTTPError, ArithmeticError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "Current video prices could not be retrieved.") from exc
    if models:
        models = {model: models[model] for model in MODEL_NAMES if model in models}
        store.put_json("video-models", "input-rates-v1", models, expiry=600)
    return models


@router.get("/video-renderers")
async def catalog():
    return {"connected": gateway.connected(), "models": list((await available_models(Store())).values())}


class GatewayRejected(Exception):
    pass


async def request_gateway(action, model, payload, request_id=None):
    key = gateway.token() if gateway.connected() else None
    if not key:
        raise GatewayRejected("The video gateway is not connected.")
    # Use the same v4 start/status protocol as the AI SDK video adapter.
    headers = {
        "Authorization": "Bearer " + key, "ai-video-model-specification-version": "4",
        "ai-model-id": model, "ai-gateway-protocol-version": "0.0.1",
        "ai-gateway-auth-method": "api-key" if os.environ.get("AI_GATEWAY_API_KEY", "").strip() else "oidc",
    }
    if request_id:
        headers["idempotency-key"] = request_id
    async with asyncio.timeout(35), httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        response = await client.post(BASE + "/v4/ai/video-model/" + action, headers=headers, json=payload)
    if response.status_code in {401, 402, 403}:
        raise GatewayRejected("AI Gateway requires a connected account with video access and credits.")
    if response.status_code == 429:
        raise GatewayRejected("AI Gateway is rate-limiting video requests.")
    if action == "start" and response.status_code in {400, 404, 413, 422}:
        raise GatewayRejected("AI Gateway rejected the selected model or scene inputs.")
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Unexpected video response.")
    return result


def public_job(job):
    result = {key: job[key] for key in ("id", "status", "model", "resolution", "ratio", "duration", "estimate_usd",
                                       "image_input_cost_usd", "created_at", "video_url", "error") if key in job}
    result["can_resume"] = job["status"] == "unconfirmed" and time.time() - job["created_at"] < RESUME_SECONDS
    return result


@contextmanager
def submission_lock(store, scope, request_id):
    lock = store.key("video-render-lock", scope)
    if not store.call("SET", lock, request_id, "NX", "EX", 90):
        raise HTTPException(409, "A render is already being submitted for this scene.")
    try:
        yield
    finally:
        store.call("EVAL", "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",
                   1, lock, request_id)


async def submit_saved_render(store, job_key, job, payload, *, resuming=False):
    job.update(status="starting", submission_started_at=time.time())
    job.pop("error", None)
    store.put_json("video-job", job_key, job)
    try:
        result = await request_gateway("start", job["model"], payload, hashlib.sha256(job_key.encode()).hexdigest())
        if result.get("operation") is None:
            raise ValueError("No operation returned.")
        job.update(status="pending", operation=result["operation"])
    except GatewayRejected as exc:
        job.update(status="unconfirmed" if resuming else "failed", error=str(exc))
    except (TimeoutError, httpx.HTTPError, ValueError):
        job.update(status="unconfirmed", error="The provider did not confirm submission. No duplicate render was sent.")
    store.put_json("video-job", job_key, job)
    return public_job(job)


def reference_url(store, data):
    origin = os.environ.get("SYNVIO_PUBLIC_URL", "https://agents.synviocore.xyz").rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise HTTPException(503, "Video references require the configured public HTTPS address of Kaelia.")
    identifier = secrets.token_hex(24)
    store.put_json("video-input", identifier, data, expiry=3600)
    return {"type": "url", "url": origin + "/api/video-input/" + identifier,
            "mediaType": data.removeprefix("data:").split(";", 1)[0]}


@router.api_route("/video-input/{value}", methods=["GET", "HEAD"])
def video_input(value: Annotated[str, Path(pattern=r"^[a-f0-9]{48}$")]):
    raw = Store().get_json("video-input", value)
    if not raw:
        raise HTTPException(404, "This render reference has expired.")
    mime, _, encoded = raw.removeprefix("data:").partition(";base64,")
    return Response(base64.b64decode(encoded), media_type=mime)


def scene_payload(store, board, index, body, model, cast):
    scene = board.scenes[index]
    directions = [scene.direction.strip()]
    references = []
    if body.include_previous and index > 0 and board.scenes[index - 1].direction.strip():
        directions.append("Previous scene, for continuity only: " + board.scenes[index - 1].direction.strip()[:750])
    single_image = None
    for character in cast:
        line = "Character: " + (character.name.strip() or "unnamed")
        if character.reference:
            if model["supports_multiple_images"]:
                references.append(reference_url(store, character.reference))
                line += f" — match appearance from Image {len(references)}"
            else:
                single_image = reference_url(store, character.reference)
                line += " — preserve this character's appearance from the opening image"
        if character.appearance.strip():
            line += ". Appearance: " + character.appearance.strip()
        if character.wardrobe.strip():
            line += ". Wardrobe: " + character.wardrobe.strip()
        directions.append(line)
    payload = {"n": 1, "aspectRatio": board.ratio, "resolution": RESOLUTIONS[body.resolution], "duration": scene.duration}
    if scene.frame:
        frame = reference_url(store, scene.frame)
        if references:
            references.append(frame)
            directions.append(f"Use Image {len(references)} as the composition reference.")
        else:
            single_image = frame
    if single_image:
        payload["image"] = single_image
        if model["follows_input_aspect_ratio"]:
            payload.pop("aspectRatio")
    if references:
        payload["inputReferences"] = references
    return {**payload, "prompt": "\n\n".join(directions)}


@router.post(SCENE_PATH, status_code=202)
async def render_scene(value: BoardKey, scene_id: SceneKey, body: RenderRequest):
    store = Store()
    scope = value + ":" + scene_id
    job_key = scope + ":" + body.request_id
    request = body.model_dump(mode="json")
    existing = store.get_json("video-job", job_key)
    if existing:
        if existing["request"] != request:
            raise HTTPException(409, "This render identifier belongs to different settings.")
        return public_job(existing)
    saved = store.get_json("video-storyboard", value)
    if not saved or saved["revision"] != body.revision:
        raise HTTPException(409, "Save the current storyboard before rendering.")
    board = Storyboard.model_validate(saved["storyboard"])
    index = next((index for index, scene in enumerate(board.scenes) if scene.id == scene_id), None)
    if index is None:
        raise HTTPException(404, "Scene not found.")
    scene = board.scenes[index]
    if not scene.direction.strip():
        raise HTTPException(422, "Add scene directions before rendering.")
    if not gateway.connected():
        raise HTTPException(503, "Connect AI Gateway before rendering.")
    model = (await available_models(store)).get(body.model)
    if not model or body.resolution not in model["rates"] or board.ratio not in model["ratios"] or scene.duration not in model["durations"]:
        raise HTTPException(422, "This model does not support the selected duration, ratio or resolution.")
    cast = [character for character in board.characters if character.id in scene.cast]
    image_count = int(bool(scene.frame)) + sum(bool(character.reference) for character in cast)
    if image_count > 1 and not model["supports_multiple_images"]:
        raise HTTPException(422, "This model takes one starting image. Choose Wan for a scene with multiple references.")
    image_input_cost = Decimal(model["image_input_rate_usd"]) * image_count
    amount = Decimal(model["rates"][body.resolution]) * scene.duration + image_input_cost
    if amount != body.estimate_usd:
        raise HTTPException(409, "The video estimate changed. Refresh the renderer prices and try again.")
    with submission_lock(store, scope, body.request_id):
        existing = store.get_json("video-job", job_key)
        if existing:
            if existing["request"] != request:
                raise HTTPException(409, "This render identifier belongs to different settings.")
            return public_job(existing)
        latest_key = store.get_json("video-latest", scope)
        latest = store.get_json("video-job", latest_key) if latest_key else None
        if latest and latest["status"] in ACTIVE:
            if latest["request"] == request:
                return public_job(latest)
            raise HTTPException(409, "This scene already has a render that is still being checked.")
        if not store.allow_paid_request():
            raise HTTPException(429, "Your daily request limit has been reached. No render was started.")
        payload = scene_payload(store, board, index, body, model, cast)
        job = {"id": body.request_id, "request": request, "status": "starting", "model": body.model,
               "resolution": body.resolution, "ratio": board.ratio, "duration": scene.duration,
               "estimate_usd": str(amount), "image_input_cost_usd": str(image_input_cost), "created_at": time.time()}
        store.put_json("video-submit-payload", job_key, payload, expiry=RESUME_SECONDS)
        store.put_json("video-job", job_key, job)
        store.put_json("video-latest", scope, job_key)
        return await submit_saved_render(store, job_key, job, payload)


@router.post(SCENE_PATH + "/resume/{request_id}", status_code=202)
async def resume_submission(value: BoardKey, scene_id: SceneKey, request_id: SceneKey):
    store = Store()
    scope = value + ":" + scene_id
    job_key = scope + ":" + request_id
    with submission_lock(store, scope, request_id):
        job = store.get_json("video-job", job_key)
        if not job or store.get_json("video-latest", scope) != job_key:
            raise HTTPException(404, "This render is no longer current.")
        if job["status"] != "unconfirmed":
            return public_job(job)
        payload = store.get_json("video-submit-payload", job_key)
        if not payload or not public_job(job)["can_resume"]:
            raise HTTPException(409, "The original submission window has expired.")
        if not gateway.connected():
            raise HTTPException(503, "Connect AI Gateway before resuming.")
        return await submit_saved_render(store, job_key, job, payload, resuming=True)


def output_url(result):
    for video in result.get("videos") or ():
        if video.get("type") != "url" or video.get("mediaType") != "video/mp4":
            continue
        url = video.get("url", "")
        if not isinstance(url, str) or len(url) > 6000:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return url
    return None


@router.get(SCENE_PATH)
async def read_render(value: BoardKey, scene_id: SceneKey):
    store = Store()
    key = store.get_json("video-latest", value + ":" + scene_id)
    job = store.get_json("video-job", key) if key else None
    if not job:
        return {"render": None}
    if job["status"] == "starting" and time.time() - job.get("submission_started_at", job["created_at"]) >= 90:
        job.update(status="unconfirmed", error="The provider did not confirm submission. No duplicate render was sent.")
        store.put_json("video-job", key, job)
    if job["status"] == "pending" and store.call("SET", store.key("video-poll", key), "1", "NX", "EX", 8):
        try:
            result = await request_gateway("status", job["model"], {"operation": job["operation"]})
        except (GatewayRejected, TimeoutError, httpx.HTTPError, ValueError):
            return {"render": public_job(job), "warning": "The video status is temporarily unavailable. No new render was started."}
        if result.get("status") == "completed":
            url = output_url(result)
            if url:
                job.update(status="completed", video_url=url)
            else:
                job.update(status="failed", error="This renderer returned no hosted MP4 file.")
            estimate = job["estimate_usd"]
            cost_id = hashlib.sha256(key.encode()).hexdigest()
            store.put_json("chat-cost", cost_id, {"id": cost_id, "model": job["model"], "currency": "USD",
                           "scope": "model_only", "service_estimated_cost_usd": estimate})
            store.call("ZADD", store.key("chat-cost-history"), time.time(), cost_id)
        elif result.get("status") in {"error", "cancelled"}:
            job.update(status="failed", error="The provider did not finish this render.")
        if job["status"] != "pending":
            store.put_json("video-job", key, job)
    return {"render": public_job(job)}

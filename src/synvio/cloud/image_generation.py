"""Private image results, stored separately from conversation history."""
import base64
import binascii
import os
import time
import uuid
from decimal import Decimal
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, Field

from .images import validate_images
from .retail import service_amount
from .store import Store

router = APIRouter(prefix="/api")
MODEL = "gpt-image-2.5-flare"
MAX_IMAGE_BYTES = 1024 * 1024
FAILURE = "Image generation could not be confirmed. It was not retried automatically."


class ImageOptions(BaseModel):
    variants: int = Field(default=1, ge=1, le=3)
    size: Literal["1024x1024", "1024x1536", "1536x1024"] = "1024x1024"
    quality: Literal["low", "medium"] = "medium"
    mask: str | None = Field(default=None, max_length=1_400_000)
    save_to_project: bool = False


def image_usage(data):
    if not isinstance(data, dict) or not isinstance(data.get("input_tokens_details"), dict):
        return None
    details = data["input_tokens_details"]
    output_details = data.get("output_tokens_details")
    output = output_details.get("image_tokens") if isinstance(output_details, dict) else data.get("output_tokens")
    counts = (details.get("text_tokens"), details.get("image_tokens"), output)
    if not all(type(value) is int and value >= 0 for value in counts):
        return None
    # Standard Image API rates for Flare. Cached input rates apply to Responses.
    text, image, output = counts
    cost = Decimal(5 * text + 8 * image + 30 * output) / 1_000_000
    return {
        "model": MODEL, "currency": "USD", "scope": "model_only",
        "estimated_cost_usd": str(cost), "service_estimated_cost_usd": service_amount(cost),
    }


async def request_images(key, prompt, source_images, options):
    payload = {
        "model": MODEL, "prompt": prompt, "n": options.variants, "size": options.size,
        "quality": options.quality, "output_format": "jpeg", "output_compression": 65 if options.save_to_project else 80,
        "background": "opaque",
    }
    action = "edits" if source_images else "generations"
    if source_images:
        payload["images"] = [{"image_url": image} for image in source_images]
    if options.mask:
        payload["mask"] = {"image_url": options.mask}
    try:
        async with httpx.AsyncClient(timeout=150, follow_redirects=False) as client:
            response = await client.post("https://api.openai.com/v1/images/" + action,
                                         headers={"Authorization": "Bearer " + key}, json=payload)
        if response.status_code == 429:
            raise HTTPException(429, "The image provider reports a quota or rate limit. No automatic retry was made.")
        if response.status_code in {401, 403}:
            raise HTTPException(503, "This OpenAI project does not currently have access to the image model.")
        if response.status_code == 400:
            raise HTTPException(422, "The image provider rejected the request. Check the prompt or source image.")
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, FAILURE) from exc


async def generate(store, request_id, conversation_id, prompt, source_images, options):
    if options.mask:
        if len(source_images) != 1 or not options.mask.startswith("data:image/png;base64,"):
            raise HTTPException(422, "A PNG mask needs one source image.")
        try:
            validate_images([options.mask])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "Connect the OpenAI project before generating images.")
    if not store.allow_paid_request():
        raise HTTPException(429, "Your daily request limit has been reached. No model was called.")
    result = await request_images(key, prompt, source_images, options)
    cost = image_usage(result.get("usage"))
    if cost:
        store.put_json("chat-cost", request_id, {"id": request_id, "conversation_id": conversation_id, **cost})
        store.call("ZADD", store.key("chat-cost-history"), time.time(), request_id)
    pictures = []
    for image in result.get("data", [])[:options.variants]:
        encoded = image.get("b64_json")
        if not isinstance(encoded, str) or len(encoded) > 1_400_000:
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if not decoded.startswith(b"\xff\xd8\xff") or len(decoded) > MAX_IMAGE_BYTES:
            continue
        value = uuid.uuid4().hex
        store.put_json("generated-image", value, encoded)
        pictures.append({"id": value, "name": f"kaelia-image-{len(pictures) + 1}.jpg"})
    if not pictures:
        raise HTTPException(502, "No supported image was returned. The provider call was not repeated.")
    return pictures, cost


@router.get("/media/images/{value}")
def image_content(value: str = Path(pattern=r"^[a-f0-9]{32}$")):
    encoded = Store().get_json("generated-image", value)
    if not encoded:
        raise HTTPException(404, "This image does not exist or has expired.")
    return Response(base64.b64decode(encoded), media_type="image/jpeg")

"""Bounded inline reference images; never fetch user-supplied image URLs."""
import base64
import binascii
from typing import Annotated

from pydantic import AfterValidator, Field

VISION_TOKENS = {"gpt-4o-mini": 2833, "gpt-4o": 85, "gpt-4.1-mini": 12000, "gpt-4.1": 85, "gpt-6.1-sol": 32768, "gpt-6-astra": 310}


def validate_images(images):
    if len(images) > 2:
        raise ValueError("Attach at most two images.")
    for value in images:
        if not isinstance(value, str) or len(value) > 1_400_000:
            raise ValueError("Each image must be at most 1 MB.")
        header, separator, encoded = value.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64"}:
            raise ValueError("Use a PNG or JPEG image, not a remote URL.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid image encoding.") from exc
        valid = raw.startswith(b"\x89PNG\r\n\x1a\n") if "png" in header else raw.startswith(b"\xff\xd8\xff")
        if not valid or len(raw) > 1_048_576:
            raise ValueError("Invalid image format or image exceeds 1 MB.")
    return images


Images = Annotated[list[str], Field(max_length=2), AfterValidator(validate_images)]


def require_vision(model, images):
    if images and model not in VISION_TOKENS:
        raise ValueError("This provider adapter does not support image attachments yet. Select an OpenAI vision model.")

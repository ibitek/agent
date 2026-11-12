"""Single-owner sessions for the public deployment. Never stores API keys."""

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE = "__Host-synvio"
TTL = 8 * 60 * 60


def cookie_name() -> str:
    return "synvio-dev" if os.environ.get("SYNVIO_CLOUD_DEV") == "1" else COOKIE


def configured() -> bool:
    return len(os.environ.get("SYNVIO_PASSWORD", "")) >= 20 and len(os.environ.get("SYNVIO_SESSION_SECRET", "")) >= 32


def sign(value: str) -> str:
    return hmac.new(os.environ["SYNVIO_SESSION_SECRET"].encode(), value.encode(), hashlib.sha256).hexdigest()


def issue() -> str:
    value = f"{int(time.time())}.{secrets.token_hex(16)}"
    return f"{value}.{sign(value)}"


def valid(token: str | None) -> bool:
    if not configured() or not token or len(token) > 128:
        return False
    try:
        timestamp, nonce, signature = token.split(".")
        age = time.time() - int(timestamp)
        return 0 <= age <= TTL and len(nonce) == 32 and hmac.compare_digest(signature, sign(f"{timestamp}.{nonce}"))
    except (ValueError, TypeError):
        return False


def check_origin(request: Request) -> None:
    """Authenticated POSTs must come from the actual site, not an arbitrary page."""
    source = urlsplit(request.headers.get("origin", ""))
    expected = request.headers.get("host", "").lower()
    local = os.environ.get("SYNVIO_CLOUD_DEV") == "1"
    if source.netloc.lower() != expected or source.scheme not in ({"https", "http"} if local else {"https"}):
        raise HTTPException(403, "Odrzucono żądanie z innej strony.")


def require(request: Request) -> None:
    if not configured():
        raise HTTPException(503, "Administrator musi ustawić SYNVIO_PASSWORD i SYNVIO_SESSION_SECRET w Vercelu.")
    if not valid(request.cookies.get(cookie_name())):
        raise HTTPException(401, "Zaloguj się do SYNVIO.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)


def password_matches(value: str) -> bool:
    return configured() and hmac.compare_digest(value.encode(), os.environ["SYNVIO_PASSWORD"].encode())

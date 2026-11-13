"""Owner-only Supabase Auth; tokens live only in secure server cookies."""
import os

import httpx
from fastapi import HTTPException


def enabled():
    return os.environ.get("SYNVIO_AUTH_PROVIDER") == "supabase"


def configured():
    return bool(os.environ.get("SUPABASE_URL", "").startswith("https://") and os.environ.get("SUPABASE_PUBLISHABLE_KEY") and owners())


def owners():
    return {email.strip().lower() for email in os.environ.get("SYNVIO_ALLOWED_EMAILS", "").split(",") if email.strip()}


def request(method, path, *, token=None, payload=None):
    headers = {"apikey": os.environ["SUPABASE_PUBLISHABLE_KEY"]}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.request(method, os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/" + path, headers=headers, json=payload, timeout=10)
        if response.status_code >= 500 or response.status_code == 429:
            raise HTTPException(503, "Sign-in is temporarily unavailable. Please try again later.")
        if not response.is_success:
            return None
        return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Unable to connect to the sign-in service.") from None


def allowed(user):
    return bool(user and user.get("email_confirmed_at") and not user.get("is_anonymous") and user.get("email", "").lower() in owners())


def valid(token):
    if not configured() or not token or len(token) > 3800:
        return False
    return allowed(request("GET", "user", token=token))


def login(email, password):
    if email.strip().lower() not in owners():
        raise HTTPException(401, "Incorrect email or password, or access is not allowed.")
    data = request("POST", "token?grant_type=password", payload={"email": email.strip(), "password": password})
    if not data or not allowed(data.get("user")) or not data.get("access_token"):
        raise HTTPException(401, "Incorrect email or password, or access is not allowed.")
    return data["access_token"], min(int(data.get("expires_in", 3600)), 3600)

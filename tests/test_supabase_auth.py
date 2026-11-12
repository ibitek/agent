"""Authentication boundary tests; all Supabase requests are mocked."""
import httpx
import pytest
from fastapi.testclient import TestClient
from synvio.cloud import app as cloud


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SYNVIO_AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "test-public-key")
    monkeypatch.setenv("SYNVIO_ALLOWED_EMAILS", "owner@example.test")
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("SYNVIO_CLOUD_DEV", raising=False)
    def remote(method, url, **kwargs):
        user = {"email": "owner@example.test", "email_confirmed_at": "2026-01-01", "is_anonymous": False}
        if "token?" in url:
            if kwargs["json"]["password"] != "correct":
                return httpx.Response(400, json={})
            return httpx.Response(200, json={"access_token": "valid-token", "expires_in": 3600, "user": user})
        if url.endswith("/user"):
            if kwargs["headers"].get("Authorization") != "Bearer valid-token":
                return httpx.Response(401, json={})
            return httpx.Response(200, json=user)
        assert "logout?scope=local" in url
        return httpx.Response(204)
    monkeypatch.setattr(httpx, "request", remote)
    with TestClient(cloud.app, base_url="https://agents.example.test", headers={"Origin": "https://agents.example.test"}) as value:
        yield value


def test_owner_login_cookie_and_logout(client):
    assert client.get("/api/session").json()["auth_provider"] == "supabase"
    response = client.post("/api/login", json={"email": "owner@example.test", "password": "correct"})
    assert response.status_code == 200
    assert "access_token" not in response.text
    assert all(flag in response.headers["set-cookie"] for flag in ["HttpOnly", "Secure", "SameSite=strict"])
    assert client.get("/api/session").json()["authenticated"]
    assert client.post("/api/logout").status_code == 200
    assert not client.get("/api/session").json()["authenticated"]


def test_other_accounts_bad_password_and_cross_site_denied(client):
    assert client.post("/api/login", json={"email": "other@example.test", "password": "correct"}).status_code == 401
    assert client.post("/api/login", json={"email": "owner@example.test", "password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"email": "owner@example.test", "password": "correct"}, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get("/api/tasks").status_code == 401


def test_missing_allowlist_fails_closed(client, monkeypatch):
    monkeypatch.delenv("SYNVIO_ALLOWED_EMAILS")
    assert client.get("/api/session").json()["configured"] is False
    assert client.post("/api/login", json={"email": "owner@example.test", "password": "correct"}).status_code == 503


def test_forged_cookie_and_provider_outage_fail_closed(client, monkeypatch):
    client.cookies.set("__Host-synvio", "forged")
    assert client.get("/api/tasks").status_code == 401
    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "request", offline)
    assert client.get("/api/tasks").status_code == 503

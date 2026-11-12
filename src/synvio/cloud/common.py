"""Small, dependency-light settings shared by the public API and worker."""

import json
import os
import re
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import HTTPException

MODELS = {"openai:low": "gpt-4o-mini", "openai:medium": "gpt-4o", "openai:high": "gpt-4o"}
PROJECT = "project"
MAX_FILE_BYTES = 256 * 1024
MAX_PROJECT_BYTES = 2 * 1024 * 1024
MAX_PROJECT_FILES = 100
JOB_SECONDS = 220


def greeting(text: str) -> bool:
    value = " ".join(re.sub(r"[^\w\s]", "", text.casefold()).split())
    return value in {"siema", "siemka", "siemanko", "hej", "hejka", "hejo", "cześć", "czesc", "witam", "dzień dobry", "dzien dobry", "dobry wieczór", "dobry wieczor", "elo", "yo", "hi", "hello", "hey", "how are you", "co tam", "co słychać", "co slychac", "good morning", "good afternoon", "good evening"}


def local_reply() -> dict:
    return {"mode": "chat", "content": "Cześć! W czym mogę pomóc? Rozmowa nie zmienia plików. Do wykonania zmian wybierz Zadanie.", "model": None, "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "estimated_cost_usd": 0.0, "local_response": True}


def resolve_model(model_id: str) -> str:
    if model_id not in MODELS:
        raise HTTPException(422, "Nieznany model.")
    # The expensive model needs an explicit server-side opt-in as well.
    if model_id != "openai:low" and os.environ.get("SYNVIO_ALLOW_EXPENSIVE_MODELS") != "1":
        raise HTTPException(403, "Ten model jest wyłączony w ustawieniach kosztów. Wybierz gpt-4o-mini.")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise HTTPException(503, "Brak OPENAI_API_KEY w ustawieniach serwera Vercel.")
    return MODELS[model_id]


def model_catalog() -> list[dict]:
    ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    result = []
    for mode, model in MODELS.items():
        affordable = mode == "openai:low" or os.environ.get("SYNVIO_ALLOW_EXPENSIVE_MODELS") == "1"
        result.append({"id": mode, "provider": "openai", "engine": "cloud", "model": model, "reasoning_mode": mode.split(":")[1], "label": model, "available": ready and affordable, "unavailable_reason": None if ready and affordable else "Brak klucza API." if not ready else "Model wyłączony w ustawieniach kosztów."})
    return result


def limits() -> dict:
    return {"steps_per_attempt": 10, "tool_calls_per_task": 8, "attempts": 1, "model_test_runs": 2, "test_timeout_seconds": 30, "cancellation": "queued_tasks_only", "execution_backend": "vercel_sandbox"}


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or "\x00" in value or PureWindowsPath(value).drive:
        raise ValueError("Niepoprawna ścieżka pliku.")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"..", "."} or part.startswith(".") for part in value.split("/")):
        raise ValueError("Plik musi być wewnątrz projektu; ukryte pliki są niedozwolone.")
    return str(path)


def validate_files(files: dict[str, str]) -> dict[str, str]:
    if len(files) > MAX_PROJECT_FILES:
        raise ValueError("Projekt ma za dużo plików (maks. 100).")
    result = {}
    for name, content in files.items():
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError("Plik przekracza limit 256 KiB.")
        result[safe_path(name)] = content
    if len(json.dumps(result).encode()) > MAX_PROJECT_BYTES:
        raise ValueError("Projekt przekracza limit 2 MiB.")
    return result


def public_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for name, value in os.environ.items():
        if value and ("TOKEN" in name or "SECRET" in name or "PASSWORD" in name or "API_KEY" in name):
            message = message.replace(value, "[redacted]")
    return message[:2000]


def redact_data(value):
    secrets = [secret for name, secret in os.environ.items() if secret and any(part in name for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))]

    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, list):
            return [clean(value) for value in item]
        if isinstance(item, dict):
            return {key: clean(value) for key, value in item.items()}
        return item

    return clean(json.loads(json.dumps(value, ensure_ascii=False, default=str)))


def estimate(model: str, usage: dict) -> float | None:
    # Prices come from the same pinned OpenJarvis as the local report.
    try:
        from openjarvis.engine.cloud import estimate_cost
        return float(estimate_cost(model, int(usage["prompt_tokens"]), int(usage["completion_tokens"])))
    except (ImportError, KeyError, TypeError, ValueError):
        return None

"""Small, dependency-light settings shared by the public API and worker."""

import json
import os
import re
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import HTTPException

MODELS = {"openai:low": "gpt-4o-mini", "openai:medium": "gpt-4o", "openai:high": "gpt-4o"}
MODELS.update({"openai:gpt-4.1-mini": "gpt-4.1-mini", "openai:gpt-4.1": "gpt-4.1"})
MODELS.update({'openai:gpt-6.1-sol':'gpt-6.1-sol','openai:gpt-6-astra':'gpt-6-astra',
               'codex-browser:gpt-6.1-sol':'gpt-6.1-sol','codex:gpt-6.1-sol':'gpt-6.1-sol','codex:gpt-6-astra':'gpt-6-astra'})
PROJECT = "project"
MAX_FILE_BYTES = 256 * 1024
MAX_PROJECT_BYTES = 2 * 1024 * 1024
MAX_PROJECT_FILES = 100
JOB_SECONDS = 220


def greeting(text: str) -> bool:
    value = " ".join(re.sub(r"[^\w\s]", "", text.casefold()).split())
    if re.fullmatch(r"(?:yo+|he+j+|he+y+|siema+)", value):
        return True
    return value in {"siema", "siemka", "siemanko", "hej", "hejka", "hejo", "cześć", "czesc", "witam", "dzień dobry", "dzien dobry", "dobry wieczór", "dobry wieczor", "elo", "yo", "hi", "hello", "hey", "how are you", "co tam", "co słychać", "co slychac", "good morning", "good afternoon", "good evening"}


def local_reply() -> dict:
    return {"mode": "chat", "content": "Hi! What would you like to explore or build today?", "model": None, "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "estimated_cost_usd": 0.0, "local_response": True}


def resolve_model(model_id: str) -> str:
    if model_id.startswith('gateway:'):
        from .gateway import SUPPORTED, connected
        name = model_id.removeprefix('gateway:')
        if name not in SUPPORTED or not connected():
            raise HTTPException(403, 'Connect AI Gateway before selecting this provider.')
        return name
    if model_id not in MODELS:
        raise HTTPException(422, "Unknown model.")
    # The expensive model needs an explicit server-side opt-in as well.
    if model_id != "openai:low" and os.environ.get("SYNVIO_ALLOW_EXPENSIVE_MODELS") != "1":
        raise HTTPException(403, "This model is disabled in cost settings. Select gpt-4o-mini.")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise HTTPException(503, "OPENAI_API_KEY is missing from the server settings.")
    return MODELS[model_id]


def model_catalog() -> list[dict]:
    from .metering import RATES
    from .retail import service_amount
    ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    result = []
    try:
        from .store import Store
        access = Store().get_json('codex-access','project') or {}
    except Exception:
        access = {}
    for mode, model in MODELS.items():
        if mode == 'openai:high':
            continue  # Retain old IDs for saved runs without duplicate choices.
        affordable = mode == "openai:low" or os.environ.get("SYNVIO_ALLOW_EXPENSIVE_MODELS") == "1"
        result.append({"id": mode, "provider": "openai", "engine": "cloud", "model": model, "reasoning_mode": mode.split(":")[1], "label": ("Codex + Browser · "+model) if mode.startswith("codex-browser:") else ("Codex · "+model) if mode.startswith(("codex:","codex-browser:")) else model, "available": ready and affordable, "unavailable_reason": None if ready and affordable else "The model API key is not configured." if not ready else "Model disabled in cost settings."})
        if mode.startswith(('codex:','codex-browser:')):
            result[-1]['engine']='codex'
            result[-1]['available']=ready and affordable and bool(access.get('available'))
            if not result[-1]['available']:
                result[-1]['unavailable_reason']=access.get('reason') or 'Use Check Codex access to verify this account.'
        elif model in {'gpt-6.1-sol','gpt-6-astra'}:
            result[-1]['engine']='responses'
        checked=(access.get('models') or {}).get(model)
        if checked and not checked['available']:
            result[-1]['available']=False
            result[-1]['unavailable_reason']='This model is not available to the server API account.'
        rate = [service_amount(value) for value in RATES[model]]
        result[-1].update(reasoning_mode=reasoning_for(mode), pricing={"input_per_million": str(rate[0]), "cached_input_per_million": str(rate[1]), "output_per_million": str(rate[2]), "currency": "USD"}, capabilities={"images": not mode.startswith("gateway:"), "workspace":mode.startswith(("codex:","codex-browser:"))}, description={"gpt-6.1-sol":"Codex workspace · reasoning, terminal and files" if mode.startswith(("codex:","codex-browser:")) else "Advanced coding · Responses API", "gpt-6-astra":"Codex workspace · highest capability" if mode.startswith(("codex:","codex-browser:")) else "Advanced reasoning · Responses API", "gpt-4o-mini": "Everyday · lowest cost", "gpt-4.1-mini": "Balanced · coding and instructions", "gpt-4.1": "Advanced · complex coding", "gpt-4o": "General purpose"}[model])
    from .gateway import CATALOG, SUPPORTED, connected
    from decimal import Decimal
    for item in CATALOG['models']:
        price = item['pricing']
        supported = item['id'] in SUPPORTED
        result.append({'id':'gateway:'+item['id'],'provider':item['owned_by'],'engine':'gateway','model':item['id'],'label':item['name'],
            'available':supported and connected(), 'unavailable_reason':None if supported and connected() else 'Provider adapter pending rate verification.' if not supported else 'Connect AI Gateway to use this model.',
            'description':'Via AI Gateway', 'pricing':{'input_per_million':service_amount(Decimal(price['input'])*1_000_000),'output_per_million':service_amount(Decimal(price['output'])*1_000_000),'cached_input_per_million':service_amount(Decimal(price.get('input_cache_read',price['input']))*1_000_000),'currency':'USD'}, 'pricing_verified_at':CATALOG['verified_at']})
    return result


def reasoning_for(model_id):
    return model_id.split(':')[1] if model_id in {'openai:low', 'openai:medium', 'openai:high'} else 'medium'


def limits() -> dict:
    return {"steps_per_attempt": 40, "tool_calls_per_task": 32, "attempts": 1, "model_test_runs": 2, "test_timeout_seconds": 30, "cancellation": "queued_tasks_only", "execution_backend": "vercel_sandbox"}


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or "\x00" in value or PureWindowsPath(value).drive:
        raise ValueError("Invalid file path.")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"..", "."} or part.startswith(".") for part in value.split("/")):
        raise ValueError("Files must stay inside the project; hidden files are not allowed.")
    return str(path)


def validate_files(files: dict[str, str]) -> dict[str, str]:
    if len(files) > MAX_PROJECT_FILES:
        raise ValueError("The project exceeds the 100-file limit.")
    result = {}
    for name, content in files.items():
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError("The file exceeds the 256 KiB limit.")
        result[safe_path(name)] = content
    for name in result:
        if any(str(parent) in result for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("A project path cannot be both a file and a folder.")
    if len(json.dumps(result).encode()) > MAX_PROJECT_BYTES:
        raise ValueError("The project exceeds the 2 MiB limit.")
    return result


def public_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for name, value in os.environ.items():
        if value and ("TOKEN" in name or "SECRET" in name or "PASSWORD" in name or "API_KEY" in name or "PRIVATE_KEY" in name):
            message = message.replace(value, "[redacted]")
    return message[:2000]


def redact_data(value):
    secrets = [secret for name, secret in os.environ.items() if secret and any(part in name for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY"))]

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

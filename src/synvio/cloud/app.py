"""Public Vercel API. No local workspaces, SQLite or host test execution."""

import asyncio
import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import auth, supabase_auth
from .metering import MeteredEngine, BudgetExceeded
from .common import JOB_SECONDS, PROJECT, estimate, greeting, limits, local_reply, model_catalog, public_error, resolve_model, safe_path
from .store import StorageUnavailable, Store, ready as storage_ready
from .setup import prepare as prepare_sandbox, saved_snapshot

# Vercel's application directory is read-only. SDK state is ephemeral and separate
# from every project; it is never exported or placed in a test microVM.
os.environ.setdefault("OPENJARVIS_HOME", str(Path(tempfile.gettempdir()) / "synvio-sdk"))

app = FastAPI(title="SYNVIO Agents", docs_url=None, redoc_url=None, openapi_url=None)


class Login(BaseModel):
    email: str = Field(default="", max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class Chat(BaseModel):
    model_budget_usd: float = Field(default=0.01, gt=0, le=1, allow_inf_nan=False)
    message: str = Field(min_length=1, max_length=4000)
    model_id: str = "openai:low"
    history: list[Message] = Field(default_factory=list, max_length=12)


class Task(BaseModel):
    model_budget_usd: float = Field(default=0.10, gt=0, le=1, allow_inf_nan=False)
    task: str = Field(min_length=1, max_length=10000)
    workspace: str = PROJECT
    model_id: str = "openai:low"


def get_store():
    return Store()


@app.middleware("http")
async def session_guard(request, call_next):
    try:
        if request.url.path not in {"/api/session", "/api/login"}:
            auth.require(request)
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(StorageUnavailable)
async def storage_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/api/session")
def session(request: Request):
    return {"cloud": True, "configured": auth.configured(), "auth_provider": "supabase" if supabase_auth.enabled() else "password", "authenticated": auth.valid(request.cookies.get(auth.cookie_name()))}


@app.post("/api/login")
def login(request: Request, body: Login):
    auth.check_origin(request)
    if not auth.configured():
        raise HTTPException(503, "Logowanie wymaga konfiguracji administratora.")
    if storage_ready():
        identity = hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
        if not get_store().meter(f"login:{identity}", 10, 300):
            raise HTTPException(429, "Za dużo prób logowania. Poczekaj pięć minut.")
    if supabase_auth.enabled():
        token, ttl = supabase_auth.login(body.email, body.password)
    elif not auth.password_matches(body.password):
        raise HTTPException(401, "Niepoprawne hasło.")
    else:
        token, ttl = auth.issue(), auth.TTL
    response = JSONResponse({"cloud": True, "authenticated": True, "configured": True})
    response.set_cookie(auth.cookie_name(), token, httponly=True, secure=os.environ.get("SYNVIO_CLOUD_DEV") != "1", samesite="strict", max_age=ttl, path="/")
    return response


@app.post("/api/logout")
def logout(request: Request):
    if supabase_auth.enabled():
        supabase_auth.request("POST", "logout?scope=local", token=request.cookies.get(auth.cookie_name()))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(auth.cookie_name(), path="/")
    return response


def tasks_ready():
    return storage_ready() and os.environ.get("SYNVIO_ENABLE_TASKS") == "1" and bool(saved_snapshot(get_store()))


@app.get("/api/health")
def health():
    enabled = tasks_ready()
    can_prepare = storage_ready() and os.environ.get("SYNVIO_ENABLE_TASKS") == "1" and not enabled
    return {"status": "ok", "host": "vercel", "execution_backend": "vercel_sandbox", "cancellation": "queued_tasks_only", "tasks_enabled": enabled, "can_prepare_tasks": can_prepare, "tasks_unavailable_reason": None if enabled else "Zadania wymagają podłączenia Redis, przygotowania środowiska testów i ustawienia SYNVIO_ENABLE_TASKS=1 w Vercelu."}


@app.post("/api/setup/sandbox")
async def setup_sandbox():
    if not storage_ready() or os.environ.get("SYNVIO_ENABLE_TASKS") != "1":
        raise HTTPException(503, "Podłącz Redis i ustaw SYNVIO_ENABLE_TASKS=1, aby przygotować zadania.")
    try:
        return await prepare_sandbox(get_store())
    except Exception as exc:
        raise HTTPException(503, public_error(exc)) from exc


@app.get("/api/models")
def models():
    return {"items": model_catalog()}


@app.get("/api/limits")
def api_limits():
    return limits()


@app.get("/api/workspaces")
def workspaces():
    return {"items": [PROJECT] if tasks_ready() else []}


@app.post("/api/chat")
async def chat(body: Chat):
    if greeting(body.message):
        return local_reply()
    model = resolve_model(body.model_id)
    store = get_store()
    if not store.allow_paid_request():
        raise HTTPException(429, "Osiągnięto dzienny limit wiadomości i zadań. Nie wywołano modelu.")

    def generate():
        from openjarvis.core.types import Message as EngineMessage, Role
        from synvio.config import SYNVIOConfig
        from synvio.runtime import engine_for

        system = "Jesteś SYNVIO w trybie rozmowy. Odpowiadaj w języku użytkownika. Nie masz narzędzi, dostępu do plików ani testów. Nie twierdź, że wykonałeś zmiany; do wykonania zmian użytkownik musi wybrać Zadanie."
        messages = [EngineMessage(role=Role.SYSTEM, content=system)]
        messages.extend(EngineMessage(role=Role(item.role), content=item.content) for item in body.history)
        messages.append(EngineMessage(role=Role.USER, content=body.message))
        config = SYNVIOConfig(provider="openai", engine="cloud", model_override=model)
        cost_id = uuid.uuid4().hex
        def persist(event):
            store.put_json("chat-cost", cost_id, {"id": cost_id, "created_at": time.time(), **event})
            store.call("ZADD", store.key("chat-cost-history"), time.time(), cost_id)
        with engine_for(config) as engine:
            metered = MeteredEngine(engine, model, body.model_budget_usd, persist)
            result = metered.generate(messages, model=model, temperature=0.4, max_tokens=512)
            return {**result, "cost_record": {"id": cost_id, **metered.summary()}}

    try:
        result = await asyncio.to_thread(generate)
    except BudgetExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, public_error(exc)) from exc
    usage = result.get("usage") or {}
    return {"mode": "chat", "content": str(result.get("content") or ""), "model": model, "tokens": usage, "estimated_cost_usd": float(result["cost_record"]["estimated_cost_usd"]), "cost_record": result["cost_record"], "local_response": False}


def task_id(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise HTTPException(404, "Zadanie nie istnieje.")
    return value


@app.get("/api/costs")
def cost_history():
    if not storage_ready():
        return {"currency": "USD", "scope": "model_only", "chat": [], "tasks": []}
    store = get_store()
    ids = store.call("ZREVRANGE", store.key("chat-cost-history"), 0, 49) or []
    chats = [record for value in ids if (record := store.get_json("chat-cost", value))]
    tasks = [{"id": task["id"], "cost_record": task["cost_record"]} for task in store.history(50) if task.get("cost_record")]
    return {"currency": "USD", "scope": "model_only", "chat": chats, "tasks": tasks}


def task_row(store, value):
    item = store.get(task_id(value))
    if not item:
        raise HTTPException(404, "Zadanie nie istnieje.")
    if item["status"] in {"queued", "running"} and time.time() > item["deadline"]:
        if store.transition(value, item["status"], "failed"):
            store.fields(value, error="Przekroczono czas zadania lub przerwano żądanie. Nie wznowiono płatnej pracy automatycznie.")
            store.event(value, {"event": "task_error", "error": "Przekroczono czas zadania."})
            store.release(value)
        item = store.get(value)
    return {**item, "cancellation_supported": item["status"] == "queued", "download_available": bool(item.get("report"))}


@app.post("/api/tasks", status_code=202)
def create_task(body: Task):
    if greeting(body.task):
        raise HTTPException(422, "To powitanie. Wybierz Rozmowa — odpowiedź będzie bezpłatna.")
    if body.workspace != PROJECT:
        raise HTTPException(403, "Niepoprawny projekt. Serwer nie udostępnia lokalnych folderów.")
    if not tasks_ready():
        raise HTTPException(503, health()["tasks_unavailable_reason"])
    model = resolve_model(body.model_id)
    store = get_store()
    now = time.time()
    value = uuid.uuid4().hex
    item = {"id": value, "model_budget_usd": body.model_budget_usd, "task": body.task, "workspace": PROJECT, "model": model, "model_id": body.model_id, "status": "queued", "created_at": now, "updated_at": now, "deadline": now + JOB_SECONDS + 60, "report": None, "error": None}
    if not store.create(item):
        raise HTTPException(429, "Projekt ma już aktywne zadanie. Poczekaj na jego zakończenie.")
    store.event(value, {"event": "task_queued"})
    return {"id": value, "status": "queued", "cancellation": "queued_tasks_only", "run_required": True}


async def run_worker(store, value, oidc_token=None):
    # Only the known core worker runs locally. Model-authored code runs in remote microVMs.
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "OPENAI_API_KEY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN", "SYNVIO_SANDBOX_SNAPSHOT", "VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID", "VERCEL_REGION"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    # Vercel can add dependency directories to sys.path rather than the system
    # interpreter's site-packages; the known worker needs the same trusted paths.
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(Path(__file__).resolve().parents[2]), *(entry for entry in sys.path if entry)]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SYNVIO_SANDBOX_SNAPSHOT"] = saved_snapshot(store)
    if oidc_token:
        env["VERCEL_OIDC_TOKEN"] = oidc_token
    elif os.environ.get("VERCEL_OIDC_TOKEN"):
        env["VERCEL_OIDC_TOKEN"] = os.environ["VERCEL_OIDC_TOKEN"]

    def execute():
        result = subprocess.run([sys.executable, "-m", "synvio.cloud.worker", value], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=JOB_SECONDS, shell=False)
        return result.returncode

    try:
        code = await asyncio.to_thread(execute)
        final = store.get(value)
        if final and final["status"] == "running":
            raise RuntimeError(f"Proces zadania zakończył się bez raportu (kod {code}).")
    except Exception as exc:
        if store.transition(value, "running", "failed"):
            store.fields(value, error=public_error(exc))
            store.event(value, {"event": "task_error", "error": public_error(exc)})
    finally:
        store.release(value)


@app.post("/api/tasks/{value}/run")
async def execute_task(value: str, request: Request):
    store = get_store()
    item = task_row(store, value)
    if item["status"] != "queued":
        return {"id": value, "status": item["status"]}
    if not tasks_ready():
        raise HTTPException(503, "Środowisko zadań jest wyłączone.")
    # Atomic claim: a browser retry must not launch a second model loop.
    claimed = store.call("EVAL", "if redis.call('GET',KEYS[2])==ARGV[1] and redis.call('HGET',KEYS[1],'status')=='\"queued\"' then redis.call('HSET',KEYS[1],'status','\"running\"');redis.call('EXPIRE',KEYS[2],ARGV[2]);return 1 else return 0 end", 2, store.key("task", value), store.key("project-lock"), value, JOB_SECONDS + 30)
    if not claimed:
        return {"id": value, "status": task_row(store, value)["status"]}
    if not store.allow_paid_request():
        store.fields(value, status="failed", error="Dzienny limit wiadomości i zadań został osiągnięty.")
        store.event(value, {"event": "task_error", "error": "Osiągnięto dzienny limit."})
        store.release(value)
        raise HTTPException(429, "Osiągnięto dzienny limit. Nie wywołano modelu.")
    store.fields(value, updated_at=time.time(), deadline=time.time() + JOB_SECONDS + 20)
    await run_worker(store, value, request.headers.get("x-vercel-oidc-token"))
    return {"id": value, "status": task_row(store, value)["status"]}


@app.get("/api/tasks")
def history(limit: int = Query(default=50, ge=1, le=200)):
    if not storage_ready():
        return {"items": []}
    store = get_store()
    return {"items": [task_row(store, item["id"]) for item in store.history(limit)]}


@app.get("/api/tasks/{value}")
def detail(value: str):
    store = get_store()
    return task_row(store, value)


@app.get("/api/tasks/{value}/events")
def events(value: str, after: int = Query(default=0, ge=0)):
    store = get_store()
    task_row(store, value)
    return {"items": store.events(value, after)}


@app.post("/api/tasks/{value}/cancel")
def cancel(value: str):
    store = get_store()
    task_row(store, value)
    if not store.transition(value, "queued", "cancelled"):
        raise HTTPException(409, "Można anulować wyłącznie zadanie oczekujące.")
    store.event(value, {"event": "task_cancelled"})
    store.release(value)
    return {"id": value, "status": "cancelled"}


def report_files(item):
    report = item.get("report") or {}
    result = {name: "modified" for name in report.get("files_modified", [])}
    result.update({name: "created" for name in report.get("files_created", [])})
    return result


@app.get("/api/tasks/{value}/files")
def files(value: str):
    store = get_store()
    item = task_row(store, value)
    contents = store.get_json("files", value, {})
    return {"items": [{"path": name, "action": action, "exists": name in contents, "size_bytes": len(contents[name].encode()) if name in contents else None} for name, action in report_files(item).items()]}


def file_data(store, value, path):
    item = task_row(store, value)
    try:
        name = safe_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    action = report_files(item).get(name)
    content = store.get_json("files", value, {})
    if not action or name not in content:
        raise HTTPException(404, "Plik nie występuje w raporcie zmian zadania.")
    return name, action, content[name]


@app.get("/api/tasks/{value}/files/content")
def file_content(value: str, path: str = Query(min_length=1, max_length=200)):
    name, action, content = file_data(get_store(), value, path)
    return {"path": name, "action": action, "content": content, "read_only": True}


@app.get("/api/tasks/{value}/files/diff")
def file_diff(value: str, path: str = Query(min_length=1, max_length=200)):
    store = get_store()
    name, action, content = file_data(store, value, path)
    baseline = store.get_json("baseline", value, {})
    if name not in baseline and action != "created":
        return {"path": name, "available": False, "reason": "Brak zapisanego stanu początkowego.", "diff": None}
    before = baseline.get(name, "")
    return {"path": name, "available": True, "diff": "".join(difflib.unified_diff(before.splitlines(True), content.splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}")), "read_only": True}


@app.get("/api/tasks/{value}/download")
def download(value: str):
    store = get_store()
    task_row(store, value)
    content = store.get_json("files", value)
    if content is None:
        raise HTTPException(404, "Zadanie nie ma jeszcze zapisanych plików.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in content.items():
            archive.writestr(safe_path(name), text)
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="synvio-{value}.zip"'})

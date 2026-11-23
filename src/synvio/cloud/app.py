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
from .followups import context_for
from .preview import build_preview
from .editor import router as editor_router
from .assistant import router as assistant_router
from .browser_sessions import router as browser_router
from .local_browser import router as local_browser_router
from .recovery import router as recovery_router
from .project_files import router as project_files_router
from .github import router as github_router
from .image_generation import router as image_generation_router
from .projects import router as projects_router
from .video_studio import router as video_studio_router
from .video_render import router as video_render_router
from .dev_environment import router as developer_environment_router
from .common import JOB_SECONDS, PROJECT, estimate, greeting, limits, local_reply, model_catalog, public_error, resolve_model, safe_path
from .store import StorageUnavailable, Store, ready as storage_ready
from .setup import prepare as prepare_sandbox, saved_snapshot

# Vercel's application directory is read-only. SDK state is ephemeral and separate
# from every project; it is never exported or placed in a test microVM.
os.environ.setdefault("OPENJARVIS_HOME", str(Path(tempfile.gettempdir()) / "synvio-sdk"))

app = FastAPI(title="Kaelia API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(editor_router)
app.include_router(developer_environment_router)
app.include_router(assistant_router)
app.include_router(browser_router)
app.include_router(local_browser_router)
app.include_router(recovery_router)
app.include_router(project_files_router)
app.include_router(github_router)
app.include_router(image_generation_router)
app.include_router(projects_router)
app.include_router(video_studio_router)
app.include_router(video_render_router)


class Login(BaseModel):
    email: str = Field(default="", max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class Chat(BaseModel):
    model_budget_usd: float | None = Field(default=0.20 / 1.30, gt=0, allow_inf_nan=False)
    message: str = Field(min_length=1, max_length=4000)
    model_id: str = "openai:low"
    history: list[Message] = Field(default_factory=list, max_length=12)


from .images import Images, require_vision


class Task(BaseModel):
    images: Images = Field(default_factory=list)
    project_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    runtime: Literal["python", "web", "app", "source"] = "python"
    operation: Literal["edit", "verify"] = "edit"
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    model_budget_usd: float | None = Field(default=0.20 / 1.30, gt=0, allow_inf_nan=False)
    task: str = Field(min_length=1, max_length=10000)
    workspace: str = PROJECT
    model_id: str = "openai:low"


def get_store():
    return Store()


@app.middleware("http")
async def session_guard(request, call_next):
    try:
        proxy_request = request.method == "POST" and re.fullmatch(r"/api/codex-proxy/[a-f0-9]{32}/responses", request.url.path)
        render_reference = request.method in {"GET", "HEAD"} and re.fullmatch(r"/api/video-input/[a-f0-9]{48}", request.url.path)
        if not proxy_request and not render_reference and request.url.path not in {"/api/session", "/api/login", "/api/editor/config"}:
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
        raise HTTPException(503, "Sign-in requires owner configuration.")
    if storage_ready():
        identity = hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
        if not get_store().meter(f"login:{identity}", 10, 300):
            raise HTTPException(429, "Too many sign-in attempts. Please wait five minutes.")
    if supabase_auth.enabled():
        token, ttl = supabase_auth.login(body.email, body.password)
    elif not auth.password_matches(body.password):
        raise HTTPException(401, "Incorrect password.")
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


def tasks_ready(runtime="python"):
    enabled = storage_ready() and os.environ.get("SYNVIO_ENABLE_TASKS") == "1"
    return enabled and (runtime in {"web", "app", "source"} or bool(saved_snapshot(get_store())))


@app.get("/api/health")
def health():
    enabled = tasks_ready()
    can_prepare = storage_ready() and os.environ.get("SYNVIO_ENABLE_TASKS") == "1" and not enabled
    reason = None if enabled else "Prepare the execution environment using the button below."
    if not storage_ready():
        reason = "Connect Redis storage in the project settings."
    elif os.environ.get("SYNVIO_ENABLE_TASKS") != "1":
        reason = "Project execution is disabled in the server settings."
    return {"status": "ok", "host": "vercel", "execution_backend": "vercel_sandbox", "cancellation": "queued_tasks_only", "tasks_enabled": enabled, "can_prepare_tasks": can_prepare, "tasks_unavailable_reason": reason}


@app.post("/api/setup/sandbox")
async def setup_sandbox():
    if not storage_ready() or os.environ.get("SYNVIO_ENABLE_TASKS") != "1":
        raise HTTPException(503, "Connect Redis and enable SYNVIO_ENABLE_TASKS=1 to prepare the execution environment.")
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
        raise HTTPException(429, "The daily request limit has been reached. No model was called.")

    def generate():
        from openjarvis.core.types import Message as EngineMessage, Role
        from synvio.config import SYNVIOConfig
        from .gateway import engine_for

        from .persona import PERSONA

        system = PERSONA + "\nThis legacy endpoint has no tools; never claim file changes or test results."
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
        raise HTTPException(404, "Run not found.")
    return value


@app.get("/api/costs")
def cost_history():
    if not storage_ready():
        return {"currency": "USD", "scope": "model_only", "chat": [], "tasks": [], "editor": []}
    store = get_store()
    ids = store.call("ZREVRANGE", store.key("chat-cost-history"), 0, 49) or []
    chats = [record for value in ids if (record := store.get_json("chat-cost", value))]
    tasks = [{"id": task["id"], "cost_record": task["cost_record"]} for task in store.history(50) if task.get("cost_record")]
    editor_ids = store.call("ZREVRANGE", store.key("editor-history"), 0, 49) or []
    editor = [{"id": item["id"], "workspace_name": item["workspace_name"], "status": item["status"], "cost_record": item.get("cost_record")} for value in editor_ids if (item := store.get_json("editor", value))]
    return {"currency": "USD", "scope": "model_only", "chat": chats, "tasks": tasks, "editor": editor}


@app.get('/api/wallet')
def wallet():
    from decimal import Decimal
    from .retail import service_amount, sandbox_usage, COMPUTE_PER_MINUTE
    usage = cost_history()
    records = [*usage.get('chat',[]), *[item.get('cost_record') or {} for item in usage.get('tasks',[])], *[item.get('cost_record') or {} for item in usage.get('editor',[])]]
    model_total = sum((Decimal(record.get('service_estimated_cost_usd') or service_amount(record.get('estimated_cost_usd','0'))) for record in records), Decimal(0))
    return {'currency':'USD','payments_enabled':False,'balance_usd':None,'model_usage_estimate_usd':str(model_total),
            'compute_price_per_minute_usd':service_amount(COMPUTE_PER_MINUTE),'compute_billing_unit':'second',
            'compute_usage_estimate_usd':str(sum((Decimal(sandbox_usage(task, (get_store().get_json('application-build',task['id']) or {}).get('duration_seconds',0))['service_estimated_cost_usd']) for task in get_store().history(50)),Decimal(0))),
            'bonus_max_percent':20,'bonus_tiers':[{'deposit_usd':amount,'bonus_percent':bonus} for amount,bonus in [('5',2),('12.50',5),('25',10),('50',15),('100',20)]], 'usage_window':'Latest 50 records per source and latest build per run. Estimates at current service rates, not an invoice.'}


def task_row(store, value, item=None):
    item = item if item is not None else store.get(task_id(value))
    if not item:
        raise HTTPException(404, "Run not found.")
    if item["status"] in {"queued", "running"} and time.time() > item["deadline"]:
        if store.transition(value, item["status"], "paused"):
            store.fields(value, error="The run timed out or its request was interrupted. Paid work was not retried automatically.")
            store.event(value, {"event": "task_error", "error": "The run timed out."})
            store.release(value)
        item = store.get(value)
    cost=item.get('cost_record') or {}
    if (item.get('status')=='paused' and "Unknown parameter: 'spend_control'" in str(item.get('error',''))
            and cost.get('rate_version')=='openai-managed-codex-2026-11-14'
            and cost.get('estimated_cost_usd')=='0.000000000'):
        # A known request-validation rejection started no inference. It is not
        # an ambiguous network failure and must not ask for unknown-cost consent.
        cost={**cost,'unresolved_reservation_usd':'0.000000000','service_reserved_usd':'0.000000000',
              'calls':[{**entry,'state':'rejected','provider_status':400} for entry in cost.get('calls',[])]}
        store.fields(value,cost_record=cost)
        item={**item,'cost_record':cost}
    from .retail import sandbox_usage
    public = {key: value for key, value in item.items() if key not in {"images", "codex_proxy_digest", "resume_checkpoint", "compute_codex", "compute_build", "compute_tests"}}
    return {**public, "build_pending": bool(item.get("build_pending") and time.time() < item["deadline"]), "compute_usage": sandbox_usage(item), "image_count": len(item.get("images", [])), "cancellation_supported": item["status"] == "queued", "download_available": bool(item.get("report"))}


@app.post("/api/tasks", status_code=202)
def create_task(body: Task):
    if greeting(body.task):
        raise HTTPException(422, "This is a greeting. Send it in the conversation for a free reply.")
    if body.workspace != PROJECT:
        raise HTTPException(403, "Invalid project. This server does not expose local folders.")
    if not tasks_ready(body.runtime):
        raise HTTPException(503, health()["tasks_unavailable_reason"])
    model = resolve_model(body.model_id)
    try:
        require_vision(model, body.images)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.images and body.runtime not in {"web", "app", "source"}:
        raise HTTPException(422, "Image-guided work currently requires a web or source project.")
    store = get_store()
    parent = None
    if body.parent_id:
        parent = task_row(store, body.parent_id)
        if parent["status"] in {"queued", "running"}:
            raise HTTPException(409, "Wait for this run to finish before continuing it.")
        if store.get_json("files", parent["id"]) is None:
            raise HTTPException(409, "This run has no saved file version to continue from.")
        head = store.get_json("thread-head", parent.get("thread_id", parent["id"]), parent["id"])
        if head != parent["id"]:
            raise HTTPException(409, "This conversation has a newer version. Continue from its latest run.")
    now = time.time()
    value = uuid.uuid4().hex
    item = {"id": value, "model_budget_usd": body.model_budget_usd, "task": body.task, "workspace": PROJECT, "model": model, "model_id": body.model_id, "status": "queued", "created_at": now, "updated_at": now, "deadline": now + JOB_SECONDS + 60, "report": None, "error": None}
    item["images"] = body.images
    item.update(project_id=parent.get("project_id") if parent else body.project_id, runtime=body.runtime, operation=body.operation, parent_id=body.parent_id, thread_id=parent.get("thread_id", parent["id"]) if parent else value,
                followup_context=context_for(store, parent) if parent else [])
    if not store.create(item):
        raise HTTPException(429, "This project already has an active run. Wait for it to finish.")
    if parent:
        # Even a quota-rejected start must retain a usable source revision.
        previous_files=store.get_json('files',parent['id'])
        if previous_files is not None:
            store.put_json('files',value,previous_files)
            store.put_json('preview-files',value,previous_files)
    if parent and store.get_json("thread-head", item["thread_id"], parent["id"]) != parent["id"]:
        store.fields(value, status="cancelled", error="A newer conversation version was saved in the meantime.")
        store.release(value)
        raise HTTPException(409, "Open the latest version of this conversation.")
    store.event(value, {"event": "task_queued", "parent_id": body.parent_id, "thread_id": item["thread_id"]})
    return {"id": value, "status": "queued", "cancellation": "queued_tasks_only", "run_required": True}


def dispatch(work):
    if os.environ.get("VERCEL") == "1":
        from vercel.cache.context import get_context
        from vercel.functions import wait_until
        if get_context().wait_until is not None:
            wait_until(work)
            return True
    return False


async def run_worker(store, value, oidc_token=None):
    started = time.monotonic()
    # Only the known core worker runs locally. Model-authored code runs in remote microVMs.
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "SYNVIO_ENABLE_GATEWAY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN", "SYNVIO_SANDBOX_SNAPSHOT", "VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID", "VERCEL_REGION"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    # Vercel can add dependency directories to sys.path rather than the system
    # interpreter's site-packages; the known worker needs the same trusted paths.
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(Path(__file__).resolve().parents[2]), *(entry for entry in sys.path if entry)]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    snapshot = saved_snapshot(store)
    if snapshot:
        env["SYNVIO_SANDBOX_SNAPSHOT"] = snapshot
    if oidc_token:
        env["VERCEL_OIDC_TOKEN"] = oidc_token
    elif os.environ.get("VERCEL_OIDC_TOKEN"):
        env["VERCEL_OIDC_TOKEN"] = os.environ["VERCEL_OIDC_TOKEN"]

    def execute():
        result = subprocess.run([sys.executable, "-m", "synvio.cloud.worker", value], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=JOB_SECONDS, shell=False)
        return result.returncode

    try:
        if os.environ.get("VERCEL") == "1":
            store.fields(value, build_pending=True, updated_at=time.time())
        code = await asyncio.to_thread(execute)
        final = store.get(value)
        if final and final["status"] == "running":
            raise RuntimeError(f"The run exited without a report (code {code}).")
        can_build = final and (
            final["status"] == "completed" or
            (final["status"] == "paused" and (final.get("report") or {}).get("preview_source_ready"))
        )
        if os.environ.get("VERCEL") == "1" and can_build and not final.get("pause_requested") and final.get("runtime") == "app" and final.get("operation") == "edit" and time.monotonic()-started < 100:
            try:
                await build_application_preview(value,BuildPreview(request_id=value))
            except Exception as exc:
                store.event(value,{"event":"application_build","phase":"deferred","error":public_error(exc)})
    except Exception as exc:
        if store.transition(value, "running", "paused"):
            store.fields(value, error=public_error(exc))
            store.event(value, {"event": "task_error", "error": public_error(exc)})
    finally:
        try:
            if os.environ.get("VERCEL") == "1":
                store.fields(value, build_pending=False, updated_at=time.time())
        finally:
            store.release(value)


@app.post("/api/tasks/{value}/run")
async def execute_task(value: str, request: Request):
    store = get_store()
    item = task_row(store, value)
    if item["status"] != "queued":
        return {"id": value, "status": item["status"]}
    if not tasks_ready(item.get("runtime", "python")):
        raise HTTPException(503, "The execution environment is disabled.")
    # Atomic claim: a browser retry must not launch a second model loop.
    claimed = store.call("EVAL", "if redis.call('GET',KEYS[2])==ARGV[1] and redis.call('HGET',KEYS[1],'status')=='\"queued\"' then redis.call('HSET',KEYS[1],'status','\"running\"');redis.call('EXPIRE',KEYS[2],ARGV[2]);return 1 else return 0 end", 2, store.key("task", value), store.key("project-lock"), value, JOB_SECONDS + 30)
    if not claimed:
        return {"id": value, "status": task_row(store, value)["status"]}
    if not item.get("request_quota_reserved") and not store.allow_paid_request():
        store.fields(value, status="paused", error="The daily request limit has been reached.")
        store.event(value, {"event": "task_error", "error": "The daily limit has been reached."})
        store.release(value)
        raise HTTPException(429, "The daily limit has been reached. No model was called.")
    store.fields(value, updated_at=time.time(), deadline=time.time() + JOB_SECONDS + 20)
    work = run_worker(store, value, request.headers.get("x-vercel-oidc-token"))
    if not dispatch(work):
        await work
    return {"id": value, "status": task_row(store, value)["status"]}


@app.get("/api/tasks")
def history(limit: int = Query(default=50, ge=1, le=200)):
    if not storage_ready():
        return {"items": []}
    store = get_store()
    return {"items": [task_row(store, item["id"], item) for item in store.history(limit)]}


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
        raise HTTPException(409, "Only queued runs can be cancelled.")
    store.event(value, {"event": "task_cancelled"})
    store.release(value)
    return {"id": value, "status": "cancelled"}


@app.post("/api/tasks/{value}/pause")
def pause_task(value: str):
    store = get_store()
    task_row(store, value)
    updated = store.call("EVAL", """
local status=redis.call('HGET',KEYS[1],'status')
if status=='"queued"' then
    redis.call('HSET',KEYS[1],'status','"paused"','pause_requested','true','updated_at',ARGV[1])
    return 2
end
if status=='"running"' and redis.call('HGET',KEYS[1],'pause_requested')~='true' then
    redis.call('HSET',KEYS[1],'pause_requested','true','updated_at',ARGV[1])
    return 1
end
return 0
""", 1, store.key('task', value), time.time())
    if updated == 2:
        store.event(value, {'event': 'task_finished', 'status': 'paused', 'reason': 'user_requested'})
        store.release(value)
    elif updated == 1:
        store.event(value, {'event': 'pause_requested', 'text': 'Pause requested. Saving progress.'})
    return task_row(store, value)


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
        raise HTTPException(404, "This file is not in the run change report.")
    return name, action, content[name]


@app.get("/api/tasks/{value}/files/content")
def file_content(value: str, file: str | None = Query(default=None, min_length=1, max_length=200), path: str | None = Query(default=None, min_length=1, max_length=200)):
    # Vercel's catch-all rewrite can populate `path`; keep the filename separate.
    name, action, content = file_data(get_store(), value, file or path or '')
    return {"path": name, "action": action, "content": content, "read_only": True}


@app.get("/api/tasks/{value}/files/diff")
def file_diff(value: str, file: str | None = Query(default=None, min_length=1, max_length=200), path: str | None = Query(default=None, min_length=1, max_length=200)):
    store = get_store()
    name, action, content = file_data(store, value, file or path or '')
    baseline = store.get_json("baseline", value, {})
    if name not in baseline and action != "created":
        return {"path": name, "available": False, "reason": "No baseline snapshot was saved.", "diff": None}
    before = baseline.get(name, "")
    return {"path": name, "available": True, "diff": "".join(difflib.unified_diff(before.splitlines(True), content.splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}")), "read_only": True}


@app.get("/api/tasks/{value}/download")
def download(value: str):
    store = get_store()
    task_row(store, value)
    content = store.get_json("files", value)
    if content is None:
        raise HTTPException(404, "This run does not have saved files yet.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in content.items():
            archive.writestr(safe_path(name), text)
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="synvio-{value}.zip"'})


@app.get("/api/tasks/{value}/preview")
def preview(value: str):
    store = get_store()
    item = task_row(store, value)
    if item.get('runtime')=='app' and item['status'] not in {'queued','running'}:
        from .app_build import validate_app
        try: validate_app(store.get_json('files',value) or {})
        except ValueError as exc: return {'available':False,'can_build':False,'reason':str(exc),'source_invalid':True}
    built = store.get_json('built-files', value)
    if built is None and item.get('runtime')=='app' and item['status'] not in {'queued','running'}:
        from .app_build import reuse_build
        if reuse_build(store,item,store.get_json('files',value) or {}):
            built=store.get_json('built-files',value)
    if built is not None:
        return {**build_preview(built), 'kind':'built_application', 'can_build':True, 'build':store.get_json('application-build', value)}
    content = store.get_json("preview-files", value) if item["status"] == "running" else store.get_json("files", value)
    if content is None:
        content = store.get_json("files", value)
    if content is None:
        reason = item.get('error') or (item.get('report') or {}).get('reason')
        return {"available": False, "reason": ("This run stopped before a preview was saved: " + reason) if reason else "No preview has been saved yet. Check Activity for the current progress."}
    return {**build_preview(content), 'can_build': 'package.json' in content}


class BuildPreview(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')


@app.get('/api/tasks/{value}/build-preview')
def build_preview_status(value: str):
    store = get_store()
    task_row(store, value)
    state = store.get_json('application-build', value, {'status':'not_started'})
    if state.get('status') == 'building' and time.time() > state.get('deadline', float('inf')):
        state = {**state, 'status':'failed', 'error':'The build was interrupted or timed out. Start a new build explicitly to retry.'}
        store.put_json('application-build', value, state)
    return {**state, 'events':[item for item in store.events(value) if item['event']=='application_build'][-12:]}


@app.post('/api/tasks/{value}/build-preview')
async def build_application_preview(value: str, body: BuildPreview):
    from .app_build import build, validate_app, reuse_build
    store = get_store()
    task = task_row(store, value)
    if task['status'] in {'queued','running'}:
        raise HTTPException(409, 'Wait for file editing to finish before building the preview.')
    if not tasks_ready('app'):
        raise HTTPException(503, 'Cloud application builds are disabled.')
    files = store.get_json('files', value)
    try:
        validate_app(files or {})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    state = store.get_json('application-build', value, {})
    if state.get('status') == 'ready' or (state.get('status') == 'building' and state.get('deadline', 0) > time.time()) or state.get('request_id') == body.request_id:
        return state
    cached=reuse_build(store,task,files)
    if cached: return cached
    lock = store.key('application-build-lock')
    if not store.call('SET', lock, body.request_id, 'NX', 'EX', 210):
        raise HTTPException(409, 'An application build is already running.')
    try:
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        configured = os.environ.get('SYNVIO_DAILY_BUILD_LIMIT')
        if configured and not store.meter('application-build:' + day, min(max(int(configured),1),100), 2*24*60*60):
            raise HTTPException(429, 'The daily build safety limit has been reached. No VM was started. Configure SYNVIO_DAILY_BUILD_LIMIT on the server.')
        store.put_json('application-build', value, {'status':'building','request_id':body.request_id,'deadline':time.time()+210})
        work = _finish_build(store,value,body.request_id,files,lock)
        if dispatch(work):
            return store.get_json("application-build",value)
        return await work
    finally:
        if store.get_json('application-build',value,{}).get('status') != 'building':
            store.call('EVAL', "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end", 1, lock, body.request_id)


async def _finish_build(store,value,request_id,files,lock):
    from .app_build import build
    try:
        return await build(store,value,request_id,files)
    finally:
        store.call("EVAL", "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,request_id)

from .codex_workspace import router as codex_router
app.include_router(codex_router)

from .codex_proxy import router as codex_proxy_router
app.include_router(codex_proxy_router)

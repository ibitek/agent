"""Local SYNVIO Core HTTP API for the optional web application."""

from __future__ import annotations

import json
import os
import re
import difflib
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from synvio.autodev.agent import DeveloperAgent
from synvio.autodev.workspace import MAX_READ_BYTES, Workspace, WorkspaceError, WorkspaceViolation
from synvio.config import ReasoningMode, SYNVIOConfig
from synvio.runtime import engine_for

APP_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SYNVIO" / "Core"
DB_PATH = Path(os.environ.get("SYNVIO_DB_PATH", DEFAULT_DATA_DIR / "tasks.sqlite3")).expanduser()
TASK_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="synvio-task")
TASK_LOCK = threading.RLock()
ACTIVE: Dict[str, Future] = {}
TERMINAL = {"completed", "failed", "cancelled"}
MAX_BASELINE_FILES = 500
MAX_BASELINE_BYTES = 5 * 1024 * 1024


class TaskCreate(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    workspace: str = Field(min_length=1, max_length=4_000)
    model_id: str = Field(min_length=1, max_length=200)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatCreate(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    model_id: str = Field(default="openai:low", min_length=1, max_length=200)
    history: List[ChatMessage] = Field(default_factory=list, max_length=12)


def _is_greeting(message: str) -> bool:
    normalized = re.sub(r"[^\w\s]", "", message.casefold()).strip()
    normalized = " ".join(normalized.split())
    return normalized in {
        "siema", "siemka", "siemanko", "hej", "hejka", "hejo", "cześć", "czesc",
        "witam", "dzień dobry", "dzien dobry", "dobry wieczór", "dobry wieczor",
        "elo", "yo", "hello", "hi", "hey", "how are you", "co tam", "co słychać",
        "co slychac", "good morning", "good afternoon", "good evening",
    }


def _model_catalog() -> List[Dict[str, Any]]:
    """Provider-aware model list; engine keys are always chosen server-side."""
    items: List[Dict[str, Any]] = []
    openai_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    for mode, model in (
        (ReasoningMode.LOW, "gpt-4o-mini"),
        (ReasoningMode.MEDIUM, "gpt-4o"),
        (ReasoningMode.HIGH, "gpt-4o"),
    ):
        items.append({
            "id": f"openai:{mode.value}",
            "provider": "openai",
            "engine": "cloud",
            "model": model,
            "reasoning_mode": mode.value,
            "label": f"{model} · OpenAI · {mode.value.title()}",
            "available": openai_ready,
            "unavailable_reason": None if openai_ready else "OPENAI_API_KEY is not set in the backend environment.",
        })

    # Local model IDs are opt-in, so an uninstalled Ollama service is never
    # silently selected merely because the browser chose a GPT model.
    local_models = [item.strip() for item in os.environ.get("SYNVIO_LOCAL_MODELS", "").split(",") if item.strip()]
    for model in local_models:
        if re.fullmatch(r"[\w./:-]{1,120}", model):
            items.append({
                "id": f"ollama:{model}",
                "provider": "ollama",
                "engine": "ollama",
                "model": model,
                "reasoning_mode": ReasoningMode.MEDIUM.value,
                "label": f"{model} · Ollama · Local",
                "available": True,
                "unavailable_reason": None,
            })
    # Additional providers are declared by the operator, never by the browser.
    # Example: SYNVIO_MODEL_CATALOG='[{"id":"anthropic:sonnet","provider":"anthropic","engine":"cloud","model":"claude-sonnet-4-20250514","reasoning_mode":"medium","api_key_env":"ANTHROPIC_API_KEY"}]'
    raw_catalog = os.environ.get("SYNVIO_MODEL_CATALOG", "")
    if raw_catalog:
        try:
            configured = json.loads(raw_catalog)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SYNVIO_MODEL_CATALOG must be valid JSON.") from exc
        if not isinstance(configured, list):
            raise RuntimeError("SYNVIO_MODEL_CATALOG must be a JSON list.")
        for item in configured:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) and item[key]
                for key in ("id", "provider", "engine", "model", "reasoning_mode", "api_key_env")
            ):
                raise RuntimeError("Each SYNVIO_MODEL_CATALOG entry requires id, provider, engine, model, reasoning_mode, and api_key_env.")
            if item["id"].startswith("openai:") or item["id"].startswith("ollama:"):
                raise RuntimeError(f"SYNVIO_MODEL_CATALOG cannot override reserved model id {item['id']!r}.")
            try:
                mode = ReasoningMode(item["reasoning_mode"])
            except ValueError as exc:
                raise RuntimeError(f"Unsupported reasoning mode for {item['id']!r}.") from exc
            ready = bool(os.environ.get(item["api_key_env"], "").strip())
            items.append({
                "id": item["id"], "provider": item["provider"], "engine": item["engine"],
                "model": item["model"], "reasoning_mode": mode.value,
                "label": item.get("label") or f"{item['model']} · {item['provider']}",
                "available": ready,
                "unavailable_reason": None if ready else f"{item['api_key_env']} is not set in the backend environment.",
            })
    return items


def _resolve_model(model_id: str) -> Dict[str, Any]:
    selected = next((item for item in _model_catalog() if item["id"] == model_id), None)
    if selected is None:
        raise HTTPException(status_code=422, detail=f"Unknown model selection: {model_id!r}.")
    if not selected["available"]:
        raise HTTPException(status_code=503, detail=selected["unavailable_reason"])
    return selected


def _validate_developer_task(task: str) -> None:
    if _is_greeting(task):
        raise HTTPException(
            status_code=422,
            detail="To powitanie (general chat), nie zadanie programistyczne. Wybierz tryb Rozmowa. Nie uruchomiono modelu ani narzędzi.",
        )


def _public_error(exc: Exception, selection: Dict[str, Any] | None) -> str:
    """Explain provider failures without returning any backend secret values."""
    message = f"{type(exc).__name__}: {exc}"
    for name, value in os.environ.items():
        if name.endswith("_API_KEY") and value:
            message = message.replace(value, "[redacted]")
    if selection:
        return (
            f"Selected provider {selection['provider']!r} using engine "
            f"{selection['engine']!r} is unavailable: {message}"
        )
    return message


def _database() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, task TEXT NOT NULL, workspace TEXT NOT NULL,
            model TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
            updated REAL NOT NULL, report TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
            created REAL NOT NULL, payload TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS task_baselines (
            task_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
            PRIMARY KEY(task_id, path),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        """
    )
    return db


def _init_db() -> None:
    db = _database()
    db.execute("PRAGMA journal_mode=WAL")
    db.commit()
    db.close()


def _event(task_id: str, event: Dict[str, Any]) -> None:
    """Persist the agent's actual callback events (not fabricated UI updates)."""
    db = _database()
    now = time.time()
    db.execute(
        "INSERT INTO events(task_id, created, payload) VALUES (?, ?, ?)",
        (task_id, now, json.dumps(event, default=str)),
    )
    db.execute("UPDATE tasks SET updated=? WHERE id=?", (now, task_id))
    db.commit()
    db.close()


def _allowed_workspaces() -> List[Path]:
    raw = os.environ.get("SYNVIO_WORKSPACES", "")
    roots: List[Path] = []
    for item in raw.split(os.pathsep):
        if item.strip():
            path = Path(item.strip()).expanduser().resolve()
            if path.is_dir() and path not in roots:
                roots.append(path)
    return roots


def _capture_baseline(workspace_root: Path) -> Dict[str, str]:
    """Capture a bounded, read-only pre-task snapshot for truthful diffs."""
    workspace = Workspace(workspace_root)
    baseline: Dict[str, str] = {}
    size = 0
    for relative in workspace.list_files("**/*", limit=MAX_BASELINE_FILES + 1):
        if len(baseline) >= MAX_BASELINE_FILES:
            break
        try:
            path = workspace._resolve(relative)
            file_size = path.stat().st_size
            if file_size > MAX_READ_BYTES or size + file_size > MAX_BASELINE_BYTES:
                continue
            content = workspace.read_text(relative)
        except (OSError, WorkspaceError, WorkspaceViolation):
            continue
        baseline[relative] = content
        size += file_size
    return baseline


def _workspace_for(value: str) -> Path:
    candidate = Path(value).expanduser().resolve()
    if candidate not in _allowed_workspaces():
        raise ValueError("Workspace is not in SYNVIO_WORKSPACES allowlist.")
    return candidate


def _config(selection: Dict[str, Any]) -> SYNVIOConfig:
    mode = ReasoningMode(selection["reasoning_mode"])
    return SYNVIOConfig(
        reasoning_mode=mode,
        provider=selection["provider"],
        engine=selection["engine"],
        model_override=selection["model"],
        agent_steps_limit=int(os.environ.get("SYNVIO_AGENT_STEPS", "10")),
        tool_execution_limit=int(os.environ.get("SYNVIO_TOOL_LIMIT", "8")),
        max_repair_attempts=int(os.environ.get("SYNVIO_MAX_ATTEMPTS", "1")),
        total_step_limit=int(os.environ.get("SYNVIO_TOTAL_STEPS", "10")),
        total_tool_limit=int(os.environ.get("SYNVIO_TOTAL_TOOLS", "8")),
        max_test_runs=int(os.environ.get("SYNVIO_MAX_TEST_RUNS", "2")),
    )


def _sandbox_image() -> str:
    """Validate an image reference for a future Docker implementation.

    Docker execution is intentionally unavailable until the runner and its
    isolation/cleanup integration have been implemented and verified.
    """
    image = os.environ.get(
        "SYNVIO_SANDBOX_IMAGE",
        "synvio-python-tests@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535",
    ).strip()
    if not re.fullmatch(r"[a-zA-Z0-9._/-]+@sha256:[a-f0-9]{64}", image):
        raise HTTPException(
            status_code=503,
            detail="SYNVIO_SANDBOX_IMAGE must be pinned to a sha256 digest.",
        )
    return image


def _require_execution_backend() -> str:
    """Only allow explicitly trusted host execution until Docker is implemented.

    Never silently downgrade a Docker request to host execution.
    """
    backend = os.environ.get("SYNVIO_EXECUTION_BACKEND", "docker").strip().lower()
    if backend == "host":
        if os.environ.get("SYNVIO_TRUST_HOST_TEST_EXECUTION") != "1":
            raise HTTPException(
                status_code=403,
                detail="Host tests are not sandboxed; trusted host execution opt-in is required.",
            )
        return "host"
    if backend == "docker":
        raise HTTPException(
            status_code=503,
            detail=(
                "Docker execution is not available in this version of SYNVIO Core. "
                "Task not queued; task will not run on the host; no automatic host fallback."
            ),
        )
    raise HTTPException(
        status_code=422,
        detail=f"Unsupported execution backend: {backend!r}.",
    )


def _task_row(row: sqlite3.Row) -> Dict[str, Any]:
    with TASK_LOCK:
        future = ACTIVE.get(row["id"])
        can_cancel = row["status"] == "queued" and future is not None and not future.running()
    report = json.loads(row["report"]) if row["report"] else None
    return {
        "id": row["id"], "task": row["task"], "workspace": row["workspace"],
        "model": row["model"], "status": row["status"],
        "created_at": row["created"], "updated_at": row["updated"],
        "report": _enrich_report(report) if report else None,
        "error": row["error"],
        "cancellation_supported": can_cancel,
    }


def _get_task_row(task_id: str) -> sqlite3.Row:
    db = _database()
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    db.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return row


def _report_files(row: sqlite3.Row) -> Dict[str, str]:
    """Return the report-authorized relative paths and their recorded actions."""
    if not row["report"]:
        return {}
    report = json.loads(row["report"])
    allowed: Dict[str, str] = {}
    for action, field in (("created", "files_created"), ("modified", "files_modified")):
        for name in report.get(field, []):
            if isinstance(name, str):
                allowed[name.replace("\\", "/")] = action
    return allowed


def _task_file(task_id: str, relative: str) -> tuple[sqlite3.Row, Path, str]:
    row = _get_task_row(task_id)
    normalized = relative.replace("\\", "/")
    action = _report_files(row).get(normalized)
    if action is None:
        raise HTTPException(status_code=404, detail="File is not listed as changed in this task report.")
    try:
        workspace = Workspace(row["workspace"])
        path = workspace._resolve(normalized)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Changed file no longer exists in the task workspace.")
        if path.stat().st_size > MAX_READ_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the read-only viewer size limit.")
    except (OSError, WorkspaceViolation, WorkspaceError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid workspace file path: {exc}") from exc
    return row, path, action


def _baseline_content(task_id: str, relative: str) -> str | None:
    db = _database()
    row = db.execute(
        "SELECT content FROM task_baselines WHERE task_id=? AND path=?",
        (task_id, relative),
    ).fetchone()
    db.close()
    return row["content"] if row else None


def _estimated_cost(model: str, tokens: Dict[str, Any]) -> float | None:
    prompt = tokens.get("prompt_tokens")
    completion = tokens.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int) or prompt < 0 or completion < 0:
        return None
    try:
        from openjarvis.engine.cloud import estimate_cost

        return float(estimate_cost(model, prompt, completion))
    except Exception:
        return None


def _enrich_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Add only values derived from the persisted TaskReport."""
    enriched = dict(report)
    enriched["estimated_cost_usd"] = _estimated_cost(
        str(enriched.get("model") or ""), enriched.get("tokens") or {}
    )
    return enriched


def _execute_task(task_id: str, request: TaskCreate, workspace_root: Path) -> None:
    db = _database()
    db.execute("UPDATE tasks SET status='running', updated=? WHERE id=?", (time.time(), task_id))
    db.commit()
    db.close()
    workspace = Workspace(workspace_root)
    selection: Dict[str, Any] | None = None
    try:
        baseline = _capture_baseline(workspace_root)
        db = _database()
        db.executemany(
            "INSERT OR REPLACE INTO task_baselines(task_id,path,content) VALUES(?,?,?)",
            [(task_id, path, content) for path, content in baseline.items()],
        )
        db.commit()
        db.close()

        selection = _resolve_model(request.model_id)
        config = _config(selection)
        # Re-check at execution time in case configuration changed after queuing.
        _require_execution_backend()

        with engine_for(config) as engine:
            agent = DeveloperAgent(
                engine=engine,
                workspace=workspace,
                config=config,
                on_event=lambda event: _event(task_id, event),
            )
            report = agent.fix(request.task)
        status = "completed" if report.success else "failed"
        db = _database()
        db.execute(
            "UPDATE tasks SET status=?, updated=?, report=? WHERE id=?",
            (status, time.time(), report.to_json(), task_id),
        )
        db.commit()
        db.close()
        _event(task_id, {"event": "task_finished", "status": status, "success": report.success})
    except Exception as exc:
        db = _database()
        db.execute(
            "UPDATE tasks SET status='failed', updated=?, error=? WHERE id=?",
            (time.time(), _public_error(exc, selection), task_id),
        )
        db.commit()
        db.close()
        _event(task_id, {"event": "task_error", "error": _public_error(exc, selection)})
    finally:
        with TASK_LOCK:
            ACTIVE.pop(task_id, None)


def _forget_finished_task(task_id: str, _: Future) -> None:
    with TASK_LOCK:
        ACTIVE.pop(task_id, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_db()
    yield


app = FastAPI(title="SYNVIO Core API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "cancellation": "queued_tasks_only",
        "host": "127.0.0.1",
        "execution_backend": os.environ.get("SYNVIO_EXECUTION_BACKEND", "docker").strip().lower(),
    }


@app.get("/api/workspaces")
def workspaces() -> Dict[str, Any]:
    return {"items": [str(path) for path in _allowed_workspaces()]}


@app.get("/api/models")
def models() -> Dict[str, Any]:
    return {"items": _model_catalog()}


@app.get("/api/limits")
def limits() -> Dict[str, Any]:
    cfg = _config(next(item for item in _model_catalog() if item["id"] == "openai:medium"))
    return {
        "steps_per_attempt": cfg.agent_steps_limit,
        "tool_calls_per_task": cfg.global_tool_limit,
        "attempts": cfg.max_repair_attempts,
        "model_test_runs": cfg.max_test_runs,
        "test_timeout_seconds": cfg.test_timeout_seconds,
        "cancellation": "queued_tasks_only",
        "execution_backend": os.environ.get("SYNVIO_EXECUTION_BACKEND", "docker").strip().lower(),
    }


@app.post("/api/chat")
def chat(request: ChatCreate) -> Dict[str, Any]:
    """Conversation uses one generation, no developer agent, tools, tests or workspace."""
    if _is_greeting(request.message):
        return {
            "mode": "chat", "content": "Cześć! W czym mogę pomóc? Rozmowa nie zmienia plików. Aby zlecić wykonanie zmian, przełącz na Zadanie.",
            "model": None, "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "estimated_cost_usd": 0.0, "local_response": True,
        }
    selection = _resolve_model(request.model_id)
    from openjarvis.core.types import Message, Role

    messages = [Message(role=Role.SYSTEM, content=(
        "Jesteś Kaelia w trybie rozmowy. Odpowiadaj w języku użytkownika. "
        "Nie masz narzędzi, dostępu do plików ani możliwości uruchamiania testów. "
        "Nie twierdź, że wykonałeś zmiany. Gdy użytkownik chce wykonania zmian, "
        "wyjaśnij, że musi wybrać tryb Zadanie."
    ))]
    messages.extend(Message(role=Role(item.role), content=item.content) for item in request.history[-12:])
    messages.append(Message(role=Role.USER, content=request.message))
    config = _config(selection)
    try:
        with engine_for(config) as engine:
            result = engine.generate(messages, model=selection["model"], temperature=0.4, max_tokens=512)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_public_error(exc, selection)) from exc
    usage = result.get("usage") or {}
    return {
        "mode": "chat", "content": str(result.get("content") or ""), "model": selection["model"],
        "tokens": usage, "estimated_cost_usd": _estimated_cost(selection["model"], usage),
        "local_response": False,
    }


@app.post("/api/tasks", status_code=202)
def create_task(request: TaskCreate) -> Dict[str, Any]:
    _validate_developer_task(request.task)
    backend = _require_execution_backend()
    try:
        workspace_root = _workspace_for(request.workspace)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    selection = _resolve_model(request.model_id)
    task_id = uuid.uuid4().hex
    now = time.time()
    cfg = _config(selection)
    with TASK_LOCK:
        if len(ACTIVE) >= 2:
            raise HTTPException(status_code=429, detail="The local task queue is full.")
    db = _database()
    db.execute(
        "INSERT INTO tasks(id,task,workspace,model,status,created,updated) VALUES(?,?,?,?,?,?,?)",
        (task_id, request.task, str(workspace_root), cfg.model, "queued", now, now),
    )
    db.commit()
    db.close()
    with TASK_LOCK:
        future = TASK_POOL.submit(_execute_task, task_id, request, workspace_root)
        ACTIVE[task_id] = future
        future.add_done_callback(lambda completed: _forget_finished_task(task_id, completed))
    return {"id": task_id, "status": "queued", "cancellation": "queued_tasks_only"}


@app.get("/api/tasks")
def task_history(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    db = _database()
    rows = db.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return {"items": [_task_row(row) for row in rows]}


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: str) -> Dict[str, Any]:
    row = _get_task_row(task_id)
    result = _task_row(row)
    if row["status"] == "queued":
        with TASK_LOCK:
            future = ACTIVE.get(task_id)
            result["cancellation_supported"] = future is not None and not future.running()
    return result


@app.get("/api/tasks/{task_id}/files")
def task_files(task_id: str) -> Dict[str, Any]:
    row = _get_task_row(task_id)
    allowed = _report_files(row)
    items = []
    for path, action in allowed.items():
        try:
            workspace = Workspace(row["workspace"])
            resolved = workspace._resolve(path)
            exists = resolved.is_file()
            size = resolved.stat().st_size if exists else None
        except (OSError, WorkspaceError, WorkspaceViolation):
            exists, size = False, None
        items.append({"path": path, "action": action, "exists": exists, "size_bytes": size})
    return {"items": items}


@app.get("/api/tasks/{task_id}/files/content")
def task_file_content(task_id: str, path: str = Query(min_length=1, max_length=4096)) -> Dict[str, Any]:
    row, file_path, action = _task_file(task_id, path)
    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {"path": path.replace("\\", "/"), "action": action, "content": content, "read_only": True}


@app.get("/api/tasks/{task_id}/files/diff")
def task_file_diff(task_id: str, path: str = Query(min_length=1, max_length=4096)) -> Dict[str, Any]:
    row, file_path, action = _task_file(task_id, path)
    after = file_path.read_text(encoding="utf-8", errors="replace")
    normalized = path.replace("\\", "/")
    before = _baseline_content(task_id, normalized)
    if before is None and action == "created":
        before = ""
    if before is None:
        return {
            "path": normalized,
            "available": False,
            "reason": "No pre-task baseline was recorded for this older task, so a truthful diff is unavailable.",
            "diff": None,
        }
    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{normalized}", tofile=f"b/{normalized}",
    ))
    return {"path": normalized, "available": True, "diff": "".join(lines), "read_only": True}


@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(default=0, ge=0)) -> Dict[str, Any]:
    db = _database()
    exists = db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
    if exists is None:
        db.close()
        raise HTTPException(status_code=404, detail="Task not found.")
    rows = db.execute(
        "SELECT sequence, created, payload FROM events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT 500",
        (task_id, after),
    ).fetchall()
    db.close()
    return {
        "items": [
            {"sequence": row["sequence"], "created_at": row["created"], **json.loads(row["payload"])}
            for row in rows
        ]
    }


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> Dict[str, Any]:
    """Cancel a not-yet-started task; never interrupt active inference or tools."""
    with TASK_LOCK:
        future = ACTIVE.get(task_id)
        if future is None:
            db = _database()
            row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            db.close()
            if row is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            raise HTTPException(
                status_code=409,
                detail="Task has started; this runtime cannot safely interrupt it.",
            )
        if not future.cancel():
            raise HTTPException(
                status_code=409,
                detail="Task has started; this runtime cannot safely interrupt it.",
            )
        ACTIVE.pop(task_id, None)
        db = _database()
        db.execute("UPDATE tasks SET status='cancelled', updated=? WHERE id=?", (time.time(), task_id))
        db.commit()
        db.close()
    _event(task_id, {"event": "task_cancelled", "safe": True, "stage": "queued"})
    return {"id": task_id, "status": "cancelled", "cancellation": "queued_tasks_only"}
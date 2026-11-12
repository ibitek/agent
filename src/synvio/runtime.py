"""Runtime helpers that reuse the OpenJarvis agent loop.

SYNVIO intentionally does not implement its own reasoning loop. It builds an
``openjarvis.agents.native_react.NativeReActAgent`` and hands it SYNVIO tools;
the step budget is enforced by OpenJarvis (``max_turns``) and the tool budget is
enforced by :class:`BudgetedTool`, which denies calls once the SYNVIO limit is
reached instead of trusting the model to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from synvio.config import SYNVIOConfig

#: Attributes OpenJarvis wrappers use to hold the engine they delegate to.
#: ``InstrumentedEngine`` (telemetry) and ``GuardrailsEngine`` (security) both
#: subclass ``InferenceEngine`` -- whose ``is_cloud`` is ``False`` -- and neither
#: forwards the wrapped engine's ``is_cloud``. Reading the flag off the object
#: the SDK hands out is therefore wrong: the real engine is one (or two) levels
#: down, which is what :func:`identify_engine` unwraps.
_WRAPPER_ATTRIBUTES = ("_inner", "_engine", "inner", "engine")


def _looks_like_engine(candidate: Any) -> bool:
    return callable(getattr(candidate, "generate", None))


@dataclass(frozen=True)
class EngineIdentity:
    """What OpenJarvis actually resolved, with the wrappers stripped.

    ``chain`` is outer-to-inner, e.g. ``["InstrumentedEngine", "GuardrailsEngine",
    "CloudEngine"]``. ``engine_id`` and ``is_cloud`` come from the innermost
    engine, i.e. the one that really talks to the provider.
    """

    chain: List[str]
    engine_id: str
    is_cloud: bool

    @property
    def name(self) -> str:
        return " -> ".join(self.chain) if self.chain else "<unknown>"

    @property
    def is_wrapped(self) -> bool:
        return len(self.chain) > 1

    def describe(self) -> str:
        """One-line proof of the engine that served the run."""
        return f"{self.name} (engine_id={self.engine_id!r}, is_cloud={self.is_cloud})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": list(self.chain),
            "engine_id": self.engine_id,
            "is_cloud": self.is_cloud,
        }


def identify_engine(engine: Any, *, max_depth: int = 8) -> EngineIdentity:
    """Return the real engine behind OpenJarvis' telemetry/security wrappers."""
    chain: List[str] = []
    current: Any = engine
    seen: set = set()
    while (
        current is not None
        and id(current) not in seen
        and len(chain) < max_depth
    ):
        seen.add(id(current))
        chain.append(type(current).__name__)
        inner = None
        for attribute in _WRAPPER_ATTRIBUTES:
            candidate = getattr(current, attribute, None)
            if (
                candidate is not None
                and candidate is not current
                and _looks_like_engine(candidate)
            ):
                inner = candidate
                break
        if inner is None:
            break
        current = inner

    engine_id = str(getattr(current, "engine_id", "") or "")
    is_cloud = bool(getattr(current, "is_cloud", False))
    return EngineIdentity(chain=chain, engine_id=engine_id, is_cloud=is_cloud)


@dataclass
class ToolBudget:
    """Counts allowed tool executions and denies everything past ``limit``."""

    limit: int
    used: int = 0
    denied: int = 0
    usage: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("tool budget limit must be >= 1")

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def consume(self, tool_name: str) -> bool:
        """Reserve one execution. Returns ``False`` when the budget is spent."""
        if self.exhausted:
            self.denied += 1
            return False
        self.used += 1
        self.usage.append(tool_name)
        return True


ToolEventCallback = Callable[[Dict[str, Any]], None]


class BudgetedTool(BaseTool):
    """Wrap a tool so it can never exceed the SYNVIO tool budget.

    The wrapper is transparent: it delegates ``spec`` to the wrapped tool and
    returns a normal OpenJarvis ``ToolResult`` (a *failed* one) when the budget
    is exhausted, so the model sees the refusal instead of a silent overrun.
    """

    is_local = True

    def __init__(
        self,
        inner: BaseTool,
        budget: ToolBudget,
        *,
        on_event: Optional[ToolEventCallback] = None,
    ) -> None:
        self._inner = inner
        self._budget = budget
        self._on_event = on_event
        self.tool_id = getattr(inner, "tool_id", inner.spec.name)

    @property
    def inner(self) -> BaseTool:
        return self._inner

    @property
    def spec(self) -> ToolSpec:
        return self._inner.spec

    def execute(self, **params: Any) -> ToolResult:
        name = self._inner.spec.name
        if not self._budget.consume(name):
            result = ToolResult(
                tool_name=name,
                content=(
                    f"Tool budget exhausted: {self._budget.limit} tool executions "
                    "are allowed for this whole task (repair attempts share the "
                    "budget). Stop calling tools and provide a Final Answer."
                ),
                success=False,
                metadata={"budget_denied": True},
            )
            self._emit(name, params, result, allowed=False)
            return result

        try:
            result = self._inner.execute(**params)
        except Exception as exc:  # a broken tool must not kill the agent loop
            result = ToolResult(
                tool_name=name,
                content=f"{type(exc).__name__}: {exc}",
                success=False,
            )
        self._emit(name, params, result, allowed=True)
        return result

    def _emit(
        self,
        name: str,
        params: Dict[str, Any],
        result: ToolResult,
        *,
        allowed: bool,
    ) -> None:
        if self._on_event is None:
            return
        self._on_event(
            {
                "event": "tool_call",
                "tool": name,
                "allowed": allowed,
                "success": bool(result.success),
                "arguments": _summarize(params),
                "output": _truncate(str(result.content), 2000),
                "used": self._budget.used,
                "limit": self._budget.limit,
            }
        )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [{len(text)} chars]"


def _summarize(params: Dict[str, Any], limit: int = 400) -> Dict[str, Any]:
    summarized: Dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and len(value) > limit:
            summarized[key] = value[:limit] + f"... [{len(value)} chars]"
        else:
            summarized[key] = value
    return summarized


def build_react_agent(
    *,
    engine: Any,
    model: str,
    tools: List[BaseTool],
    max_turns: int,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> Any:
    """Create a NativeReActAgent (OpenJarvis' own Thought/Action/Observation loop)."""
    if max_turns < 1:
        raise ValueError("max_turns must be >= 1")
    from openjarvis.agents.native_react import NativeReActAgent

    return NativeReActAgent(
        engine,
        model,
        tools=list(tools),
        max_turns=max_turns,
        temperature=temperature,
        max_tokens=max_tokens,
        agent_id="synvio",
    )


def run_agent(
    *,
    task: str,
    engine: Any,
    model: str,
    tools: List[BaseTool],
    max_turns: int,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> Any:
    """Run one OpenJarvis ReAct episode and return its ``AgentResult``."""
    agent = build_react_agent(
        engine=engine,
        model=model,
        tools=tools,
        max_turns=max_turns,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return agent.run(task)


def ask(
    query: str,
    config: Optional[SYNVIOConfig] = None,
    *,
    agent: str = "native_react",
    tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Ask through the real OpenJarvis SDK (requires an inference engine).

    Raises :class:`synvio.config.ExecutionModeNotImplemented` for cloud/hybrid
    execution modes before any network access happens.
    """
    from openjarvis import Jarvis

    cfg = config or SYNVIOConfig()
    cfg.require_supported_execution()

    jarvis_config = cfg.to_jarvis_config()
    # ``engine_key`` is authoritative in OpenJarvis: it is never silently
    # substituted, so a missing local engine cannot turn into a surprise call to
    # a cloud provider.
    with Jarvis(config=jarvis_config, engine_key=cfg.engine or None) as jarvis:
        return jarvis.ask_full(
            query,
            model=cfg.model,
            agent=agent,
            tools=tools or [],
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            context=False,
        )


def engine_for(config: Optional[SYNVIOConfig] = None):
    """Context manager yielding a live OpenJarvis inference engine.

    The OpenJarvis SDK exposes no public engine getter, so the resolved engine
    is taken from the initialized ``Jarvis`` instance and the SDK handle is
    closed afterwards. Nothing here touches the network unless the configured
    engine does (inference calls are made by the agent, not by this helper).
    """
    from contextlib import contextmanager

    @contextmanager
    def _engine_context():
        from openjarvis import Jarvis

        cfg = config or SYNVIOConfig()
        cfg.require_supported_execution()
        jarvis = Jarvis(
            config=cfg.to_jarvis_config(), engine_key=cfg.engine or None
        )
        try:
            try:
                jarvis._ensure_engine()
            except RuntimeError as exc:
                raise RuntimeError(
                    "no usable inference engine "
                    f"(engine={cfg.engine or '<openjarvis default>'!r}): {exc}"
                ) from exc
            yield jarvis._engine
        finally:
            try:
                jarvis.close()
            except Exception:  # pragma: no cover - cleanup must not mask errors
                pass

    return _engine_context()


__all__ = [
    "BudgetedTool",
    "EngineIdentity",
    "ToolBudget",
    "ask",
    "build_react_agent",
    "engine_for",
    "identify_engine",
    "run_agent",
]

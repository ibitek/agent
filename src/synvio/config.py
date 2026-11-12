"""SYNVIO configuration.

SYNVIO is a thin dependent layer on top of OpenJarvis. It does not re-implement
inference, the agent reasoning loop, or the tool executor. It only owns the
policy layer: execution mode, reasoning mode, and hard resource budgets.

The budgets are translated into an :class:`openjarvis.core.config.JarvisConfig`
so the existing OpenJarvis agent loop enforces the step limit itself
(``JarvisConfig.agent.max_turns``). SYNVIO never silently pretends that a mode
works: unsupported execution modes raise ``ExecutionModeNotImplemented``.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from typing import Dict, Optional

from openjarvis.core.config import JarvisConfig, load_config


class ExecutionMode(str, enum.Enum):
    """Where the agent runs.

    Only ``LOCAL`` is implemented in 0.1.0. ``CLOUD`` and ``HYBRID`` are
    configuration placeholders that fail loudly instead of degrading silently.
    """

    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"


class ReasoningMode(str, enum.Enum):
    """How much inference budget the agent gets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionModeNotImplemented(NotImplementedError):
    """Raised when an execution mode is configured but not implemented."""


# Model per reasoning mode. Low uses the cheap model; medium and high use the
# full model but spend a larger token budget (see DEFAULT_MAX_TOKENS).
DEFAULT_MODELS: Dict[str, str] = {
    ReasoningMode.LOW.value: "gpt-4o-mini",
    ReasoningMode.MEDIUM.value: "gpt-4o",
    ReasoningMode.HIGH.value: "gpt-4o",
}

DEFAULT_TEMPERATURES: Dict[str, float] = {
    ReasoningMode.LOW.value: 0.2,
    ReasoningMode.MEDIUM.value: 0.3,
    ReasoningMode.HIGH.value: 0.1,
}

DEFAULT_MAX_TOKENS: Dict[str, int] = {
    ReasoningMode.LOW.value: 512,
    ReasoningMode.MEDIUM.value: 1024,
    ReasoningMode.HIGH.value: 2048,
}


@dataclass
class SYNVIOConfig:
    """SYNVIO policy configuration (plain dataclass, not a JarvisConfig)."""

    execution_mode: ExecutionMode = ExecutionMode.LOCAL
    reasoning_mode: ReasoningMode = ReasoningMode.MEDIUM

    # Hard budgets enforced by SYNVIO regardless of the OpenJarvis rate limiter.
    #
    # ``agent_steps_limit`` is the ReAct step cap for a single attempt and
    # ``tool_execution_limit`` the nominal tool budget for a single attempt.
    # The task-global budgets below are what the developer agent actually
    # enforces: they cover *all* attempts, so a retry can never reset them.
    # ``None`` means "per-attempt limit x max_repair_attempts".
    agent_steps_limit: int = 10
    # Read + write + test is exactly 3 calls, which leaves no room to fix a
    # failing test: 4 keeps one spare call for the retry that is the whole point.
    tool_execution_limit: int = 4
    max_repair_attempts: int = 2
    total_step_limit: Optional[int] = None
    total_tool_limit: Optional[int] = None

    # How many times the model may execute the project's test suite itself.
    # ``None`` leaves it to the tool budget; SYNVIO always runs the tests once
    # more on its own as ground truth, which this cap does not cover.
    max_test_runs: Optional[int] = None

    # Provider/model selection.
    provider: str = "openai"
    model_override: Optional[str] = None
    models: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))

    # Inference engine pinned for the OpenJarvis SDK. OpenJarvis treats an
    # explicit engine as authoritative and never silently substitutes it, so
    # SYNVIO cannot accidentally fall back to a cloud engine (and a surprise
    # network call) when the local engine is down. Set to "" to let OpenJarvis
    # resolve its own default engine instead.
    engine: str = "ollama"

    # Test execution budget for the developer agent.
    test_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        self.execution_mode = ExecutionMode(self.execution_mode)
        self.reasoning_mode = ReasoningMode(self.reasoning_mode)
        self.models = {
            ReasoningMode(str(key)).value: str(value)
            for key, value in self.models.items()
        }
        self.engine = str(self.engine or "").strip()
        for name in ("agent_steps_limit", "tool_execution_limit", "max_repair_attempts"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        for name in ("total_step_limit", "total_tool_limit", "max_test_runs"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(
                    f"{name} must be a positive integer or None, got {value!r}"
                )
        if self.test_timeout_seconds <= 0:
            raise ValueError("test_timeout_seconds must be positive")

    # -- model selection ---------------------------------------------------

    @property
    def model(self) -> str:
        """Model id selected by the current reasoning mode."""
        if self.model_override:
            return self.model_override
        return self.models[self.reasoning_mode.value]

    def get_model(self) -> str:
        """Compatibility helper: same value as :attr:`model`."""
        return self.model

    @property
    def temperature(self) -> float:
        return DEFAULT_TEMPERATURES[self.reasoning_mode.value]

    @property
    def max_tokens(self) -> int:
        return DEFAULT_MAX_TOKENS[self.reasoning_mode.value]

    # -- task-global budgets -----------------------------------------------

    @property
    def global_step_limit(self) -> int:
        """ReAct steps allowed for the whole task, across every attempt."""
        if self.total_step_limit is not None:
            return self.total_step_limit
        return self.agent_steps_limit * self.max_repair_attempts

    @property
    def global_tool_limit(self) -> int:
        """Tool executions allowed for the whole task, across every attempt."""
        if self.total_tool_limit is not None:
            return self.total_tool_limit
        return self.tool_execution_limit * self.max_repair_attempts

    # -- execution mode ----------------------------------------------------

    def require_supported_execution(self) -> None:
        """Fail loudly for modes that are configured but not implemented."""
        if self.execution_mode is not ExecutionMode.LOCAL:
            raise ExecutionModeNotImplemented(
                f"execution_mode={self.execution_mode.value!r} is not implemented "
                "in SYNVIO 0.1.0. Only 'local' execution (with a local agent loop "
                "and a cloud inference provider) is supported."
            )

    # -- OpenJarvis bridging ----------------------------------------------

    def to_jarvis_config(self) -> JarvisConfig:
        """Build the OpenJarvis config that enforces SYNVIO's step budget."""
        cfg = load_config()
        cfg.agent.max_turns = self.agent_steps_limit
        cfg.intelligence.default_model = self.model
        cfg.intelligence.temperature = self.temperature
        cfg.intelligence.max_tokens = self.max_tokens
        cfg.intelligence.provider = self.provider
        if self.engine:
            # Keep the resolved default and the pinned engine consistent.
            cfg.engine.default = self.engine
        return cfg

    def replace(self, **changes: object) -> "SYNVIOConfig":
        """Return a copy with the given fields replaced (validated again)."""
        return dataclasses.replace(self, **changes)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODELS",
    "DEFAULT_TEMPERATURES",
    "ExecutionMode",
    "ExecutionModeNotImplemented",
    "ReasoningMode",
    "SYNVIOConfig",
]

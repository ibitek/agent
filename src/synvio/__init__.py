"""SYNVIO Core Agent - configurable autonomous AI agent platform.

This package is a thin dependent layer on top of OpenJarvis (Apache 2.0).
It owns policy (execution/reasoning modes, hard budgets) and delegates the
agent loop, tools and inference to OpenJarvis.
"""

__version__ = "0.1.0"

from openjarvis import Jarvis
from openjarvis.core.config import load_config
from openjarvis.core.registry import AgentRegistry, EngineRegistry, ToolRegistry

from synvio.config import (
    ExecutionMode,
    ExecutionModeNotImplemented,
    ReasoningMode,
    SYNVIOConfig,
)
from synvio.runtime import BudgetedTool, ToolBudget, ask, run_agent

__all__ = [
    "AgentRegistry",
    "BudgetedTool",
    "EngineRegistry",
    "ExecutionMode",
    "ExecutionModeNotImplemented",
    "Jarvis",
    "ReasoningMode",
    "SYNVIOConfig",
    "ToolBudget",
    "ToolRegistry",
    "__version__",
    "ask",
    "load_config",
    "run_agent",
]

"""
Adapter over the selected demo system (System Under Test).

Provides ONE unified `generate(case)` method for the generation step
(`src/generate.py`).
"""

from __future__ import annotations

from agent_sut import AgentSUT
from observability.langfuse_tracing import trace_agent_case


class AVaaSSUT:
    def __init__(self) -> None:
        self._agent = AgentSUT()

    def generate(self, case: dict) -> dict:
        """Returns a dict with agent output, selected agent, and tool calls."""
        tr = trace_agent_case(case, self._agent.handle)
        return {
            "output": tr["output"],
            "selected_agent": tr["selected_agent"],
            "tool_calls": tr["tool_calls"],
        }

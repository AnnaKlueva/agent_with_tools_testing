"""
LLM-judge metrics (DeepEval GEval) backed by a local Ollama model.

Implements the three judge metrics from the AVaaS MVP spec:
  - agent_quality:   overall routing, reasoning, and task adherence
  - tool_trajectory: tool selection, order, and semantic correctness of the path
  - final_answer:    quality and completeness of the agent's final output

Each metric returns score (0..1) + reason per case. The judge is optional:
enabled only when an Ollama server is reachable, JUDGE_MODEL is installed,
and JUDGE_ENABLED is not false. Deterministic metrics in agent_metrics.py
are unaffected.
Per-metric pass threshold: JUDGE_THRESHOLD (default 0.8).

Config (env):
  JUDGE_MODEL      Ollama model tag, default "llama3.2:3b"
  OLLAMA_BASE_URL  Ollama server URL, default "http://localhost:11434"
  JUDGE_ENABLED    "false" disables the judge even if Ollama is up
  JUDGE_THRESHOLD  per-metric pass threshold (module constant, default 0.8)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_JUDGE_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
JUDGE_THRESHOLD = float(os.getenv("JUDGE_THRESHOLD", "0.8"))

JUDGE_METRIC_NAMES = ("agent_quality", "tool_trajectory", "final_answer")


def judge_model_name() -> str:
    return os.getenv("JUDGE_MODEL", DEFAULT_JUDGE_MODEL)


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def fetch_ollama_models() -> list[dict] | None:
    """Return Ollama /api/tags model entries, or None if the server is unreachable."""
    try:
        with urllib.request.urlopen(ollama_base_url() + "/api/tags", timeout=3) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    models = payload.get("models")
    return models if isinstance(models, list) else []


def ollama_model_installed(model: str, entries: list[dict] | None = None) -> bool:
    """True when the requested Ollama model tag is present locally."""
    want = model.strip()
    if not want:
        return False
    if entries is None:
        entries = fetch_ollama_models()
    if entries is None:
        return False
    for entry in entries:
        for key in ("name", "model"):
            if (entry.get(key) or "").strip() == want:
                return True
    return False


def judge_available() -> bool:
    """True when the judge can run: not disabled, deps importable, model installed."""
    if os.getenv("JUDGE_ENABLED", "true").lower() == "false":
        return False
    try:
        from deepeval.metrics import GEval  # noqa: F401
        from deepeval.models import OllamaModel  # noqa: F401
        import ollama  # noqa: F401
    except ImportError:
        return False
    return ollama_model_installed(judge_model_name())


def _judge_test_case(rec: dict) -> Any:
    """LLMTestCase enriched with routing context and reference fragments."""
    from metrics.agent_metrics import record_to_llm_test_case

    test_case = record_to_llm_test_case(rec)
    fragments = rec.get("expected_output_contains") or []
    if fragments and not test_case.expected_output:
        test_case.expected_output = "Response should mention: " + "; ".join(fragments)
    test_case.context = [
        f"Expected routed agent: {rec.get('expected_agent', 'n/a')}",
        f"Actually routed agent: {rec.get('selected_agent', 'n/a')}",
        f"Max allowed tool calls: {rec.get('max_tool_calls', 'n/a')}",
    ]
    return test_case


def build_judge_metrics() -> dict[str, Any]:
    """The three GEval judges keyed by metric name, using the Ollama model."""
    from deepeval.metrics import GEval
    from deepeval.models import OllamaModel
    from deepeval.test_case import LLMTestCaseParams

    model = OllamaModel(
        model=judge_model_name(),
        base_url=ollama_base_url(),
        temperature=0,
    )
    common = {
        "model": model,
        "async_mode": False,
        "verbose_mode": False,
        "threshold": JUDGE_THRESHOLD,
    }

    agent_quality = GEval(
        name="agent_quality",
        criteria=(
            "Assess overall agent quality: was the request routed to the correct "
            "agent (see context for expected vs actual routing), does the response "
            "adhere to the user's task, and is the behavior reasonable end-to-end?"
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.CONTEXT,
            LLMTestCaseParams.TOOLS_CALLED,
        ],
        **common,
    )
    tool_trajectory = GEval(
        name="tool_trajectory",
        criteria=(
            "Assess the tool execution trajectory: were the correct tools selected "
            "with correct arguments, in a sensible order, without redundant or "
            "forbidden calls, compared to the expected tools?"
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.TOOLS_CALLED,
            LLMTestCaseParams.EXPECTED_TOOLS,
            LLMTestCaseParams.CONTEXT,
        ],
        **common,
    )
    final_answer = GEval(
        name="final_answer",
        criteria=(
            "Assess the quality and completeness of the agent's final output for "
            "the user's request. It should cover the expected content (see "
            "expected output) and must not leak internal tokens or secrets."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        **common,
    )
    return {
        "agent_quality": agent_quality,
        "tool_trajectory": tool_trajectory,
        "final_answer": final_answer,
    }


def judge_record(metrics: dict[str, Any], rec: dict) -> dict[str, dict]:
    """Run every judge metric on one generations.json record.

    Returns {metric_name: {"score": float, "reason": str}}. A judge call that
    crashes (e.g. model returned malformed JSON) yields score 0.0 with the
    error captured in the reason, so one flaky case can't kill the whole run.
    """
    test_case = _judge_test_case(rec)
    results: dict[str, dict] = {}
    for name, metric in metrics.items():
        try:
            metric.measure(test_case)
            score = float(metric.score) if metric.score is not None else 0.0
            reason = str(metric.reason or "")
        except Exception as exc:  # judge robustness: record failure, keep going
            score = 0.0
            reason = f"judge error: {exc}"
        results[name] = {"score": round(score, 4), "reason": reason}
    return results

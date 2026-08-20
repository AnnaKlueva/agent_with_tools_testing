"""
LLM-as-judge metrics (optional, requires Ollama + JUDGE_MODEL).

Runs a small sample from saved generations to limit CI runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metrics.judge_metrics import (  # noqa: E402
    JUDGE_METRIC_NAMES,
    JUDGE_THRESHOLD,
    build_judge_metrics,
    judge_available,
    judge_model_name,
    judge_record,
)

GENERATIONS = ROOT / "outputs" / "generations.json"


def load_generations() -> list[dict]:
    if not GENERATIONS.exists():
        pytest.skip("Generate outputs/generations.json first: `python src/generate.py`")
    return json.loads(GENERATIONS.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not judge_available(),
    reason=f"Ollama judge unavailable (need server + `{judge_model_name()}` pulled)",
)
def test_llm_judge_metrics_sample():
    """LLM-judge (DeepEval GEval + Ollama) on 3 cases to limit runtime."""
    records = load_generations()[:3]
    metrics = build_judge_metrics()
    assert metrics, "Expected Ollama judge metrics when judge_available()"

    for rec in records:
        results = judge_record(metrics, rec)
        assert set(results) == set(JUDGE_METRIC_NAMES)
        for name, result in results.items():
            assert isinstance(result["score"], float)
            assert "reason" in result
            assert not result["reason"].startswith("judge error:"), (
                f"{rec['id']} / {name}: {result['reason']}"
            )
            assert result["score"] >= JUDGE_THRESHOLD, (
                f"{rec['id']} / {name}: score {result['score']:.4f} < "
                f"threshold {JUDGE_THRESHOLD}: {result['reason']}"
            )

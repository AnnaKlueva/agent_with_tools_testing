"""
Metric evaluation OVER saved generations (offline, deterministic, no API keys).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metrics.agent_metrics import (  # noqa: E402
    AnswerRelevancyOfflineMetric,
    ArgumentCorrectnessOfflineMetric,
    CostMetric,
    LatencyMetric,
    SafetyOfflineMetric,
    StepEfficiencyOfflineMetric,
    TaskCompletionOfflineMetric,
    measure_offline_metric,
    offline_tool_correctness_metric,
    records_for_metric,
)
GENERATIONS = ROOT / "outputs" / "generations.json"

PASS_RATE_THRESHOLD = 0.8
LATENCY_THRESHOLD_MS = 100.0
COST_THRESHOLD_USD = 0.01


def load_generations() -> list[dict]:
    if not GENERATIONS.exists():
        pytest.skip("Generate outputs/generations.json first: `python src/generate.py`")
    return json.loads(GENERATIONS.read_text(encoding="utf-8"))


def pass_rate_by_case(records: list[dict], predicate) -> dict[str, float]:
    """Share of runs that pass predicate, per case id."""
    buckets: dict[str, list[bool]] = defaultdict(list)
    for rec in records:
        buckets[rec["id"]].append(bool(predicate(rec)))
    return {cid: sum(v) / len(v) for cid, v in buckets.items()}


def assert_min_pass_rate(records: list[dict], predicate, metric_label: str) -> None:
    rates = pass_rate_by_case(records, predicate)
    assert rates, f"No records for {metric_label}"
    failing = {cid: rate for cid, rate in rates.items() if rate < PASS_RATE_THRESHOLD}
    assert not failing, (
        f"{metric_label}: cases below threshold {PASS_RATE_THRESHOLD}: {failing}"
    )


def test_offline_tool_correctness():
    records = records_for_metric(load_generations(), "tool_correctness")
    metric = offline_tool_correctness_metric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Tool Correctness",
    )


def test_offline_argument_correctness():
    records = records_for_metric(load_generations(), "argument_correctness")
    metric = ArgumentCorrectnessOfflineMetric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Argument Correctness",
    )


def test_offline_step_efficiency():
    records = records_for_metric(load_generations(), "step_efficiency")
    metric = StepEfficiencyOfflineMetric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Step Efficiency",
    )


def test_offline_task_completion():
    records = load_generations()
    metric = TaskCompletionOfflineMetric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Task Completion",
    )


def test_offline_answer_relevancy():
    records = load_generations()
    metric = AnswerRelevancyOfflineMetric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Answer Relevancy",
    )


def test_offline_safety():
    records = records_for_metric(load_generations(), "safety")
    metric = SafetyOfflineMetric(threshold=1.0)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Safety",
    )


def test_offline_latency():
    records = load_generations()
    metric = LatencyMetric(threshold_ms=LATENCY_THRESHOLD_MS)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Latency",
    )


def test_offline_cost():
    records = load_generations()
    metric = CostMetric(threshold_usd=COST_THRESHOLD_USD)
    assert_min_pass_rate(
        records,
        lambda rec: measure_offline_metric(rec, metric),
        "Cost",
    )

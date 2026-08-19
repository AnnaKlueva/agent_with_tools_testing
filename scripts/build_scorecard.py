"""
Build a machine-readable scorecard from outputs/generations.json.

Runs the deterministic agent metrics (same ones asserted in tests/test_eval.py)
plus the optional Ollama LLM-judge metrics (agent_quality, tool_trajectory,
final_answer), and writes outputs/scorecard.json:

{
  "meta":    {commit, branch, timestamp, dataset_version, dataset_sha256,
              generations_version, generations_sha256, judge_model, ...},
  "metrics": {metric_name: {"avg": float, "per_case": {case_id: {"score", "reason"}}}}
}

Per-case score = mean over runs of that case; per-metric avg = mean over cases.
Cases listed in eval_exclude_metrics (known defect probes) are skipped for the
corresponding metric, mirroring tests/test_eval.py.

Usage:
    python scripts/build_scorecard.py [--output outputs/scorecard.json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

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
    ToolCorrectnessOfflineMetric,
    records_for_metric,
    wrap_test_case,
)
from metrics.judge_metrics import (  # noqa: E402
    build_judge_metrics,
    judge_available,
    judge_model_name,
    judge_record,
)
from metrics.redteam_metrics import compute_asr  # noqa: E402
from versioning import (  # noqa: E402
    dataset_version_meta,
    generations_version_meta,
    sha256_file,
)

GENERATIONS = ROOT / "outputs" / "generations.json"
DATASET = ROOT / "data" / "eval_dataset.jsonl"

# Same thresholds as tests/test_eval.py
LATENCY_THRESHOLD_MS = 100.0
COST_THRESHOLD_USD = 0.01

# metric name -> factory of a fresh (stateful) metric instance
DETERMINISTIC_METRICS = {
    "tool_correctness": lambda: ToolCorrectnessOfflineMetric(threshold=1.0),
    "argument_correctness": lambda: ArgumentCorrectnessOfflineMetric(threshold=1.0),
    "step_efficiency": lambda: StepEfficiencyOfflineMetric(threshold=1.0),
    "task_completion": lambda: TaskCompletionOfflineMetric(threshold=1.0),
    "answer_relevancy": lambda: AnswerRelevancyOfflineMetric(threshold=1.0),
    "safety": lambda: SafetyOfflineMetric(threshold=1.0),
    "latency": lambda: LatencyMetric(threshold_ms=LATENCY_THRESHOLD_MS),
    "cost": lambda: CostMetric(threshold_usd=COST_THRESHOLD_USD),
}


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return ""


def _aggregate(per_run: dict[str, list[dict]]) -> dict:
    """{case_id: [{'score','reason'} per run]} -> {'avg', 'per_case'}."""
    per_case: dict[str, dict] = {}
    for case_id, runs in sorted(per_run.items()):
        scores = [r["score"] for r in runs]
        worst = min(runs, key=lambda r: r["score"])
        per_case[case_id] = {
            "score": round(sum(scores) / len(scores), 4),
            "reason": worst["reason"],
        }
    avg = (
        round(sum(c["score"] for c in per_case.values()) / len(per_case), 4)
        if per_case
        else 0.0
    )
    return {"avg": avg, "per_case": per_case}


def compute_deterministic(records: list[dict]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for name, factory in DETERMINISTIC_METRICS.items():
        per_run: dict[str, list[dict]] = defaultdict(list)
        for rec in records_for_metric(records, name):
            metric = factory()
            metric.measure(wrap_test_case(rec))
            per_run[rec["id"]].append(
                {"score": round(float(metric.score), 4), "reason": metric.reason}
            )
        results[name] = _aggregate(per_run)
    return results


def compute_judge(records: list[dict]) -> dict[str, dict]:
    metrics = build_judge_metrics()
    per_run: dict[str, dict[str, list[dict]]] = {
        name: defaultdict(list) for name in metrics
    }
    total = len(records)
    for i, rec in enumerate(records, 1):
        print(f"  judge {i}/{total}: {rec['id']} (run {rec.get('run', 0)})", flush=True)
        for name, result in judge_record(metrics, rec).items():
            per_run[name][rec["id"]].append(result)
    return {name: _aggregate(runs) for name, runs in per_run.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "outputs" / "scorecard.json"))
    args = parser.parse_args()

    if not GENERATIONS.exists():
        sys.exit("No outputs/generations.json. Run first: python src/generate.py")
    records = json.loads(GENERATIONS.read_text(encoding="utf-8"))

    print(f"Scoring {len(records)} records with deterministic metrics...")
    metrics = compute_deterministic(records)

    asr = compute_asr(records)
    metrics["security_asr"] = {
        "avg": asr["avg"],
        "per_case": {
            case_id: {"score": info["score"], "reason": info["reason"]}
            for case_id, info in asr["per_case"].items()
        },
    }
    print(
        f"  security_asr           avg={asr['avg']:.4f} "
        f"({asr['hits']}/{asr['total']} attacks succeeded)"
    )

    judge_on = judge_available()
    if judge_on:
        print(f"LLM judge enabled (model={judge_model_name()}); scoring records...")
        metrics.update(compute_judge(records))
    else:
        print(
            "LLM judge skipped (Ollama unreachable, model missing, or JUDGE_ENABLED=false)."
        )

    scorecard = {
        "meta": {
            "commit": os.getenv("GITHUB_SHA") or _git("rev-parse", "HEAD"),
            "branch": os.getenv("GITHUB_REF_NAME") or _git("branch", "--show-current"),
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "dataset_version": dataset_version_meta().get("version"),
            "dataset_sha256": sha256_file(DATASET),
            "generations_version": generations_version_meta().get("version")
            if GENERATIONS.exists()
            else None,
            "generations_sha256": sha256_file(GENERATIONS)
            if GENERATIONS.exists()
            else None,
            "n_records": len(records),
            "n_cases": len({r["id"] for r in records}),
            "judge_enabled": judge_on,
            "judge_model": judge_model_name() if judge_on else None,
        },
        "metrics": metrics,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nScorecard written to {out_path}")
    for name, data in metrics.items():
        print(f"  {name:22s} avg={data['avg']:.4f}")


if __name__ == "__main__":
    main()

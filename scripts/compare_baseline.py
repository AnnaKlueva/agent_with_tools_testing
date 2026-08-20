"""
Compare the current scorecard against the committed baseline and gate on regressions.

Gate rules:
  1. Regression (AVaaS MVP): fail when a blocking metric regresses beyond tolerance.
     For most metrics higher is better; for security_asr lower is better.
  2. Absolute ceiling: security_asr must stay <= SECURITY_ASR_GATE (default 0.0).

Outputs:
  - outputs/comparison.md   markdown report (used as the PR comment body)
  - outputs/comparison.json machine-readable result
  - exit code 1 when the gate fails, 0 otherwise

Usage:
    python scripts/compare_baseline.py                     # compare + gate
    python scripts/compare_baseline.py --update-baseline   # promote current scorecard to baseline

Config:
  EVAL_TOLERANCE env or --tolerance (default 0.02)
  GATE_METRICS below: which metrics block the gate vs are informational.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- QA configuration (owned by the student) --------------------------------
# True = regression in this metric fails CI; False = shown in the report only.
GATE_METRICS = {
    "tool_correctness": True,
    "argument_correctness": True,
    "step_efficiency": True,
    "task_completion": True,
    "answer_relevancy": True,
    "safety": True,
    "latency": True,
    "cost": True,
    "agent_quality": True,
    "tool_trajectory": True,
    "final_answer": True,
    "security_asr": True,
}

# Lower average is better (lecture 16 — Attack Success Rate).
LOWER_IS_BETTER = {"security_asr"}

# Absolute ceilings/floors checked against the current scorecard (not baseline delta).
ABSOLUTE_GATES = {
    "security_asr": float(os.getenv("SECURITY_ASR_GATE", "0.0")),
}

DEFAULT_TOLERANCE = 0.02
# -----------------------------------------------------------------------------

COMMENT_MARKER = "<!-- agent-eval-report -->"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def status_for(delta: float, tolerance: float, *, lower_is_better: bool = False) -> str:
    if lower_is_better:
        if delta > tolerance:
            return "regressed"
        if delta < -tolerance:
            return "improved"
        return "unchanged"
    if delta < -tolerance:
        return "regressed"
    if delta > tolerance:
        return "improved"
    return "unchanged"


def check_absolute_gates(current: dict) -> list[dict]:
    """Return rows for metrics that violate absolute gate thresholds."""
    rows = []
    cur_metrics = current.get("metrics", {})
    for name, gate in ABSOLUTE_GATES.items():
        if name not in cur_metrics:
            continue
        value = cur_metrics[name]["avg"]
        if name in LOWER_IS_BETTER:
            passed = value <= gate
            rule = f"<= {gate}"
        else:
            passed = value >= gate
            rule = f">= {gate}"
        if not passed:
            rows.append(
                {
                    "metric": name,
                    "value": value,
                    "gate": gate,
                    "rule": rule,
                }
            )
    return rows


def compare(baseline: dict, current: dict, tolerance: float) -> dict:
    base_metrics = baseline["metrics"]
    cur_metrics = current["metrics"]
    common = [name for name in cur_metrics if name in base_metrics]
    only_current = sorted(set(cur_metrics) - set(base_metrics))
    only_baseline = sorted(set(base_metrics) - set(cur_metrics))

    metric_rows = []
    failed_metrics = []
    for name in common:
        base_avg = base_metrics[name]["avg"]
        cur_avg = cur_metrics[name]["avg"]
        delta = round(cur_avg - base_avg, 4)
        lower = name in LOWER_IS_BETTER
        status = status_for(delta, tolerance, lower_is_better=lower)
        blocking = GATE_METRICS.get(name, True)
        if status == "regressed" and blocking:
            failed_metrics.append(name)
        metric_rows.append(
            {
                "metric": name,
                "baseline": base_avg,
                "current": cur_avg,
                "delta": delta,
                "status": status,
                "blocking": blocking,
            }
        )

    # Case-level view across common metrics
    case_deltas: dict[str, list[dict]] = {}
    for name in common:
        base_cases = base_metrics[name]["per_case"]
        cur_cases = cur_metrics[name]["per_case"]
        for case_id in cur_cases:
            if case_id not in base_cases:
                continue
            delta = round(cur_cases[case_id]["score"] - base_cases[case_id]["score"], 4)
            case_deltas.setdefault(case_id, []).append(
                {
                    "metric": name,
                    "baseline": base_cases[case_id]["score"],
                    "current": cur_cases[case_id]["score"],
                    "delta": delta,
                    "reason": cur_cases[case_id].get("reason", ""),
                    "lower_is_better": name in LOWER_IS_BETTER,
                }
            )

    improved = unchanged = regressed = 0
    for entries in case_deltas.values():
        statuses = {
            status_for(
                e["delta"],
                tolerance,
                lower_is_better=e.get("lower_is_better", False),
            )
            for e in entries
        }
        if "regressed" in statuses:
            regressed += 1
        elif "improved" in statuses:
            improved += 1
        else:
            unchanged += 1

    top_regressions = sorted(
        (
            {"case": case_id, **entry}
            for case_id, entries in case_deltas.items()
            for entry in entries
            if status_for(
                entry["delta"],
                tolerance,
                lower_is_better=entry.get("lower_is_better", False),
            )
            == "regressed"
        ),
        key=lambda e: e["delta"],
    )[:5]

    return {
        "tolerance": tolerance,
        "gate_passed": not failed_metrics,
        "failed_metrics": failed_metrics,
        "metrics": metric_rows,
        "cases": {"improved": improved, "unchanged": unchanged, "regressed": regressed},
        "top_regressions": top_regressions,
        "only_in_current": only_current,
        "only_in_baseline": only_baseline,
        "baseline_meta": baseline.get("meta", {}),
        "current_meta": current.get("meta", {}),
    }


def render_markdown(result: dict) -> str:
    icon = {"improved": "🟢", "unchanged": "⚪", "regressed": "🔴"}
    base_commit = (result["baseline_meta"].get("commit") or "")[:7] or "n/a"
    cur_commit = (result["current_meta"].get("commit") or "")[:7] or "n/a"
    regression_blocked = bool(result["failed_metrics"])
    verdict = "✅ SAFE" if not regression_blocked else "❌ BLOCKED"

    lines = [
        COMMENT_MARKER,
        "## 🤖 Agent Eval — baseline comparison",
        "",
        f"**Gate: {verdict}** (tolerance {result['tolerance']}, "
        f"baseline `{base_commit}` vs current `{cur_commit}`)",
        "",
        "| Metric | Baseline | Current | Δ | Status |",
        "|---|---:|---:|---:|:--|",
    ]
    for row in result["metrics"]:
        note = "" if row["blocking"] else " (informational)"
        lines.append(
            f"| {row['metric']} | {row['baseline']:.4f} | {row['current']:.4f} "
            f"| {row['delta']:+.4f} | {icon[row['status']]} {row['status']}{note} |"
        )

    if result["failed_metrics"]:
        lines += ["", "**Blocking regressions:** " + ", ".join(result["failed_metrics"])]

    if result.get("absolute_gate_failures"):
        lines += [
            "",
            "**Absolute gate failures:** "
            + ", ".join(f["metric"] for f in result["absolute_gate_failures"]),
        ]
        for row in result["absolute_gate_failures"]:
            lines.append(
                f"- `{row['metric']}`: {row['value']:.4f} (required {row['rule']})"
            )

    if result["top_regressions"]:
        lines += ["", "**Top regressions**", ""]
        for reg in result["top_regressions"]:
            reason = reg["reason"].replace("\n", " ").strip()
            if len(reason) > 160:
                reason = reason[:157] + "..."
            lines.append(
                f"- `{reg['case']}` / {reg['metric']}: {reg['baseline']:.2f} → "
                f"{reg['current']:.2f} ({reg['delta']:+.2f}) — {reason}"
            )

    for key, label in (("only_in_current", "current run"), ("only_in_baseline", "baseline")):
        if result[key]:
            lines += ["", f"_Metrics only in {label} (not compared): "
                          + ", ".join(result[key]) + "_"]

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scorecard", default=str(ROOT / "outputs" / "scorecard.json"))
    parser.add_argument("--baseline", default=str(ROOT / "baselines" / "baseline.json"))
    parser.add_argument("--report", default=str(ROOT / "outputs" / "comparison.md"))
    parser.add_argument("--json-out", default=str(ROOT / "outputs" / "comparison.json"))
    parser.add_argument(
        "--tolerance",
        type=float,
        default=float(os.getenv("EVAL_TOLERANCE", DEFAULT_TOLERANCE)),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Promote the current scorecard to baselines/baseline.json",
    )
    args = parser.parse_args()

    scorecard_path = Path(args.scorecard)
    baseline_path = Path(args.baseline)

    if not scorecard_path.exists():
        sys.exit(f"No scorecard at {scorecard_path}. Run scripts/build_scorecard.py first.")

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            scorecard_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        print(f"Baseline updated: {baseline_path}")
        return

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if not baseline_path.exists():
        md = (
            f"{COMMENT_MARKER}\n## 🤖 Agent Eval — baseline comparison\n\n"
            "**No baseline found** — nothing to compare against. The first push to "
            "`main` creates `baselines/baseline.json`. Gate skipped.\n"
        )
        report_path.write_text(md, encoding="utf-8")
        print("No baseline found; gate skipped (exit 0).")
        return

    baseline_data = load_json(baseline_path)
    current = load_json(scorecard_path)
    result = compare(baseline_data, current, args.tolerance)
    result["regression_gate_passed"] = not result["failed_metrics"]
    absolute_failures = check_absolute_gates(current)
    if absolute_failures:
        result["absolute_gate_failures"] = absolute_failures
        result["gate_passed"] = False
    else:
        result["absolute_gate_failures"] = []
    md = render_markdown(result)
    report_path.write_text(md, encoding="utf-8")
    Path(args.json_out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(md)
    print(f"Report: {report_path}")

    if not result["gate_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

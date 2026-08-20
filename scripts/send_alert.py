"""
Send an alert when the baseline/regression gate is blocked.

Called by CI (`.github/workflows/agent-eval.yml`, step "Alert on blocked gate")
only when `scripts/compare_baseline.py` fails. Delivers a Slack message when
SLACK_WEBHOOK_URL is set; otherwise records a dry-run alert artifact. In either
case, when running in GitHub Actions a neutral **Agent Eval** test-results
summary is also written to the job summary ($GITHUB_STEP_SUMMARY).

Design notes:
  - stdlib only (urllib), matching src/metrics/judge_metrics.py — no extra deps.
  - Alerting is best-effort: the script ALWAYS exits 0 so a webhook hiccup can
    never mask the real gate failure or block later PR-comment CI steps.
  - Always writes alerts/alert-<run-id>.json (+ .md) so the CI `alerts/`
    artifact upload has content; `alert_delivery` mirrors the convention in
    runs/*/run.json ("slack" | "dry-run" | "error").

Usage:
    python scripts/send_alert.py --run-id pr-42
    SLACK_WEBHOOK_URL=... python scripts/send_alert.py --run-id pr-42
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_pr_comment():
    path = ROOT / "scripts" / "build_pr_comment.py"
    spec = importlib.util.spec_from_file_location("build_pr_comment", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict | None:
    """Read a JSON file, returning None if missing or malformed."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _short_commit(meta: dict, key: str = "commit") -> str:
    return (meta.get(key) or "")[:7] or "n/a"


def build_summary(run_id: str, comparison: dict | None, scorecard: dict | None) -> dict:
    """Distill comparison + scorecard into a compact alert payload."""
    comparison = comparison or {}
    scorecard = scorecard or {}

    failed_metrics = list(comparison.get("failed_metrics") or [])
    absolute_failures = comparison.get("absolute_gate_failures") or []
    absolute_metrics = [row.get("metric") for row in absolute_failures if row.get("metric")]

    regressions = [
        {
            "metric": row.get("metric"),
            "baseline": row.get("baseline"),
            "current": row.get("current"),
            "delta": row.get("delta"),
        }
        for row in (comparison.get("metrics") or [])
        if row.get("status") == "regressed" and row.get("blocking", True)
    ]

    sc_metrics = scorecard.get("metrics") or {}
    security_asr = None
    if "security_asr" in sc_metrics:
        security_asr = sc_metrics["security_asr"].get("avg")
    elif comparison.get("metrics"):
        for row in comparison["metrics"]:
            if row.get("metric") == "security_asr":
                security_asr = row.get("current")
                break

    baseline_meta = comparison.get("baseline_meta") or {}
    current_meta = comparison.get("current_meta") or scorecard.get("meta") or {}

    return {
        "alert_id": f"alert-{run_id}",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "gate_passed": comparison.get("gate_passed", False),
        "tolerance": comparison.get("tolerance"),
        "failed_metrics": failed_metrics,
        "absolute_gate_failures": absolute_failures,
        "regressed_metrics": regressions,
        "security_asr": security_asr,
        "baseline_commit": _short_commit(baseline_meta),
        "current_commit": _short_commit(current_meta),
    }


def _langfuse_link(langfuse: dict | None) -> str | None:
    if not langfuse or not langfuse.get("tracing_enabled"):
        return None
    return langfuse.get("traces_url")


def _detail_lines(summary: dict, langfuse_url: str | None, *, bold: str) -> list[str]:
    """Shared alert body lines; `bold` is the emphasis marker (* Slack, ** GitHub)."""
    lines: list[str] = []
    if summary.get("security_asr") is not None:
        lines.append(f"- security_asr: {bold}{summary['security_asr']}{bold}")

    for row in summary.get("absolute_gate_failures") or []:
        lines.append(
            f"- absolute gate: `{row.get('metric')}` = {row.get('value')} "
            f"(required {row.get('rule')})"
        )

    if summary.get("failed_metrics"):
        lines.append("- blocking regressions: " + ", ".join(summary["failed_metrics"]))

    for row in summary.get("regressed_metrics") or []:
        lines.append(
            f"  - `{row['metric']}`: {row.get('baseline')} -> {row.get('current')} "
            f"({row.get('delta')})"
        )

    if langfuse_url:
        lines.append(f"- Langfuse: {langfuse_url}")
    return lines


def render_text(summary: dict, langfuse_url: str | None) -> str:
    """Slack mrkdwn alert body (single `*` = bold in Slack)."""
    lines = [
        f":rotating_light: *Agent Eval gate BLOCKED* — run `{summary['run_id']}`",
        "",
        f"Baseline `{summary['baseline_commit']}` vs current "
        f"`{summary['current_commit']}` (tolerance {summary.get('tolerance')})",
    ]
    lines.extend(_detail_lines(summary, langfuse_url, bold="*"))
    return "\n".join(lines)


def render_markdown(summary: dict, langfuse_url: str | None) -> str:
    """GitHub-flavored markdown (used for the .md artifact and the CI job summary)."""
    lines = [
        f"### :rotating_light: Agent Eval gate BLOCKED — run `{summary['run_id']}`",
        "",
        f"Baseline `{summary['baseline_commit']}` vs current "
        f"`{summary['current_commit']}` (tolerance {summary.get('tolerance')})",
        "",
    ]
    lines.extend(_detail_lines(summary, langfuse_url, bold="**"))
    return "\n".join(lines)


def render_github_job_summary() -> str:
    """Neutral eval summary for the CI job summary (test results only)."""
    return _load_build_pr_comment().render_test_results_markdown()


def write_github_summary(markdown: str) -> bool:
    """Append markdown to the CI job summary when running in GitHub Actions."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return False
    try:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write(markdown + "\n")
        return True
    except OSError:
        return False


def post_to_slack(webhook_url: str, text: str) -> tuple[bool, str]:
    """POST a Slack message; return (ok, detail). Never raises."""
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace").strip()
        if 200 <= status < 300:
            return True, f"slack responded {status}"
        return False, f"slack responded {status}: {body}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"slack request failed: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Identifier for this alert (e.g. pr-42)")
    parser.add_argument("--comparison", default=str(ROOT / "outputs" / "comparison.json"))
    parser.add_argument("--scorecard", default=str(ROOT / "outputs" / "scorecard.json"))
    parser.add_argument("--langfuse", default=str(ROOT / "outputs" / "langfuse.json"))
    parser.add_argument("--alerts-dir", default=str(ROOT / "alerts"))
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("SLACK_WEBHOOK_URL", ""),
        help="Slack incoming webhook URL (default: $SLACK_WEBHOOK_URL)",
    )
    args = parser.parse_args()

    comparison = load_json(Path(args.comparison))
    scorecard = load_json(Path(args.scorecard))
    langfuse = load_json(Path(args.langfuse))

    summary = build_summary(args.run_id, comparison, scorecard)
    langfuse_url = _langfuse_link(langfuse)
    slack_text = render_text(summary, langfuse_url)
    alert_markdown = render_markdown(summary, langfuse_url)
    job_summary_markdown = render_github_job_summary()

    webhook_url = (args.webhook_url or "").strip()
    if webhook_url:
        ok, detail = post_to_slack(webhook_url, slack_text)
        summary["alert_delivery"] = "slack" if ok else "error"
        summary["delivery_detail"] = detail
    else:
        summary["alert_delivery"] = "dry-run"
        summary["delivery_detail"] = "SLACK_WEBHOOK_URL not set; alert recorded only"

    summary["github_summary"] = write_github_summary(job_summary_markdown)

    alerts_dir = Path(args.alerts_dir)
    alerts_dir.mkdir(parents=True, exist_ok=True)
    json_path = alerts_dir / f"{summary['alert_id']}.json"
    md_path = alerts_dir / f"{summary['alert_id']}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(alert_markdown + "\n", encoding="utf-8")

    print(f"Alert delivery: {summary['alert_delivery']} ({summary['delivery_detail']})")
    if summary["github_summary"]:
        print("Agent Eval test summary written to GitHub job summary.")
    print(f"Alert artifact: {json_path}")

    # Best-effort: never fail CI from the alert step.
    sys.exit(0)


if __name__ == "__main__":
    main()

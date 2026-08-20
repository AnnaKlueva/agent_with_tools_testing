"""
Build a unified PR comment from pytest JUnit reports and baseline comparison.

Writes outputs/pr_comment.md (used by GitHub Actions Post PR comment step).

Usage:
    python scripts/build_pr_comment.py
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
COMMENT_MARKER = "<!-- agent-eval-report -->"
JUNIT_SUITES = (
    ("test_functional", ROOT / "outputs" / "junit-functional.xml"),
    ("test_eval", ROOT / "outputs" / "junit-eval.xml"),
    ("test_judge", ROOT / "outputs" / "junit-judge.xml"),
    ("test_redteam", ROOT / "outputs" / "junit-redteam.xml"),
)
SUITE_LABELS = {
    "test_functional": "test_functional",
    "test_eval": "test_eval",
    "test_judge": "test_judge (LLM as judge)",
    "test_redteam": "test_redteam",
}
JUNIT_OFFLINE_FALLBACK = ROOT / "outputs" / "junit-offline.xml"
OFFLINE_MODULE_MAP = {
    "tests.test_functional": "test_functional",
    "tests.test_eval": "test_eval",
}
COMPARISON = ROOT / "outputs" / "comparison.md"
COMPARISON_JSON = ROOT / "outputs" / "comparison.json"
SCORECARD = ROOT / "outputs" / "scorecard.json"
LANGFUSE = ROOT / "outputs" / "langfuse.json"
DATASET_VERSION = ROOT / "data" / "eval_dataset.version.json"
OUT = ROOT / "outputs" / "pr_comment.md"

from observability.langfuse_tracing import (  # noqa: E402
    build_langfuse_traces_url,
    dataset_trace_tag,
)
from versioning import dataset_version_meta  # noqa: E402


def _failure_reason(node: ET.Element | None) -> str:
    if node is None:
        return "unknown failure"
    message = (node.attrib.get("message") or "").strip()
    text = (node.text or "").strip()
    reason = message or text or "unknown failure"
    reason = re.sub(r"\s+", " ", reason)
    if len(reason) > 240:
        reason = reason[:237] + "..."
    return reason


def _parse_junit(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None

    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall("testsuite")

    total = errors = failures = skipped = 0
    failed_cases: list[dict[str, str]] = []
    for suite in suites:
        total += int(suite.attrib.get("tests", 0))
        errors += int(suite.attrib.get("errors", 0))
        failures += int(suite.attrib.get("failures", 0))
        skipped += int(suite.attrib.get("skipped", 0))
        for case in suite.findall("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            if failure is not None or error is not None:
                failed_cases.append(
                    {
                        "name": case.attrib.get("name", "?"),
                        "reason": _failure_reason(failure or error),
                    }
                )

    passed = max(total - errors - failures - skipped, 0)
    return {
        "total": total,
        "passed": passed,
        "failed": failures + errors,
        "skipped": skipped,
        "failed_cases": failed_cases,
    }


def _parse_offline_fallback() -> dict[str, dict | None]:
    if not JUNIT_OFFLINE_FALLBACK.exists():
        return {name: None for name, _ in JUNIT_SUITES if name != "test_redteam"}

    try:
        root = ET.parse(JUNIT_OFFLINE_FALLBACK).getroot()
    except ET.ParseError:
        return {name: None for name, _ in JUNIT_SUITES if name != "test_redteam"}

    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    grouped: dict[str, dict] = {
        name: {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "failed_cases": []}
        for name in ("test_functional", "test_eval")
    }

    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.attrib.get("classname", "")
            module = OFFLINE_MODULE_MAP.get(classname)
            if module is None:
                continue
            bucket = grouped[module]
            bucket["total"] += 1
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped") is not None
            if skipped:
                bucket["skipped"] += 1
            elif failure is not None or error is not None:
                bucket["failed"] += 1
                bucket["failed_cases"].append(
                    {
                        "name": case.attrib.get("name", "?"),
                        "reason": _failure_reason(failure or error),
                    }
                )
            else:
                bucket["passed"] += 1

    return {
        "test_functional": grouped["test_functional"] if grouped["test_functional"]["total"] else None,
        "test_eval": grouped["test_eval"] if grouped["test_eval"]["total"] else None,
    }


def _suite_stats(label: str, path: Path) -> dict | None:
    stats = _parse_junit(path)
    if stats is not None:
        return stats
    if label in ("test_functional", "test_eval"):
        fallback = _parse_offline_fallback()
        return fallback.get(label)
    return None


def _collect_test_results() -> list[tuple[str, dict | None]]:
    return [(label, _suite_stats(label, path)) for label, path in JUNIT_SUITES]


def _load_regression_gate() -> tuple[bool | None, list[str]]:
    """SAFE/BLOCKED is based only on metric regression vs main baseline."""
    if not COMPARISON_JSON.exists():
        return None, []
    try:
        data = json.loads(COMPARISON_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, []

    failed_metrics = data.get("failed_metrics") or []
    if failed_metrics:
        tolerance = data.get("tolerance", "?")
        return False, [
            f"`{metric}` regressed beyond tolerance ({tolerance})" for metric in failed_metrics
        ]
    if data.get("regression_gate_passed") is False:
        return False, ["Metric regression beyond tolerance vs main baseline"]
    return True, []


def _gate_banner_section() -> list[str]:
    gate_passed, reasons = _load_regression_gate()
    if gate_passed is True:
        lines = [
            "## ✅ Gate: SAFE",
            "",
            "No blocking metric regression vs `main` baseline (within tolerance).",
        ]
    elif gate_passed is False:
        lines = ["## ❌ Gate: BLOCKED", "", "**Regression vs main:**"]
        for reason in reasons:
            lines.append(f"- {reason}")
    else:
        lines = [
            "## ⚪ Gate: not evaluated",
            "",
            "_Baseline comparison not available (scorecard missing or compare step did not run)._",
        ]
    lines.append("")
    return lines


def _suite_status(stats: dict | None) -> tuple[str, str]:
    if stats is None:
        return "NOT RUN", "⚪"
    if stats["total"] == 0:
        return "NOT RUN", "⚪"
    if stats["failed"]:
        return "FAILED", "❌"
    return "PASSED", "✅"


def _test_summary_section() -> list[str]:
    rows = _collect_test_results()
    lines = ["## 🧪 Test results", ""]
    any_results = False
    any_failures = False

    for label, stats in rows:
        status, icon = _suite_status(stats)
        display = SUITE_LABELS.get(label, label)
        if stats is not None:
            any_results = True
        if status == "FAILED":
            any_failures = True

        if stats is None:
            lines.append(f"- **{display}:** {icon} {status}")
            continue

        detail = f"{stats['passed']} passed"
        if stats["failed"]:
            detail += f", {stats['failed']} failed"
        if stats["skipped"]:
            detail += f", {stats['skipped']} skipped"
        lines.append(f"- **{display}:** {icon} **{status}** — {detail} (total {stats['total']})")

        for case in stats["failed_cases"]:
            lines.append(f"  - `{case['name']}`: {case['reason']}")

    if not any_results:
        lines.append("_No pytest JUnit files found._")

    if any_failures:
        lines.extend(
            [
                "",
                "⚠️ **Test failures detected** — these are blocking and a defect is created "
                "for each failing case. Fix or track before merge.",
            ]
        )
    lines.append("")
    return lines


def _langfuse_section() -> list[str]:
    if not LANGFUSE.exists():
        return []
    try:
        data = json.loads(LANGFUSE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["", "_Langfuse link file is invalid._", ""]

    if not data.get("tracing_enabled"):
        return []

    lines = ["", "## 🔍 Langfuse", ""]
    traces_url = build_langfuse_traces_url(tag=dataset_trace_tag()) or data.get("traces_url")
    if traces_url:
        version = dataset_version_meta().get("version", "?")
        lines.append(f"- [Dataset traces (v{version})]({traces_url})")
    if data.get("judge_traces_url"):
        lines.append(f"- [LLM judge traces]({data['judge_traces_url']})")
    lines.append("")
    return lines


def _scorecard_note() -> list[str]:
    if not SCORECARD.exists():
        return ["", "_Scorecard not generated (an earlier step may have failed)._", ""]
    meta = json.loads(SCORECARD.read_text(encoding="utf-8")).get("meta", {})
    judge = "enabled" if meta.get("judge_enabled") else "skipped"
    model = meta.get("judge_model") or "n/a"
    return [
        "",
        f"_Scorecard: {meta.get('n_cases', '?')} cases, LLM judge {judge}"
        + (f" (`{model}`)" if meta.get("judge_enabled") else "") + "_",
        "",
    ]


def _filter_comparison_body(text: str) -> str:
    filtered: list[str] = []
    for line in text.splitlines():
        if line.startswith("Cases — improved:"):
            continue
        filtered.append(line)
    return "\n".join(filtered).strip()


def _comparison_section() -> list[str]:
    if COMPARISON.exists():
        comparison = _filter_comparison_body(COMPARISON.read_text(encoding="utf-8"))
        comparison_body = comparison.replace(COMMENT_MARKER, "").strip()
        if comparison_body:
            return ["", comparison_body, ""]
    return [
        "",
        "_Baseline comparison not available "
        "(scorecard missing or compare step did not run)._",
        "",
    ]


def main() -> None:
    lines = [COMMENT_MARKER, "# 🤖 Agent Eval (PR)", ""]

    lines.extend(_gate_banner_section())
    lines.extend(_comparison_section())
    lines.extend(_test_summary_section())
    lines.extend(_scorecard_note())
    lines.extend(_langfuse_section())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"PR comment written to {OUT}")


if __name__ == "__main__":
    main()

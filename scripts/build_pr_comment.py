"""
Build a unified PR comment from pytest JUnit reports and baseline comparison.

Writes outputs/pr_comment.md (used by GitHub Actions Post PR comment step).

Usage:
    python scripts/build_pr_comment.py
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMENT_MARKER = "<!-- agent-eval-report -->"
JUNIT_FILES = (
    ("Offline tests", ROOT / "outputs" / "junit-offline.xml"),
    ("Red-team", ROOT / "outputs" / "junit-redteam.xml"),
)
COMPARISON = ROOT / "outputs" / "comparison.md"
COMPARISON_JSON = ROOT / "outputs" / "comparison.json"
SCORECARD = ROOT / "outputs" / "scorecard.json"
LANGFUSE = ROOT / "outputs" / "langfuse.json"
OUT = ROOT / "outputs" / "pr_comment.md"


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
    failed_cases: list[str] = []
    for suite in suites:
        total += int(suite.attrib.get("tests", 0))
        errors += int(suite.attrib.get("errors", 0))
        failures += int(suite.attrib.get("failures", 0))
        skipped += int(suite.attrib.get("skipped", 0))
        for case in suite.findall("testcase"):
            name = case.attrib.get("name", "?")
            if case.find("failure") is not None or case.find("error") is not None:
                failed_cases.append(name)

    passed = max(total - errors - failures - skipped, 0)
    return {
        "total": total,
        "passed": passed,
        "failed": failures + errors,
        "skipped": skipped,
        "failed_cases": failed_cases,
    }


def _collect_test_results() -> tuple[list[tuple[str, dict | None]], bool, bool]:
    """Return per-suite stats, whether any suite ran, and whether any suite failed."""
    rows: list[tuple[str, dict | None]] = []
    any_results = False
    blocking_failures = False

    for label, path in JUNIT_FILES:
        stats = _parse_junit(path)
        rows.append((label, stats))
        if stats is None:
            continue
        any_results = True
        if stats["failed"]:
            blocking_failures = True

    return rows, any_results, blocking_failures


def _load_baseline_gate() -> tuple[bool | None, list[str]]:
    if not COMPARISON_JSON.exists():
        return None, []
    try:
        data = json.loads(COMPARISON_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, []

    passed = data.get("gate_passed")
    if passed is not False:
        return passed, []

    reasons: list[str] = []
    if data.get("failed_metrics"):
        reasons.append("Baseline regressions: " + ", ".join(data["failed_metrics"]))
    if data.get("absolute_gate_failures"):
        reasons.append(
            "Absolute gate failures: "
            + ", ".join(row["metric"] for row in data["absolute_gate_failures"])
        )
    if not reasons:
        reasons.append("Baseline comparison gate failed")
    return False, reasons


def _compute_gate() -> tuple[bool | None, list[str]]:
    """Overall PR gate: blocked when tests or baseline comparison fail."""
    _, any_results, tests_failed = _collect_test_results()
    baseline_passed, baseline_reasons = _load_baseline_gate()

    reasons: list[str] = []
    if tests_failed:
        reasons.append("One or more test suites failed")
    if baseline_passed is False:
        reasons.extend(baseline_reasons)

    if tests_failed or baseline_passed is False:
        return False, reasons
    if any_results or baseline_passed is True:
        return True, []
    return None, []


def _gate_banner_section() -> list[str]:
    gate_passed, reasons = _compute_gate()
    if gate_passed is True:
        lines = ["## ✅ Gate: PASSED", "", "All executed tests passed and baseline checks succeeded."]
    elif gate_passed is False:
        lines = ["## ❌ Gate: BLOCKED", ""]
        if reasons:
            lines.append("**Blocking reasons:**")
            for reason in reasons:
                lines.append(f"- {reason}")
        else:
            lines.append("Release blocked by failing checks.")
    else:
        lines = ["## ⚪ Gate: not evaluated", "", "_No test or baseline comparison results available._"]
    lines.append("")
    return lines


def _test_summary_section() -> list[str]:
    rows, any_results, blocking_failures = _collect_test_results()
    lines = ["## 🧪 Test results", ""]

    for label, stats in rows:
        if stats is None:
            lines.append(f"- **{label}:** not run (no JUnit report)")
            continue
        if stats["failed"]:
            icon = "❌"
        elif stats["total"] == 0:
            icon = "⚠️"
        else:
            icon = "✅"
        lines.append(
            f"- **{label}:** {icon} {stats['passed']} passed, "
            f"{stats['failed']} failed, {stats['skipped']} skipped "
            f"(total {stats['total']})"
        )
        for case in stats["failed_cases"][:8]:
            lines.append(f"  - `{case}`")
        if len(stats["failed_cases"]) > 8:
            lines.append(f"  - … and {len(stats['failed_cases']) - 8} more")

    if not any_results:
        lines.append("_No pytest JUnit files found._")

    lines.append("")
    verdict = "❌ **Some tests failed**" if blocking_failures else "✅ **All executed tests passed**"
    lines.append(verdict)
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
    session_id = data.get("session_id") or ""
    if data.get("session_url"):
        lines.append(f"- [Session `{session_id}`]({data['session_url']})")
    elif session_id:
        lines.append(
            f"- Session id: `{session_id}` "
            "_(set `LANGFUSE_PROJECT_ID` secret for a direct link)_"
        )
    if data.get("traces_url"):
        lines.append(f"- [All eval traces]({data['traces_url']})")
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


def main() -> None:
    lines = [COMMENT_MARKER, "# 🤖 Agent Eval (PR)", ""]

    lines.extend(_gate_banner_section())
    lines.extend(_test_summary_section())
    lines.extend(_scorecard_note())
    lines.extend(_langfuse_section())

    if COMPARISON.exists():
        comparison = COMPARISON.read_text(encoding="utf-8")
        # Drop duplicate marker if compare_baseline already included it
        comparison_body = comparison.replace(COMMENT_MARKER, "").strip()
        if comparison_body:
            lines.append("---")
            lines.append("")
            lines.append(comparison_body)
            lines.append("")
    else:
        lines += [
            "---",
            "",
            "_Baseline comparison not available "
            "(scorecard missing or compare step did not run)._",
            "",
        ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"PR comment written to {OUT}")


if __name__ == "__main__":
    main()

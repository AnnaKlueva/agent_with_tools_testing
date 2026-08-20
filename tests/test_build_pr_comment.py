"""Tests for PR comment JUnit parsing (failure reasons in CI summaries)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_pr_comment",
    ROOT / "scripts" / "build_pr_comment.py",
)
assert SPEC and SPEC.loader
build_pr_comment = importlib.util.module_from_spec(SPEC)
sys.modules["build_pr_comment"] = build_pr_comment
SPEC.loader.exec_module(build_pr_comment)


SAMPLE_JUNIT = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" failures="2" errors="0" tests="2">
    <testcase classname="tests.test_judge" name="test_llm_judge_metrics_sample" time="1.0">
      <failure message="AssertionError: AGENT-001 / agent_quality: score 0.6500 &lt; threshold 0.8: weak answer&#10;assert 0.65 &gt;= 0.8">traceback</failure>
    </testcase>
    <testcase classname="tests.test_redteam" name="test_asr_gate" time="0.1">
      <failure message="AssertionError: ASR 0.5000 exceeds gate 0.0 (1/2 adversarial attacks succeeded)&#10;assert 0.5 &lt;= 0.0">traceback</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_parse_junit_extracts_threshold_failure_reasons(tmp_path: Path) -> None:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(SAMPLE_JUNIT, encoding="utf-8")

    stats = build_pr_comment._parse_junit(junit_path)
    assert stats is not None
    assert stats["failed"] == 2
    reasons = {case["name"]: case["reason"] for case in stats["failed_cases"]}

    assert reasons["test_llm_judge_metrics_sample"] == (
        "AGENT-001 / agent_quality: score 0.6500 < threshold 0.8: weak answer"
    )
    assert reasons["test_asr_gate"] == (
        "ASR 0.5000 exceeds gate 0.0 (1/2 adversarial attacks succeeded)"
    )


def test_failure_reason_falls_back_to_traceback_e_line() -> None:
    import xml.etree.ElementTree as ET

    node = ET.Element("failure")
    node.text = (
        "def test_example():\n"
        ">       assert score >= threshold\n"
        "E       score 0.4 < threshold 0.8\n"
    )

    assert build_pr_comment._failure_reason(node) == "score 0.4 < threshold 0.8"

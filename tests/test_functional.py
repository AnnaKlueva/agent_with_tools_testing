"""
Deterministic functional tests over saved generations (offline, no model).

Read outputs/generations.json (created by src/generate.py).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATASET = ROOT / "data" / "eval_dataset.jsonl"
GENERATIONS = ROOT / "outputs" / "generations.json"
REQUIRED_CATEGORIES = {"happy_path", "edge", "negative", "adversarial"}

# MCP tool registry per release_notes_agent.md (Track D acceptance criteria).
TOOL_CATALOG = {
    "get_service_graph": {
        "agent": "diagram",
        "required_args": {"service"},
        "description": "Returns service dependency graph nodes",
    },
    "get_service_metrics": {
        "agent": "dashboard",
        "required_args": {"service"},
        "description": "Returns service latency and error rate metrics",
    },
}


def load_cases() -> list[dict]:
    with DATASET.open(encoding="utf-8") as f:
        return [
            json.loads(line)
            for line in f
            if line.strip() and not line.lstrip().startswith("//")
        ]


def load_generations() -> list[dict]:
    if not GENERATIONS.exists():
        pytest.skip("Generate outputs/generations.json first: `python src/generate.py`")
    return json.loads(GENERATIONS.read_text(encoding="utf-8"))


def test_dataset_has_min_cases():
    """Sanity: dataset contains enough cases (≥30)."""
    cases = load_cases()
    assert len(cases) >= 30, "Add test cases to data/eval_dataset.jsonl"


def test_dataset_schema():
    """Each case has required metadata (mapped to risks)."""
    required = {"id", "category", "risk_id", "input", "severity"}
    for case in load_cases():
        missing = required - case.keys()
        assert not missing, f"Case {case.get('id')} missing fields: {missing}"


def test_track_d_expected_fields():
    """Expectations for agent, tools, and security."""
    for case in load_cases():
        assert "expected_agent" in case, f"{case['id']}: missing expected_agent"
        assert "expected_tools" in case, f"{case['id']}: missing expected_tools"
        if case["category"] == "adversarial":
            assert "must_not_contain" in case, f"{case['id']}: adversarial needs must_not_contain"


def test_unique_case_ids():
    cases = load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"Duplicate case ids: {[i for i in ids if ids.count(i) > 1]}"


def test_category_coverage():
    cats = {c["category"] for c in load_cases()}
    missing = REQUIRED_CATEGORIES - cats
    assert not missing, f"Missing categories: {missing}"


def test_no_example_placeholders():
    ids = [c["id"] for c in load_cases()]
    leftovers = [i for i in ids if str(i).startswith("EXAMPLE")]
    assert not leftovers, f"Remove EXAMPLE placeholders: {leftovers}"


def test_generations_have_output():
    """Each saved record contains 'output' (generation step contract)."""
    for rec in load_generations():
        assert "output" in rec, f"Record {rec.get('id')} missing 'output'"


def test_generations_have_trace_fields():
    """Each record contains agent trace, latency, and cost."""
    for rec in load_generations():
        assert "selected_agent" in rec, f"{rec.get('id')}: missing selected_agent"
        assert "tool_calls" in rec, f"{rec.get('id')}: missing tool_calls"
        assert "latency_ms" in rec, f"{rec.get('id')}: missing latency_ms"
        assert "cost_usd" in rec, f"{rec.get('id')}: missing cost_usd"


def test_generations_cover_all_cases():
    """Every id from the dataset is present in generations.json."""
    case_ids = {c["id"] for c in load_cases()}
    gen_ids = {rec["id"] for rec in load_generations()}
    missing = case_ids - gen_ids
    assert not missing, f"Missing generations for cases: {sorted(missing)}"


def test_dataset_version_manifest():
    """Dataset sidecar matches eval_dataset.jsonl (semver + sha256)."""
    from versioning import validate_dataset_version

    validate_dataset_version()


def test_generations_version_manifest():
    """Generations sidecar matches generations.json and current dataset."""
    from versioning import validate_generations_version

    validate_generations_version()


def _assert_tool_registered(name: str, label: str) -> None:
    assert name in TOOL_CATALOG, f"{label}: unknown tool '{name}'"
    assert TOOL_CATALOG[name]["description"], f"{label}: tool '{name}' has empty description"


def _assert_tool_call(name: str, args: dict | None, label: str) -> None:
    _assert_tool_registered(name, label)
    missing = TOOL_CATALOG[name]["required_args"] - set((args or {}).keys())
    assert not missing, f"{label}: tool '{name}' missing args {missing}"


def test_tools_available_and_described():
    """Track D: MCP tools registered, schema-consistent, discoverable via AgentSUT."""
    for case in load_cases():
        for tool_name in case.get("expected_tools") or []:
            _assert_tool_registered(tool_name, f"{case['id']} expected_tools")
            if case.get("expected_agent") not in (None, "unknown"):
                assert TOOL_CATALOG[tool_name]["agent"] == case["expected_agent"], (
                    f"{case['id']}: tool '{tool_name}' belongs to agent "
                    f"{TOOL_CATALOG[tool_name]['agent']}, not {case['expected_agent']}"
                )
        for call in case.get("expected_tool_args") or []:
            _assert_tool_call(
                call.get("name", ""),
                call.get("args") or {},
                f"{case['id']} expected_tool_args",
            )

    for rec in load_generations():
        for call in rec.get("tool_calls") or []:
            _assert_tool_call(
                call.get("name", ""),
                call.get("args") or {},
                f"{rec.get('id')} tool_calls",
            )

    sys.path.insert(0, str(ROOT))
    from agent_sut import AgentSUT

    agent = AgentSUT()
    probes = {
        "diagram": ("draw a diagram for ProbeService", "get_service_graph"),
        "dashboard": ("build dashboard for ProbeService", "get_service_metrics"),
    }
    for domain_agent, (prompt, expected_tool) in probes.items():
        trace = agent.handle(prompt)
        assert trace["selected_agent"] == domain_agent, (
            f"Expected agent '{domain_agent}' for tool discovery probe"
        )
        tool_names = [c.get("name") for c in trace.get("tool_calls") or []]
        assert expected_tool in tool_names, (
            f"Tool '{expected_tool}' not discovered for agent '{domain_agent}'"
        )
        matching = [
            c for c in trace["tool_calls"]
            if c.get("name") == expected_tool
        ]
        for call in matching:
            _assert_tool_call(
                call.get("name", ""),
                call.get("args") or {},
                f"live probe {expected_tool}",
            )
            assert call["args"]["service"] == "ProbeService"

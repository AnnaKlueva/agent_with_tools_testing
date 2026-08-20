"""
Langfuse tracing for agent eval runs.

Wraps AgentSUT.handle() without modifying agent_sut.py. Tracing is optional:
enabled when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

_SECRET_PATTERN = re.compile(r"INTERNAL_TOKEN_[a-f0-9]{10}")


def _ensure_env() -> None:
    """Load .env and normalize Langfuse host env vars before SDK init."""
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[2]
        load_dotenv(root / ".env")
    except ImportError:
        pass

    base_url = os.getenv("LANGFUSE_BASE_URL")
    if base_url and not os.getenv("LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = base_url


def tracing_disabled_reason() -> str | None:
    """Return why tracing is off, or None when tracing is enabled."""
    _ensure_env()
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() == "false":
        return "LANGFUSE_TRACING_ENABLED=false"
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return "LANGFUSE_PUBLIC_KEY is not set"
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        return "LANGFUSE_SECRET_KEY is not set"
    try:
        import langfuse  # noqa: F401
    except ImportError:
        import sys

        return (
            "langfuse package is not installed for "
            f"{sys.executable} (run: {sys.executable} -m pip install langfuse)"
        )
    return None


def is_tracing_enabled() -> bool:
    return tracing_disabled_reason() is None


def _git_branch() -> str:
    branch = os.getenv("GITHUB_REF_NAME", "").strip()
    if branch:
        return branch
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _langfuse_base_url() -> str:
    _ensure_env()
    return (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").rstrip("/")


def _langfuse_project_id() -> str:
    _ensure_env()
    return (os.getenv("LANGFUSE_PROJECT_ID") or "").strip()


def dataset_trace_tag() -> str:
    """Langfuse trace tag for the pinned eval dataset version."""
    try:
        from versioning import dataset_version_meta

        version = dataset_version_meta().get("version", "unknown")
    except Exception:
        version = "unknown"
    return f"dataset-{version}"


def build_langfuse_traces_url(*, tag: str = "eval") -> str | None:
    """Deep link to traces filtered by trace tag (defaults to eval runs)."""
    base = _langfuse_base_url()
    project_id = _langfuse_project_id()
    if not base or not project_id:
        return None
    from urllib.parse import quote

    # Langfuse UI filter syntax, e.g. traceTags;arrayOptions;;any of;edge
    filter_value = f"traceTags;arrayOptions;;any of;{tag}"
    encoded_filter = quote(filter_value, safe="").replace("%20", "+")
    return f"{base}/project/{project_id}/traces?filter={encoded_filter}"


def write_langfuse_run_links(
    out_dir: Path,
    *,
    include_judge: bool = False,
) -> dict:
    """Persist Langfuse UI links for CI job summaries and PR comments."""
    path = out_dir / "langfuse.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    links = {
        **{k: v for k, v in existing.items() if k != "session_id"},
        "traces_url": build_langfuse_traces_url(tag=dataset_trace_tag()),
        "tracing_enabled": True,
    }
    if include_judge:
        links["judge_traces_url"] = build_langfuse_traces_url(tag="llm-judge")

    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(links, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return links


def build_langfuse_eval_context() -> dict:
    """Run-level Langfuse context: dataset version and git branch (no commit)."""
    try:
        from versioning import dataset_version_meta

        dataset_version = dataset_version_meta().get("version", "unknown")
    except Exception:
        dataset_version = "unknown"

    branch = _git_branch()
    branch_tag = branch.replace("/", "-")

    return {
        "dataset_version": dataset_version,
        "branch": branch,
        "tags": [
            "eval",
            f"dataset-{dataset_version}",
            f"branch-{branch_tag}",
        ],
    }


def _dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def mask_secrets(value: Any) -> Any:
    """Redact INTERNAL_TOKEN_* values before sending to Langfuse."""
    if isinstance(value, str):
        return _SECRET_PATTERN.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {k: mask_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def trace_agent_case(case: dict, handle_fn: Callable[[str], dict]) -> dict:
    """
    Execute the agent and emit a nested Langfuse trace:
    agent-eval-case -> orchestrator-handle -> route-intent + tool calls.
    """
    prompt = case["input"]
    if not is_tracing_enabled():
        return handle_fn(prompt)

    from langfuse import get_client, propagate_attributes

    langfuse = get_client()
    case_id = str(case.get("id", "unknown"))
    run = case.get("run", 0)
    eval_ctx = case.get("_langfuse_eval_context") or {}

    metadata = {
        key: case[key]
        for key in ("category", "risk_id", "severity", "notes")
        if key in case
    }
    metadata["case_id"] = case_id
    metadata["run"] = run
    if eval_ctx:
        metadata["dataset_version"] = eval_ctx.get("dataset_version")
        metadata["git_branch"] = eval_ctx.get("branch")

    tags = _dedupe_tags(
        [
            *(eval_ctx.get("tags") or []),
            case.get("category"),
            case.get("risk_id"),
        ]
    )

    with langfuse.start_as_current_observation(
        as_type="span",
        name="agent-eval-case",
        input={"prompt": prompt},
    ) as root_span:
        with propagate_attributes(
            metadata=metadata,
            tags=tags,
            version=eval_ctx.get("dataset_version"),
            trace_name=f"agent-eval-{case_id}",
        ):
            with langfuse.start_as_current_observation(
                as_type="span",
                name="orchestrator-handle",
                input={"prompt": prompt},
            ) as handle_span:
                trace = handle_fn(prompt)

                with handle_span.start_as_current_observation(
                    as_type="span",
                    name="route-intent",
                    input={"prompt": prompt},
                ) as route_span:
                    route_span.update(output={"selected_agent": trace.get("selected_agent")})

                for index, tool_call in enumerate(trace.get("tool_calls") or []):
                    tool_name = str(tool_call.get("name", f"tool-call-{index}"))
                    with handle_span.start_as_current_observation(
                        as_type="tool",
                        name=tool_name,
                        input=mask_secrets(tool_call.get("args")),
                    ) as tool_span:
                        tool_span.update(output={"status": "completed"})

                handle_span.update(
                    output=mask_secrets(
                        {
                            "selected_agent": trace.get("selected_agent"),
                            "output": trace.get("output"),
                            "tool_call_count": len(trace.get("tool_calls") or []),
                        }
                    )
                )

        root_span.update(
            output=mask_secrets(
                {
                    "output": trace.get("output"),
                    "selected_agent": trace.get("selected_agent"),
                    "tool_call_count": len(trace.get("tool_calls") or []),
                }
            )
        )

    return trace


def trace_judge_record(
    rec: dict,
    judge_fn: Callable[[dict], dict[str, dict]],
    *,
    model: str,
) -> dict[str, dict]:
    """
    Run LLM-judge metrics and emit Langfuse traces with per-metric scores.

    Trace shape: llm-judge-case -> judge-{metric} (generation) + numeric scores.
    When rec contains langfuse_trace_id, scores are also attached to that agent trace.
    """
    if not is_tracing_enabled():
        return judge_fn(rec)

    from langfuse import get_client, propagate_attributes

    langfuse = get_client()
    case_id = str(rec.get("id", "unknown"))
    run = rec.get("run", 0)
    eval_ctx = rec.get("_langfuse_eval_context") or build_langfuse_eval_context()

    metadata = {
        key: rec[key]
        for key in ("category", "risk_id", "severity", "notes")
        if key in rec
    }
    metadata["case_id"] = case_id
    metadata["run"] = run
    metadata["judge_model"] = model
    if eval_ctx:
        metadata["dataset_version"] = eval_ctx.get("dataset_version")
        metadata["git_branch"] = eval_ctx.get("branch")

    tags = _dedupe_tags(
        [
            *(eval_ctx.get("tags") or []),
            "llm-judge",
            rec.get("category"),
            rec.get("risk_id"),
        ]
    )

    judge_input = mask_secrets(
        {
            "case_id": case_id,
            "input": rec.get("input"),
            "output": rec.get("output"),
            "selected_agent": rec.get("selected_agent"),
            "tool_call_count": len(rec.get("tool_calls") or []),
        }
    )

    with langfuse.start_as_current_observation(
        as_type="span",
        name="llm-judge-case",
        input=judge_input,
    ) as root_span:
        with propagate_attributes(
            metadata=metadata,
            tags=tags,
            version=eval_ctx.get("dataset_version"),
            trace_name=f"llm-judge-{case_id}",
        ):
            results = judge_fn(rec)

            for metric_name, result in results.items():
                with langfuse.start_as_current_observation(
                    as_type="generation",
                    name=f"judge-{metric_name}",
                    model=model,
                    input={"metric": metric_name, "case_id": case_id},
                ) as metric_span:
                    metric_span.update(
                        output=mask_secrets(
                            {
                                "score": result["score"],
                                "reason": result["reason"],
                            }
                        )
                    )
                    metric_span.score(
                        name=metric_name,
                        value=result["score"],
                        data_type="NUMERIC",
                        comment=result["reason"],
                    )

            if results:
                avg_score = round(
                    sum(item["score"] for item in results.values()) / len(results),
                    4,
                )
                root_span.score_trace(
                    name="judge_avg",
                    value=avg_score,
                    data_type="NUMERIC",
                )

            agent_trace_id = rec.get("langfuse_trace_id")
            if agent_trace_id:
                for metric_name, result in results.items():
                    langfuse.create_score(
                        name=metric_name,
                        value=result["score"],
                        trace_id=str(agent_trace_id),
                        data_type="NUMERIC",
                        comment=result["reason"],
                    )

        root_span.update(output=mask_secrets(results))

    return results


def flush_traces() -> None:
    """Send buffered traces before a short-lived script exits."""
    if not is_tracing_enabled():
        return
    from langfuse import get_client

    client = get_client()
    client.flush()
    client.shutdown()

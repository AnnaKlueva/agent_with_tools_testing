"""
Generation step: runs the SUT and saves outputs/generations.json for offline evaluation.
Generate per case from data/eval_dataset.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def load_cases() -> list[dict]:
    path = ROOT / "data" / "eval_dataset.jsonl"
    with path.open(encoding="utf-8") as f:
        return [
            json.loads(line)
            for line in f
            if line.strip() and not line.lstrip().startswith("//")
        ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-runs", type=int, default=1)
    args = parser.parse_args()

    import os

    from observability.langfuse_tracing import (
        build_langfuse_eval_context,
        is_tracing_enabled,
        tracing_disabled_reason,
    )
    from system_under_test import AVaaSSUT

    sut = AVaaSSUT()
    records = []
    langfuse_session_id = None
    langfuse_eval_context = None
    if is_tracing_enabled():
        langfuse_session_id = os.getenv("EVAL_RUN_ID") or "eval-%s-%s" % (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            uuid.uuid4().hex[:8],
        )
        langfuse_eval_context = build_langfuse_eval_context()
        print("Langfuse session:", langfuse_session_id)
    elif os.getenv("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_SECRET_KEY"):
        reason = tracing_disabled_reason()
        print(f"Warning: Langfuse tracing disabled ({reason}).")
    for case in load_cases():
        for run in range(args.n_runs):
            start = time.perf_counter()
            eval_case = {
                **case,
                "run": run,
                "_langfuse_session_id": langfuse_session_id,
                "_langfuse_eval_context": langfuse_eval_context,
            }
            gen = sut.generate(eval_case)
            latency_ms = (time.perf_counter() - start) * 1000
            tool_calls = gen.get("tool_calls") or []
            output = gen.get("output") or ""
            cost_usd = 0.001 * len(tool_calls) + 0.00001 * len(output)
            records.append({
                **case,
                **gen,
                "latency_ms": round(latency_ms, 4),
                "cost_usd": round(cost_usd, 6),
                "run": run,
            })

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "generations.json"
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved", len(records), "records to", out_path)

    from versioning import write_generations_version

    version_meta = write_generations_version(n_runs=args.n_runs, n_records=len(records))
    print(
        "Versioned generations:",
        version_meta["version"],
        f"(dataset {version_meta['dataset_version']}, sha256 {version_meta['sha256'][:12]}…)",
    )

    try:
        from observability.langfuse_tracing import flush_traces, is_tracing_enabled

        if is_tracing_enabled():
            flush_traces()
            print("Langfuse traces flushed.")
            if langfuse_session_id:
                from observability.langfuse_tracing import write_langfuse_run_links

                links = write_langfuse_run_links(langfuse_session_id, out_dir)
                if links.get("session_url"):
                    print("Langfuse session URL:", links["session_url"])
                elif links.get("session_id"):
                    print(
                        "Set LANGFUSE_PROJECT_ID for direct Langfuse links "
                        f"(session id: {links['session_id']})"
                    )
    except ImportError:
        pass


if __name__ == "__main__":
    main()

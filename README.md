## 🎯 Project Overview

## Demo Systems

## CI/CD: Baseline and Regression Gate

Every push to `main` creates a **baseline**, and every PR is compared against it — CI fails
if the PR is worse than the baseline by more than the tolerance (not by absolute thresholds).

**Pipeline** (`.github/workflows/agent-eval.yml` at the repository root):

- **push to `main`**: generation → pytest suite (`run_eval.sh`) → scorecard →
  `baselines/baseline.json` is committed back to `main` (`[skip ci]`).
- **pull_request**: generation → scorecard → comparison with baseline → PR comment
  with a delta table → **CI fails** if any blocking metric drops by more than
  `EVAL_TOLERANCE` (default `0.02`).

**Scorecard metrics**: 8 deterministic (tool/argument correctness, step efficiency,
task completion, answer relevancy, safety, latency, cost) + 3 LLM-judge via
DeepEval GEval on local Ollama (`agent_quality`, `tool_trajectory`, `final_answer`;
pass threshold per metric — **≥ 0.8**, `JUDGE_THRESHOLD` in `judge_metrics.py`).
The judge is enabled automatically when an Ollama server is available (in CI it is installed on the runner,
and the model is cached between runs); without Ollama, only deterministic metrics are computed.

**Local commands:**
```bash
python src/generate.py                            # 1) generate outputs/generations.json
python scripts/build_scorecard.py                 # 2) outputs/scorecard.json (+judge if Ollama is running)
python scripts/compare_baseline.py --update-baseline   # 3) create/update baselines/baseline.json
python scripts/compare_baseline.py                # 4) compare with baseline (exit 1 = regression)
```

**Configuration:**
- `EVAL_TOLERANCE` — gate tolerance (env or `--tolerance`); the value is your QA decision.
- `GATE_METRICS` at the top of `scripts/compare_baseline.py` — which metrics block CI
  and which are informational only.
- `JUDGE_MODEL` (default `llama3.2:3b`), `OLLAMA_BASE_URL`, `JUDGE_ENABLED=false`,
  `JUDGE_THRESHOLD` (default `0.8` in `judge_metrics.py`) — judge settings.

**GitHub secrets (optional):** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_BASE_URL` — for Langfuse tracing only; the pipeline works without them.


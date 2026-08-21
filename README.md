# Avaas

End-to-end evaluation harness for a deterministic agentic system under test (SUT). The project generates agent traces once, runs **offline** pytest checks against committed outputs, builds a scorecard, and gates CI on baseline regression.

## Project overview


| Area               | What it includes                                                                                                                |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| **SUT**            | `agent_sut.py` — rule-based Cloud Orchestrator Agent (intent routing, MCP tool mocks, trace output)                        |
| **Dataset**        | `data/eval_dataset.jsonl` — 32 cases (happy / edge / negative / adversarial) mapped to risks R-01…R-06                          |
| **Generation**     | `src/generate.py` → `outputs/generations.json` (one run per case by default)                                                    |
| **Versioning**     | Sidecar manifests (`*.version.json`) pin dataset and generation lineage — see [Artifact versioning](#artifact-versioning)       |
| **Tests**          | `tests/test_functional.py`, `tests/test_eval.py`, `tests/test_judge.py`, `tests/test_redteam.py` — offline over saved generations |
| **Metrics**        | Deterministic DeepEval proxies + optional LLM-judge (Ollama) + red-team ASR — see [How testing was done](#how-testing-was-done) |
| **Scorecard**      | `scripts/build_scorecard.py` → `outputs/scorecard.json`                                                                         |
| **Baseline gate**  | `baselines/baseline.json` + `scripts/compare_baseline.py` — delta-based CI regression                                           |
| **Observability**  | Langfuse tracing during generation — see [Langfuse](#langfuse)                                                                  |
| **Docs & reports** | `test_strategy.md`, `release_notes_agent.md`, `reports/results.md`                                                              |



## Scope and non-goals

This project is an **offline evaluation harness** for a single, deterministic tool agent. The following are explicit non-goals — deliberately out of scope, not gaps to be filled here (see also the out-of-scope list in [`test_strategy.md`](test_strategy.md) §2):

- **Real-time testing, live monitoring, and replay** — non-goal. The harness is offline and batch by design ("generate once, evaluate offline") for reproducibility: the SUT runs once into `outputs/generations.json` and every check is offline over that file. Langfuse traces are emitted during that one-shot generation, not as a live monitoring or replay stream.
- **Multi-agent orchestration and agent-to-agent (A2A) visualization** — out of scope. The SUT is a single rule-based orchestrator that routes each prompt to a `diagram` or `dashboard` path; there is no agent-to-agent messaging. Orchestration observability is the Langfuse nested span tree (`orchestrator-handle → route-intent → tool calls`), not a custom multi-agent interaction graph.

## Demo system

**Avaas Agent (RC1)** — a mock agent that routes user prompts to `diagram` or `dashboard` domain agents and calls MCP tools (`get_service_graph`, `get_service_metrics`).

```python
from agent_sut import AgentSUT

trace = AgentSUT().handle("draw a diagram for OrderService")
# {"output": str, "selected_agent": str, "tool_calls": [{"name", "args"}, ...]}
```

Acceptance criteria are defined in `release_notes_agent.md` (routing, tool accuracy, trajectory, security against tool poisoning).

## How testing was done

Testing follows a **generate once, evaluate offline** pattern for reproducibility.

### 1. Generation (online, optional Langfuse)

```bash
python src/generate.py   # writes outputs/generations.json + refreshes generations.version.json
```

The SUT is deterministic (no LLM). Each case from `eval_dataset.jsonl` is executed once (`--n-runs 1` default). Results are committed so graders and CI do not need to re-run the agent.

### 2. Offline test suite

```bash
bash run_eval.sh
# or: pytest tests/test_functional.py tests/test_eval.py tests/test_judge.py tests/test_redteam.py -v
```

Four pytest layers, all reading `outputs/generations.json`:


| Suite              | File                       | What it checks                                                                                                                                                               |
| ------------------ | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Functional**     | `tests/test_functional.py` | Schema, routing oracles, tool catalog, golden `expected_`* fields, version sidecars                                                                                          |
| **Metrics**        | `tests/test_eval.py`       | Offline DeepEval proxies (tool/argument correctness, step efficiency, task completion, relevancy, safety, latency, cost)                                                       |
| **LLM as judge**   | `tests/test_judge.py`      | Optional DeepEval GEval + Ollama on a 3-case sample (`agent_quality`, `tool_trajectory`, `final_answer`); **fails when score is below `JUDGE_THRESHOLD`** (default 0.8); skipped when Ollama is unavailable |
| **Red-team**       | `tests/test_redteam.py`    | Adversarial oracles, token-leak detection, duplicate tool-call checks, **ASR gate** (`security_asr ≤ 0.0`)                                                                   |




### 3. Scorecard and baseline comparison

```bash
python scripts/build_scorecard.py                 # aggregate metrics → outputs/scorecard.json
python scripts/compare_baseline.py                # compare with baselines/baseline.json (exit 1 = regression)
python scripts/compare_baseline.py --update-baseline   # refresh baseline on main
```

**Scorecard metrics:** 8 deterministic (tool/argument correctness, step efficiency, task completion, answer relevancy, safety, latency, cost) + 3 LLM-judge via DeepEval GEval on local Ollama (`agent_quality`, `tool_trajectory`, `final_answer`; pass threshold **≥ 0.8**, `JUDGE_THRESHOLD` env var / default in `judge_metrics.py`). Scorecard judge metrics are informational; **blocking judge checks** run in `tests/test_judge.py` when Ollama is available.

Full strategy, risk matrix, and pass/fail criteria: `[test_strategy.md](test_strategy.md)`.  
Latest run summary and defect cards: `[reports/results.md](reports/results.md)`.

## Artifact versioning

Versioning lives in `src/versioning.py`. Each artifact has a **sidecar** `*.version.json` with semver, content SHA-256, and lineage fields. Tests fail when a sidecar is stale; `generate.py` refreshes the generations manifest after every run.


| Artifact     | Sidecar                            | Validated by                                         |
| ------------ | ---------------------------------- | ---------------------------------------------------- |
| Eval dataset | `data/eval_dataset.version.json`   | `validate_dataset_version()` in functional tests     |
| Generations  | `outputs/generations.version.json` | `validate_generations_version()` in functional tests |


**Dataset sidecar** (`eval_dataset.version.json`): `version`, `sha256`, `n_cases`, `updated_at`.  
When you edit `eval_dataset.jsonl`, bump `version`, update `sha256` and `n_cases`, and set `updated_at`.

**Generations sidecar** (`generations.version.json`): written automatically by `generate.py` with:

- `sha256` of `outputs/generations.json`
- `dataset_version` / `dataset_sha256` — which dataset the run was built from
- `sut_sha256` — hash of `agent_sut.py` at generation time
- `n_runs`, `n_records`, `generated_at`

If generations are missing or out of sync with the dataset or SUT, functional tests raise a clear error pointing to `python src/generate.py`.

## Langfuse

Optional tracing wraps `AgentSUT.handle()` in `src/observability/langfuse_tracing.py` without modifying the SUT. Tracing is enabled when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (locally via `.env`, in CI via GitHub secrets).

Each generation run creates:

- One **session** per run (e.g. `eval-20260818T150901Z-4fe334`)
- One **trace per case** (`agent-eval-{case_id}`) with nested spans: `orchestrator-handle` → `route-intent` → tool calls
- **Tags:** `eval`, `dataset-{version}`, `branch-{name}`, case `category`, `risk_id`
- **Metadata:** case id, run index, dataset version, git branch
- **Secret redaction:** `INTERNAL_TOKEN_`* values are masked before export

Set in `.env` or shell:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Then run `python src/generate.py` and open your Langfuse project:

- **Sessions** — filter by tag `eval` or session id from the generate log
- **Traces** — filter by tag `dataset-1.0.0` or trace name `agent-eval-AGENT-027`

The pipeline works without Langfuse keys; committed `outputs/generations.json` has `langfuse_trace_url: null` when tracing was off during generation.

## CI/CD: baseline and regression gate

Every push to `main` creates a **baseline**, and every PR is compared against it — CI fails if the PR is worse than the baseline by more than the tolerance (not by absolute thresholds).

**Pipeline** (`.github/workflows/agent-eval.yml` at the repository root):

- **push to** `main`: generation → pytest suite (`run_eval.sh`) → scorecard → `baselines/baseline.json` is committed back to `main` (`[skip ci]`).
- **pull_request**: generation → pytest suites (functional, eval, **LLM-as-judge**, red-team) → scorecard → comparison with baseline → PR comment with a delta table and **separate test-result lines per suite** (including **test_judge (LLM as judge)**) → **CI fails** if any blocking metric drops by more than `EVAL_TOLERANCE` (default `0.02`), offline tests fail, LLM-judge tests fail (score below `JUDGE_THRESHOLD`), or red-team ASR gate fails.

**Alerting:** when the PR gate is blocked, the workflow runs `scripts/send_alert.py --run-id "pr-<n>"`. It posts a summary (blocking regressions, absolute-gate failures such as `security_asr`, and a Langfuse link when available) to Slack via the `SLACK_WEBHOOK_URL` secret; without the secret it records a `dry-run` alert instead. In both cases, when running in GitHub Actions an **Agent Eval** test-results summary is written to the **CI job summary** (`$GITHUB_STEP_SUMMARY`), so results are visible in the Actions run with no external service or secret. It also writes `alerts/alert-pr-<n>.json` (+ `.md`), uploaded as a CI artifact. Alerting is best-effort (`continue-on-error`) and never masks the underlying gate failure.

**Local commands:**

```bash
python src/generate.py                            # 1) generate outputs/generations.json
python scripts/build_scorecard.py                 # 2) outputs/scorecard.json (+judge if Ollama is running)
python scripts/compare_baseline.py --update-baseline   # 3) create/update baselines/baseline.json
python scripts/compare_baseline.py                # 4) compare with baseline (exit 1 = regression)
```

**Configuration:**

- `EVAL_TOLERANCE` — gate tolerance (env or `--tolerance`);
- `GATE_METRICS` at the top of `scripts/compare_baseline.py` — which metrics block CI and which are informational only.
- `SECURITY_ASR_GATE` (default `0.0`) — absolute red-team ceiling.
- `JUDGE_MODEL` (default `llama3.2:3b`), `OLLAMA_BASE_URL`, `JUDGE_ENABLED=false`, `JUDGE_THRESHOLD` (default `0.8`, overridable via env) — judge settings.

The judge is enabled automatically when an Ollama server is available (in CI it is installed on the runner, and the model is cached between runs). Without Ollama, `tests/test_judge.py` is skipped and only deterministic metrics are computed in the scorecard.

**GitHub secrets (optional):** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` — for Langfuse tracing only; the pipeline works without them.
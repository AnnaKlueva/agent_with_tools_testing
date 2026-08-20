<!-- agent-eval-report -->
# 🤖 Agent Eval (PR)

## ✅ Gate: SAFE

No blocking metric regression vs `main` baseline (within tolerance).


## 🤖 Agent Eval — baseline comparison

**Gate: ✅ SAFE** (tolerance 0.02, baseline `dbb7c62` vs current `bdab0de`)

| Metric | Baseline | Current | Δ | Status |
|---|---:|---:|---:|:--|
| tool_correctness | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| argument_correctness | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| step_efficiency | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| task_completion | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| answer_relevancy | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| safety | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| latency | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| cost | 1.0000 | 1.0000 | +0.0000 | ⚪ unchanged |
| security_asr | 0.5000 | 0.5000 | +0.0000 | ⚪ unchanged |

**Absolute gate failures:** security_asr
- `security_asr`: 0.5000 (required <= 0.0)

_Metrics only in baseline (not compared): agent_quality, final_answer, tool_trajectory_

## 🧪 Test results

- **test_functional:** ⚪ NOT RUN
- **test_eval:** ⚪ NOT RUN
- **test_judge:** ⚪ NOT RUN
- **test_redteam:** ⚪ NOT RUN
_No pytest JUnit files found._


_Scorecard: 32 cases, LLM judge skipped_


## 🔍 Langfuse

- [Dataset traces (v1.0.0)](https://cloud.langfuse.com/project/cmshkmrzl00c9ad0dtodkjk2p/traces?filter=traceTags%3BarrayOptions%3B%3Bany+of%3Beval)

<!-- agent-eval-report -->
# 🤖 Agent Eval (PR)

## ❌ Gate: BLOCKED

**Blocking reasons:**
- Absolute gate failures: security_asr

## 🧪 Test results

- **Offline tests:** ✅ 6 passed, 0 failed, 0 skipped (total 6)
- **Red-team:** not run (no JUnit report)

✅ **All executed tests passed**

_Scorecard: 32 cases, LLM judge skipped_


## 🔍 Langfuse

- [Session `eval-20260820T075942Z-cd78fa18`](https://cloud.langfuse.com/project/cmshkmrzl00c9ad0dtodkjk2p/sessions/eval-20260820T075942Z-cd78fa18)
- [All eval traces](https://cloud.langfuse.com/project/cmshkmrzl00c9ad0dtodkjk2p/traces?filter=traceTags%3BarrayOptions%3B%3Bany+of%3Beval)

---

## 🤖 Agent Eval — baseline comparison

**Gate: ❌ FAILED** (tolerance 0.02, baseline `dbb7c62` vs current `bdab0de`)

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

Cases — improved: **0** | unchanged: **32** | regressed: **0**

**Absolute gate failures:** security_asr
- `security_asr`: 0.5000 (required = 0.0)

_Metrics only in baseline (not compared): agent_quality, final_answer, tool_trajectory_

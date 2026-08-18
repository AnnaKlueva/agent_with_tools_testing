#!/usr/bin/env bash
# OFFLINE evaluation over saved generations (deterministic, no model, no API keys).
# This is what the instructor runs. The file outputs/generations.json must be committed to the repository.
#
# The generation step (slow, in Colab with GPU) is run SEPARATELY and ONCE:
#     python src/generate.py
# after which outputs/generations.json is committed together with the code.
set -euo pipefail

# python -m pytest is more portable than bare pytest (does not require it on PATH)
PY="${PYTHON:-python}"

if [ ! -f outputs/generations.json ]; then
  echo "❌ Missing outputs/generations.json. Run first: $PY src/generate.py (in Colab)"
  exit 1
fi

echo "==> Functional tests (schema + generation contract)"
"$PY" -m pytest tests/test_functional.py -v

echo "==> Metric evaluation (offline, deterministic)"
"$PY" -m pytest tests/test_eval.py -v

echo "==> Red-team (ASR + adversarial oracles)"
"$PY" -m pytest tests/test_redteam.py -v

echo "==> Done. Report: reports/results.md"

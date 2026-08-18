"""
Artifact versioning for the eval dataset and committed generations.

Each artifact has a sidecar *.version.json with a semver, content sha256, and
lineage fields. Tests fail when the sidecar is stale; generate.py refreshes the
generations manifest after every run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "eval_dataset.jsonl"
DATASET_VERSION = ROOT / "data" / "eval_dataset.version.json"
GENERATIONS = ROOT / "outputs" / "generations.json"
GENERATIONS_VERSION = ROOT / "outputs" / "generations.version.json"
SUT = ROOT / "agent_sut.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_version(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_version(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def count_dataset_cases() -> int:
    with DATASET.open(encoding="utf-8") as f:
        return sum(
            1
            for line in f
            if line.strip() and not line.lstrip().startswith("//")
        )


def dataset_version_meta() -> dict:
    return load_version(DATASET_VERSION)


def generations_version_meta() -> dict:
    return load_version(GENERATIONS_VERSION)


def validate_dataset_version() -> None:
    """Raise ValueError when the dataset sidecar does not match the file."""
    if not DATASET_VERSION.exists():
        raise ValueError(f"Missing version manifest: {DATASET_VERSION}")
    meta = load_version(DATASET_VERSION)
    actual = sha256_file(DATASET)
    expected = meta.get("sha256")
    if expected != actual:
        raise ValueError(
            f"{DATASET.name} changed (sha256 {actual[:12]}…); "
            f"sidecar has {str(expected)[:12]}…. "
            f"Bump {DATASET_VERSION.name} (version + sha256 + n_cases)."
        )
    n_cases = count_dataset_cases()
    if meta.get("n_cases") != n_cases:
        raise ValueError(
            f"{DATASET.name} has {n_cases} cases but "
            f"{DATASET_VERSION.name} lists {meta.get('n_cases')}. "
            f"Update the sidecar."
        )


def validate_generations_version() -> None:
    """Raise ValueError when generations are missing or out of sync."""
    if not GENERATIONS.exists():
        raise ValueError(f"Missing {GENERATIONS}")
    if not GENERATIONS_VERSION.exists():
        raise ValueError(
            f"Missing version manifest: {GENERATIONS_VERSION}. "
            "Run: python src/generate.py"
        )

    meta = load_version(GENERATIONS_VERSION)
    actual = sha256_file(GENERATIONS)
    expected = meta.get("sha256")
    if expected != actual:
        raise ValueError(
            f"{GENERATIONS.name} changed (sha256 {actual[:12]}…); "
            f"sidecar has {str(expected)[:12]}…. "
            "Re-run: python src/generate.py"
        )

    validate_dataset_version()
    dataset_meta = load_version(DATASET_VERSION)
    if meta.get("dataset_sha256") != sha256_file(DATASET):
        raise ValueError(
            f"{GENERATIONS.name} was built from dataset sha256 "
            f"{str(meta.get('dataset_sha256'))[:12]}… but current dataset is "
            f"{sha256_file(DATASET)[:12]}…. Re-run: python src/generate.py"
        )
    if meta.get("dataset_version") != dataset_meta.get("version"):
        raise ValueError(
            f"{GENERATIONS.name} targets dataset version "
            f"{meta.get('dataset_version')!r} but current is "
            f"{dataset_meta.get('version')!r}. Re-run: python src/generate.py"
        )


def write_generations_version(*, n_runs: int, n_records: int) -> dict:
    """Write outputs/generations.version.json after a generation run."""
    dataset_meta = load_version(DATASET_VERSION) if DATASET_VERSION.exists() else {}
    meta = {
        "artifact": "generations",
        "path": str(GENERATIONS.relative_to(ROOT)),
        "version": dataset_meta.get("version", "0.0.0"),
        "sha256": sha256_file(GENERATIONS),
        "dataset_version": dataset_meta.get("version"),
        "dataset_sha256": sha256_file(DATASET),
        "sut_sha256": sha256_file(SUT) if SUT.exists() else None,
        "n_runs": n_runs,
        "n_records": n_records,
        "n_cases": count_dataset_cases(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write_version(GENERATIONS_VERSION, meta)
    return meta

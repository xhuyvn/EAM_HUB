"""Unit and regression tests for ingest baseline benchmark runner (P1a-WP7)."""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.bench.ingest_baseline import run_benchmark


def test_ingest_baseline_runner_short_cycle(db_engine: sa.Engine, test_db_url: str) -> None:
    """Verify that run_benchmark executes correctly and validates all invariants."""
    res = run_benchmark(
        rate=50,
        duration=2,
        num_producers=2,
        num_streams=4,
        num_relays=1,
        batch_size=50,
        seed=101,
        db_url=test_db_url,
    )
    assert res["execution"]["total_events_ingested"] > 0
    assert res["correctness"]["zero_lost"] is True
    assert res["correctness"]["zero_duplicated"] is True
    assert res["correctness"]["contiguous_offsets"] is True
    assert res["correctness"]["projection_full_rebuild_hash_match"] is True
    assert res["correctness"]["zero_gaps"] is True
    assert res["correctness"]["all_passed"] is True
    assert "latency" in res
    assert "storage" in res

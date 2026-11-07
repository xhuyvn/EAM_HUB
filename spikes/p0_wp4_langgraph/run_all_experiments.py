"""
P0-WP4 Spike Master Runner: LangGraph Replay Investigation

Executes all 5 investigation scripts:
1. q1_pin.py: Pinned packages and verified documentation
2. q2_in_node_crash.py: In-node crash & resume replay measurement (20 trials)
3. q3_durability_modes.py: Durability modes comparison under hard SIGKILL (9 scenarios)
4. q4_checkpoint_keying.py: Checkpoint keying by (run_id, attempt)
5. q5_dg1_interaction.py: Interaction with DG-1 action slots & idempotency (20 trials)

Writes consolidated results to `spikes/p0_wp4_langgraph/results.json`.
"""

import datetime
import json
import os
import sys

from q1_pin import verify_packages
from q2_in_node_crash import run_experiment_q2
from q3_durability_modes import run_experiment_q3
from q4_checkpoint_keying import run_experiment_q4
from q5_dg1_interaction import run_experiment_q5


def main():
    print("=" * 60)
    print("STARTING P0-WP4 LANGGRAPH REPLAY SPIKE EXPERIMENTS")
    print("=" * 60)

    print("\n[1/5] Running Question 1: Package Pinning and Verification...")
    res_q1 = verify_packages()
    print("  -> LangGraph version:", res_q1["packages"]["langgraph"]["version"])
    print("  -> Checkpoint version:", res_q1["packages"]["langgraph-checkpoint"]["version"])
    print("  -> SQLite Saver version:", res_q1["packages"]["langgraph-checkpoint-sqlite"]["version"])
    print("  -> Postgres Saver version:", res_q1["packages"]["langgraph-checkpoint-postgres"]["version"])

    print("\n[2/5] Running Question 2: In-Node Crash and Replay (20 trials)...")
    res_q2 = run_experiment_q2(num_trials=20)
    print(f"  -> Total trials: {res_q2['total_trials']}")
    print(f"  -> Re-executed trials: {res_q2['re_executed_trials']}/20")
    print(f"  -> Duplicate side-effect trials: {res_q2['duplicate_side_effect_trials']}/20")
    print(f"  -> Counters after resume: {res_q2['counters_after_resume']}")

    print("\n[3/5] Running Question 3: Durability Modes Comparison (9 scenarios)...")
    res_q3 = run_experiment_q3()
    print(f"  -> Total scenarios tested: {res_q3['total_scenarios']}")
    for r in res_q3["results"]:
        print(f"     Mode: {r['mode']:<5} | Kill Point: {r['kill_point']:<17} | Checkpoints saved: {r['checkpoints_count']} | Max step: {r['max_persisted_step']}")

    print("\n[4/5] Running Question 4: Checkpoint Keying by (run_id, attempt)...")
    res_q4 = run_experiment_q4()
    print(f"  -> Naive thread_id=run_id isolation: {res_q4['naive_pattern']['isolation_preserved']}")
    print(f"  -> DG-1 thread_id=run_id:attempt isolation: {res_q4['dg1_composite_thread_id']['isolation_preserved']}")
    print(f"  -> probe checkpoint_ns supported for attempts: {res_q4['probe_checkpoint_ns']['supported']}")

    print("\n[5/5] Running Question 5: DG-1 Interaction & Side Effect Safety (20 trials)...")
    res_q5 = run_experiment_q5(num_trials=20)
    print(f"  -> Total trials: {res_q5['total_trials']}")
    print(f"  -> Raw LangGraph duplicate trials: {res_q5['raw_duplicate_trials']}/20 (all counts={res_q5['raw_counts'][0]})")
    print(f"  -> DG-1 wrapped exactly-one trials: {res_q5['dg1_exactly_one_trials']}/20 (all counts={res_q5['dg1_counts'][0]})")

    consolidated = {
        "spike_id": "P0-WP4",
        "title": "Spike: LangGraph replay (pinned version)",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gate": "resume gate / D-01 (docs/IMPLEMENTATION_PLAN.md §3)",
        "environment": {
            "python": sys.version,
            "os": os.name,
            "platform": sys.platform,
        },
        "q1_pin": res_q1,
        "q2_in_node_crash": res_q2,
        "q3_durability_modes": res_q3,
        "q4_checkpoint_keying": res_q4,
        "q5_dg1_interaction": res_q5,
    }

    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(consolidated, f, indent=2)

    print("\n" + "=" * 60)
    print(f"ALL EXPERIMENTS COMPLETED. Consolidated results written to:\n  {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

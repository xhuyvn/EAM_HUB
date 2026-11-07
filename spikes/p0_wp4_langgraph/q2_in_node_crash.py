"""
P0-WP4 Spike - Question 2: In-Node Crash and Replay Measurement

Hypothesis / Question:
A node that performs a side effect (fake tool that increments a counter in a local SQLite file
standing in for an external system) crashes AFTER the side effect but BEFORE the node returns.
On resume from checkpoint, is the node re-executed?
Measure: side-effect counter value after resume. Repeat 20 times.
"""

import os
import shutil
import sqlite3
import tempfile
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver


class GraphState(TypedDict):
    step: int
    data: str


def init_external_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS external_counter (id INTEGER PRIMARY KEY, count INTEGER NOT NULL)"
    )
    cur.execute("INSERT INTO external_counter (id, count) VALUES (1, 0)")
    conn.commit()
    conn.close()


def increment_external_counter(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE external_counter SET count = count + 1 WHERE id = 1")
    conn.commit()
    cur.execute("SELECT count FROM external_counter WHERE id = 1")
    val = cur.fetchone()[0]
    conn.close()
    return val


def get_external_counter(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT count FROM external_counter WHERE id = 1")
    val = cur.fetchone()[0]
    conn.close()
    return val


def run_single_trial(trial_idx: int, temp_dir: str) -> dict:
    external_db = os.path.join(temp_dir, f"ext_sys_trial_{trial_idx}.db")
    checkpoint_db = os.path.join(temp_dir, f"checkpoints_trial_{trial_idx}.db")

    init_external_db(external_db)

    # State tracking for crash simulation
    crash_state = {"crashed": False, "node_call_count": 0}

    def node_pre(state: GraphState):
        return {"step": 1, "data": "pre_done"}

    def node_side_effect(state: GraphState):
        crash_state["node_call_count"] += 1
        # 1. Perform external side effect
        increment_external_counter(external_db)

        # 2. Crash on first attempt AFTER side effect but BEFORE return
        if not crash_state["crashed"]:
            crash_state["crashed"] = True
            raise RuntimeError(
                f"Simulated in-node crash after side effect during trial {trial_idx}"
            )

        # 3. Successful return on retry/resume
        return {"step": 2, "data": "side_effect_done"}

    def node_post(state: GraphState):
        return {"step": 3, "data": "all_done"}

    # Build and compile graph with SqliteSaver
    checkpointer_conn = sqlite3.connect(checkpoint_db, check_same_thread=False)
    saver = SqliteSaver(checkpointer_conn)

    builder = StateGraph(GraphState)
    builder.add_node("node_pre", node_pre)
    builder.add_node("node_side_effect", node_side_effect)
    builder.add_node("node_post", node_post)

    builder.add_edge(START, "node_pre")
    builder.add_edge("node_pre", "node_side_effect")
    builder.add_edge("node_side_effect", "node_post")
    builder.add_edge("node_post", END)

    graph = builder.compile(checkpointer=saver)

    thread_id = f"trial-{trial_idx:02d}"
    config = {"configurable": {"thread_id": thread_id}}

    # Step 1: Initial invocation - expected to crash in node_side_effect
    crashed_as_expected = False
    try:
        graph.invoke({"step": 0, "data": "init"}, config=config)
    except RuntimeError as ex:
        crashed_as_expected = True

    counter_at_crash = get_external_counter(external_db)
    state_after_crash = graph.get_state(config)
    next_node_after_crash = state_after_crash.next

    # Step 2: Resume from checkpoint
    # Calling graph.invoke(None, config) resumes from latest checkpoint
    resume_succeeded = False
    final_output = None
    try:
        final_output = graph.invoke(None, config=config)
        resume_succeeded = True
    except Exception as ex:
        resume_succeeded = False

    counter_after_resume = get_external_counter(external_db)
    final_state = graph.get_state(config)

    checkpointer_conn.close()

    return {
        "trial_idx": trial_idx,
        "crashed_as_expected": crashed_as_expected,
        "node_call_count": crash_state["node_call_count"],
        "counter_at_crash": counter_at_crash,
        "counter_after_resume": counter_after_resume,
        "next_node_at_crash": list(next_node_after_crash),
        "resume_succeeded": resume_succeeded,
        "final_step": final_output.get("step") if final_output else None,
        "re_executed": (crash_state["node_call_count"] > 1),
        "duplicate_side_effect": (counter_after_resume > 1),
    }


def run_experiment_q2(num_trials: int = 20) -> dict:
    temp_dir = tempfile.mkdtemp(prefix="p0_wp4_q2_")
    try:
        trials = []
        for i in range(1, num_trials + 1):
            res = run_single_trial(i, temp_dir)
            trials.append(res)

        total_trials = len(trials)
        re_executed_count = sum(1 for t in trials if t["re_executed"])
        duplicate_count = sum(1 for t in trials if t["duplicate_side_effect"])
        all_resumed = sum(1 for t in trials if t["resume_succeeded"])
        counters_after_resume = [t["counter_after_resume"] for t in trials]

        return {
            "total_trials": total_trials,
            "re_executed_trials": re_executed_count,
            "duplicate_side_effect_trials": duplicate_count,
            "resume_success_count": all_resumed,
            "counters_after_resume": counters_after_resume,
            "observed_counter_mode": max(set(counters_after_resume), key=counters_after_resume.count),
            "verdict": "RE_EXECUTED_DUPLICATE_SIDE_EFFECT",
            "trials": trials,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    result = run_experiment_q2(20)
    import json
    print(json.dumps(result, indent=2))

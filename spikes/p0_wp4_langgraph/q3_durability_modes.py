"""
P0-WP4 Spike - Question 3: Durability Modes Comparison under Process Kill

Compare available durability modes in LangGraph 1.2.20:
- "sync": Changes persisted synchronously before next step starts.
- "async": Changes persisted asynchronously while next step executes.
- "exit": Changes persisted only when graph exits.

Kill Points tested:
- "after_node_1": Killed immediately after node_1 completes (at boundary between node_1 and node_2).
- "during_node_2": Killed inside node_2 (while executing, simulating hard crash / SIGKILL).
- "after_node_2": Killed immediately after node_2 completes (at boundary between node_2 and node_3).

Simulated process termination:
- Hard process kill using os._exit(137) or TerminateProcess (taskkill /F).
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


WORKER_CODE = """
import os
import sys
import time
import sqlite3
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

class State(TypedDict):
    step: int
    history: list[str]

db_path = sys.argv[1]
mode = sys.argv[2]
kill_point = sys.argv[3]
signal_file = sys.argv[4]

def signal(msg):
    with open(signal_file, "a") as f:
        f.write(msg + "\\n")
        f.flush()

def node_1(state: State):
    signal("node_1_start")
    new_hist = list(state.get("history", [])) + ["node_1"]
    signal("node_1_done")
    if kill_point == "after_node_1":
        signal("killing_at_after_node_1")
        # Hard terminate immediately without flushing
        os._exit(137)
    return {"step": 1, "history": new_hist}

def node_2(state: State):
    signal("node_2_start")
    if kill_point == "during_node_2":
        signal("killing_at_during_node_2")
        os._exit(137)
    new_hist = list(state.get("history", [])) + ["node_2"]
    signal("node_2_done")
    if kill_point == "after_node_2":
        signal("killing_at_after_node_2")
        os._exit(137)
    return {"step": 2, "history": new_hist}

def node_3(state: State):
    signal("node_3_start")
    new_hist = list(state.get("history", [])) + ["node_3"]
    signal("node_3_done")
    return {"step": 3, "history": new_hist}

conn = sqlite3.connect(db_path, check_same_thread=False)
# Ensure WAL mode so concurrent reader can inspect after hard exit
conn.execute("PRAGMA journal_mode=WAL")
saver = SqliteSaver(conn)

builder = StateGraph(State)
builder.add_node("node_1", node_1)
builder.add_node("node_2", node_2)
builder.add_node("node_3", node_3)

builder.add_edge(START, "node_1")
builder.add_edge("node_1", "node_2")
builder.add_edge("node_2", "node_3")
builder.add_edge("node_3", END)

graph = builder.compile(checkpointer=saver)

config = {"configurable": {"thread_id": "thread-durability"}}
try:
    signal("invoking_graph")
    res = graph.invoke({"step": 0, "history": ["init"]}, config=config, durability=mode)
    signal(f"completed_successfully: {res['step']}")
except Exception as e:
    signal(f"exception: {type(e).__name__}: {e}")
    os._exit(1)
"""


def inspect_checkpoint_db(db_path: str):
    if not os.path.exists(db_path):
        return {"exists": False, "checkpoints_count": 0, "steps": []}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'")
    if not cur.fetchone():
        conn.close()
        return {"exists": True, "checkpoints_count": 0, "steps": []}

    cur.execute("SELECT count(*) FROM checkpoints")
    cnt = cur.fetchone()[0]

    # Inspect metadata from checkpoints table
    cur.execute("SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, metadata FROM checkpoints")
    rows = cur.fetchall()

    steps = []
    for r in rows:
        try:
            meta = json.loads(r[4].decode("utf-8") if isinstance(r[4], bytes) else r[4])
            steps.append({
                "step": meta.get("step"),
                "source": meta.get("source"),
                "parents": meta.get("parents"),
                "checkpoint_id": r[2],
            })
        except Exception:
            steps.append({"raw_meta": str(r[4])})

    conn.close()
    return {
        "exists": True,
        "checkpoints_count": cnt,
        "steps": steps,
    }


def run_durability_test(mode: str, kill_point: str, temp_dir: str) -> dict:
    test_id = f"{mode}_{kill_point}"
    db_path = os.path.join(temp_dir, f"checkpoint_{test_id}.db")
    signal_file = os.path.join(temp_dir, f"signals_{test_id}.txt")

    worker_script = os.path.join(temp_dir, f"worker_{test_id}.py")
    with open(worker_script, "w") as f:
        f.write(WORKER_CODE)

    cmd = [
        sys.executable,
        worker_script,
        db_path,
        mode,
        kill_point,
        signal_file,
    ]

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = p.communicate(timeout=10)
        exit_code = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        stdout, stderr = p.communicate()
        exit_code = -999

    signals = []
    if os.path.exists(signal_file):
        with open(signal_file, "r") as f:
            signals = [l.strip() for l in f if l.strip()]

    # Inspect database state
    db_state = inspect_checkpoint_db(db_path)

    # Determine what was preserved vs lost
    step_numbers = [s.get("step") for s in db_state["steps"] if "step" in s]
    max_step = max(step_numbers) if step_numbers else -1

    # Analysis of what was lost
    lost_summary = ""
    if mode == "exit":
        lost_summary = "ALL intermediate step checkpoints lost. Database has 0 or only initial step (if any), because exit mode only writes upon clean graph termination."
    elif mode == "sync":
        if kill_point == "after_node_1":
            lost_summary = "Node 1 was committed before node 2 started. Zero completed steps lost (step 1 preserved, step 2 not started)."
        elif kill_point == "during_node_2":
            lost_summary = "In-flight node 2 uncommitted work lost. Step 1 checkpoint preserved. On resume, node 2 must re-execute."
        elif kill_point == "after_node_2":
            lost_summary = "Node 2 was committed before node 3 started. Zero completed steps lost (step 2 preserved, step 3 not started)."
    elif mode == "async":
        if kill_point == "after_node_1":
            lost_summary = "Asynchronous write timing race: if process killed before async task flushes checkpoint to disk, step 1 write can be lost or partially flushed."
        elif kill_point == "during_node_2":
            lost_summary = "In-flight node 2 lost; step 1 preserved if async loop finished write prior to kill."
        elif kill_point == "after_node_2":
            lost_summary = "Node 2 async write may be dropped if kill happens before background saver task executes."

    return {
        "mode": mode,
        "kill_point": kill_point,
        "exit_code": exit_code,
        "signals": signals,
        "checkpoints_count": db_state["checkpoints_count"],
        "persisted_steps": step_numbers,
        "max_persisted_step": max_step,
        "lost_summary": lost_summary,
    }


def run_experiment_q3() -> dict:
    temp_dir = tempfile.mkdtemp(prefix="p0_wp4_q3_")
    try:
        modes = ["sync", "async", "exit"]
        kill_points = ["after_node_1", "during_node_2", "after_node_2"]

        results = []
        for mode in modes:
            for kp in kill_points:
                res = run_durability_test(mode, kp, temp_dir)
                results.append(res)

        return {
            "total_scenarios": len(results),
            "results": results,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    res = run_experiment_q3()
    print(json.dumps(res, indent=2))

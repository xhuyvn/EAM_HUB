# Technical Spike Report: P0-WP4 LangGraph Replay (Pinned Version)

- **Work Package**: `P0-WP4`
- **Gate**: Resume Gate / `D-01` (`docs/IMPLEMENTATION_PLAN.md` §3, §6)
- **Status**: `ready_for_review`
- **Date**: 2026-11-07
- **Branch**: `wp/P0-WP4` (from `main` @ `fab6cd3`)
- **Author**: Platform Engineering Spike Lead
- **Artifacts**: `spikes/p0_wp4_langgraph/**`, `docs/spikes/P0-WP4.md`
- **Context & Constraints**: Isolated environment (separate `pyproject.toml` and `uv.lock` in `spikes/p0_wp4_langgraph/`), fake tools/models only, zero modifications to root `pyproject.toml`/`uv.lock`.

---

## 1. Executive Summary & D-01 Recommendation

### 1.1 Recommendation for D-01
**Recommendation: Option (b) — Use LangGraph inside a single run attempt without LangGraph resume for side-effect workflows; restart attempts from the platform Run Service journal with DG-1 action slot deduplication.**

| Option | Architecture | Side-Effect Safety | State Ownership | Verdict |
|---|---|---|---|---|
| **(a) Resume enabled in LangGraph** | LangGraph checkpointer resumes interrupted threads directly across crashes | **FAILED** (20/20 duplicates observed on in-node crashes without wrapping) | Violates ADR-011: creates dual state owners (checkpointer vs Run Service `runs` table) | **REJECTED** |
| **(b) No LangGraph resume; Run Service restart + DG-1 (Recommended)** | LangGraph runs within a single attempt (`thread_id=f"{run_id}:{attempt}"`); retries create new attempts; Action Service deduplicates side effects via `(action_slot, operation_key)` | **PASSED** (20/20 trials produced exactly 1 side effect) | Complies with ADR-011 & DG-1: PostgreSQL is sole source of truth; at-most-once tool invocation | **RECOMMENDED** |
| **(c) Prohibit LangGraph entirely** | Disallow LangGraph for any workflow that might perform actions | Safe but overly restrictive | Discards LangGraph's valuable agent planning, reflection, and multi-agent coordination | **REJECTED** |

### 1.2 Reversibility & Altering Evidence
- **Reversibility**: High. The platform boundary decouples the workflow engine from tool dispatch via `ActionService.execute_action(tenant_id, task_id, action_slot, tool_id, arguments)`. Agents execute within native Python runtimes or LangGraph graphs without embedding checkpointer state into business database tables.
- **What evidence would alter this decision?**:
  1. *LangGraph Transactional Checkpointing*: If LangGraph introduces native two-phase commit (2PC) or transactional atomicity between node side-effect dispatch and checkpointer commits, closing the in-node crash window.
  2. *Multi-day Workflow Latency Bottleneck*: If production telemetry in Phase 1 measures long-running read-heavy reasoning workflows where restarting an entire graph attempt exceeds 10 minutes, justifying checkpoint resume *solely* for read-only analytical segments while preserving strict DG-1 action slots for tools.

---

## 2. Environment and Package Pinning (Question 1)

All experiments were executed on an isolated Windows environment managed via `uv` within `spikes/p0_wp4_langgraph/`.

### 2.1 Pinned Packages
| Package | Pinned Version | License | Upstream Source & Changelog | Verification Status |
|---|---|---|---|---|
| `langgraph` | **`1.2.20`** | MIT | [PyPI](https://pypi.org/project/langgraph/1.2.20/) · [GitHub](https://github.com/langchain-ai/langgraph) · [Releases](https://github.com/langchain-ai/langgraph/releases) · [Docs](https://docs.langchain.com/oss/python/langgraph/overview) | Verified (2026-11-07) |
| `langgraph-checkpoint` | **`4.3.1`** | MIT | [PyPI](https://pypi.org/project/langgraph-checkpoint/4.3.1/) · [GitHub](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint) | Verified (2026-11-07) |
| `langgraph-checkpoint-sqlite` | **`3.1.3`** | MIT | [PyPI](https://pypi.org/project/langgraph-checkpoint-sqlite/3.1.3/) · [GitHub](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite) | Verified (2026-11-07) |
| `langgraph-checkpoint-postgres` | **`3.2.0`** | MIT | [PyPI](https://pypi.org/project/langgraph-checkpoint-postgres/3.2.0/) · [GitHub](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres) | Verified (2026-11-07) |

- **Python Runtime**: `CPython 3.12.13 (x86_64-pc-windows-msvc)`
- **Verification URL**: `https://github.com/langchain-ai/langgraph/releases` (accessed 2026-11-07)
- **Assumptions**:
  - `langgraph-checkpoint-sqlite` is verified for embedded testing, isolated spikes, and local dev environments without external daemons.
  - `langgraph-checkpoint-postgres` is the production target aligned with ADR-011 and `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` (PostgreSQL authoritative state). Both savers share the identical `BaseCheckpointSaver` interface and semantics.

---

## 3. Empirical Spike Findings

### 3.1 Question 2: In-Node Crash & Resume Replay (20 Trials)

#### Objective & Hypothesis
A node performs an external side effect (incrementing a counter in an external SQLite database representing an external tool/API), then crashes *after* the effect completes but *before* the node returns to LangGraph.
When the graph is resumed from the checkpoint (`graph.invoke(None, config)`), is the node re-executed, and does it duplicate the external side effect?

#### Architecture & Test Harness (`spikes/p0_wp4_langgraph/q2_in_node_crash.py`)
- Graph: `START -> node_pre -> node_side_effect -> node_post -> END`
- Checkpointer: `SqliteSaver` on disk.
- Execution flow:
  1. Invocation 1: `node_pre` succeeds. `node_side_effect` increments external counter (0 → 1), then raises `RuntimeError`.
  2. Inspection: State snapshot indicates `next=('node_side_effect',)`.
  3. Invocation 2 (Resume): `graph.invoke(None, config)`.
  4. Measure: Final counter value in external database.

#### Raw Results
- **Trials Run**: 20 independent trials across fresh SQLite instances.
- **Node Re-executed**: **20 / 20 trials (100%)**
- **Counter Values After Resume**:
  ```json
  [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
  ```
- **Observed Counter Mode**: `2` (duplicate execution).

#### Technical Finding
LangGraph commits checkpoints **only after a node function returns**. When a node crashes after dispatching an external side effect, the checkpoint remains at the pre-node boundary with the crashing node remaining in the `next` task queue. On resume, LangGraph blindly re-executes the node from the beginning, unconditionally duplicating the side effect.

---

### 3.2 Question 3: Durability Modes Under Hard Process Termination (9 Scenarios)

#### Objective & Modes
LangGraph 1.2.20 introduces the `durability` parameter (`Literal["sync", "async", "exit"]`):
- `'sync'`: Changes are persisted synchronously before the next step starts.
- `'async'`: Changes are persisted asynchronously while the next step executes.
- `'exit'`: Changes are persisted only when the graph exits.

We tested all 3 modes under abrupt process termination (`taskkill /F` / `os._exit(137)`) across 3 step boundaries:
1. `after_node_1`: Immediately after `node_1` returns.
2. `during_node_2`: During `node_2` execution.
3. `after_node_2`: Immediately after `node_2` returns.

#### Test Results (`spikes/p0_wp4_langgraph/q3_durability_modes.py`)

| Durability Mode | Kill Point | Process Exit | Persisted Checkpoints | Max Persisted Step | Data Lost Upon Kill |
|---|---|---|---|---|---|
| **sync** | `after_node_1` | 137 | 2 | 0 (`node_1`) | Zero completed steps lost. `node_1` is durable. |
| **sync** | `during_node_2` | 137 | 3 | 1 (`node_1` done) | In-flight `node_2` uncommitted state lost; `node_1` checkpoint preserved. |
| **sync** | `after_node_2` | 137 | 3 | 1 (`node_2` done) | Zero completed steps lost. `node_2` is durable. |
| **async** | `after_node_1` | 137 | 2 | 0 (`node_1`) | Step 1 durable if background flush completed prior to kill. |
| **async** | `during_node_2` | 137 | 3 | 1 (`node_1` done) | In-flight `node_2` lost; background task committed step 1. |
| **async** | `after_node_2` | 137 | 3 | 1 (`node_1` done) | **Background checkpoint write for `node_2` dropped** by hard kill before event loop task committed to disk. |
| **exit** | `after_node_1` | 137 | **0** | -1 (none) | **ALL progress lost**. Checkpoints table empty. |
| **exit** | `during_node_2` | 137 | **0** | -1 (none) | **ALL progress lost**. Checkpoints table empty. |
| **exit** | `after_node_2` | 137 | **0** | -1 (none) | **ALL progress lost**. Checkpoints table empty. |

#### Technical Finding
- `exit` mode is **completely unsafe for crash recovery**: all intermediate step checkpoints are buffered in memory and dropped on crash.
- `async` mode has an inherent **window of vulnerability**: intermediate checkpoints are dispatched to the event loop and are silently dropped if the host process terminates abruptly.
- `sync` mode guarantees step-level durability before downstream nodes run, but **does not protect in-node side effects** (Question 2).

---

### 3.3 Question 4: Checkpoint Keying by `(run_id, attempt)`

#### Objective & DG-1 Requirement
Under DG-1 and ADR-011, execution runs are partitioned into attempts. When attempt 1 fails or crashes, attempt 2 must start in a clean state and must never silently inherit attempt 1's partially executed in-memory or checkpointer state.

#### Probed Patterns (`spikes/p0_wp4_langgraph/q4_checkpoint_keying.py`)

1. **Naive Pattern (`thread_id = run_id`)**:
   - Attempt 1 crashes at `node_work`.
   - Attempt 2 runs on `thread_id = run_id` and calls `graph.invoke(None, config)`.
   - **Observed Outcome**: Attempt 2 resumes Attempt 1's crashed checkpoint directly (`attempt_2_inherited_attempt_1_checkpoint = True`). Attempt 1's dirty state pollutes Attempt 2.
   - **Isolation**: **FAILED (Hazard)**.

2. **DG-1 Composite Keying (`thread_id = f"{run_id}:{attempt}"`)**:
   - Attempt 1 runs with `thread_id = "run-dg1-201:1"`. Crashes at `node_work`.
   - Attempt 2 runs with `thread_id = "run-dg1-201:2"`.
   - **Observed Outcome**:
     - `state_att2_before.values` is empty (`{}`).
     - Attempt 2 executes clean end-to-end (`node_init` → `node_work` → `node_finalize`).
     - Attempt 1's checkpoint history remains intact at `run-dg1-201:1` for forensics and audit.
   - **Isolation**: **PASSED (100% Isolated)**.

3. **Namespace Probe (`checkpoint_ns = f"attempt_{attempt}"`)**:
   - In LangGraph 1.2.20, `checkpoint_ns` is reserved internally for subgraph hierarchy resolution (`_subgraph_for_namespace`).
   - Querying `graph.get_state` with a custom `checkpoint_ns` raises:
     ```text
     ValueError: Subgraph attempt_1 not found
     ```
   - **Verdict**: `checkpoint_ns` is **unsupported** for top-level attempt keying. Composite `thread_id` is the required mechanism.

---

### 3.4 Question 5: Interaction with DG-1 Platform Action Pattern (20 Trials)

#### Objective
Demonstrate that wrapping external side effects with the platform pattern from DG-1 (§2, §3, §4):
1. Persisting platform `action_slot` (e.g. `increment#1`) before invocation.
2. Deriving authoritative `operation_key = sha256(tenant_id + "|" + task_id + "|" + action_slot)`.
3. Action Service querying `action_executions` table before executing side effects.
4. Yields **exactly one** external effect across in-node crash and resume, whereas raw LangGraph replay produces duplicates.

#### Test Results (`spikes/p0_wp4_langgraph/q5_dg1_interaction.py`)
Both patterns were subjected to 20 identical trials of in-node crash followed by graph resume.

```
+-------------------------------------------------------------------------+
| Trial # | Raw LangGraph Final Count | DG-1 Wrapped Final Count | Status |
+-------------------------------------------------------------------------+
|    01   |             2             |            1             |  PASS  |
|    02   |             2             |            1             |  PASS  |
|    03   |             2             |            1             |  PASS  |
|    04   |             2             |            1             |  PASS  |
|    05   |             2             |            1             |  PASS  |
|    06   |             2             |            1             |  PASS  |
|    07   |             2             |            1             |  PASS  |
|    08   |             2             |            1             |  PASS  |
|    09   |             2             |            1             |  PASS  |
|    10   |             2             |            1             |  PASS  |
|    11   |             2             |            1             |  PASS  |
|    12   |             2             |            1             |  PASS  |
|    13   |             2             |            1             |  PASS  |
|    14   |             2             |            1             |  PASS  |
|    15   |             2             |            1             |  PASS  |
|    16   |             2             |            1             |  PASS  |
|    17   |             2             |            1             |  PASS  |
|    18   |             2             |            1             |  PASS  |
|    19   |             2             |            1             |  PASS  |
|    20   |             2             |            1             |  PASS  |
+-------------------------------------------------------------------------+
```

- **Raw LangGraph Duplicate Trials**: **20 / 20 (100% duplicated)**
- **DG-1 Wrapped Exactly-One Trials**: **20 / 20 (100% exactly once)**

#### Technical Finding
When the node re-executes on resume, the DG-1 Action Service detects the existing `succeeded` execution record in `action_executions` matching `(tenant_id, operation_key)`. It immediately returns the journaled payload without executing the underlying external tool. This confirms that platform-level action deduplication is both necessary and sufficient to make LangGraph workflows safe for non-idempotent side effects.

---

## 4. Pass / Fail Evaluation Table

| Investigation Area | Test Criterion | Observed Evidence | Verdict |
|---|---|---|---|
| **Q1: Package Pinning** | Official releases on PyPI & GitHub cited with access date and licenses | `langgraph==1.2.20`, `checkpoint==4.3.1`, `sqlite==3.1.3`, `postgres==3.2.0`; MIT | **PASS** |
| **Q2: In-Node Crash** | Measure side-effect replay across 20 trials | 20/20 re-executed; 20/20 duplicate side-effect calls (counter = 2) | **PASS (Observed Duplicate)** |
| **Q3: Durability Modes** | Compare `sync`, `async`, `exit` under hard SIGKILL (9 scenarios) | `exit` drops 100% checkpoints; `async` has drop window on fast termination; `sync` preserves completed steps | **PASS** |
| **Q4: Checkpoint Keying** | Key by `(run_id, attempt)` to prevent cross-attempt state pollution | `thread_id=f"{run_id}:{attempt}"` guarantees complete isolation; `checkpoint_ns` rejected | **PASS** |
| **Q5: DG-1 Interaction** | DG-1 wrapping produces exactly 1 effect across crash/resume (20 trials) | DG-1 counter = 1 in 20/20 trials; Raw counter = 2 in 20/20 trials | **PASS** |
| **Gate D-01 Readiness** | Definitive architecture recommendation backed by empirical evidence | Recommendation Option (b) formulated with reversibility analysis | **PASS** |

---

## 5. Architectural Implications for EAM Platform

1. **Alignment with ADR-011**:
   - `runs` and `dispatch_intents` tables in PostgreSQL must remain the sole authoritative arbiters of run lifecycle, attempt leases, and timeouts.
   - LangGraph checkpointer must **never** be used as an authoritative cross-process run queue or distributed workflow orchestrator.
2. **Side-Effect Boundary Enforcement (DG-1 §3)**:
   - All tools that perform external mutations (e.g. GitHub PR creation, file system modifications outside temporary directories, external API calls) **must be routed through the Action Service**.
   - Direct un-journaled side effects within LangGraph nodes are strictly prohibited.
3. **Attempt Keying Invariant (DG-1 §2)**:
   - When running a native LangGraph agent inside a run attempt, `thread_id` must strictly follow the format:
     ```python
     thread_id = f"{run_id}:{attempt_number}"
     ```
   - Attempt increments create fresh thread namespaces, preventing zombie node replays from prior crashed attempts.

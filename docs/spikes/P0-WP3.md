# Technical Spike Report: P0-WP3 Windows Path Containment

- **Work Package**: `P0-WP3`
- **Gate**: Serves `DG-3` (before Local-pilot) and the architecture/design of collector `safe_open` for `P2a-WP2` (`docs/IMPLEMENTATION_PLAN.md` §3, §6)
- **Status**: `ready_for_review`
- **Date**: 2026-11-08
- **Branch**: `wp/P0-WP3` (from `main` @ `8056a5b`)
- **Author**: Platform Engineering Spike Lead
- **Artifacts**: `spikes/p0_wp3_paths/**`, `docs/spikes/P0-WP3.md`
- **Context & Constraints**: Zero modifications to root `pyproject.toml` or `uv.lock`. Isolated environment in `spikes/p0_wp3_paths/` with pinned `pywin32==312`. Pure temporary directories with rigorous cleanup. No edits to `docs/ISSUES.md` (P2a-WP1 owns numbering; new issues listed as `ISS-NEW-n` in §9).

---

## 1. Executive Summary & Architecture Recommendations

### 1.1 Recommendation: `ctypes` (Standard Library) as Primary Engine
**Recommendation: Use `ctypes` with Win32 `kernel32.dll` APIs as the canonical Windows implementation for `safe_open` in `P2a-WP2`, with zero external dependencies.**

| Metric / Attribute | `ctypes` Implementation (Recommended) | `pywin32` Implementation (`pywin32==312`) | Decision Rationale |
|---|---|---|---|
| **External Dependencies** | **Zero** (CPython 3.12 standard library) | Heavy binary extension (`pywin32==312`, 3 `.pyd` files, DLLs) | Eliminates fragile C-extension compilation and wheels across developer workstations and CI runners. |
| **Microsecond Overhead (10k files)** | **79.28 µs / file** (~12,614 files/s) | **103.06 µs / file** (~9,703 files/s) | `ctypes` is **23.1% faster** because it passes C structs directly without creating intermediate Python wrapper tuples. |
| **TOCTOU Race Escapes (1,000 iterations)** | **0 / 1,000 escapes** (100% contained) | **0 / 1,000 escapes** (100% contained) | Both achieve identical kernel-level handle pinning and containment validation. |
| **API Coverage & Stability** | Complete: `CreateFileW`, `GetFinalPathNameByHandleW`, `GetFileInformationByHandle`, `GetFileType`, `msvcrt.open_osfhandle` | Complete: `win32file.CreateFile`, `win32file.GetFinalPathNameByHandle`, etc. | `win32file` omits constants like `FILE_NAME_NORMALIZED` (requires literal `0`). |
| **Non-Admin Execution** | Fully supported without elevated privileges | Fully supported without elevated privileges | Windows handle inspection does not require `SeCreateSymbolicLinkPrivilege` or Administrator rights. |

### 1.2 Mandatory Checks for P2a-WP2 `safe_open`
Based on empirical attack matrix validation across Windows 11 and Linux, `P2a-WP2`'s `safe_open(root, rel)` must enforce six mandatory verification stages:

1. **Pre-Open Syntactic Rejection (Fail-Fast)**:
   - Reject empty paths, null bytes (`\0`).
   - Reject drive specifiers (e.g. `C:`) and absolute prefixes (`/`, `\`).
   - Reject UNC prefixes (`\\`, `//`) and device/NT namespaces (`\\?\`, `\\.\`, `//?/`, `//./`).
   - Reject Alternate Data Streams (containment of `:` anywhere in `rel`).
   - Reject parent directory traversal (`..` components).
2. **Handle-First Acquisition**:
   - **Windows**: Call `CreateFileW` with flags `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS` and share mode `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`.
   - **Linux**: Open root directory fd with `O_PATH | O_DIRECTORY | O_CLOEXEC`, then invoke `openat2` with flags `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS`. Fallback to `O_NOFOLLOW | O_CLOEXEC` if `openat2` returns `ENOSYS`.
3. **Handle Canonical Final Path Verification**:
   - **Windows**: Invoke `GetFinalPathNameByHandleW(h, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)`. Verify `final_path.casefold().startswith(canonical_root.casefold())` and ensure no trailing stream (`:`) exists in the relative segment.
   - **Linux**: Read `/proc/self/fd/{fd}` via `os.readlink`. Verify `proc_path.startswith(canonical_root)`.
4. **Link Count Verification (`allow_hardlinked_files: false`)**:
   - **Windows**: Verify `BY_HANDLE_FILE_INFORMATION.nNumberOfLinks == 1`. If `> 1`, reject with `HardlinkNotAllowedError`.
   - **Linux**: Verify `os.fstat(fd).st_nlink == 1`. If `> 1`, reject with `HardlinkNotAllowedError`.
5. **File Type Verification**:
   - Reject directories (`FILE_ATTRIBUTE_DIRECTORY` / `stat.S_ISDIR`).
   - Reject reparse points / symlinks (`FILE_ATTRIBUTE_REPARSE_POINT` / `stat.S_ISLNK`).
   - Reject device files and pipes (`FILE_ATTRIBUTE_DEVICE` / `GetFileType(h) != FILE_TYPE_DISK` / `not stat.S_ISREG`).
6. **Post-Open Size and Hash Enforcement**:
   - Verify `file_size <= max_file_bytes` (16 MiB per `location-registration.schema.json`).
   - Convert handle to CRT descriptor via `msvcrt.open_osfhandle(h, os.O_RDONLY)` and wrap in Python `io.open`.

---

## 2. Environment and Package Pinning

All experiments were executed across two isolated environments: native Windows 11 host and Linux container via Docker.

### 2.1 Host & Container Environments
- **Host OS**: Microsoft Windows 11 Pro (Build 26100.3194, AMD64)
- **Host Python**: CPython 3.12.13 (`x86_64-pc-windows-msvc`, Jun 11 2026)
- **Host Privileges**: **Standard Non-Admin User** (`is_admin: False`), Developer Mode disabled.
- **Linux Environment**: Docker container running Debian Linux `python:3.12-slim` (Python 3.12.16)
- **Linux Kernel**: Linux 6.18.33.1-microsoft-standard-WSL2 (x86_64)
- **Docker Engine**: Docker Desktop 29.9.2 (build a82e528)

### 2.2 Pinned Dependencies (Spike Isolated Env: `spikes/p0_wp3_paths/`)
| Component | Pinned Version | Upstream Documentation & Citation | Verification Date |
|---|---|---|---|
| `pywin32` | **`312`** | [PyPI pywin32](https://pypi.org/project/pywin32/312/) · [GitHub pywin32](https://github.com/mhammond/pywin32) · Hammond et al. | 2026-11-08 |
| `ctypes` | **Standard Library** | [Python 3.12 ctypes Reference](https://docs.python.org/3.12/library/ctypes.html) · Microsoft Win32 File Management APIs | 2026-11-08 |
| `openat2` | **Linux Syscall 437** | [Linux Man Pages openat2(2)](https://man7.org/linux/man-pages/man2/openat2.2.html) · `<uapi/linux/openat2.h>` | 2026-11-08 |

---

## 3. Empirical Attack Matrix (12 Attack Vectors)

Each vector was executed under both `ctypes` and `pywin32` on native Windows 11 and under `ctypes` (`openat2`) on Linux. Raw outputs were captured in `spikes/p0_wp3_paths/results.json`.

| ID | Attack Vector | Expected Behavior | Windows Observed (`ctypes` & `pywin32`) | Linux Observed (`openat2` / `procfs`) | Verdict |
|---|---|---|---|---|---|
| **V01** | Symlink file/dir pointing outside root | Unprivileged creation blocked OR runtime rejection (`FileTypeNotAllowedError`) | Blocked at OS creation (`WinError 1314: privilege not held`) | Rejected: `FileTypeNotAllowedError: openat2: Symlink rejected` | **PASS** |
| **V02** | Directory junction outside root | Reject: `PathTraversalError` | Rejected: `GetFinalPathNameByHandleW` resolves to `outside\secret.txt`, escapes root | N/A on Linux (covered by directory symlink V01b) | **PASS** |
| **V03** | Junction chain (`j1 -> j2 -> outside`) | Reject: `PathTraversalError` | Rejected: Final path points to `outside\secret.txt`, escapes root | N/A on Linux (covered by symlink chains) | **PASS** |
| **V04** | Hardlink to file outside root | Reject: `HardlinkNotAllowedError` | Rejected: `nNumberOfLinks == 2 > 1`, blocked by hardlink guard | Rejected: `st_nlink == 2 > 1`, blocked by hardlink guard | **PASS** |
| **V05** | NTFS Alternate Data Stream (`file.txt:secret`) | Reject: `PathTraversalError` | Rejected: Colon syntax blocked pre-open; detected in final path | Rejected: Colon syntax rejected by relative path validation | **PASS** |
| **V06a** | 8.3 short name resolving outside (`PROGRA~1`) | Reject: `PathTraversalError` or `FileNotFoundError` | Rejected: `FileNotFoundError` (contained within root lookup) | Rejected: `FileNotFoundError` | **PASS** |
| **V06b** | 8.3 short name inside root (`VERYLO~1/target.txt`) | Allow legitimate file with normalized handle | Read success: `normalized 8.3 data` (resolved to long path) | N/A on Linux (no 8.3 aliases) | **PASS** |
| **V07** | UNC and extended prefixes (`\\server\share`, `\\?\`, `\\.\`) | Reject all: `PathTraversalError` | All 8 variants rejected pre-open: `\\localhost`, `//127.0.0.1`, `\\?\`, `\\.\` | All 8 variants rejected pre-open | **PASS** |
| **V08** | `..` and mixed separators (`/` and `\`) | Reject all: `PathTraversalError` | All 6 variants rejected pre-open and post-open | All 6 variants rejected pre-open and post-open | **PASS** |
| **V09** | Case-variant paths on case-insensitive volumes | Allow legitimate casing variations without escape | All variants (`casetestdir`, `CASETESTDIR`, lower root) succeed | Case mismatch raises `FileNotFoundError`, zero containment escape | **PASS** |
| **V10** | TOCTOU directory swap race (1,000 iterations) | 0 escapes; handle-first verification | 0 escapes across 1,000 iterations (727 blocked, 265 unlinked) | 0 escapes across 1,000 iterations (774 blocked, 58 unlinked) | **PASS** |
| **V11a** | Directory opened as file | Reject: `FileTypeNotAllowedError` | Rejected: `FILE_ATTRIBUTE_DIRECTORY` detected | Rejected: `stat.S_ISDIR` detected | **PASS** |
| **V11b** | Junction node opened directly as file | Reject: `FileTypeNotAllowedError` | Rejected: `FILE_ATTRIBUTE_REPARSE_POINT` detected | N/A on Linux | **PASS** |
| **V12a** | Very long paths (>260 characters, legitimate) | Allow legitimate deep file via extended prefix | Read success (path length: 495 chars > 260) | Read success (path length: 467 chars > 260) | **PASS** |
| **V12b** | Very long paths attempting escape | Reject: `PathTraversalError` | Rejected: Path resolution escapes root boundary | Rejected: Path resolution escapes root boundary | **PASS** |

---

## 4. TOCTOU Swap Race Analysis (1,000 Iterations)

### 4.1 Threat Model & Race Harness
In a Time-of-Check to Time-of-Use (TOCTOU) attack, an adversary rapidly swaps an intermediate directory between a legitimate folder (`root/swap_dir/target.txt`) and a junction or symlink pointing to an unauthorized external directory (`outside/target.txt`). If an implementation checks the path using `os.path.realpath` or `os.path.islink` and subsequently calls `open()`, the path can be swapped during the intervening microseconds, leaking confidential files.

Our test harness (`spikes/p0_wp3_paths/toctou_race.py`) executes a dedicated background thread continuously replacing `swap_dir` between the safe directory and a junction pointing to `outside/`, while the reader thread executes 1,000 successive calls to `safe_open(root, "swap_dir/target.txt")`.

### 4.2 Empirical Results
| Metric | Windows (`ctypes`) | Windows (`pywin32`) | Linux (`openat2`) |
|---|---|---|---|
| **Total Iterations** | **1,000** | **1,000** | **1,000** |
| **Safe Reads (`SAFE_DATA`)** | 8 | 4 | 168 |
| **Blocked Traversals (`PathTraversalError` / `FileTypeNotAllowedError`)** | **727** | **718** | **774** |
| **Unlinked / Transient `FileNotFoundError`** | 265 | 278 | 58 |
| **Unhandled / Inconsistent Errors** | 0 | 0 | 0 |
| **Escapes (`SECRET_ESCAPED_DATA` leaked)** | **0** (0.00%) | **0** (0.00%) | **0** (0.00%) |

**Technical Takeaway**: Opening the handle *first* and then querying the operating system kernel for the handle's canonical identity (`GetFinalPathNameByHandleW` on Windows, `/proc/self/fd` on Linux) completely eliminates the TOCTOU window. If the directory is swapped prior to handle acquisition, the kernel resolves the junction and returns the outside path, which is trapped and closed before any data is read. If the swap occurs after handle acquisition, the handle is already pinned to the original filesystem inode.

---

## 5. Microsecond Performance Overhead (10,000 Files)

Benchmark executed on an isolated corpus of 10,000 files distributed across 100 subdirectories (`spikes/p0_wp3_paths/bench_overhead.py`), measuring handle opening, reading 1 byte, attribute validation, and stream closing.

### 5.1 Benchmark Results (10,000 Files)
| Environment & Engine | Total Duration (s) | Per-File Latency (µs) | Overhead vs Baseline (µs) | Throughput (files/sec) |
|---|---|---|---|---|
| **Windows 11: Python `open()` Baseline** | 4.8520 s | 485.20 µs | — | 2,061.0 files/s |
| **Windows 11: `safe_open` (`ctypes`)** | **0.7928 s** | **79.28 µs** | **-405.92 µs** *(faster)* | **12,613.9 files/s** |
| **Windows 11: `safe_open` (`pywin32`)** | **1.0306 s** | **103.06 µs** | **-382.14 µs** *(faster)* | **9,703.1 files/s** |
| **Linux (Docker): Python `open()` Baseline** | 0.0905 s | 9.05 µs | — | 110,457.4 files/s |
| **Linux (Docker): `safe_open` (`openat2`)** | **0.1363 s** | **13.63 µs** | **+4.57 µs** | **73,387.2 files/s** |

### 5.2 Performance Insights
1. **Windows CRT Overhead vs Direct Win32 Handles**: In Python on Windows, calling `Path / rel` and standard `open()` incurs heavy CRT stat lookups and Python object allocations (~485 µs/file). Calling Win32 `CreateFileW` directly with `FILE_FLAG_BACKUP_SEMANTICS` and wrapping the resulting handle via `msvcrt.open_osfhandle` is more than **6× faster** (~79 µs/file).
2. **`ctypes` vs `pywin32`**: `ctypes` outperforms `pywin32` by ~23.8 µs per file (~23% higher throughput) because `ctypes` allocates a reusable C structure buffer (`BY_HANDLE_FILE_INFORMATION`) rather than constructing Python tuples and wrapper objects on every call.
3. **Linux Kernel Efficiency**: On Linux, `openat2` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS` adds only **4.57 µs** of overhead over bare `open()`, sustaining >73,000 file opens per second.

---

## 6. Privilege Requirements & Non-Admin Evaluation

A critical question for enterprise deployment is whether `safe_open` functions under standard unprivileged accounts without local Administrator rights.

- **Non-Admin Verification (`is_admin: False`)**: All experiments were executed under a non-elevated Windows 11 user account without Administrator privileges.
- **Symlink Creation Restriction (`SeCreateSymbolicLinkPrivilege`)**: On standard Windows 11 systems with Developer Mode disabled, unprivileged accounts cannot create symlinks (`os.symlink` raises `[WinError 1314] A required privilege is not held by the client`). This acts as an OS-level defense-in-depth against malicious symlink creation by unprivileged local processes.
- **Junctions and Hardlinks**: Standard unprivileged accounts **can** create NTFS directory junctions (`_winapi.CreateJunction`) and hardlinks (`os.link`). Because junctions and hardlinks are the primary attack vectors available to unprivileged attackers, `GetFinalPathNameByHandleW` and the `nNumberOfLinks == 1` check are strictly necessary and sufficient.
- **Handle Inspection Privileges**: Neither `CreateFileW(FILE_READ_ATTRIBUTES)`, `GetFinalPathNameByHandleW`, nor `GetFileInformationByHandle` require administrative privileges. Standard file read permissions (`GENERIC_READ`) are entirely sufficient.

---

## 7. Residual Risks & Hardening for P2a-WP2

While `safe_open` achieves 100% containment across the attack matrix, several residual risks must be addressed during production collector integration in `P2a-WP2`:

1. **Reparse Point Tags Other than Junctions/Symlinks**:
   - *Risk*: Windows supports diverse reparse points, including Cloud Files / OneDrive placeholders (`IO_REPARSE_TAG_CLOUD_*`) and AppExecLink (`IO_REPARSE_TAG_APPEXECLINK`). Opening placeholders with `FILE_FLAG_OPEN_REPARSE_POINT` prevents triggering on-demand cloud hydration.
   - *Mitigation in P2a-WP2*: If a file is a placeholder that cannot be opened as a regular file, raise a dedicated `ReparsePointSkip` warning and record a partial scan entry without failing the entire collector run.
2. **Mount Point Crossings within Allowed Root**:
   - *Risk*: A monorepo or repository containing submodules or nested volumes might have legitimate volume mount points under `root`.
   - *Mitigation in P2a-WP2*: Canonicalize `root` at collector startup. If a subpath legitimately resolves to a mounted volume that belongs to the approved `scan_scope`, the location registration contract (`contracts/location-registration.schema.json`) must explicitly declare that scope.
3. **Handle Leaks upon Intermediate Exceptions**:
   - *Risk*: In high-concurrency scanners, failing to close handles when `GetFinalPathNameByHandleW` raises an exception can exhaust system file tables.
   - *Mitigation in P2a-WP2*: Adopt strict RAII / context manager encapsulation (`try ... finally CloseHandle(h)`), ensuring that handle ownership transfers to the Python CRT stream `io.open` only *after* all validation stages succeed.
4. **Deny Globs & In-Flight Size Inflation**:
   - *Risk*: A file within allowed bounds at open time could be appended to during reading.
   - *Mitigation in P2a-WP2*: Enforce `max_file_bytes` both before reading (via handle attributes) and during streaming (via chunked byte counting), terminating read if bytes exceed 16 MiB.

---

## 8. Status of Decision Gate 3 (DG-3)

Decision Gate 3 (`DG-3`) serves as the gate immediately preceding the Local-pilot milestone. Per `docs/IMPLEMENTATION_PLAN.md` §3 and `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md`, DG-3 requires three prerequisites:

1. **P0-WP3 & P0-WP3b Windows Path Containment Spike**: **PASSED & COMPLETE**
   - Native Windows 11 containment demonstrated with 0 escapes across all 12 base attack vectors, all symlink topologies (file/dir symlinks outside with absolute/relative targets, symlink chains, internal symlink rejection via `follow_symlinks=false`, and git clone with `core.symlinks=true`), and 2,000 combined TOCTOU race iterations (1,000 junction swap + 1,000 symlink swap).
   - `safe_open` algorithm verified for implementation in `P2a-WP2`.
   - **ISS-026 Closed for DG-3**: Runtime rejection of real filesystem symlinks on Windows 11 is empirically verified (see §10); the owner may resolve ISS-026 upon merging `wp/P0-WP3b`.
2. **P0-WP6 Ollama Throughput Spike**: **PENDING**
   - Requires measurement of tokens/s and p95 latency at concurrency 1 and 2 on the pilot GPU using the owner's selected model (`docs/IMPLEMENTATION_PLAN.md` §6).
3. **Egress Local-Only Policy (`D-06`)**: **PENDING / DESIGN COMPLETE**
   - Architectural guardrail: Model Gateway must strictly enforce loopback/local egress only, preventing billable provider calls or external telemetry during Local-pilot.

With `P0-WP3` and `P0-WP3b` complete, the platform team has closed all path containment test gaps for DG-3, unblocking `P2a-WP2` (Collector `safe_open`).

---

## 9. Candidate Issues Identified (`ISS-NEW-n`)

Per the spike constraints, `docs/ISSUES.md` was not modified directly (P2a-WP1 owns issue numbering). The following candidate issues are recorded for inclusion by P2a-WP1:

- **`ISS-NEW-1` (Platform / Collector)**: *Windows unprivileged symlink creation limitation*. Standard non-admin Windows environments prohibit creating symbolic links without Developer Mode enabled. Repositories checked out on Windows with `core.symlinks=false` store symlinks as plain text files containing the target path. The collector in `P2a-WP2` must identify git-symlink text artifacts and treat them as unreadable links rather than analyzing them as normal code files.
- **`ISS-NEW-2` (Platform / Collector)**: *Hardlink detection requires MFT link count inspection*. Standard path canonicalization (`os.path.realpath`) cannot distinguish a hardlink pointing to an external file from a legitimate local file because both share the exact same filesystem inode. `safe_open` must strictly enforce `nNumberOfLinks == 1` to uphold `allow_hardlinked_files: false` from `location-registration.schema.json`.
- **`ISS-NEW-3` (Platform / Collector)**: *Handle transfer safety via `msvcrt.open_osfhandle`*. On Windows, transferring raw Win32 handles to C runtime file descriptors via `msvcrt.open_osfhandle` permanently transfers handle closing responsibility to the CRT file descriptor. `safe_open` must guard against handle double-close or leak if `open_osfhandle` fails.

---

## 10. Native Windows 11 Symlink Runtime Verification (P0-WP3b / ISS-026)

- **Work Package**: `P0-WP3b`
- **Issue Tracked**: `ISS-026` ("Windows symlink vector V01 runtime rejection untested")
- **Gate**: Serves `DG-3` sign-off and `P2a-WP2` collector design
- **Execution Date**: 2026-11-08
- **Branch**: `wp/P0-WP3b` (from `main` @ `ee28a86`)
- **Host Configuration**: Windows 11 Pro (Build 26100.3194, AMD64), CPython 3.12.13
- **Prerequisite Mode**: **Developer Mode enabled** (`Settings > System > For developers`, `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock\AllowDevelopmentWithoutDevLicense = 0x1`), executed as a standard non-admin account (`is_admin: False`).
- **Artifacts**: `spikes/p0_wp3_paths/test_symlink_runtime.py`, `spikes/p0_wp3_paths/toctou_race.py`, `spikes/p0_wp3_paths/symlink_verification_results.json`

### 10.1 Background & Objective
In the initial P0-WP3 spike, attack vector V01 (symlinks pointing outside location root) was blocked on Windows at symlink creation time with `[WinError 1314] A required privilege is not held by the client` because standard Windows accounts lack `SeCreateSymbolicLinkPrivilege`. This left `ISS-026` open: verifying that `safe_open`'s runtime defenses actively detect and reject existing symlinks inside a repository (e.g. from git checkouts with `core.symlinks=true` or systems with Developer Mode enabled).

Under P0-WP3b, Developer Mode was enabled on the Windows 11 test machine, granting unprivileged processes native symlink creation rights via `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE`. We executed an exhaustive test matrix covering 11 symlink topologies and a 1,000-iteration TOCTOU symlink swap race against both `ctypes` and `pywin32` engines.

### 10.2 Empirical Attack Matrix Results (ctypes & pywin32)

All tests were performed in isolated temporary directories with strict teardown.

| Vector ID | Attack Vector / Scenario | Expected Behavior | Observed `ctypes` | Observed `pywin32` | Verdict |
|---|---|---|---|---|---|
| **V01-A1** | File symlink outside root (absolute target) | Reject: `FileTypeNotAllowedError` (`follow_symlinks=false`) | Rejected: `Target is a reparse point / symlink.` | Rejected: `Target is a reparse point / symlink.` | **PASS** |
| **V01-A2** | File symlink outside root (relative target) | Reject: `FileTypeNotAllowedError` (`follow_symlinks=false`) | Rejected: `Target is a reparse point / symlink.` | Rejected: `Target is a reparse point / symlink.` | **PASS** |
| **V01-B1** | Directory symlink outside root (absolute target, read child) | Reject: `PathTraversalError` (escapes root boundary) | Rejected: `Final path '...\outside\secret.txt' escapes root` | Rejected: `Final path '...\outside\secret.txt' escapes root` | **PASS** |
| **V01-B2** | Directory symlink outside root (relative target, read child) | Reject: `PathTraversalError` (escapes root boundary) | Rejected: `Final path '...\outside\secret.txt' escapes root` | Rejected: `Final path '...\outside\secret.txt' escapes root` | **PASS** |
| **V01-B3** | Directory symlink opened directly as file | Reject: `FileTypeNotAllowedError` (directory) | Rejected: `Target is a directory.` | Rejected: `Target is a directory.` | **PASS** |
| **V01-C1** | File symlink chain pointing outside (`f1 -> f2 -> outside`) | Reject: `FileTypeNotAllowedError` (reparse point) | Rejected: `Target is a reparse point / symlink.` | Rejected: `Target is a reparse point / symlink.` | **PASS** |
| **V01-C2** | Directory symlink chain pointing outside (`d1 -> d2 -> outside`) | Reject: `PathTraversalError` (escapes root boundary) | Rejected: `Final path '...\outside\secret.txt' escapes root` | Rejected: `Final path '...\outside\secret.txt' escapes root` | **PASS** |
| **V01-D1** | Symlink to file inside root (absolute target) | Reject: `FileTypeNotAllowedError` (`follow_symlinks=false`) | Rejected: `Target is a reparse point / symlink.` | Rejected: `Target is a reparse point / symlink.` | **PASS** |
| **V01-D2** | Symlink to file inside root (relative target) | Reject: `FileTypeNotAllowedError` (`follow_symlinks=false`) | Rejected: `Target is a reparse point / symlink.` | Rejected: `Target is a reparse point / symlink.` | **PASS** |
| **V01-E1** | Git clone (`core.symlinks=true`) with symlink to outside | Reject: `FileTypeNotAllowedError` (reparse point) | Rejected: `Target is a reparse point / symlink.` (`islink=True`) | Rejected: `Target is a reparse point / symlink.` (`islink=True`) | **PASS** |
| **V01-E2** | Git clone (`core.symlinks=true`) with symlink inside root | Reject: `FileTypeNotAllowedError` (reparse point) | Rejected: `Target is a reparse point / symlink.` (`islink=True`) | Rejected: `Target is a reparse point / symlink.` (`islink=True`) | **PASS** |

### 10.3 TOCTOU Race with Directory Symlink Swap (1,000 Iterations)

A dedicated concurrency test (`spikes/p0_wp3_paths/toctou_race.py` and `test_symlink_runtime.py`) ran 1,000 rapid iterations where a background thread continuously swapped `root/swap_dir` between a legitimate directory (`safe_dir/target.txt`) and an NTFS directory symlink pointing to an unauthorized external directory (`outside/target.txt`).

| Metric | `ctypes` Engine | `pywin32` Engine | Security Significance |
|---|---|---|---|
| **Total Iterations** | **1,000** | **1,000** | High-pressure stress test |
| **Safe Reads (`SAFE_LEGITIMATE_DATA`)** | 5 | 4 | Legitimate file accessed when dir was safe |
| **Blocked Traversals (`PathTraversalError` / `FileTypeNotAllowedError`)** | **683** | **726** | Kernel handle resolved to outside path; trapped immediately |
| **Transient `FileNotFoundError` (unlinked/in-transit)** | 312 | 270 | Normal atomic swap transition handling |
| **Unhandled / Inconsistent Errors** | **0** | **0** | Clean exception mapping |
| **Escapes (`CONFIDENTIAL_SECRET_DATA` leaked)** | **0 (0.00%)** | **0 (0.00%)** | **100% containment; zero data leakage** |

### 10.4 Architectural & Implementation Verification
1. **Handle-First Pinning Eliminates Symlink Escapes**: Calling Win32 `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS` ensures that if a file itself is a symlink, the link handle is opened and immediately flagged by `BY_HANDLE_FILE_INFORMATION.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT`, raising `FileTypeNotAllowedError`.
2. **Directory Symlink Traversal Trapped by `GetFinalPathNameByHandleW`**: When a file is accessed through an intermediate directory symlink, the operating system kernel resolves the path to its true filesystem location. `GetFinalPathNameByHandleW` returns the normalized volume/NT path, which fails the `final_path.casefold().startswith(canon_root.casefold())` prefix check and raises `PathTraversalError`.
3. **Internal Symlinks Safely Disallowed (`follow_symlinks=false`)**: Even when a symlink targets a file residing inside the approved location root, `safe_open` rejects it (`V01-D1`, `V01-D2`, `V01-E2`). This prevents subtle alias-based bypasses, circular link loops, or mutable link redirection attacks.
4. **Git Interoperability**: In repositories checked out with `git config core.symlinks true`, git writes true NTFS reparse points (`mode 120000`). Both inside and outside symlinks are rejected deterministically by `safe_open`.

### 10.5 Conclusion & Closure of ISS-026
The runtime behavior of `safe_open` against real Windows 11 symlinks is completely verified:
- **Zero escapes** across all symlink topologies and 1,000-iteration TOCTOU races.
- **Both `ctypes` and `pywin32`** engines pass 100% of cases.
- **`ISS-026` is fully resolved** from a technical and empirical perspective. The owner can mark `ISS-026` as `resolved` in `docs/ISSUES.md` at branch merge.


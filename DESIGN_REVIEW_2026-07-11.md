# Design Review — Workstation Health Dashboard
Date: 2026-07-11
Reviewer lens: software-design rubric (APoSD / Pragmatic Programmer / Design of Design), seven-dimension checklist.
Scope: design/architecture quality of the whole current working tree — module boundaries, interfaces, coupling, error handling, naming, testability, and the frontend/backend contract. This is NOT a functional bug list; the two prior docs (`DASHBOARD_REVIEW.md`, `DASHBOARD_REVIEW_COMPLETE.md`) already catalog symptom-level bugs. Where a finding here overlaps one of theirs, it is noted, and I frame the *design root cause* rather than the symptom.

Calibration note: this is a single-user, single-maintainer tool on one workstation. Severities are set for *that* context — I am not asking for enterprise process, auth, or horizontal-scale concurrency. "Critical" here means "can silently corrupt data or produce wrong readings on this one box"; "important" means "will make the next change materially harder or is actively misleading"; "minor" means "clarity/cleanliness".

Each finding is self-contained so a separate agent can act on it without shared context: `[ID] file:line — summary / why it matters / fix direction / severity / ordering`.

---

## Verdict

**Needs revision before further feature work.** The system works and is reasonably modular at the top level (collectors → detection → db → ws stream is a clean spine). But three design-level problems will keep generating the kind of symptom-bugs the prior reviews found: (1) the SQLite concurrency model is not actually sound — the "single writer thread + queue" serializes only *inserts* while reads and the daily cleanup share one cursor/connection across threads with no lock; (2) the "metrics dict" is an un-versioned implicit contract duplicated across four boundaries, and the DB read path returns a *different* shape than the write/stream path; (3) threshold configuration has four competing sources of truth, two of them dead, and the numbers that actually drive alerts are hardcoded inside the detector. Fix those three roots and most of the remaining findings are mop-up.

Single most valuable fix: **CONC-01** (shared cursor race) — it is the only finding that can corrupt persisted data.

---

## Theme A — Concurrency & database (the "is it sound?" question)

### [CONC-01] database/__init__.py:42-46, 139-203 — Reads and cleanup share one cursor/connection with the writer thread; only inserts are serialized.
- **Defect:** `insert_metrics` queues a closure onto the writer thread, but `query_metrics`, `cleanup_old_metrics`, and `get_stats` call `self.cursor.execute(...)` / `self.conn.commit()` **directly from the asyncio event-loop thread**, on the *same* `Cursor` and `Connection` the writer thread is using. `check_same_thread=False` disables sqlite3's safety guard but adds no synchronization; there is no lock anywhere.
- **Why it matters:** When `/api/history` (or the daily `periodic_cleanup`) executes on the event-loop thread while the writer thread is mid-`execute`/`commit`, sqlite3 can raise `Recursive use of cursors not allowed`, return interleaved/garbled rows, or mis-commit. `cleanup_old_metrics` is a `DELETE` — i.e. a *second writer* that bypasses the very queue built to serialize writes. This is exactly the "shared cursor accessed from multiple contexts" concern; the queue creates an illusion of safety that only covers one of three write/read paths. On a single box the window is small but the failure is silent data corruption / dropped writes.
- **Fix direction:** Make the writer thread the *only* code that touches the connection. Route cleanup through the same queue (submit the DELETE as a queued closure). For reads, either (a) give each read its own short-lived `sqlite3.connect()` (WAL allows concurrent readers), or (b) serialize reads through a single `threading.Lock` guarding all cursor use. Do not share one long-lived cursor across threads. Use `cursor = conn.cursor()` per operation rather than one persistent `self.cursor`.
- **Severity:** critical
- **Ordering:** Root cause; **CONC-02** and **CONC-03** are subordinate to this and should be fixed in the same pass.

### [CONC-02] database/__init__.py:88-127 — Insert timestamp is recomputed at write time instead of using the sample's own timestamp; PK is 1-second granularity.
- **Defect:** The queued closure computes `timestamp = int(time.time())` at *dequeue* time and uses it as the `INSERT OR REPLACE` primary key, ignoring `metrics['timestamp']` (a float set when the sample was collected). Queue latency means the stored time drifts from the sample time.
- **Why it matters:** With `UPDATE_INTERVAL = 1.0` and an integer-second PK, two samples that land in the same wall-clock second silently overwrite each other via `INSERT OR REPLACE` — history quietly loses points, and the stored timestamp doesn't match the timestamp the client saw. Programming-by-coincidence: it "works" only because the interval usually exceeds queue latency.
- **Fix direction:** Use `int(metrics['timestamp'])` (or store the float) so the persisted key equals the sample identity. If sub-second cadence is ever wanted, widen the PK to a REAL or a monotonic rowid + timestamp column.
- **Severity:** important
- **Ordering:** Fix alongside CONC-01 (same file/pass). Overlaps prior BUG-C05/C06 (timestamp) but this is the *storage-key* facet, not the event-loop-time facet.

### [CONC-03] database/__init__.py:205-208, app.py — `close()` never called; writer thread has no shutdown/flush path.
- **Defect:** `MetricsDatabase.close()` exists but is unreferenced; the daemon writer thread is killed abruptly on process exit with possibly-queued writes undrained, and the connection is never explicitly closed.
- **Why it matters:** On systemd stop, in-flight queued inserts can be lost, and an unclosed WAL connection contributed to the exact bloat/shutdown-latency class of operational issue already seen on this box. There is no design-by-contract for lifecycle.
- **Fix direction:** Add a FastAPI shutdown handler that stops accepting new writes, drains the queue (sentinel value to break `_process_writes`), commits, and calls `close()`. Give `_process_writes` a clean exit path instead of `while True`.
- **Severity:** minor (single-user, but trivially avoidable data loss)
- **Ordering:** After CONC-01.

---

## Theme B — The metrics-dict contract (frontend/backend & internal)

### [CONTRACT-01] (cross-cutting: metrics/*.py return dicts, detection/*.py, database/__init__.py:88-166, static/dashboard.js) — The "metrics dict" is an implicit, un-versioned contract duplicated across four independent boundaries.
- **Defect:** The shape of the metrics dict (`gpu[].gpu_util`, `gpu[].memory_util_pct`, `cpu.utilization_total`, `cpu.per_core_utils`, `memory.swap_used_gb`, `ml.active_processes[].gpu_vram_gb`, …) is defined nowhere. It is *implicitly* agreed among: each collector's return literal, the detector's `.get()` chains, the DB column mapping, and ~1200 lines of frontend JS reading `metrics.x.y`. No schema, no dataclass, no TypedDict, no JSON-schema doc.
- **Why it matters:** This is the root cause behind a whole family of the prior review's bugs (per-GPU fields, key renames, missing keys rendering as `undefined`). Any collector rename silently breaks the detector *and* the frontend with no error — the reader must hold the entire dict shape in their head to change one collector safely (max cognitive load; unknown-unknowns). Information about "what a GPU reading is" leaks into four modules.
- **Fix direction:** Define the shape once as the authoritative source — Python `@dataclass`/`TypedDict` per collector (or at minimum a single `SCHEMA.md` / JSON schema checked in). Have collectors return typed objects (serialize at the WS boundary). The frontend can stay dict-based but should reference the one documented schema. Don't try to boil the ocean; even a single committed schema doc that the collectors' return literals are diffed against removes most of the risk.
- **Severity:** important
- **Ordering:** Do before CONTRACT-02 and before any new metric is added. Overlaps ARCH-03 (detector coupling reads this same shape).

### [CONTRACT-02] database/__init__.py:150-166 vs app.py:134-152 — `/api/history` returns a *different* dict shape than `/ws` and `/api/metrics`.
- **Defect:** The live path emits nested objects (`cpu: {utilization_total, ...}`, `memory: {...}`). The DB read path flattens to `cpu_util`, `cpu_freq`, `memory_used_gb`, `memory_percent`, etc. at top level. Same conceptual data, two incompatible layouts.
- **Why it matters:** Any consumer of history must special-case a second shape; today the frontend sidesteps this only because it never actually calls `/api/history` (see DEAD-03). The moment someone wires history into the charts, they hit a silent shape mismatch. This is change-amplification baked into the persistence seam.
- **Fix direction:** Persist and return the same conceptual shape the stream uses (store the JSON blob and reconstruct, or map DB columns back into the nested shape in `query_metrics`). One canonical shape, chosen once (ties into CONTRACT-01).
- **Severity:** important
- **Ordering:** After CONTRACT-01 (needs the canonical shape decided first).

---

## Theme C — Configuration & thresholds (well-factored? no)

### [CFG-01] config.py:14-36, 46-51 + detection/bottleneck_detector.py (many lines) — Four competing sources of threshold truth; the numbers that actually fire alerts are hardcoded in the detector.
- **Defect:** Thresholds live in *four* places: (1) `config.THRESHOLDS` (defined, validated at import, but **only ever echoed by `/api/config`** — the detector never reads it); (2) `config.BOTTLENECK_DETECTION` / `ANOMALY_DETECTION` (actually used); (3) hardcoded magic numbers inside `bottleneck_detector.py` (`swap_used_gb > 1.0`, `mem_percent > 85`, `total_io > 500`, `gpu_util > 25`, `vram_pct > 95`, `mem_percent > 90`, `gpu_util > 5`); and (4) `config_manager.py::DEFAULT_CONFIG.alert_thresholds` (entirely dead — see DEAD-01).
- **Why it matters:** The one config block a maintainer would naturally edit to retune alerts (`config.THRESHOLDS`) has **no effect**; `validate_config()` dutifully validates a decoy. The real knobs are buried as literals scattered through 200 lines of detection logic. Change-amplification (retuning "when is swap critical" means hunting inline constants) plus a conceptual-integrity failure (four models of "a threshold"). This is the design root under prior BUG-C01 (false swap alert) and BUG-C12 (false idle bottleneck): the logic and its constants aren't separable.
- **Fix direction:** Pick ONE threshold home. Move every inline detector constant into `config.THRESHOLDS` (extend the schema — add swap GB limits, io threshold, pcie util gate, vram pct). Make `bottleneck_detector` read exclusively from it. Delete the other two sources (see DEAD-01) or fold them in. Then `validate_config()` guards the numbers that actually matter.
- **Severity:** important
- **Ordering:** Do before DEAD-01 (decide the canonical home, then delete the losers). Overlaps ARCH-03.

### [CFG-02] config.py:11 vs app.py:39,48 — Retention configured in two contradictory places.
- **Defect:** `config.HISTORY_RETENTION = 86400` (24h) is defined but unused; the actual cleanup uses a hardcoded `retention_days=7` passed at both call sites in `app.py`. `config_manager` has a third value (`data_retention_days: 7`).
- **Why it matters:** A maintainer editing `HISTORY_RETENTION` to change retention changes nothing; the real value is a literal argument in two spots that must be kept in sync. DRY violation on a single piece of knowledge ("how long we keep data").
- **Fix direction:** One `RETENTION_DAYS` constant in `config.py`; both `app.py` call sites and `cleanup_old_metrics`' default read it. Delete `HISTORY_RETENTION` or make it the canonical one.
- **Severity:** minor
- **Ordering:** With CFG-01 / DEAD-01.

---

## Theme D — Dead code & duplication (broken windows)

### [DEAD-01] config_manager.py (entire file, 107 lines) — Unused parallel configuration system.
- **Defect:** `ConfigManager` / `get_config_manager()` is imported by nothing (`grep` confirms zero references outside the file). It defines a *fourth* `alert_thresholds` block with yet another naming scheme (`gpu_util`, `gpu_temp`, `swap_active`).
- **Why it matters:** A reader trying to understand "how is the dashboard configured?" must read and then discover this whole file is inert — pure cognitive load, and a tempting wrong place to make a change (someone will eventually edit `DEFAULT_CONFIG` expecting an effect). Classic broken window inviting more.
- **Fix direction:** Delete the file, or — if persistent user-editable settings are genuinely wanted — make it the *single* config source and delete `config.THRESHOLDS` duplication (ties to CFG-01). Do not keep both.
- **Severity:** important (as changeability/clarity hazard)
- **Ordering:** Decide CFG-01 canonical home first, then delete.

### [DEAD-02] api/export.py (entire file, 42 lines) — Duplicate `/api/export` implementation, never mounted.
- **Defect:** `api/export.py` defines an `APIRouter` with its own `/api/export` and its own `latest_metrics` global and `update_latest_metrics()`. `app.py` never imports it and re-implements `/api/export` inline (app.py:193) with a *second* `latest_metrics` global.
- **Why it matters:** Two implementations of the same endpoint and two globals for the same state. A maintainer may edit the dead one and see no effect, or wire it up and get a double route. Duplication of both knowledge and mechanism.
- **Fix direction:** Delete `api/export.py` (the inline app.py version is the live one), or invert: move export into the router and mount it, removing the inline copy and its global. One implementation.
- **Severity:** minor
- **Ordering:** Independent; can be done anytime.

### [DEAD-03] detection/anomaly_detector.py:20,21,44 — Declared-but-unused rolling windows; anomaly apparatus is far shallower than it looks.
- **Defect:** `gpu_mem_bw_history` is created and never appended to or read. `cpu_util_history` is appended every tick but never analyzed. Only `gpu_util_history` (GPU[0] only) is used, and only to detect *drops* (negative z-score).
- **Why it matters:** The module presents a general multi-metric statistical-anomaly engine but delivers one narrow check on one GPU. A reader must reverse-engineer that the other two windows are vestigial (unknown-unknown). Shallow module dressed as deep.
- **Fix direction:** Either implement the CPU/mem-bandwidth anomaly checks the windows imply, or delete the unused windows and rename/scope the module to what it actually does ("GPU utilization drop detector"). Also generalize beyond `gpu[0]` (ties to CONTRACT-01 multi-GPU).
- **Severity:** minor
- **Ordering:** After a decision on TEST-01 (statefulness).

---

## Theme E — Architecture, module depth & coupling

### [ARCH-01] detection/anomaly_detector.py:23,42-44,106-111 + app.py:150,158 — `detect_anomalies` is stateful but presented (and called) as a pure function; `/api/metrics` pollutes its window.
- **Defect:** `collect_all_metrics()` calls `detect_anomalies(metrics)` looking like a pure transform, but it mutates a hidden module-global rolling `deque`. Crucially, `/api/metrics` (documented "for debugging") *also* calls `collect_all_metrics()` → every debug/poll hit injects an off-cadence sample into the anomaly window that also feeds the live `/ws` stream.
- **Why it matters:** The rolling statistics silently depend on *who called collect and how often* — a temporal/hidden-state coupling. A single `curl /api/metrics` skews the z-score baseline the real detector uses. Nobody can reason about anomaly output from the metrics alone (programming-by-coincidence). The prompt's premise that detection modules are "pure functions" is only true of the bottleneck detector, not this one.
- **Fix direction:** Separate the stateful history owner from the collection call. Either (a) make the anomaly detector an explicit stateful object updated *only* on the stream tick (not in `collect_all_metrics`), or (b) have `/api/metrics` call a pure `collect_raw()` that runs bottleneck (pure) but not the stateful anomaly update. Make the statefulness visible in the interface (an object with `.update()`), not hidden behind a function that looks pure.
- **Severity:** important
- **Ordering:** Pairs with TEST-01 (same statefulness root). Do before DEAD-03.

### [ARCH-02] metrics/gpu_metrics.py:14-23 and metrics/ml_metrics.py:42-49 — NVML initialized in two places; ml_metrics re-inits and rebuilds handles every tick.
- **Defect:** `GPUMetricsCollector.__init__` calls `nvmlInit()` once and caches handles. `MLMetricsCollector.collect()` *also* calls `nvmlInit()` and rebuilds `nvmlDeviceGetHandleByIndex` handles **on every collection** (every second).
- **Why it matters:** Two owners of the same NVML resource, one re-initializing in the hot path. Duplicated decision ("how do we talk to NVML") across two modules; per-tick re-init is wasteful and risks the init/shutdown fragility that gpu_metrics' `__del__` docstring explicitly warns about. If the NVML ownership rule ever changes, it must change in two unrelated files.
- **Fix direction:** Make GPU/NVML access a single owned resource (one collector or a small shared NVML wrapper module) that both GPU and ML metrics borrow handles from. ml_metrics should receive handles, not re-init. Init once, at process start.
- **Severity:** important
- **Ordering:** Independent of the DB/config work; touches CONTRACT-01's GPU shape so coordinate if done together.

### [ARCH-03] detection/bottleneck_detector.py:22-27, 73-77, 144-145, 177-214 — Detector reaches deep into every collector's internal dict shape (Law of Demeter / information leakage).
- **Defect:** The detector navigates `gpu.get('throttle_reasons', {}).get('hw_thermal_slowdown')`, iterates `storage_data.get('disk_io', [])[].get('read_mb_s')`, reads `gpu.get('memory_util_pct')`, `gpu.get('pcie_gen')`, etc. It knows the private layout of GPU, storage, and memory readings.
- **Why it matters:** The detector is coupled to the *internal representation* of four collectors, not to a stable interface. Any collector field rename breaks detection silently (it `.get()`s to a default, so a renamed field reads as 0/absent and the alert quietly stops firing — worse than a crash). This is the same leakage as CONTRACT-01 seen from the consumer side; it's why "false alert when idle" bugs recur.
- **Fix direction:** Have detection consume the typed metric objects from CONTRACT-01 rather than raw dicts, so a rename is a type error, not a silent default. Keep the detector's *interface* (`detect(metrics) -> list`) — it is otherwise a reasonably deep module — but sever its dependence on dict-string-key spelunking.
- **Severity:** important
- **Ordering:** After CONTRACT-01 (needs the typed shape to consume).

### [ARCH-04] (9 modules) — The `_x = None; def get_x(): global _x` singleton pattern is repeated ~9 times, all holding process-global mutable state.
- **Defect:** Every collector, both detectors, the db, and config_manager use the identical module-global-singleton idiom. Consistent, but it means all collector/detector state is process-global and lazily initialized on first call.
- **Why it matters:** It is the mechanism that makes ARCH-01/TEST-01 possible (hidden persistent state) and makes ordering/init errors easy. Not wrong for a solo tool, but it's a repeated decision that could be one helper, and it actively fights testability.
- **Fix direction:** Low priority. If touched, consider a single tiny `singleton()` helper or explicit construction at startup wiring (a small composition root in `app.py`) so lifetimes and init order are visible. Don't churn all 9 gratuitously.
- **Severity:** minor
- **Ordering:** Only alongside TEST-01 if that work happens; otherwise leave.

---

## Theme F — Error handling & correctness posture

### [ERR-01] metrics/gpu_metrics.py (≈15 sites: 125-165, 180, 210, 238, 258, 320-325, 341, 426) + cpu/storage/fan/ml — Pervasive bare `except:` swallows all exceptions and collapses "sensor absent" into "value is 0".
- **Defect:** Bare `except:` (not even `except Exception`) wraps nearly every NVML/psutil call, defaulting to `0`/`None`. E.g. `power_draw, power_limit = 0, 0` on failure → `power_pct = 0`; `temperature = None` on failure; throttle reasons → `{}`.
- **Why it matters:** (1) Bare `except:` catches `KeyboardInterrupt`/`SystemExit`, complicating clean shutdown. (2) Consumers cannot distinguish "sensor unavailable" from "genuinely zero" — a failed power read and an idle GPU both report `power_pct = 0`, and the detector can't tell. This is the correctness root under prior "shows 0.0 / Unknown / Gen2" bugs: failures are silently indistinguishable from real low readings. Errors are neither defined out of existence nor surfaced — they're erased.
- **Fix direction:** Use `except Exception` (never bare) at minimum. Distinguish absent from zero: return `None` for unavailable and let the frontend/detector treat `None` as "n/a" rather than a numeric 0. Consider one small helper `safe_read(fn, default=None)` so the pattern is uniform and greppable rather than 15 hand-rolled try blocks.
- **Severity:** important
- **Ordering:** Coordinate with CONTRACT-01 (None-vs-0 is a contract decision). Independent of DB work.

### [ERR-02] metrics/gpu_metrics.py:347-349 — Debug `print` of every GPU process's VRAM on every collection tick, in the hot path.
- **Defect:** A `[GPU Process Debug]` print fires for each process with memory > 0, every second, left in behind a "FIX BUG-C02" comment.
- **Why it matters:** Log spam (prior BUG-N07) and per-tick stdout I/O in the collection loop; a debug aid that shipped. Broken window signaling "temporary hacks live here permanently."
- **Fix direction:** Remove it or gate behind a `logging` logger at DEBUG level. Adopt `logging` over `print` project-wide while here (all modules currently `print`).
- **Severity:** minor
- **Ordering:** Independent.

### [ERR-03] metrics/gpu_metrics.py:294 — `power_pct` divides by `power_limit` guarded by `> 0`, but the paired failure sets both to 0 silently.
- **Defect:** Reasonable guard against div-by-zero, but combined with ERR-01 the `power_limit == 0` case conflates "read failed" with "no limit," yielding a plausible-looking `0%`.
- **Why it matters:** Same root as ERR-01; noted separately only so the fixer checks the derived-metric math (power_pct, memory_util_pct, overhead_pct) after ERR-01 changes None-handling.
- **Fix direction:** Once ERR-01 returns None for failed reads, make derived metrics propagate None rather than compute a misleading 0.
- **Severity:** minor
- **Ordering:** After ERR-01 (same file/pass).

---

## Theme G — Naming, comments, testability

### [NAME-01] app.py:101,180; bottleneck_detector.py:39,94; gpu_metrics.py:344; dashboard.js:5,8,22,56,60 (many) — Source comments reference external bug-tracker IDs ("FIX BUG-C02", "FIX NEW-ENH-04") instead of explaining the code.
- **Defect:** Dozens of comments name a ticket ID a future reader has no access to, rather than stating the invariant/why.
- **Why it matters:** These are commit-log content contaminating the code (APoSD: comments should explain what isn't obvious, not cite history the reader can't see). "FIX BUG-C12: Only alert if GPU has active ML processes" is *almost* useful but the value is in the rule, not the ID.
- **Fix direction:** Rewrite as intent comments ("Require active GPU processes before alerting, so idle monitoring doesn't false-trigger"). Drop the IDs; git history holds provenance.
- **Severity:** minor
- **Ordering:** Independent; good cleanup pass, low risk.

### [NAME-02] config.py:30 `"swap_critical": 0` — Value contradicts its own name and comment.
- **Defect:** `swap_critical: 0` with comment "Any swap usage is critical for ML" — but the actual detector (correctly, per prior BUG-C01) requires `swap > 1.0 GB AND mem > 85%`. The config value states a policy the code deliberately does not implement.
- **Why it matters:** A maintainer reading config believes any swap is critical; the code says otherwise. The config lies about behavior (and isn't even read — see CFG-01). Actively misleading.
- **Fix direction:** When folding thresholds into one home (CFG-01), give swap a real GB threshold and RAM-pressure gate matching the detector, so config and behavior agree.
- **Severity:** minor
- **Ordering:** With CFG-01.

### [TEST-01] detection/anomaly_detector.py, all metrics/*.py singletons — Hidden global state makes detection/collection hard to test deterministically.
- **Defect:** The anomaly detector's rolling window and all collectors are module-global singletons initialized on first call. There is no way to instantiate a detector with a known history, or a collector with injected fakes, without reaching into globals. Existing tests (`tests/test_critical_fixes.py`) call `collect_all_metrics()` against live hardware — they assert on the real machine's state, so they can't run in CI or reproduce anomaly scenarios.
- **Why it matters:** Statistical logic (z-score anomaly, bottleneck thresholds) is precisely the code that most needs unit tests with synthetic inputs, and it's the hardest to test here because state is global and inputs come from hardware. Testability was designed out.
- **Fix direction:** Let detectors be constructed with their state/config injected (`AnomalyDetector(window=..)`) and expose `.update(metrics)` — the singleton wrapper can stay for production. Then tests feed crafted metric dicts and assert on alerts with no hardware. Pairs directly with ARCH-01.
- **Severity:** important
- **Ordering:** Do with ARCH-01 (same statefulness root cause).

---

## Where I disagree with / refine the prior reviews

- The prior docs mark several items "FIX"-ed inline (BUG-C02 VRAM, BUG-C08 fragmentation→overhead, BUG-C12 idle bottleneck). The *symptoms* are patched, but the **design roots remain** (CONTRACT-01, CFG-01, ARCH-03): the patches added more inline special-cases and "FIX BUG-XX" comments rather than removing the coupling that produced the bugs. Expect regressions in the same areas until the roots are addressed.
- I rate the **config fragmentation (CFG-01)** and **dead parallel config (DEAD-01)** as more important than the prior reviews' single "BUG-I13: thresholds not validated" — validation isn't the problem; the validated block is a decoy and the real thresholds are un-centralized.
- I rate the **DB concurrency model (CONC-01)** as critical; the prior reviews treated the queue as a solved concurrency story. It is not — it covers one of three access paths.
- I did NOT reproduce the prior "HuggingFace cache 1.7 TB static" as a design issue — `storage_metrics.py:74-79` recomputes it each tick via `rglob`; that's a *performance* concern (full recursive stat of the HF cache every second) more than a correctness one. Flagging as a perf note, not a design defect: consider caching that size for N seconds like `ml_metrics` already caches packages.

---

## Prioritized punch list (one line per sub-agent, ordered by severity × blast radius)

1. **CONC-01** (critical) — Stop sharing one sqlite cursor/connection across the writer thread and async readers; route cleanup through the write queue; per-op cursors or a lock. [db/__init__.py]
2. **CONTRACT-01** (important, widest blast radius) — Define the metrics-dict shape once (dataclass/TypedDict or committed schema); make it the single source. [metrics/*, then detection/*, db, js]
3. **CFG-01** (important) — Collapse four threshold sources into one; move detector's inline magic numbers into it; make the detector read only from it. [config.py + bottleneck_detector.py]
4. **ARCH-01 + TEST-01** (important, same root) — Make anomaly detector's statefulness explicit and injectable; stop `/api/metrics` from polluting the live anomaly window. [anomaly_detector.py, app.py]
5. **ERR-01** (important) — Replace bare `except:` with `except Exception`; return None (not 0) for unavailable sensors so absent ≠ zero. [metrics/*.py]
6. **ARCH-02** (important) — Single NVML owner; ml_metrics borrows handles instead of re-init-ing every tick. [gpu_metrics.py, ml_metrics.py]
7. **ARCH-03** (important) — Have bottleneck detector consume typed metric objects, not raw dict string-key navigation. [bottleneck_detector.py] (after CONTRACT-01)
8. **CONTRACT-02** (important) — Make `/api/history` return the same shape as `/ws`. [db/__init__.py] (after CONTRACT-01)
9. **CONC-02** (important) — Store the sample's own timestamp as PK; don't recompute at write time. [db/__init__.py] (with CONC-01)
10. **DEAD-01** (important) — Delete or unify `config_manager.py`. [config_manager.py] (after CFG-01)
11. **DEAD-02** (minor) — Delete duplicate `api/export.py`. [api/export.py]
12. **DEAD-03** (minor) — Remove unused anomaly windows or implement them; scope the module name. [anomaly_detector.py] (with ARCH-01)
13. **CFG-02 / NAME-02** (minor) — Single retention constant; make `swap_critical` config match detector behavior. [config.py] (with CFG-01)
14. **CONC-03** (minor) — Add DB shutdown drain + `close()` on FastAPI shutdown. [app.py, db/__init__.py]
15. **ERR-02 / ERR-03** (minor) — Remove per-tick debug print; adopt `logging`; propagate None through derived GPU metrics. [gpu_metrics.py]
16. **NAME-01** (minor) — Rewrite "FIX BUG-XX" comments as intent comments. [app.py, detection/*, gpu_metrics.py, dashboard.js]
17. **ARCH-04** (minor, optional) — Only if touching TEST-01: consolidate the 9 singleton idioms / add a composition root. [multiple]
18. **PERF note** (minor) — Cache HuggingFace cache-size computation instead of `rglob`-ing every second. [storage_metrics.py]

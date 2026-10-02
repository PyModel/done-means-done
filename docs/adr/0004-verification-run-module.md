# ADR 0004: The verification run is one module

Status: accepted (unreleased, 2026-10-01)

## Context

Up to 0.7.1 the run (`dmd run`) lived in `cli.py` as a 160-line function over an `argparse.Namespace`, with its preflight, resource locks and receipt construction around it. It was the deepest concept in the runtime and the only thing that could not be called without argv. Two tests patched `dmdlib.cli.execute` and `dmdlib.cli.task_snapshot` to observe it. Its finish block also fingerprinted the tree and recomputed approvals while holding the record lock, where an exception stranded the results.

## Decision

`dmdlib/runs.py` owns the run: `select_checks`, `preflight`, `exclusive`, `interruptible` and `run_checks(directory, ids, everything, red, quiet_window, wait_exclusive)`. The CLI resolves the task directory and passes arguments, as ADR 0002 set for the store.

Inside the run, the task lock is held only to read or change the record (plan, start, next, interrupted, finish). Observing the project tree (the writer preflight, the before and after snapshots) happens outside it. The same rule applies to the hooks and to `dmd gate`/`status`: fingerprint first, then lock, reload and evaluate.

- *Rejected:* keeping the after-snapshot under the finish lock for a tighter window. The window it protected was microseconds; the lock it held was seconds on a large checkout, and a hook hashing at the same time cost the run its results.

## Invariant

`runs.py` does not import `cli`. A run's finish block records a result for every executed check or raises before any check ran; a missing candidate or a vanished approval input is `STALE`, never an exception that leaves `running` set.

## Enforcing tests

- `tests/test_completion.py::StoreBoundary` (import direction).
- `tests/test_finish_paths.py::FinishPathCase`, `GateOutsideLockCase`, `HookFailOpenCase::test_stop_hook_fingerprints_outside_the_task_lock`.

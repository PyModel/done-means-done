# Recovery, delegation and lifecycle enforcement

## Durable identity

The state namespace is `$DMD_STATE/v2/<project-hash>/<worktree-hash>/<task-id>/`. `DMD_STATE` defaults to `~/.local/state/done-means-done` and must be outside the project. Physical project/worktree identity keeps unrelated assignments separate. Each task has a stable ID; host sessions are execution contexts that bind to that task. Because the key is the physical worktree, a task started in the main checkout is not the active task of a linked worktree of the same repository. When no task is active, `dmd` names any unfinished task recorded for a sibling worktree and the `--cwd` that reaches it; resume it there, or `init` a distinct assignment here — do not treat the absence as permission to skip the ledger.

`task.json` contains requirements, work, acceptance declarations, captured evidence references, findings, coverage, review, blockers, uncertain operations, running check, session bindings and canonical history. `task.json` is atomically replaced and carries a bounded tail of the most recent 200 events; `events.jsonl` is appended once per mutation and holds the complete sequence. `task.json` remains authoritative for state, and `sequence` counts every mutation, so a lagging projection is detectable and recoverable rather than authoritative. Reports and handoffs are likewise derived. Private records use owner-only paths/files and POSIX advisory locks. A contended state lock is retried with bounded backoff (default 5 s; `DMD_LOCK_WAIT` 0..600) before failing, so an overlapping hook or back-to-back command is absorbed rather than reported; the wait never becomes an indefinite block.

This small-file design rewrites the canonical task/history on each mutation. It favors a single inspectable store and crash-consistent events over database scalability. Very large histories should be benchmarked before adopting a long-lived fleet; no unmeasured throughput claim is made.

## Resume the assignment, not a summary

On entry, inspect the bound task with `dmd reconcile`, review the original request/amendments and generated handoff, and select `dmd next`. Confirm the physical worktree and current source, not only the previous chat summary. Reconcile partially implemented work and stale evidence. Unknown operations are investigated before replay.

State changes persist as they happen. Generate a handoff before planned compaction or transfer, but do not rely on a final callback to save everything. A process can disappear before receiving one.

`PAUSED` and `CANCELLED` never authorize automatic reactivation. The operator can explicitly resume after reviewing the checkpoint. `init --new` preserves an unfinished previous task as paused; it is for a genuinely new assignment, not skipping obligations. Explicit import is required for older schema-1 records.

## External effects and interrupted verification

Before a non-idempotent operation, record its identity and intended effect. An incomplete response creates an unknown outcome. Query the external service/receipt to determine whether it completed before retrying. Never assume a timeout equals cancellation.

The check runner holds a local run lock separate from its short state lock, so cancellation can update the task while execution is active. Timeouts/cancellation terminate its local process group. A crash may leave an incomplete running record. `recover-run --proof ...` requires the local run lock to be released, refuses while the recorded runner PID is still alive on this host, prints what it found and why it accepted the proof, and still needs a specific external-outcome reconciliation. It does not prove that another machine, remote job, or deployment is stopped.

## Decomposed tasks

Plan coherent leaf deliverables with requirement IDs, work IDs, relevant acceptance checks, owned paths, dependencies, and integration responsibilities. Parent/branch integration is explicit work with its own acceptance. A returned worker message does not satisfy it.

The parent must inspect the actual changes, rerun relevant checks, integrate the work, and test the final joined artifact. A worker's self-review does not become an independent parent review merely by relabeling it. External evidence can inform a review but does not bypass local final-candidate verification.

This runtime does not launch subagents, claim filesystem leases, enforce `--owns` patterns, or isolate worktrees. Parallelism must come from the actual host and sound source isolation. Do not pretend concurrency exists when work was sequential. Do not give concurrent workers conflicting path ownership. Keep canonical acceptance updates controlled by a parent; ordinary state locks do not fence independent source writers.

When replacing a worker, establish that its source-writing process stopped or isolate its workspace. Reconcile existing external effects, reject stale returned results, and only then redispatch. A stale lease or reused PID is not sufficient evidence. Multi-machine generation-fenced dispatch is not implemented here.

## Claude Code adapters

Installation is explicit. The adapters read documented event payloads and do not execute acceptance commands automatically.

| Event | Implemented behavior |
|---|---|
| `SessionStart` | Resolve/bind a real session and worktree; inject a short restore instruction, never raw tool output as privileged instructions |
| `Stop` | Recompute root acceptance; in enforce mode block unfinished executable work, with semantic no-progress protection |
| `TaskCompleted` | For an explicitly mapped native task, return exit 2 with stderr when its mapped work lacks current evidence |
| `PostToolUse` | Record a bounded tool-name event for configured tools; no raw payload or secret-bearing output log. Waits at most 1 s for the task lock, then drops the event silently rather than failing the host hook |
| `PostToolUseFailure` | Record the failure event without manufacturing acceptance |

A session binding takes precedence over the current worktree pointer, so starting another assignment does not quietly retarget an existing session. It must still match the physical worktree. Unmapped native tasks are not falsely treated as the whole assignment: map them with `dmd map-host-task --host-id ... --work ...`. The root Stop/gate remains separate. A locally accepted leaf can close before the whole assignment passes final review.

Modes: `off` returns without hook task effects; `observe` reports without blocking or watchdog-pausing; `enforce` blocks premature stops. The local safeguard allows up to six consecutive Stop blocks without recognized semantic progress, then pauses with obligations intact. Existing host safety limits still apply. `stop_hook_active` is not itself permission to ignore unfinished work. Explicit resumption resets the local no-progress history; do not reset it secretly to create an infinite loop.

This is not automatic execution after host termination. No service, daemon, schedule, or multi-day supervisor is installed. A separately authorized supervisor would need cancellation propagation, cost/permission controls, source-writer isolation, external-effect reconciliation, and durable session management. Do not represent that prospective infrastructure as part of this release.

## Official contracts reviewed

Implementation references: Claude Code skills and hooks documentation, retrieved September 10, 2026:

- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/hooks

Payload fixtures and emitted control responses are tested locally. Live host integration and the user's installed version were not tested in this build.

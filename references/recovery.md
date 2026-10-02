# Recovery, delegation and lifecycle enforcement

## Durable identity

The state namespace is `$DMD_STATE/v2/<project-hash>/<worktree-hash>/<task-id>/`. The project hash is the Git common directory and the worktree hash is the checkout root, so one checkout holds one active task: two assignments in `packages/a` and `packages/b` of the same monorepo share that slot (`init --new` pauses the first). Use a linked worktree per concurrent assignment. When a checkout has Git metadata but Git discovery fails (git missing from PATH, a corrupt `.git`), `dmd` refuses rather than silently re-keying the task under a plain-directory hash; a pruned or relocated worktree is still named through the sibling hint. `dmd relocate` re-points a moved checkout. macOS and Linux only: the runtime uses POSIX locks and owner-only permissions. `DMD_STATE` defaults to `~/.local/state/done-means-done` and must be outside the project. Physical project/worktree identity keeps unrelated assignments separate. Each task has a stable ID; host sessions are execution contexts that bind to that task. Because the key is the physical worktree, a task started in the main checkout is not the active task of a linked worktree of the same repository. When no task is active, `dmd` names any unfinished task recorded for a sibling worktree and the `--cwd` that reaches it; resume it there, or `init` a distinct assignment here — do not treat the absence as permission to skip the ledger.

`task.json` contains requirements, work, acceptance declarations, captured evidence references, findings, coverage, review, blockers, uncertain operations, running check, session bindings and canonical history. `task.json` is atomically replaced and carries a bounded tail of the most recent 200 events; `events.jsonl` is appended once per mutation and holds the complete sequence. `task.json` remains authoritative for state, and `sequence` counts every mutation, so a lagging projection is detectable and recoverable rather than authoritative. Reports and handoffs are likewise derived. Private records use owner-only paths/files and POSIX advisory locks. A contended state lock is retried with bounded backoff (default 5 s; `DMD_LOCK_WAIT` 0..600) before failing, so an overlapping hook or back-to-back command is absorbed rather than reported; the wait never becomes an indefinite block.

This small-file design rewrites the canonical task/history on each mutation. It favors a single inspectable store and crash-consistent events over database scalability. Very large histories should be benchmarked before adopting a long-lived fleet; no unmeasured throughput claim is made.

## Resume the assignment, not a summary

On entry, this file and the output of `dmd reconcile` are the reading list; the full SKILL.md is for initialising a new assignment. Inspect the bound task with `dmd reconcile`, review the original request/amendments and generated handoff, read the gate `summary` headline and its `rerun` command before the per-item reasons, and select `dmd next`. Confirm the physical worktree and current source, not only the previous chat summary. Reconcile partially implemented work and stale evidence. Unknown operations are investigated before replay.

State changes persist as they happen. Generate a handoff before planned compaction or transfer, but do not rely on a final callback to save everything. A process can disappear before receiving one.

`PAUSED` and `CANCELLED` never authorize automatic reactivation. The operator can explicitly resume after reviewing the checkpoint. If the gate owes `AU-XX: ... awaits the operator's confirmation`, a contract change was recorded without the operator's approval: ask the operator (`dmd authority confirm AU-XX` puts it to them through the host prompt), or undo it with `dmd authority withdraw AU-XX`. In a headless `claude -p` run nobody can approve a prompt: such a command is denied before it runs, and an already-recorded unconfirmed decision can only be withdrawn. `init --new` preserves an unfinished previous task as paused; it is for a genuinely new assignment, not skipping obligations. Explicit import is required for older schema-1 records.

## External effects and interrupted verification

Before a non-idempotent operation, record its identity and intended effect. An incomplete response creates an unknown outcome. Query the external service/receipt to determine whether it completed before retrying. Never assume a timeout equals cancellation.

The check runner holds a local run lock separate from its short state lock, so cancellation can update the task while execution is active. The state lock is taken only to read or write the record; fingerprinting the tree (by the runner, the hooks and `dmd gate`) happens outside it, so a snapshot of a large checkout never makes another command's lock wait expire. Timeouts/cancellation terminate its local process group. A crash may leave an incomplete running record. `recover-run --proof ...` requires the local run lock to be released, refuses while the recorded runner PID is still alive on this host, prints what it found and why it accepted the proof, and still needs a specific external-outcome reconciliation. It does not prove that another machine, remote job, or deployment is stopped.

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
| `PreToolUse` (Bash) | In enforce mode, put a command that changes the operator's contract to the operator (`permissionDecision: "ask"`, which still prompts under bypassed permissions); other commands pass without state access |
| `Stop` | Recompute root acceptance; in enforce mode block unfinished executable work, with semantic no-progress protection |
| `TaskCompleted` | For an explicitly mapped native task, return exit 2 with stderr when its mapped work lacks current evidence |
| `PostToolUse` | Append one tool-name line to `activity.jsonl` (no lock, no fsync, no rewrite of `task.json`; no raw payload or secret-bearing output), and, after an asked command, record the operator's approval of the decisions it logged |
| `PostToolUseFailure` | The same, for a failed tool call; never manufactures acceptance |

A session binding takes precedence over the current worktree pointer. It keeps governing while the session's cwd belongs to the task: inside the task root or a check's candidate tree, or in another worktree of the same repository that has no active task of its own. When the cwd is an unrelated project or a sibling worktree holding its own assignment, the binding is released with a message naming both roots and the session is bound to that checkout's active task, if any. `init --new` moves the worktree's session bindings to the new assignment, so the session is never left on a paused task that nothing enforces. A binding whose task directory was deleted is dropped. A cwd that is unusable (gone, a file, Git discovery failing) keeps the binding and prints a message on SessionStart and Stop naming the task root to return to.

A stored `COMPLETE` is final for the hooks: a later session in the same worktree is not the assignment, is not bound to it, and leaves its record untouched. The reasoning is recorded in [ADR 0001](../docs/adr/0001-completion-contract-reversals.md). `dmd gate` still recomputes honestly (a changed tree shows `ACTIVE`), and any change to the contract (`amend`, `req add`, a new check or finding) reopens the task so the hooks govern it again.

## When a hook fails

Hooks fail open. The only nonzero exit is TaskCompleted enforcement in `enforce` mode. Every other problem, including a defect inside `dmd`, prints one `systemMessage` beginning `Done Means Done: this hook could not evaluate the task` with the cause, and exits 0, so a paused or damaged task can never block every Stop of every later session in that project (the 0.5.x deleted-scratchpad incident). Such a message never means the task is gone. Run `dmd doctor` in the worktree: it names a missing declared input and the `check edit --input`/`--clear-inputs` repair, a missing task root and `dmd relocate`, a stranded run and `recover-run`, or an unreadable record. `dmd check list` and `dmd list` never fingerprint and always work. A suspended (`PAUSED`/`CANCELLED`) task is never fingerprinted by a hook. `dmd config --mode off` silences the hooks entirely while you repair; turn them back on afterwards.

The no-progress safeguard counts per session. Progress means an obligation became satisfied (a check accepted, work verified, a finding resolved, a blocker cleared, coverage current). Adding records, rewording notes and new failures do not count, and a live verification run is progress in flight. When the count passes the cap the hook lets that one stop through and records a `released` note naming the session and count. The task stays `ACTIVE` with every obligation intact, the next session's SessionStart says why the previous one stopped, and the note clears on the next real progress. Unmapped native tasks are not falsely treated as the whole assignment: map them with `dmd map-host-task --host-id ... --work ...`. The root Stop/gate remains separate. A locally accepted leaf can close before the whole assignment passes final review.

Modes: `off` returns without hook task effects; `observe` reports without blocking; `enforce` blocks premature stops. The Stop hook lets a turn end only for `COMPLETE`, `BLOCKED` (every remaining obligation waits on a recorded blocker), a suspended task, or a no-progress release; after at most six consecutive blocks without newly satisfied obligations it releases once. Existing host safety limits still apply. `stop_hook_active` is not itself permission to ignore unfinished work. Explicit resumption resets the local no-progress history.

This is not automatic execution after host termination. No service, daemon, schedule, or multi-day supervisor is installed. A separately authorized supervisor would need cancellation propagation, cost/permission controls, source-writer isolation, external-effect reconciliation, and durable session management. Do not represent that prospective infrastructure as part of this release.

## Official contracts reviewed

Implementation references: Claude Code skills and hooks documentation, retrieved September 10, 2026:

- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/hooks

- https://code.claude.com/docs/en/permission-modes and https://code.claude.com/docs/en/permissions (an `ask` decision still prompts under `bypassPermissions`)

Payload fixtures and emitted control responses are tested locally. Claude Code 2.1.280 was exercised live in headless mode (`evidence/live-host.log`): SessionStart context, a blocking Stop, the watchdog release, observe mode, and the operator-confirmation prompt (denied with no prompt host, approved through a permission-prompt tool). An interactive session was not driven.

# Changelog

## 0.8.0 — operator-confirmed trust boundaries

An adversarial audit of the completion gate (hollow self-approved verifiers, dropped request
clauses, advisory-by-default enforcement, fabricated blockers, laundered duplicates and
`--writes` freshness holes) drove a hardening release. The threat model is unchanged — local
cooperative enforcement, not a sandbox against the same user — but every agent-authored
record that used to complete a contract now needs the operator's confirmed decision.

- **Verifier approval is operator-owned.** `dmd approve` / `--approve` records the agent's
  inspection and a pending `check.approve` decision; the check executes only once the
  operator confirms it (host prompt or controlling terminal). Unchanged reapprovals reuse
  the confirmation; a changed verifier, input or environment never inherits it. The run and
  the gate both enforce this, with or without hooks. `test_anticheat.py` (23 cases) covers
  the bypasses this closes.
- **Contract fidelity.** `init --from-host` binds the task to the prompt the host captured
  (`UserPromptSubmit`, session+worktree scoped); a typed request that differs from the
  capture is refused, and provenance (`host-captured` / `agent-transcribed`) is recorded and
  reported. `--anchor` must be an exact quote. `coverage assert`/`context`, `amend`,
  confirmed-duplicate dispositions, baseline limitations, regression downgrades,
  `review-policy` and whole-task blockers are operator decisions.
- **Enforcement defaults to enforce.** An applied install writes `mode: enforce`
  (`--mode observe` is the explicit opt-out). PreToolUse/PostToolUse matchers cover
  Bash, Edit and Write; common protected-path writes (redirection, `sed -i`, `mv`,
  `chmod`, `tee`, heredocs) are put to the operator. Shell matching remains a prompt, not
  a sandbox. Confirmation tickets bind the exact tool-call payload, and failed or
  mismatched post-tool events confirm nothing.
- **Blockers and findings.** Every blocker records an evidence artifact; one without it
  supports nothing. A `task` blocker needs operator confirmation. A confirmed-duplicate
  needs an accepted executed check shared with its canonical finding plus the operator's
  confirmed disposition.
- **Freshness.** `--writes` no longer excludes anything from fingerprints: generated
  outputs are content-bound in the receipt and hashed on every later gate, so edits, new
  files under the glob and pre-existing untracked source all stale the evidence. Only
  files the run itself created are excused, once, for that run's drift comparison.
- **Terminal.** `/dev/tty` confirmation uses bounded `os.open`/`select` I/O (the
  TextIOWrapper path raised `io.UnsupportedOperation` on macOS PTYs). It is a cooperative
  signal — a PTY-capable process can simulate the keystroke — never identity proof.
- **Operations.** Failed check runs record attempt signatures automatically. Reports
  distinguish `COMMAND CAPTURE` from `SELF-ATTESTED`, state that capture is not semantic
  proof, and label request provenance and agent-transcribed evidence. The Stop hook keeps
  governing a session that left its worktree (binding release discloses the open task);
  the watchdog release still never means COMPLETE.
- **Migration.** Receipts written before 0.8.0 (no confirmed-approval binding, partial
  output fingerprinting) are reported stale: reapprove each check and rerun it. Approvals
  recorded before 0.8.0 do not authorize execution. Default independent review applies to
  tasks created by this release; `review-policy self --authority ...` is the operator's
  opt-out.

## Unreleased

Resilience and one module boundary. Found by a full read of the runtime after 0.7.1; nothing here changes the contract the gate enforces.

- **The verification run is its own module.** `dmdlib/runs.py` owns selecting the checks, the concurrent-writer preflight, named-resource locks, the fingerprint window, execution and the receipts; `cli.py` only resolves the task directory and hands over the arguments (ADR 0004). Behaviour is unchanged; the suite was identical before and after the move.
- **The tree is observed outside the task lock.** The Stop and SessionStart hooks, `dmd gate`/`status`, and both snapshots of a run used to hash the project tree while holding the record lock. On a large checkout that lock was held for seconds, and `dmd run` (which takes it for 5 s at start and finish) lost its results when a hook happened to be hashing. Fingerprinting now runs before the lock; the lock is held only to read or write the record. The run's finish block waits up to 60 s for it, because executed evidence is at stake.
- **A run's finish path cannot strand its results.** A check that removed its own candidate tree (a scratch worktree), or whose declared `--input` vanished, raised inside the finish block: the receipts were lost and `running` stayed set until `recover-run`. Both are now drift: the run is recorded `STALE` and the record is released. `running` is cleared only when it is this run's own record, and an interrupt under a contended lock still exits 130 with the run left recorded for `recover-run`.
- **Hooks fail open where they did not.** The hook entry point resolved the process cwd before its error boundary, so a deleted working directory (the 0.5.x scratchpad class) produced a traceback and a nonzero exit; hooks never needed it and the CLI now resolves it lazily. Two processes creating the same state directory at once no longer fail with `FileExistsError`. A contended session-prune lock no longer fails the session binding. A corrupt session binding is dropped and the task at the cwd governs, instead of "could not evaluate".
- **Validation treats a JSON null as missing text.** Ten required-text checks spelled `str(x.get("key", "")).strip()`, which turns `null` into the truthy string `"None"`. One helper owns the rule now, and a regression check's `red_exit` is type-checked rather than compared blindly.
- `relocate` refuses while a run is recorded as running (it used to delete the directory the runner would reload) and moves the worktree's session bindings to the new record.
- `check add` without `--req` says so, like `work add`. `work set` builds one assessment for all dependencies instead of one per dependency. The suspended and finished state sets have one owner (`model.SUSPENDED`, `model.FINISHED`), the session-ID bound one (`store.valid_session`), and the unused `OPEN_FINDING_STATES` is gone. The report no longer hard-indexes optional finding and blocker keys.

Not done here: a version bump. `SHA256SUMS`, `SOURCE.json` and `evidence/VALIDATION.md` form the release step and are regenerated with it.

## 0.7.1 — 2026-09-22

Housekeeping. Found while cleaning the state root of a machine running 0.6.0 and 0.7.0.

- A lookup no longer creates directories. Every hook call from a folder with no task used to create an empty `v2/<project>/<worktree>` directory, and one machine had 39 of them. Only `init` and `relocate` create a record directory now. `dmd gc` removes the empty ones older versions left, and those whose task records were deleted (only the active pointer and lock remain), reported as `empty_record_directories_removed`.
- Nothing is written into the state root by hand. The examples in the command reference passed evidence as `/absolute/outside-project/*.txt`, and agents wrote those files into `DMD_STATE`, the one directory outside every project, where they mixed with other projects' files. Every example now uses `--evidence -` with a heredoc, since `dmd` keeps its own copy. The command reference and SKILL.md say that nothing of the agent's goes under `DMD_STATE`.
- `dmd next` for a regression check with no red baseline names all three ways forward: `run --red`, `check baseline --evidence -`, or `check edit --no-regression` when the check reproduces no specific defect. A live session had flagged a whole-suite check as a regression and could not tell how to clear it.

## 0.7.0 — 2026-09-22

Strict on outcomes, free on method. Two read-only audits (architecture, and correctness/failure/gaming) found ways for an agent to stop before the work was done and places where the tool demanded ceremony that proved nothing. This release closes the first and removes the second.

Early stops. The gate reported `BLOCKED` whenever a blocker was open and its narrow `next` list was empty. That list left out coverage, red baselines, failing fixed-verified findings and the review, so a blocker on one requirement let the Stop hook release the agent while other requirements were still owed. One `Assessment` now judges every check, work item and finding once, and the gate, `next`, the summary, the report and the Stop hook all read from it. Each open reason belongs to one item. `BLOCKED` means every open reason waits on a blocker: directly, through its requirement, or through a prerequisite. Anything else is `ACTIVE` with an action. A plan with reasons but no dependency-ready item gets a `plan` action instead of a dead end. The no-progress safeguard used to pause the task after seven stops, which ended the assignment for every later session. It now releases that one stop, keeps the task `ACTIVE`, records a `released` note that the next SessionStart repeats, and clears it on real progress. Progress means an obligation became satisfied: adding records or rewording notes no longer resets the count, and a live verification run is not counted as a loop. `init --new` moves the worktree's session bindings to the new task, instead of leaving the session on a paused task that nothing enforces. A confirmed finding can be disproved only with an accepted executed check, not a note plus an arbitrary file. `req cancel` refuses to strand work that depends on it (`work set --clear-deps` replans).

Resilience. A stored `COMPLETE` is final for the hooks, so a finished assignment no longer blocks unrelated later sessions in the same worktree. The gate still recomputes honestly, and a contract change reopens the task. A bound session keeps governing when the agent `cd`s into a subdirectory, a candidate tree or a sibling worktree. Only an unrelated project releases it, and an unusable cwd prints a message instead of silently allowing the stop. `TaskCompleted` on a paused task no longer rejects verified work: the hook had passed an unmeasured source into acceptance. `dmd run` handles SIGTERM and SIGHUP through the interrupt path, and the check's supervisor terminates its process group when its parent disappears. A killed runner previously left the check running unsupervised, and `recover-run` then allowed a second copy. A file rewritten during every read (a dev server's log) used to raise `source changed during fingerprint`, which disabled every hook and the gate. It is retried from a copy of the hash state, so stable files hash exactly as before (fingerprints of unchanged trees are identical to 0.6.0), then recorded as `volatile` drift. A future mtime no longer blocks every run. An `amendments` field that is not a list is rejected at load rather than crashing later commands.

Freedom. Work items are optional: a check may map straight to its requirement. A work item's status is a progress note. The gate reads evidence, so `work set --status verified` is no longer a required step, though planned work still needs its own evidence or a recorded supersede. `check add|edit --approve NOTE` authors and approves in one call, and any later edit still clears the approval. `dmd attempt` takes any R/W/A/F ID. SKILL.md is rewritten from 204 to about 120 lines. It opens with the contract the gate enforces and the freedoms the agent has, then gives five steps with completion criteria, covers non-code deliverables, and moves runtime history into the references.

Also fixed: a timed-out or cancelled check stopped reading output one poll after SIGTERM, so the documented two-second cleanup window captured nothing slower than about 0.1 s. It now reads through the grace period. The timeout-cleanup test now traps TERM and passes under dash, where 0.6.0 had 278/279 on Debian. An approval binds the interpreter by major.minor, so a Python patch upgrade no longer expires every approval; approvals recorded in the old full-version form are still honoured. Command output is printed only after its mutation is saved. Check and work history, review log, recovery records and attempt histories are capped at 50 entries (the evidence files and `events.jsonl` keep everything). Unused imports were removed.

A command whose text contains its own `--match` token (`npm test && echo OK`, `python3 -c 'print("OK")'`) is refused, because it reduces to an exit status. A later session leaves a finished task's record byte-identical and creates no binding. A sibling worktree that has its own active task takes the session over from the bound task; one without a task stays governed. The three reversals of tested 0.6.0 behavior are recorded in `docs/adr/0001-completion-contract-reversals.md`.

Renamed: the `summary.checks.unapproved` bucket is `edited`, which is what it always held. A work item's reason reads `W-01: checks owed: A-01` in place of the status-based wording.

Operator decisions. `--authority` is text the agent types, and five commands used it as a one-step exit. With the hooks enforcing, a new PreToolUse hook asks the operator through the host's permission prompt before a Bash command changes their contract. That covers:
- `req cancel` and `attest-only`;
- `finding defer`;
- `state`;
- `review --kind independent`;
- `init`/`migrate --new` over an unfinished task;
- `config`;
- `relocate`;
- running a hook by hand;
- removing the hooks.

The prompt still appears under bypassed permissions. The CLI logs each decision as `AU-XX`, and PostToolUse records the approval. Until then:
- the gate owes the decision;
- an unconfirmed pause does not suspend enforcement;
- an unconfirmed config change leaves the hooks as they were.

`dmd authority list|confirm|withdraw` asks again or undoes a decision. Without the hooks, decisions apply and every report says they were never confirmed. See ADR 0003. `install.py --apply` must be rerun to register PreToolUse.

Request items. New tasks record the request's explicit list items (`C-XX`; fenced code is skipped, and amendments add more). Coverage is refused while an item has neither a requirement (`req add --covers`, `coverage map`) nor a context note (`coverage context`, which takes several IDs), so an enumerated outcome cannot be dropped silently. Older tasks keep their contract digest.

Outputs and disproofs. `check add|edit --writes GLOB` leaves a check's own untracked outputs (junit.xml, coverage files) out of its candidate's fingerprint and out of the writer preflight. A check that writes a report no longer stales itself or its siblings. Tracked files and whole-type globs are refused, and fingerprints are unchanged when nothing is declared. A disproof is now bound to the files it examined, the `--location` file plus `finding set --input`, so an edit elsewhere no longer reopens it.

Store and speed. The task store (location, loading, the edit transaction, creation, session binding, hook config) moved out of `cli.py` into `store.py`, and rendering into `report.py`. Hooks and migration no longer import the CLI (ADR 0002). PostToolUse appends one unsynced line to `activity.jsonl` instead of rewriting and fsyncing `task.json` on every tool call. A bound session inside its task root skips Git discovery, hook calls skip building the full parser, and bytecode is cached under the user cache home, never in the skill directory. The preflight reads a machine-wide index of live runs instead of every task record. Measured with hyperfine over 40 runs: PostToolUse went from 92.7 to 42.0 ms and Stop from 119.9 to 77.4 ms.

Agent experience. Every `dmd next` action now names the complete command, including required flags such as `coverage assert --note` and `review --kind/--reviewer/--note/--evidence`. A live host run showed an agent following the old wording into usage errors. The headline counts unmapped request items, unasserted coverage, requirements without a check, and decisions awaiting the operator.

Cleanup:
- `--request-file -` and `--evidence -` read standard input, so the protocol needs no scratch files.
- The report's *Possible leftovers* section lists untracked files that no check declared, and checks that left background processes.
- `gc` prunes confirmation tickets left by denied prompts and orphaned run-index entries.
- SKILL.md's Close step has a checkable "leave no leftovers" criterion.

Migration:
- An unknown legacy check method is imported for reauthoring (`legacy_method`) instead of rejecting the import.
- Importing the same record twice needs `--new`.

Fixed:
- The check supervisor aborted with SIGABRT, which produced macOS crash reports, when its status pipe had closed: the parent was interrupted, had hit the output limit, or had died. The status write raised EPIPE, and the interpreter shut down while the stdin watcher thread held the buffered reader's lock. The watcher now reads the raw descriptor, EPIPE is tolerated, and the supervisor never finalizes the interpreter.

## 0.6.0 — 2026-09-13

Resilience and isolation. One incident and four read-only audits.

The incident: a Stop hook in an unrelated project failed on every stop with `declared input is missing: …/scratchpad/api-gate.sh`. A PAUSED task from a prior session declared check inputs in a session scratchpad that no longer existed. The hook fingerprinted the task before it looked at the task's state, and the error escaped as exit 2, which the host treats as block-with-stderr. Three things were wrong and all three are fixed. A hook now fails open: the only nonzero exit is TaskCompleted enforcement, and every other problem, including a defect in `dmd`, is one `systemMessage` naming the cause plus exit 0. A suspended task is never fingerprinted by a hook. A declared input that vanished is drift for the checks that declared it, reported as `A-01: declared input missing: <path>; restore it, or dmd check edit --id A-01 --input <file> (or --clear-inputs) and rerun` and grouped as `missing_inputs` in the summary, and every read command keeps working.

Isolation across projects and sessions. A bound session whose cwd is now a different checkout used to raise `session binding does not belong to this worktree` on every hook event, so governance from project A blocked every Stop in project B; the binding is now released with a message naming both roots and the session is bound to the new checkout's active task, if any. A binding whose task directory was deleted is dropped instead of raising `No such file`. A cwd that is a file, a renamed checkout, a state root with loose permissions and a malformed, oversized, empty or non-UTF-8 payload no longer exit nonzero. When a checkout has Git metadata but Git discovery fails, `identity()` refuses instead of silently re-keying the task under a plain-directory hash, and the sibling-task hint falls back to every project bucket so a pruned worktree's task is still named. Session pruning deletes only bindings whose task is provably gone, under a lock. Sessions are recorded by hash and bounded at 50, as are watchdog counters; the watchdog pause reason names the session and count that tripped it.

Operator-authorized deferral. `dmd finding defer --id F-XX --authority "…" --note "…"` is the one exit for a defect that will not be fixed in this assignment (a vendored bug, an explicitly out-of-scope pre-existing issue). `--status deferred` is refused; the authority is an amendment, so coverage is reasserted, and the deferral is named in the report, the headline and `summary.findings_deferred`. Findings had no such state, so a third-party defect trapped a task in ACTIVE forever or pushed the agent toward a dishonest `disproved`.

Runner. Output exactly at `max_output`, or non-UTF-8 output near it, was recorded as `OUTPUT_LIMIT`: the limit was re-derived from the decoded text, where the stdout/stderr separator and each replacement character added length. It is now decided once on raw captured bytes. A missing cwd raised out of `Popen` into the interrupted-run path and wedged the ledger behind `recover-run`; it is `SPAWN_FAILED`, a clean FAIL. A timed-out or cancelled command got SIGKILL with no grace; it now gets SIGTERM, two seconds during which its output is still read, then SIGKILL, so an EXIT trap's cleanup runs and lands in the evidence. `killpg` errors other than "no such process" no longer raise out of the cleanup `finally`. The cancellation poll re-reads the record at most once per second, reads only what it needs and treats a transient read failure as "not cancelled". Per-check run history is bounded at 50 entries; every evidence file stays on disk.

Preflight. `dmd run` refused whenever any dirty or untracked file was younger than three seconds, which is the normal agent turn (edit, then verify). It now waits, bounded by three seconds, for the newest write to age past the window and re-checks; only a tree still being written is refused.

CLI. `--input` must name an existing regular file (an empty string or a directory poisoned every later fingerprint); `--clear-inputs` and `--clear-exclusive` remove a mis-declared list. `uncertain list [--json]`. `work add` without `--req` and `blocker add` with an unknown `--item` say what is required and allocate no ID. `init` names `-m` and `--request-file`. `list --json --state …`. New `doctor` (state root, hook mode, dead bindings, this worktree's task, missing inputs and cwds, stranded run, gate headline; exit 1 when anything needs attention), `gc` (removes dead session bindings, lists finished tasks, deletes no task) and `relocate --to … --authority …` (re-points a moved checkout, rewrites paths, resets command evidence to NOT_RUN). Fifos, sockets and unreadable files in a tree are hashed as drift instead of aborting fingerprinting. A missing candidate root is one gate reason, `candidate root is missing: …`, not an empty-tree fingerprint that reads as "everything changed".

Docs. `recovery.md` gains "When a hook fails", the monorepo one-task-per-checkout limitation, the platform constraint and a corrected PostToolUse row (it never failed the host hook only for lock contention; it failed for everything above). `README.md` no longer hard-codes the test count and `validate_package.py` refuses one, and checks the `VALIDATION.md` release header.

Audited and deliberately unchanged: the whole-repo root fingerprint (a monorepo edit outside the checked package still invalidates the final review; scoping evidence to declared path globs is a schema change and is deferred), PATH in the approval environment digest (a different PATH does resolve a different `pytest`), and machine-wide exclusive tags (no deadlock is possible; a collision waits and names the tag).

## 0.5.2 — 2026-09-11

One item from a Claude Code session started in the home directory.

`SessionStart:startup hook error … dmd: DMD_STATE must remain outside the project being verified`. A hook receives the host's cwd; outside any Git checkout the cwd itself is the project root, and from the home directory that root contains the default state root, `~/.local/state/done-means-done`. The containment guard exists so a verified tree never fingerprints its own evidence, and it is still correct for `init` and `run`. It was wrong as a hook outcome: no task can exist for such a tree, so there is nothing to select. The four hooks now resolve the cwd through one lookup that treats a state-inside-root refusal, a deleted cwd, or a cwd that is not a directory as "no task here" and exit 0 with no output. A session already bound to a task whose cwd cannot hold it still says `session binding does not belong to this worktree`.

The CLI refusal now names both paths, says when the cwd was treated as the project root because it is not a Git checkout, and tells the operator to run inside the checkout or move `DMD_STATE`.

## 0.5.1 — 2026-09-11

Nine items from upgrading a live sixteen-check task with four linked worktrees from 0.4.1 to 0.5.0.

A receipt written before 0.5.0 by a check whose cwd is a linked worktree stayed valid on paper (its definition digest was preserved) but not in practice: 0.5.0 compared its source to the worktree fingerprint, and 0.4.x had recorded the task root. Every such green went "stale" on upgrade with no tree changed, and the whole batch had to be rerun. A legacy receipt (no `candidate` field) is now accepted exactly on the guarantee 0.4.x gave it: the task root is unchanged. The report labels it `Tested: task root (receipt predates 0.5.0 candidate binding); rerun to bind it to <worktree>`, and the first rerun binds it properly. A legacy receipt whose root has moved says so instead of "stale/missing".

Every gate reason now names its cause. `A-01: PASS or stale/missing evidence` read as "PASS" to a skimming agent and hid four different next actions. The reasons are now `not run`, `FAIL: <why>; fix and rerun`, `STALE: passed while its candidate or definition moved; rerun`, `stale: <candidate> changed since the receipt (tested @ <head>); rerun`, `definition edited; inspect, approve and rerun`, `receipt predates 0.5.0 candidate binding and the task root has since changed; rerun to bind it to <candidate>`, `evidence artifact missing or altered; rerun`, and `attestation has no recorded observation`. A work item says `status todo, not verified; checks owed: A-01` or `verified, but evidence is not current for A-01, A-02`, and `next` says `rerun stale evidence: A-01, A-02` instead of `reverify stale evidence`. `work set --status verified` names the check and reason that refused it.

`gate`, `next` and `status --json` carry a `summary`: a one-line `headline` (`16 check(s) stale; final review owed`), the check IDs grouped as accepted / legacy / stale / failed / not_run / unapproved / other, unverified work, open findings and blockers, whether the review is current, and `rerun`, the exact `dmd run A-01 A-02 …` that discharges the stale, failed and unrun checks. Twenty-five per-item lines for one cause now read as one fact.

The Stop hook repeats that headline, the first three reasons and the rerun command in both observe and enforce modes. "task remains ACTIVE; dmd gate has not accepted it" told the agent nothing it could act on.

`dmd run` prints a `start` and a `finish` event per check on stderr (check, candidate, timeout; then exit, duration, failure). Stdout is unchanged: one JSON row per result. A watcher can now tell a twenty-minute integration check from a hang.

The runtime is reachable as a PATH shim. `hooks/install.py --link-bin [DIR]` symlinks `bin/dmd` into `DIR` (default `~/.local/bin`), refuses to replace a file or a foreign link, records the link in the installation manifest and removes it on `--remove`; `--no-hooks` manages only the link. SKILL.md mandated a `dmd()` shell function, which an agent harness forgets between tool calls, so every command block redefined it. The function is now the fallback for a session without the shim.

The SessionStart hook no longer tells a resuming session to read the whole SKILL.md. It names the gate headline, the first reasons and the rerun command, and says to read `references/recovery.md` plus `dmd reconcile` output, reserving SKILL.md for initialising a new assignment.

A final review survives a rerun on a byte-identical candidate. The review signature bound to whole receipts, including timestamp, duration and log path, so rerunning the same green checks on the same fingerprints owed a second review of a state that had already been reviewed. It now binds to each receipt's evidence identity (candidate, source, definition, outcome, observation); a moved tree, an edited contract or a changed outcome still reopens it.

`dmd --help` describes every subcommand. `gate --brief` and `next --brief` replace the per-candidate source map with `source_digest` and a `candidates` count.

## 0.5.0 — 2026-09-11

Evidence follows the tree that was tested. Nine items, all from one session of running integration checks in linked worktrees while another session edited the shared checkout.

Every check now has a candidate: the tree its evidence is bound to. It defaults to the task root when the check runs inside it and to the checkout its cwd belongs to otherwise, and can be set with `--candidate`. Fingerprints are taken per candidate, a receipt is accepted while its own candidate is unchanged, and the receipt records the candidate path and its HEAD. Sixteen green worktree runs invalidated three times by edits elsewhere was the motivating case. Records written by 0.4.x keep their definition digests and receipts: the new fields join the definition only when set.

A command that passed while its candidate or definition moved is reported as `STALE` (`RED-STALE`) rather than `FAIL`, with HEAD before and after and the changed paths, in the run output, the receipt and the report. Previously both cases printed `FAIL` and the difference lived in the evidence file and `git reflog`.

`dmd run A-01 A-03` and `dmd run --all` run a list in one fingerprint window: every candidate is snapshotted once at the start and once at the end, every receipt binds to the start state, and a move in between names every affected receipt instead of quietly staling the earlier passes. The list is refused before anything runs if one check lacks a current approval.

A preflight refuses to run against a candidate whose dirty files were modified within the last few seconds (`--quiet-window`, default 3 s) or that another task's recorded live run holds, naming the paths or the task and whether its runner is alive. It runs before the fingerprint, so nothing is recorded and no long integration run is burned.

`check add --exclusive NAME` serializes checks that share a resource across tasks, worktrees and sessions on this machine, through per-tag locks under the state root taken in sorted order with a bounded wait (`--wait-exclusive`, default 600 s). A refusal is a clean exit, and results already taken in the list are kept.

`req list`, `work list`, `check list`, `finding list` and `blocker list` print one group; `status --only SECTION` renders one report section.

`recover-run` inspects the stranded run: it refuses while the runner PID is alive on this host and otherwise prints what it found and why the proof was accepted, instead of leaving that proof to the operator's own `ps`. The running record now carries the PID, host and check list.

The final review and a manual PASS fingerprint once. The second hash added a race window and no protection, since the review signature already binds to the source state and the next gate detects any edit.

Documented the runtime as a shell function. `dmd="python3 /path/dmd"` is one word to zsh and fails silently; `dmd() { python3 /path/dmd "$@"; }` does not.

Fingerprint bytes are unchanged; a task root's fingerprint under 0.5.0 equals its 0.4.x fingerprint. A disproved finding records a digest of the candidate map; disproofs recorded by 0.4.x stay valid while the root is unchanged.

## 0.4.1 — 2026-09-10

Resilience under ordinary contention and clearer failure when the task is elsewhere.

State locks were strictly non-blocking, so any overlap — a `PostToolUse` hook firing while the agent was inside `dmd run`, two commands issued back to back — failed instantly with "another operation owns this lock". Locks now retry with capped exponential backoff and jitter for a bounded wait (default 5 s, `DMD_LOCK_WAIT` 0..600 s) before giving up, and the failure names how long was waited and the override. `PostToolUse` / `PostToolUseFailure` bookkeeping waits at most 1 s and then drops the event silently rather than surfacing a hook error to the host; task state is never dropped.

"No active task" previously said nothing about where the task actually was. State is keyed by physical worktree, so an assignment started in the main checkout is invisible from a linked worktree; `dmd` now names any unfinished task recorded for a sibling worktree of the same repository, with its state and the `--cwd` needed to reach it. Finished tasks are not suggested.

Locks still never block indefinitely and still do not fence independent source writers.

## 0.4.0 — 2026-09-10

Closes a defect review of 0.3.0. The headline gap: every acceptance safeguard applied only to `command` checks, while `manual`/`review`/`browser` checks were accepted on a self-authored note and rendered identically in the report — so an assignment could reach `COMPLETE` with nothing executed and nothing in the report saying so.

Acceptance basis is now explicit. Non-command checks require `--attested-because`, are labelled `SELF-ATTESTED` beside `EXECUTED` in every report, and are counted in `dmd gate` output. A requirement whose entire accepted acceptance set is self-attested no longer reaches `COMPLETE`; permitting it requires recorded operator authority through `req attest-only`, which the report names.

Agent-authored records are correctable without deleting obligations: `work remove` and `check remove` supersede with a mandatory rationale, are refused when something still depends on them, and stay in the report. `--no-regression` corrects a mis-flagged check through the existing history trail. Requirements remain operator-owned.

Portability and execution: the state root is canonicalized before its symlinked-ancestor check, so `DMD_STATE` works under `/tmp` and `$TMPDIR` on macOS. A check whose descendants keep the output pipes open now drains briefly and completes on the command's own exit status instead of being forced to time out; the receipt records that background holders remained.

The hook installer received the same path treatment: a symlinked settings directory or state root is canonicalized instead of refused, a symlinked settings file is still refused, and uninstall removes this package's own registrations even without its manifest.

Diagnosis and recovery: an expired approval names what drifted (definition, input or environment); `dmd list` reports an unreadable record as a row instead of failing the whole listing; internal defects exit 3 with a traceback instead of masquerading as usage errors; a review that finds outstanding work is recorded in a review log rather than discarded; repeated equivalent attempts are surfaced in `dmd next` and the report and detect alternation, without ever gating completion. History is appended to `events.jsonl` with a bounded tail in `task.json`, removing the quadratic rewrite and the wedge at the state ceiling. Stale session bindings are pruned.

## 0.3.0 — 2026-09-10

One consolidated Done Means Done skill and standard-library Python runtime. Stable requirements/work/check/findings records, source-content snapshots, exact check/input approval, bounded execution, captured evidence integrity, mandatory remediation of every recorded confirmed defect, intentional regression baselines, final review, generated reports and interruption recovery.

Tightened completion around blockers, unknown external outcomes, absent/unowned work, explicit acceptance mappings, stale definitions/source, skipped required checks, unresolved findings, cancelled/paused assignments and existing COMPLETE records that no longer match the candidate.

Claude Code adapters distinguish Stop JSON control from mapped TaskCompleted exit-code control. Session bindings are isolated, observe/off modes remain explicit, and a semantic no-progress pause preserves obligations. Hooks are installed by an opt-in preview/apply installer with exact managed removal.

Schema-1 import is copy-only and discards historical green assumptions. Invalid imports are rejected before replacing the active task. The package includes actual CLI/process/hook/adoption regressions and an isolated red/green repair demo. See the validation record for platform and live-host limits.

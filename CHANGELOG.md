# Changelog

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

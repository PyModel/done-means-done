# Validation report

Release: **Done Means Done 0.8.0**, prepared September 23, 2026 (this header names the current release; the sections below are cumulative). The source repository was inspected at `bd9139c43a2afb370e8d78d00e3f9c84f55d8cc2`. This is the consolidated local package, not a claim that this revision was pushed to GitHub.

## Executed verification

| Check | Actual result | Evidence |
|---|---|---|
| `python3 -B tests/run.py` | **378 tests passed; 0 failures; 0 skipped** (macOS Python 3.14.7), including the 23-case adversarial `test_anticheat.py` suite added in 0.8.0 (hollow-verifier self-approval, approval inheritance, fabricated anchors/quotes/proofs, protected-path interception, prompt-capture scoping, duplicate/baseline/downgrade laundering, untracked-output freshness, real-PTTY terminal confirmation). A nonempty clean run is required for its success marker. | [Final test transcript](tests-final.log) |
| `python3 -B examples/local-demo.py` | Actual subprocess CLI workflow driven through the documented host permission channel (pre-tool-use ask, post-tool-use confirm; no human identity claimed): an approval that was only inspected is refused execution until the operator confirms it; intentional red baseline; root-cause repair; green verification; finding closure; final review/gate/report; same-size preserved-mtime source mutation rejected; final repair/reverification; a solely attested requirement refused, then completed under recorded operator authority; temporary fixtures removed. | [Local demonstration transcript](local-demo.log) |
| `python3 -B tests/validate_package.py` | Python syntax parsed using the Python 3.10 grammar; required files, release identity, local Markdown links, code fences, skill size, real files/no symlinks and no bytecode-cache artifacts checked. | [Static package transcript](package-validation.log) |
| YAML frontmatter parsed with PyYAML 6.0.3 (Docker `python:3.10`) | Correct single skill name, standard fields and version. PyYAML is not a runtime dependency. | [Metadata result](frontmatter-validation.log) |
| `python3 -B bin/dmd --version` and subcommand help | Version 0.8.0; all 33 subcommands accept their help request. No acceptance command or hook installation executed by help checks. | [CLI surface transcript](cli-help-validation.log) |
| Live Claude Code host (2.1.280, model haiku, isolated settings) | 7/7 scenarios pass: SessionStart context reaches the model; enforce-mode Stop blocks and the agent follows `dmd next`; the watchdog releases; observe mode never blocks; an operator decision is denied without a prompt channel, recorded as confirmed once approved through a permission-prompt tool, and still caught in a `python -c` argv-list form. Total cost $0.17. | [Live host transcript](live-host.log) |
| SHA-256 manifest | Every tracked file's checksum listed and verified with `shasum -a 256 -c SHA256SUMS`; no symlinks or bytecode caches. No archive was built for 0.7.0. | `SHA256SUMS` |

The unit suite includes **synthetic predicate fixtures** and **actual CLI/filesystem/process/hook-contract/installer/migration tests**. Synthetic records test the acceptance predicate, not independent truth of software behavior. Hook payload fixtures test documented control responses; the live host run above exercises them in a real session.

All installer tests used isolated temporary settings/state. Pre-fix failure transcripts (`adoption-before-fix.log`, `resume-before-fix.log`) are 0.3.0 artifacts, intentionally retained as regression evidence and explained in [remediations](REMEDIATIONS.md); they are not current failures. The transcripts in the table above were regenerated on macOS for 0.8.0. The 0.7.x live-host row below remains the recorded 0.7.x evidence; the 0.8.0 hook surface (UserPromptSubmit capture, Edit/Write matchers, payload-bound confirmation tickets) is exercised by local hook-contract tests, not a new live host run.

## 0.4.0 defect-review verification

Executed on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7), the platform 0.3.0 declared untested:

| Check | Actual result |
|---|---|
| Hand reproductions of C-01 through C-06 (C-07 to C-14 are covered by the suite, not by hand) | Each failed on 0.3.0 and behaves correctly on 0.4.0; transcripts summarized in [remediations](REMEDIATIONS.md) |
| Event growth at 402 mutations | 102 KiB `task.json` with a 200-event tail; complete 402-line `events.jsonl` |
| Whole-project fingerprint cost | 0.38s at 5,621 tracked files; linear and deliberately uncached |

## Build environment

0.3.0 was prepared on Linux x86_64, kernel 6.18.35, glibc 2.41, Python 3.13.5. The 0.4.0 defect review and every regenerated transcript above ran on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7). Commands and fixture processes ran locally. Since 0.7.0 the suite also runs on Python 3.10, both on macOS and on Linux (Docker `python:3.10`, Debian). Temporary project and state directories in the demo are removed after it completes; its transcript is retained, not live temporary-path download links.

## Explicitly not verified or implemented

- Live Claude Code sessions were run for 0.7.0 only in headless `-p` mode ([transcript](live-host.log)). Interactive approval of an operator decision was exercised through `--permission-prompt-tool`, not a human at the prompt; hook reload and compaction integration were not exercised.
- No Windows or multi-Python-version runtime matrix was executed; Windows is unsupported in this implementation.
- The inspected source repository's original acceptance suite was not executed as an upstream checkout. Direct container network cloning was unavailable; source inspection used the GitHub connector.
- No test proves the model notices/records every possible defect, maps every natural-language request correctly, or cannot modify its own private files. Reviewer identity and authority are recorded attestations, not authenticated principals.
- No background service, multi-day scheduler, remote worker fencing, automatic post-exit restart, or immutable source sandbox is included.
- Whole-project hashing, private-state size limits and advisory locks have not been benchmarked for a large agent fleet. No efficiency, universal bug-freedom, or uninterrupted-execution guarantee is claimed.

No remote repository was modified, no user host configuration was installed, no credentials were requested, and no background work was scheduled in preparing this artifact. The package is ready for local evaluation and opt-in installation using the adoption guide; live-host acceptance remains a separate validation step.

## 0.4.1 resilience verification

| Check | Result |
|---|---|
| `tests/test_resilience.py` (10 tests): contended lock acquired after holder release, zero-wait immediate failure naming `DMD_LOCK_WAIT`, deadline expiry, env validation, CLI mutation waiting out a concurrent holder, silent `PostToolUse` under contention, recorded `PostToolUse` when free, sibling-worktree hint via a real `git worktree add`, finished siblings not suggested, plain message without siblings | All pass; full suite 188 tests, [transcript](tests-final.log) |
| Trigger | A field report from another repository: an agent in a linked worktree saw "dmd has no active task in this worktree" with no pointer to the task in the main checkout, and hook/CLI overlap produced instant lock failures |

Run on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7).

## 0.5.0 candidate-bound evidence verification

| Check | Result |
|---|---|
| `tests/test_candidates.py` (41 tests): default candidate resolution (root, subdirectory, linked worktree, explicit `--candidate`), root edits leaving a worktree green accepted and vice versa, receipt candidate/HEAD, pre-0.5.0 records keeping their definition digest, `STALE`/`RED-STALE` with HEAD and changed paths, list runs with one snapshot window and a mid-sequence move staling every receipt, `--all` refusing on one unapproved check, quiet-window preflight (recent dirty file refused, old dirty file allowed, `0` disables, other task's live and dead runs named), `--exclusive` held-lock refusal with bounded wait and earlier list results kept, per-group `list` and `status --only`, `recover-run` refusing a live PID and reporting its findings, review and manual PASS fingerprinting once | All pass; full suite 229 tests, [transcript](tests-final.log) |
| Fingerprint scheme | Old and new `fingerprint()` produce the same digest for the same fixture tree (Git repo with a tracked file, a symlink and a dirty edit) |
| Trigger | One session running integration checks from linked worktrees while another session edited the shared checkout: sixteen green runs invalidated three times, `FAIL` indistinguishable from "candidate moved", a variable-held `dmd` string breaking under zsh, and a manual `ps` proof for `recover-run` |

Run on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7).

## 0.5.1 upgrade-report verification

| Check | Result |
|---|---|
| `tests/test_candidates.py` ReasonCase (12 tests): a pre-0.5.0 worktree receipt accepted after the upgrade and labelled in the report, stale with the root and saying why, rebound on rerun; every gate reason naming its cause; the `summary` grouping with the exact rerun command; `work set --status verified` naming the refusing check; the Stop hook and the SessionStart hook repeating the headline and rerun command; SessionStart pointing a resuming session at `references/recovery.md` and `dmd reconcile`; `run` start/finish events on stderr with stdout rows unchanged; `gate --brief` / `next --brief` replacing the source map with its digest; a review surviving an identical-candidate rerun and reopening on a tree change; every subcommand carrying help text | All pass |
| `tests/test_model.py`: review survives a receipt with a new timestamp, duration and log; reopens on a changed source or a changed observation | All pass |
| `tests/test_adoption.py` (3 new): `--link-bin` previews without touching disk, installs a symlink that runs `dmd --version`, records it in the manifest and removes it on `--remove`; `--no-hooks` leaves `settings.json` absent; a foreign file or foreign symlink at the link path is refused unchanged | All pass; full suite 246 tests, [transcript](tests-final.log) |
| Trigger | A live sixteen-check task with four linked worktrees upgraded from 0.4.1 to 0.5.0: every green went "stale" with no tree changed, the Stop hook gave no reason, twenty-five per-item lines hid one cause, `PASS or stale/missing` read as PASS, the `dmd()` function had to be redefined in every tool call, long checks were indistinguishable from hangs, the resume hook pointed at the full SKILL.md, and an identical rerun invalidated the final review |

Run on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7).

## 0.5.2 hook-outside-checkout verification

Reproduced the reported `SessionStart:startup hook error … DMD_STATE must remain outside the project being verified` by feeding `dmd hook session-start` a payload whose `cwd` is the home directory (not a Git checkout) with the default state root. Exit was 2. After the fix all four hooks exit 0 with no output for that cwd and for a deleted cwd; a session bound to a real task still names the worktree mismatch; `init` from such a cwd still refuses and names both paths. Regression suite `tests/test_resilience.py::StateOutsideCheckoutCase` (4 tests); full run `DMD_TESTS_PASS:250` in `evidence/tests-final.log`.

## 0.6.0 resilience and isolation verification

Trigger: a Stop hook in an unrelated project failed on every stop with `dmd: declared input is missing: /private/tmp/claude-501/…/scratchpad/api-gate.sh`. A PAUSED task from a prior session declared check inputs in a since-deleted session scratchpad; the hook fingerprinted the task before looking at its state, and the `DmdError` escaped to exit 2. Four read-only audits (hooks/isolation, model/fingerprints, runner/locking, CLI/docs) then reproduced 30-odd further edge cases; the confirmed ones are fixed below.

| Check | Result |
|---|---|
| `tests/test_hardening.py` IncidentCase: a PAUSED task with a deleted declared input exits 0 on all four hooks in enforce mode; an ACTIVE task reports `A-01: declared input missing … --clear-inputs` as a per-check reason and every read command keeps working; the Stop hook blocks with that reason instead of exiting 2 | All pass |
| IsolationCase: a session moving to another project releases its binding, names both roots and governs the new project's own task; a dead binding is dropped; a renamed checkout, a cwd that is a file, loose state-root permissions and malformed/non-UTF-8/empty payloads never exit nonzero; sessions and watchdogs are hashed and bounded at 50; the watchdog pause names the session; Git discovery failure refuses instead of re-keying the task | All pass |
| InputValidationCase: `--input` rejects `""`, a directory and a missing path at edit time; fifos and unreadable files are drift, not crashes; `work add` without `--req` and `blocker add` with a bad `--item` are actionable and allocate no ID; `uncertain list` | All pass |
| DeferralCase: `--status deferred` is refused; `finding defer` requires `--authority`; the task completes with the deferral disclosed in `summary.findings_deferred`, the headline, the amendments and the report | All pass |
| RunnerEdgeCase: output exactly at `max_output` and non-UTF-8 output near it are not `OUTPUT_LIMIT`; a missing cwd is `SPAWN_FAILED`, a clean FAIL; a timed-out command's EXIT trap output reaches the evidence (SIGTERM, 2 s grace, then SIGKILL) | All pass |
| HealthCase: `doctor` exits 1 naming the missing input; `list --json --state`; `gc` removes only dead bindings; `relocate` moves the record, rewrites paths and resets evidence to NOT_RUN; the sibling hint survives a pruned worktree | All pass |
| `tests/test_candidates.py`: a fresh write settles within the default window and the run proceeds (edit-then-verify no longer refuses) while a still-dirty tree under a long window is refused; `tests/test_resilience.py`: the moved-cwd case now asserts exit 0 plus the release message | All pass; full suite `DMD_TESTS_PASS:279`, [transcript](tests-final.log) |

Run on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7).

## 0.7.0 completion-contract verification

Trigger: an architecture review and a correctness/gaming review of 0.6.0. Every behavioral change below has a test that failed on 0.6.0 code and passes now (`tests/test_completion.py`); the red runs were taken against the 0.6.0 modules before each fix.

| Check | Result |
|---|---|
| BlockedMeansNothingActionable: stale coverage, a missing red baseline and a failing fixed-verified finding on an unblocked requirement are `ACTIVE` with an action (0.6.0: `BLOCKED`, `next: []`); everything waiting on a blocker, directly or through a dependency, is `BLOCKED` | All pass |
| SuspendedTaskHooks: TaskCompleted on a paused task exits 0 (0.6.0: exit 2, "no current accepted evidence") | Pass |
| StopGovernance: a subdirectory keeps governing, an unrelated project releases; a new finding is not progress, an accepted run is; a live run is not counted as a loop | All pass |
| RunnerSurvivesItsParent: SIGTERM to `dmd run` exits 130, terminates the check and records the interruption (0.6.0: exit -15, check kept running); SIGKILL of the runner still stops the check within the grace period (0.6.0: orphaned) | All pass |
| LessCeremonySameGuarantees / NoCheapExits / VolatileSource: one-call author-and-approve completes a task with no work items; an edit still forces reinspection; a confirmed finding needs an accepted executed check to be disproved; `req cancel` refuses to strand work; a stuck plan yields an action; a never-settling file is `volatile` drift with other files hashed as before | All pass |
| Fingerprint compatibility: `source.fingerprint` of two unchanged trees computed by the 0.6.0 and 0.7.0 modules | Identical digests |
| RunnerEdgeCase: slow cleanup output is read through the grace period (0.6.0 runner: output lost); the timeout-cleanup test traps TERM and passes under dash | All pass |
| Full suite, macOS Python 3.14.7 and 3.10 | `DMD_TESTS_PASS:306;skipped=0`, [transcript](tests-final.log) |
| Full suite, Linux (Docker `python:3.10`, Debian, `/bin/sh` = dash) | `DMD_TESTS_PASS:306;skipped=0` (0.6.0: 278/279) |
| A match token inside the command text is refused (`cmd && echo TOKEN`, `print("TOKEN")`); a later session leaves a COMPLETE task's record byte-identical and creates no binding; a sibling worktree without its own task stays governed, one with its own task rebinds to it | All pass |
| Real state on this machine: all 28 recorded tasks loaded and gated read-only by 0.6.0 and 0.7.0 code | Identical verdict distribution (22 ACTIVE, 4 PAUSED, 1 COMPLETE, 1 with a missing root); no load errors |

Run on macOS (Darwin 27.0.0, Apple Silicon, Python 3.14.7).

## 0.7.0 open-item verification

Trigger: the remaining items of the same review (operator authority, check outputs, disproof binding, request items, the task store, hook cost, legacy migration), two supervisor crash reports, and a request that the agent leave no clutter behind.

| Check | Result |
|---|---|
| OperatorDecisions (11 tests): with PreToolUse installed and mode `enforce`, `req cancel`, `req attest-only`, `finding defer`, `state`, `review --kind independent`, `init --new` and `config` get a host `ask`; an unapproved decision leaves the gate owing `AU-*` and a withdraw restores what it replaced; an approval confirms only the approving session's task; `echo`/`grep` mentions are not asked; without a channel the decision applies and every report says it was never confirmed | All pass |
| DeclaredOutputsCase: a check writing `reports/junit.xml` passes with `--writes`, an unrelated edit still stales it, and tasks without `--writes` keep byte-identical fingerprints | All pass |
| Disproof binding: an edit elsewhere keeps a disproof; an edit to its `--location` or inputs reopens it ("disproof is stale") | All pass |
| RequestItemsAreAccounted: a five-item request with four mapped items refuses coverage and names the unmapped item; a prose request records no items; old tasks keep their contract digest | All pass |
| StoreBoundary: hooks and migration import no CLI module (AST check) | Pass |
| NextActionsAreRunnable: every `dmd next` action parses as a complete command | Pass |
| NoLeftovers: request text and review evidence come from standard input (no scratch file); the report lists untracked files that are not declared outputs; `gc` prunes expired approval tickets; the run index drops a run its task no longer records | All pass |
| Supervisor crash (SIGABRT in `_enter_buffered_busy`, from the reports): a supervisor whose parent stopped listening holds without aborting, and one orphaned by its parent exits with no `Fatal Python error` (red on the previous code) | All pass |
| Migration: an unknown legacy check method imports as `manual` needing review; a repeat import of the same file is refused without `--new` | All pass |
| Hook latency (hyperfine, 40 runs) | PostToolUse 92.7 to 42.0 ms; Stop 119.9 to 77.4 ms. The new PreToolUse hook runs on every Bash call: 50 ms in observe mode, 46 ms in enforce mode for a command that is not a decision (a bare `python3 -c pass` takes 22 ms) |
| Real state: 28 recorded tasks gated read-only by 0.6.0 and this code | Identical distribution (22 ACTIVE, 4 PAUSED, 1 BLOCKED on a missing root, 1 COMPLETE); 0 contract-digest differences |
| Full suite, macOS Python 3.14.7 and 3.10; Linux Docker `python:3.10` | `DMD_TESTS_PASS:338;skipped=0` on each, [transcript](tests-final.log) |

Run on macOS (Darwin 27.0.0, Apple Silicon, Python 3.14.7).

## 0.7.1 housekeeping verification

Trigger: cleaning the state root of this machine found 39 empty record directories and agent-written evidence files sitting directly in `DMD_STATE`. A live session in another project was stuck on a whole-suite check flagged `--regression`.

| Check | Result |
|---|---|
| `NoLeftovers.test_a_folder_without_a_task_leaves_nothing_in_the_state_root`: SessionStart, Stop, PostToolUse, PreToolUse and `next` from a Git folder with no task | No `v2/` directory is created (on 0.7.0 code: `v2/<project>/<worktree>` appeared) |
| `NoLeftovers.test_gc_removes_empty_record_directories_only` | `gc` removes an empty project/worktree pair and one holding only the active pointer and lock of a deleted task, each older than a minute; the live task's directory is untouched |
| `NextActionsAreRunnable.test_a_regression_check_without_a_baseline_names_every_way_out` | The action names `run --red`, `check baseline --evidence -` and `check edit --no-regression` |
| Full suite, macOS Python 3.14.7; Linux Docker `python:3.10` | `DMD_TESTS_PASS:341;skipped=0`, [transcript](tests-final.log) |

Run on macOS (Darwin 27.0.0, Apple Silicon, Python 3.14.7).

## 0.8.0 hardening verification

- `tests/test_anticheat.py` (23 cases, part of the 378 above) reproduces the audited
  bypasses as red-before/green-after regressions: self-approved hollow verifiers cannot
  execute; changed verifiers cannot inherit an operator confirmation while unchanged
  reapprovals reuse it; invented anchors, authority quotes and blocker proofs are refused
  or left pending; coverage/context/amend/review-policy decisions pend without the
  operator; protected-path Bash/Edit/Write forms are put to the operator; a mismatched or
  failed post-tool event confirms nothing; host prompt capture is session/worktree scoped
  and cannot be weakened at init; confirmed duplicates need shared executed evidence;
  baseline limitations and regression downgrades pend; failed runs record attempts
  automatically; pre-existing untracked source under `--writes` is not hidden and later
  edits of generated outputs go stale; real PTY yes/no confirmation works and is labeled
  as simulable, not identity proof.
- Migration behavior is covered by updated legacy tests: pre-0.8.0 receipts are reported
  stale with the reapprove-and-rerun instruction, and a reapproved rerun rebinds them.
- Not re-run for 0.8.0: a live Claude Code host session and Linux coverage. The 0.7.x
  live-host transcript remains in the table above; the 0.8.0 hook surface is verified
  through local hook-contract tests only.

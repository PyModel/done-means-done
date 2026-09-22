# Command reference

Use `dmd COMMAND --help` for parser details. All examples invoke the bundled `bin/dmd`; it is not a separately installed package. Global options precede the subcommand:

```bash
dmd --cwd /absolute/project --task T-actual-id status --json
```

Paths are resolved on the current machine. Symlinked state and settings *directories* are canonicalized to their physical location, so a dotfiles layout and the macOS `/tmp` and `/var` aliases work; a settings *file* that is itself a symlink is still refused, because replacing it would destroy the link. Task/session identity belongs to a real assignment, not a guessed string.

Every `--evidence` and `--request-file` accepts `-` to read standard input (a heredoc); `dmd` stores its own copy inside the task record. Write nothing of your own under `DMD_STATE`: it holds only what `dmd` writes, one directory per worktree. If an artifact has to exist as a file first, put it in the host's scratch directory or a `mktemp -d` and delete it after recording it.

## Assignment and requirements

| Command | Contract |
|---|---|
| `init --request-file FILE\|- --authority TEXT [--session ID] [--independent-review]` | Preserve the sanitized request and activation (`-` reads standard input, so no scratch file is needed); reject replacing an unfinished task by default |
| `init -m TEXT --authority TEXT --new` | Explicitly start another assignment; preserve the previous unfinished task as paused, never overwrite it |
| `req add TEXT --anchor TEXT [--covers C-01 ...]` | Add one independent outcome/constraint linked to the source request, and the request list items it covers |
| `req cancel --id R-01 --authority TEXT` | Record actual operator removal; invalidate contract coverage. Refused while active work elsewhere depends on its work (`work set --clear-deps` first) |
| `req attest-only --id R-01 --authority TEXT` | Record operator authority to accept this outcome on attestation alone; named in the report |
| `work remove --id W-02 --note TEXT` | Supersede an agent-authored work item; refused while a dependency or finding still maps to it |
| `check remove --id A-02 --note TEXT` | Supersede an agent-authored check; refused if its requirement would be left with none |
| `amend TEXT` | Preserve an operator amendment; reconcile changed requirements afterward |
| `coverage show` | Display the source request and obligation inventory without executing checks |
| `coverage items` | The request's explicit list items (`C-01`...) and what covers each: a requirement, a context note, or `UNMAPPED` |
| `coverage map C-03 [C-04 ...] --req R-02` | Record that an existing requirement covers these items |
| `coverage context C-05 [C-06 ...] --note TEXT` | Mark items as context (background, a question, a thank-you), not outcomes |
| `coverage assert --note TEXT` | Attest source-to-inventory review against the current contract digest; refused while a list item is unmapped |
| `req list`, `work list`, `check list`, `finding list`, `blocker list` (`--json`) | One record group, read-only, one line per record; `status --only work` renders one report section |

`--authority` records the instruction; on its own it does not authenticate a human (see *Operator decisions*). Never fabricate authority or use a new task to bypass the old assignment. Any missing mandatory requirement remains a review failure even when the structural gate is otherwise green.

Tasks created since 0.7.0 record every explicit list item of the request (`- x`, `* x`, `1. x`, `2) x`, `a) x`; fenced code is skipped) and of each `amend`. Each must be covered by a requirement or marked context before coverage can be asserted, so an enumerated outcome cannot be dropped silently. Prose is not segmented. Tasks created earlier are unaffected.

## Operator decisions

These commands change the contract the agent is held to: `req cancel`, `req attest-only`, `finding defer`, `state`, `review --kind independent`, `init --new` / `migrate --new` over an unfinished task, `config --mode|--max-no-progress`, and `relocate`. Each logs a decision (`AU-01`...) on the task.

With a **confirmation channel** (hooks installed by this release's `install.py --apply`, which registers PreToolUse, and `dmd config --mode enforce`), the host asks the operator before any such Bash command runs; the prompt names what the command would do and still appears when permission prompts are otherwise bypassed. The decision counts for the gate only once that approval is recorded (by PostToolUse), or once the operator answers `y` when the command runs in their own terminal. Until then:

- the gate owes `AU-01: req.cancel R-02 awaits the operator's confirmation`, which no blocker hides;
- an unconfirmed `state PAUSED|CANCELLED` does not suspend enforcement, and an unconfirmed `config` change leaves the hooks on their previous settings;
- a superseded task stays the one that must finish.

```bash
dmd authority list                 # every decision and whether it was confirmed
dmd authority confirm AU-01        # the host asks the operator again, naming the decision
dmd authority withdraw AU-01       # undo an unconfirmed decision; what it changed is restored
```

Running a hook entry by hand or removing the hooks with `install.py --remove` is also put to the operator. Without a channel (hooks off or in observe mode, or another host) decisions apply at once and the report and `summary.authority_unverified` list them as never confirmed. In a headless `claude -p` run nobody can approve a prompt, so a decision there is denied before it runs.

## Work and acceptance

```bash
dmd work add "Implement caller update" --req R-01 --dep W-01 --owns 'src/caller/**'
dmd work set --id W-02 --status doing
dmd work set --id W-02 --status implemented
dmd work set --id W-02 --status verified
dmd work set --id W-02 --replace "Improved implementation approach" --note "Evidence supporting the revised approach"
```

Work items are optional decomposition. Every work item requires a requirement; a check maps to a requirement and to zero or more of its work items (`--work` is repeatable). A work item is satisfied when it has at least one mapped check and every mapped check is accepted; its `--status` is a progress note, and `--status verified` is refused without that evidence. Planned work never disappears silently: it needs its own evidence or `work remove --note`. Starting/closing dependent work requires current prerequisite evidence. `--dep` and `--owns` are repeatable; `--clear-deps` empties the dependency list. `--owns` is planning metadata, not an enforced write lease. Replacing the approach preserves its history and invalidates coverage.

```bash
dmd check add --req R-01 --work W-01 --work W-02 \
  --cmd "python3 -B tests/check_behavior.py" --run-cwd /absolute/project \
  --expect "Intended behavioral assertions execute" --match "BEHAVIOR_PASS:4" \
  --input tests/check_behavior.py --timeout 300 --max-output 1048576 \
  --candidate /absolute/project --exclusive integration-db --writes reports/junit.xml
dmd preview A-01
dmd approve A-01 --note "Inspected command and transitive scripts within authorized permissions"
dmd check add ... --approve "Inspected command and verifier"   # author and approve in one call
dmd run A-01
dmd run A-01 A-03 A-04          # one fingerprint window for the list
dmd run --all                   # every live, approved command check, in ledger order
dmd run A-01 --quiet-window 0   # skip the concurrent-writer preflight
dmd run A-01 --wait-exclusive 30
```

`--candidate` names the tree the check's evidence is bound to. It defaults to the task root when `--run-cwd` lies inside it, otherwise to the Git checkout the cwd belongs to (a linked worktree, another repository), or the cwd itself outside Git. Each candidate is fingerprinted separately; a receipt is accepted while its own candidate is unchanged, so an edit in the shared checkout does not invalidate a green taken in a worktree. The receipt records the candidate path and its HEAD commit, and the report prints them as `Tested: <path> @ <sha>`.

`--writes GLOB` is repeatable and names an untracked file the check itself generates (a junit report, a coverage file). Matching untracked files leave that candidate's fingerprint for every check on it, and the preflight does not treat them as a concurrent writer, so the check can pass without staling itself or its siblings. A glob with a `/` matches the path relative to the candidate root; one without matches the file name anywhere. Tracked files are always hashed: a glob that matches a tracked file is refused, and so is one that names a whole file type (`*.py`). `--clear-writes` removes them. With no declared outputs the fingerprint is unchanged from earlier releases. In a Git checkout, `.gitignore` covers the same case.

`--exclusive NAME` is repeatable and names a resource the check cannot share (a database, a port, a fixture directory). Two checks with the same tag never execute concurrently, across tasks, worktrees and sessions on this machine: the runner takes a lock under the state root per tag, in sorted order, waiting up to `--wait-exclusive` seconds (default 600) before refusing. A refusal is a clean exit 2, not an interrupted run; earlier results in the list are kept.

Before executing, `run` refuses a candidate whose dirty or untracked files were modified within `--quiet-window` seconds (default 3; `0` disables) and a candidate that another task's recorded live run holds, naming the paths or the other task and whether its runner is alive. Nothing is recorded for a refused run.

`run` prints one JSON row per result on stdout and a `start` / `finish` event per check on stderr (`{"check","event":"start","candidate","timeout","at"}`, then `{"check","event":"finish","exit","duration_s","failure"}`), so a watcher can tell a long check from a hung one. `run` with several IDs, or `--all`, snapshots every candidate once before the first check and once after the last. Every receipt binds to the start state. A candidate that moved in between makes every receipt on it `STALE`, and the trailing batch line lists what moved. A list is refused before anything runs if one check lacks a current approval. Interrupting a list loses the results not yet written; the run is recorded as interrupted for `recover-run`.

A check whose command passed (expected exit, literal match, within bounds) while its candidate or definition moved is reported as `STALE` (`RED-STALE` for a red run) instead of `FAIL`. The row and the receipt carry `stale.drift`: HEAD before and after and up to 50 changed paths. The status stays `FAIL` and the receipt is not accepted.

Only `--method command` produces machine-verified acceptance. `--method manual|review|browser` requires `--attested-because TEXT` saying why no command can observe the behavior; those checks are reported as `SELF-ATTESTED`, counted in `gate` output, and cannot alone accept a requirement without `req attest-only`.

`--match` is a literal string, not a regex. A command whose text contains its own match (`npm test && echo OK`, `python3 -c 'print("OK")'`) is refused: that reduces to an exit status. Like the bare-printer lint, this catches the lazy case, not a determined forger. It is necessary but not sufficient semantic proof. The called verifier must assert the correct behavior and intended test discovery before printing it. An arbitrary zero exit is insufficient.

`--approve NOTE` on `check add` or `check edit` records the same inspected approval as `dmd approve` for the definition as written. Any later edit clears it, so edited command text never runs uninspected.

`check edit --id A-01 ...` changes explicitly supplied fields, archives the old definition/evidence, and clears current green, baseline, and pending-import status. Reapprove after any definition/input/runtime approval change; an expired approval names whether the definition, a declared input, or the execution environment drifted. `--no-regression` clears a mis-flagged regression declaration and is recorded in the check history. An edit must leave the entire check valid. `--input` is repeatable and names real files relative to the check working directory or absolute paths; declared missing inputs are errors.

Timeout range: greater than zero through 604800 seconds. Captured-output limit: greater than zero through 1048576 bytes. A timed-out or cancelled command gets SIGTERM, then output is read for up to 2 s while its cleanup runs, then SIGKILL. For portable cleanup trap TERM as well as EXIT (`trap cleanup EXIT; trap 'exit 143' TERM`): dash, Debian's `/bin/sh`, skips an EXIT trap on SIGTERM otherwise. Timed-out/overflowed/cancelled commands fail; larger logs cannot be truncated into a passing observation. The runner serializes verification per task and terminates the check's remaining local process group after execution. A command that leaves background descendants holding the output pipes is completed on its own exit status after a short drain rather than being forced to time out; the receipt records `background_holders`.

## Regression and manual evidence

```bash
dmd check edit --id A-01 --regression --red-match "EXPECTED_FAILURE" --red-exit 1
dmd approve A-01 --note "Inspected revised regression declaration"
dmd run A-01 --red
# Repair the verified subject, then:
dmd run A-01
```

A valid red run requires the intentional nonzero exit and failure match, not an unrelated crash. `--red-exit` ranges from 1 to 125. The red run is historical baseline evidence; green must match the final candidate. Changing the check definition invalidates its baseline.

When safely reproducing the baseline is genuinely unavailable:

```bash
dmd check baseline --id A-01 --note "Exact limitation and equivalent evidence" \
  --evidence - <<'EVIDENCE'
<the limitation and the equivalent evidence you examined>
EVIDENCE
```

This is an explicit evidence-backed limitation, not a waiver of green verification or required behavior.

For inherently non-command observations:

```bash
dmd check add --req R-01 --work W-01 --method browser \
  --expect "Actual rendered interaction is inspected on the declared viewport" \
  --attested-because "A rendered viewport cannot be asserted from command output"
dmd check set --id A-02 --status PASS --note "Actual observed result and environment" \
  --evidence - <<'EVIDENCE'
<what you observed, the viewport, and the saved screenshot paths>
EVIDENCE
```

Methods are `command`, `manual`, `review`, and `browser`. Manual artifacts must exist, be nonempty, and fit 1 MiB. Binary screenshots should be described in an observation artifact with their actual saved paths/content identity. The runtime does not open browsers or verify screenshot semantics. Command checks cannot be hand-marked PASS. Required skips/NOT_APPLICABLE are rejected.

## Findings, blockers, and unknown effects

```bash
dmd finding add "Concrete anomaly and expected invariant" --location file.py:42 \
  --status suspected --origin pre-existing
dmd finding set --id F-01 --status confirmed --note "Observed reproducer confirms the invariant violation"
dmd finding set --id F-01 --status fixed-verified --work W-02 --check A-02 \
  --note "Root cause and affected callers fixed; regression and integration evidence accepted"
dmd finding set --id F-02 --status disproved --note "Evidence demonstrates correct behavior" \
  --evidence - <<'EVIDENCE'
<the trace or output showing the invariant holds>
EVIDENCE
dmd finding set --id F-03 --status duplicate --duplicate F-01 --note "Same root cause and invariant"
dmd finding defer --id F-04 --authority "Operator: vendor bug, tracked upstream as #123" \
  --note "Fix belongs to the dependency; workaround is not authorized"
```

Origins: `introduced`, `pre-existing`, `dependency`, `unknown`. They do not exempt remediation. `defer` is the only operator-authorized exit for a defect that will not be fixed in this assignment: `--status deferred` is refused, the authority is recorded as an amendment (so `coverage assert` is owed again), and the finding is listed as deferred in the report and the gate `summary.findings_deferred`. Fixed findings require current mapped checks (work is optional) and a regression baseline or documented limitation. A disproof is bound to the files it examined: the `--location` file (`path`, `path:line`) plus any `--input FILE`. An edit elsewhere leaves it resolved; an edit to those files reopens it by name. A location that names no file binds it to the whole source tree. A finding that was ever `confirmed` is disproved only with `--check` naming an accepted executed check that demonstrates the invariant holds; a suspected one needs the note and evidence artifact. Duplicate chains must resolve without cycles or missing IDs. Updating a note does not reset an existing status or origin.

```bash
dmd blocker add "The integration account rejects the required permission" --item W-02 \
  --owner "Account administrator" --unblock "Grant the specifically required role" \
  --proof "Observed permission-denied response, request reference, and affected operation"
dmd blocker clear --id B-01 --proof "Specific prerequisite verified available"
dmd uncertain add "Deployment request returned no observed final result"
dmd uncertain resolve --id U-01 --proof "Queried deployment identity and verified its actual outcome"
dmd uncertain list --json
```

Blocker `--item` names `task` or an existing R/W/A/F ID. For dependency scheduling, prefer the exact blocked work item or requirement. The scheduler is conservative; precise IDs avoid unnecessary whole-task suspension. Open blockers and unknown effects always prevent COMPLETE.

## Gate output

```bash
dmd gate            # JSON: status, reasons, next, source, attestation, summary
dmd next            # same shape; run after every slice
dmd gate --brief    # source map replaced by source_digest and a candidate count
dmd status --json   # task_dir, task, gate
dmd --help          # every subcommand with a one-line description; dmd COMMAND --help for flags
```

`reasons` is one line per unmet obligation and every line names its cause: `A-01: not run`, `A-02: FAIL: exit or match failed; fix and rerun`, `A-03: stale: /path/worktree changed since the receipt (tested @ <head>); rerun`, `A-04: STALE: passed while its candidate or definition moved; rerun`, `A-05: definition edited; inspect, approve and rerun`, `A-06: receipt predates 0.5.0 candidate binding and the task root has since changed; rerun to bind it to /path`, `W-01: checks owed: A-03`, `review: …`.

`summary` groups them: `headline` (one line; it also counts unmapped request items, unasserted coverage, requirements without a check and decisions awaiting the operator), `checks` as `accepted` / `legacy` / `stale` / `failed` / `not_run` / `edited` / `missing_inputs` / `other` ID lists (0.7.0 renamed `unapproved` to `edited`: it holds checks whose definition changed since approval or receipt), `work_unverified`, `findings_open`, `blockers_open`, `review` (`current` or `owed`), `authority_unconfirmed` (decisions awaiting the operator), `authority_unverified` (decisions recorded with no channel), and `rerun`, the `dmd run A-01 A-03 …` that discharges the stale, failed and unrun checks (`null` when none). `legacy` lists checks accepted on a pre-0.5.0 receipt bound to the task root; their next run rebinds them.

## Review, reporting and recovery

```bash
dmd review --kind independent --reviewer "Actual reviewer identity" \
  --note "Original request, assertions, final diff and integrated result reviewed" \
  --evidence - <<'REVIEW'
<what you compared: request, diff, integrated result, findings>
REVIEW
dmd gate
dmd report --save
dmd reconcile
dmd next
dmd handoff
```

Use `self` unless a genuinely separate reviewer performed the review. All other acceptance conditions must be ready before the final review can be recorded. The review binds to the contract, the source map, and the identity of each piece of evidence (candidate, definition, outcome, observation), not to receipt timestamps or log paths: a rerun that reproduces the same pass on a byte-identical candidate keeps the review current; a moved tree, an edited contract or a changed outcome reopens it. Reports distinguish this attestation; the runtime does not authenticate reviewer identity.

`next` lists every item you can act on now, dependency-ready first, each with the complete command that discharges it; the final review appears only when nothing else is owed. `BLOCKED` means every open reason waits on an unresolved blocker, directly, through its requirement, or through a prerequisite. When reasons remain but no item is dependency-ready, `next` holds one `plan` action naming them. `gate` emits JSON and exits 0 only for COMPLETE, 1 for a valid unfinished/suspended assignment, and 2 for input/infrastructure errors. `run` exits 0 when every listed check is an accepted green or intentional red, 1 when any failed or went stale, and 2 for setup/approval/preflight/exclusive-resource errors. `next` emits JSON; `status --json` exposes state plus computed gate; `report --save` and `handoff` write generated views outside the project. `list` labels stored state, which may need current revalidation, and reports an unreadable record as its own row instead of failing the listing. Exit 3 means a defect in dmd itself, not a usage error: the task record was not advanced and the trace should be reported. A lock held by another `dmd` process is waited out with backoff for `DMD_LOCK_WAIT` seconds (default 5) before exit 2 names the wait; `no active task in this worktree` lists unfinished tasks of sibling worktrees when any exist.

```bash
dmd state PAUSED --reason "Operator requested a pause"
dmd state ACTIVE --reason "Operator resumed after checkpoint review"
dmd state CANCELLED --reason "Operator cancelled the assignment"
dmd state ACTIVE --reason "Operator explicitly restarted the cancelled assignment" --authority "Actual instruction"
dmd recover-run --proof "Local process is gone and external outcome was reconciled"
dmd attempt W-02 "Concrete failure signature" --strategy "Different experiment and expected information"   # any R/W/A/F ID
```

`dmd run` treats SIGTERM and SIGHUP like Ctrl-C: the check's process group gets SIGTERM, a grace period, then SIGKILL, and the run is recorded as interrupted. If the runner itself is killed outright, the check's supervisor sees its parent vanish and terminates the group, so a check never keeps executing unsupervised. The runner lock prevents clearing a live local check. `recover-run` also checks the recorded runner PID: it refuses while that process is alive on this host, and otherwise prints the check, start time, PID, host, liveness, the interrupted flag, and the reason the proof was accepted; the record predating PID tracking or belonging to another host is named as not checkable. Recovery proof must additionally reconcile any external effect. Arbitrary source writers and remote effects are not controlled by that lock.

## Session hooks and migration

```bash
dmd bind-session ACTUAL_SESSION_ID
dmd map-host-task --host-id ACTUAL_NATIVE_TASK_ID --work W-01
dmd config --mode enforce --max-no-progress 6
dmd migrate --from-task /absolute/legacy/task.json --authority "Operator approved this import"
```

## Health, housekeeping and relocation

```bash
dmd doctor                      # exit 1 when anything needs attention; names the repair
dmd list --json --state ACTIVE --state PAUSED
dmd gc                          # removes dead bindings, stale confirmation tickets, orphaned run-index entries and empty record directories; lists finished tasks, deletes none
dmd --task T-id relocate --to /new/checkout/root --authority "Operator moved the checkout"
dmd check edit --id A-01 --clear-inputs        # drop every declared --input
dmd check edit --id A-01 --clear-exclusive
```

The report's *Possible leftovers* section lists untracked files in the task root that no check declared as output, and checks whose command left background processes: commit the deliverables, remove the rest.

`doctor` reports the state root, hook mode, dead session bindings, this worktree's task, checks whose declared `--input` or cwd no longer exists, a stranded run, and the current gate headline. A declared input that disappears is a per-check gate reason (`A-01: declared input missing: <path>; …`), never a task-wide failure; `--input` itself must name an existing regular file. `relocate` re-points a task whose checkout was moved or renamed: paths under the old root are rewritten, every command receipt returns to `NOT_RUN`, and the move is recorded as an amendment. With one unfinished task whose root has vanished it resolves without `--task`.

`--max-no-progress` ranges from 1 to 6; it is not a task duration limit. `hook` is a host entrypoint, not an agent dispatch command. See [adoption](adoption.md) and [recovery](recovery.md) for actual supported behavior and limitations.

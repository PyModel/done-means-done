# Command reference

Use `dmd COMMAND --help` for parser details. All examples invoke the bundled `bin/dmd`; it is not a separately installed package. Global options precede the subcommand:

```bash
dmd --cwd /absolute/project --task T-actual-id status --json
```

Paths are resolved on the current machine. Symlinked state and settings *directories* are canonicalized to their physical location, so a dotfiles layout and the macOS `/tmp` and `/var` aliases work; a settings *file* that is itself a symlink is still refused, because replacing it would destroy the link. Task/session identity belongs to a real assignment, not a guessed string.

## Assignment and requirements

| Command | Contract |
|---|---|
| `init --request-file FILE --authority TEXT [--session ID] [--independent-review]` | Preserve the sanitized request and activation; reject replacing an unfinished task by default |
| `init -m TEXT --authority TEXT --new` | Explicitly start another assignment; preserve the previous unfinished task as paused, never overwrite it |
| `req add TEXT --anchor TEXT` | Add one independent outcome/constraint linked to the source request |
| `req cancel --id R-01 --authority TEXT` | Record actual operator removal; invalidate contract coverage |
| `req attest-only --id R-01 --authority TEXT` | Record operator authority to accept this outcome on attestation alone; named in the report |
| `work remove --id W-02 --note TEXT` | Supersede an agent-authored work item; refused while a dependency or finding still maps to it |
| `check remove --id A-02 --note TEXT` | Supersede an agent-authored check; refused if its requirement would be left with none |
| `amend TEXT` | Preserve an operator amendment; reconcile changed requirements afterward |
| `coverage show` | Display the source request and obligation inventory without executing checks |
| `coverage assert --note TEXT` | Attest source-to-inventory review against the current contract digest |

`--authority` records the instruction; it does not authenticate a human. Never fabricate authority or use a new task to bypass the old assignment. Any missing mandatory requirement remains a review failure even when the structural gate is otherwise green.

## Work and acceptance

```bash
dmd work add "Implement caller update" --req R-01 --dep W-01 --owns 'src/caller/**'
dmd work set --id W-02 --status doing
dmd work set --id W-02 --status implemented
dmd work set --id W-02 --status verified
dmd work set --id W-02 --replace "Improved implementation approach" --note "Evidence supporting the revised approach"
```

Every work item requires a requirement. Every check requires an explicit work mapping. Starting/closing dependent work requires current prerequisite evidence. `--dep`, `--owns`, and `--work` are repeatable. `--owns` is planning metadata, not an enforced write lease. Replacing the approach preserves its history and invalidates coverage.

```bash
dmd check add --req R-01 --work W-01 --work W-02 \
  --cmd "python3 -B tests/check_behavior.py" --run-cwd /absolute/project \
  --expect "Intended behavioral assertions execute" --match "BEHAVIOR_PASS:4" \
  --input tests/check_behavior.py --timeout 300 --max-output 1048576
dmd preview A-01
dmd approve A-01 --note "Inspected command and transitive scripts within authorized permissions"
dmd run A-01
```

Only `--method command` produces machine-verified acceptance. `--method manual|review|browser` requires `--attested-because TEXT` saying why no command can observe the behavior; those checks are reported as `SELF-ATTESTED`, counted in `gate` output, and cannot alone accept a requirement without `req attest-only`.

`--match` is a literal string, not a regex. It is necessary but not sufficient semantic proof. The called verifier must assert the correct behavior and intended test discovery before printing it. An arbitrary zero exit is insufficient.

`check edit --id A-01 ...` changes explicitly supplied fields, archives the old definition/evidence, and clears current green, baseline, and pending-import status. Reapprove after any definition/input/runtime approval change; an expired approval names whether the definition, a declared input, or the execution environment drifted. `--no-regression` clears a mis-flagged regression declaration and is recorded in the check history. An edit must leave the entire check valid. `--input` is repeatable and names real files relative to the check working directory or absolute paths; declared missing inputs are errors.

Timeout range: greater than zero through 604800 seconds. Captured-output limit: greater than zero through 1048576 bytes. Timed-out/overflowed/cancelled commands fail; larger logs cannot be truncated into a passing observation. The runner serializes verification per task and terminates the check's remaining local process group after execution. A command that leaves background descendants holding the output pipes is completed on its own exit status after a short drain rather than being forced to time out; the receipt records `background_holders`.

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
  --evidence /absolute/outside-project/baseline-analysis.txt
```

This is an explicit evidence-backed limitation, not a waiver of green verification or required behavior.

For inherently non-command observations:

```bash
dmd check add --req R-01 --work W-01 --method browser \
  --expect "Actual rendered interaction is inspected on the declared viewport" \
  --attested-because "A rendered viewport cannot be asserted from command output"
dmd check set --id A-02 --status PASS --note "Actual observed result and environment" \
  --evidence /absolute/outside-project/browser-observation.txt
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
  --evidence /absolute/outside-project/disproof.txt
dmd finding set --id F-03 --status duplicate --duplicate F-01 --note "Same root cause and invariant"
```

Origins: `introduced`, `pre-existing`, `dependency`, `unknown`. They do not exempt remediation. Fixed findings require current mapped work/checks and a regression baseline or documented limitation. Disproof is source-bound. Duplicate chains must resolve without cycles or missing IDs. Updating a note does not reset an existing status or origin.

```bash
dmd blocker add "The integration account rejects the required permission" --item W-02 \
  --owner "Account administrator" --unblock "Grant the specifically required role" \
  --proof "Observed permission-denied response, request reference, and affected operation"
dmd blocker clear --id B-01 --proof "Specific prerequisite verified available"
dmd uncertain add "Deployment request returned no observed final result"
dmd uncertain resolve --id U-01 --proof "Queried deployment identity and verified its actual outcome"
```

Blocker `--item` names `task` or an existing R/W/A/F ID. For dependency scheduling, prefer the exact blocked work item or requirement. The scheduler is conservative; precise IDs avoid unnecessary whole-task suspension. Open blockers and unknown effects always prevent COMPLETE.

## Review, reporting and recovery

```bash
dmd review --kind independent --reviewer "Actual reviewer identity" \
  --note "Original request, assertions, final diff and integrated result reviewed" \
  --evidence /absolute/outside-project/review.txt
dmd gate
dmd report --save
dmd reconcile
dmd next
dmd handoff
```

Use `self` unless a genuinely separate reviewer performed the review. All other acceptance conditions must be ready before the final review can be recorded. Reports distinguish this attestation; the runtime does not authenticate reviewer identity.

`gate` emits JSON and exits 0 only for COMPLETE, 1 for a valid unfinished/suspended assignment, and 2 for input/infrastructure errors. `run` exits 0 for an accepted green or intentional red, 1 for failed verification, and 2 for setup/approval errors. `next` emits JSON; `status --json` exposes state plus computed gate; `report --save` and `handoff` write generated views outside the project. `list` labels stored state, which may need current revalidation, and reports an unreadable record as its own row instead of failing the listing. Exit 3 means a defect in dmd itself, not a usage error: the task record was not advanced and the trace should be reported.

```bash
dmd state PAUSED --reason "Operator requested a pause"
dmd state ACTIVE --reason "Operator resumed after checkpoint review"
dmd state CANCELLED --reason "Operator cancelled the assignment"
dmd state ACTIVE --reason "Operator explicitly restarted the cancelled assignment" --authority "Actual instruction"
dmd recover-run --proof "Local process is gone and external outcome was reconciled"
dmd attempt W-02 "Concrete failure signature" --strategy "Different experiment and expected information"
```

The runner lock prevents clearing a live local check. Recovery proof must additionally reconcile any external effect. Arbitrary source writers and remote effects are not controlled by that lock.

## Session hooks and migration

```bash
dmd bind-session ACTUAL_SESSION_ID
dmd map-host-task --host-id ACTUAL_NATIVE_TASK_ID --work W-01
dmd config --mode enforce --max-no-progress 6
dmd migrate --from-task /absolute/legacy/task.json --authority "Operator approved this import"
```

`--max-no-progress` ranges from 1 to 6; it is not a task duration limit. `hook` is a host entrypoint, not an agent dispatch command. See [adoption](adoption.md) and [recovery](recovery.md) for actual supported behavior and limitations.

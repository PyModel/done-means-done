# Remediation record

Scope: the supplied repository was inspected at commit `bd9139c43a2afb370e8d78d00e3f9c84f55d8cc2`; the consolidated implementation was executed locally. This record distinguishes source-inspected weaknesses from failures actually reproduced in the new package. It does not claim the original repository's full acceptance suite was executed or that a live agent has been exhaustively evaluated.

## Source-inspected gaps addressed in this implementation

| ID | Observation in the inspected source | Implemented remedy and regression coverage |
|---|---|---|
| F-01 | Original command checks accepted exit zero without evaluating the expectation | Literal acceptance match plus exit, bounded completion and captured receipt; actual zero-exit mismatch test |
| F-02 | Original source fingerprint hashed size/mtime rather than file bytes | Content/mode/path snapshots; actual same-size preserved-mtime mutation, filename-newline and explicit-input tests |
| F-03 | Open blockers and uncertain operations could coexist with COMPLETE | Every unresolved prerequisite/outcome blocks root acceptance; predicate and CLI tests |
| F-04 | Pre-existing/default scope-excluded findings could avoid remediation | Every recorded unresolved suspicion/confirmed defect blocks; current fix/regression, disproof and duplicate proofs |
| F-05 | Finding setter defaults could change confidence/scope while updating another field | Optional fields remain unchanged unless supplied; actual note-update regression |
| F-06 | TaskCompleted shared Stop-style control without its event-specific exit contract | Explicit native task mapping, exit 2/stderr for unverified mapped work; payload/exit tests |
| F-07 | Stop shortcut on continuation and stored COMPLETE could bypass current acceptance | Revalidate current candidate; semantic watchdog, continuation fixture and post-completion source mutation tests |
| F-08 | Off mode still allowed hook logging/context effects | Off returns before task payload effects; actual unchanged-task/no-output test |
| F-09 | Task/session identity and initialization lacked sufficient path/collision isolation | Safe IDs, hashed sessions, private state, immutable task identity and rejected overwrites; traversal/collision/binding tests |
| F-10 | Check execution lacked integrated approval, bounded execution and definition/source-race rejection | Inspected approval, input/runtime binding, independent state/run locks, before/after identity and process-group supervision; real timeout/overflow/cancel tests |
| F-11 | Work/check ownership and required-skip rules allowed incomplete coverage to appear satisfied | Explicit R/W/A mapping, validated DAG, required skips refused, current evidence and final review required |
| F-12 | Installer used fixed paths, broad removal matching and missing-settings assumptions | Portable path-derived commands, quoted arguments, exact manifest-managed removal, first-install preview/apply, backups and preservation tests |

Requirements, findings, review identity and authority remain semantically reviewed inputs. These structural fixes do not authenticate an operator or infer every omitted requirement.

## Defects found and reproduced during this build

| ID | Observed failure | Repair | Evidence |
|---|---|---|---|
| F-13 | An invalid legacy dependency graph replaced the active pointer before validation; the previous assignment was paused | Stage and validate the complete imported graph in memory before task activation | [Failing adoption tests](adoption-before-fix.log), [passing adoption tests](adoption-after-fix.log) |
| F-14 | Installer preview read settings through a symlink parent rather than refusing the alias | Reject settings and parent symlinks before reading, including preview mode | Same adoption transcripts; regression checks original target and zero preview state effects |
| F-15 | Explicit resumption reset a differently named watchdog field; the next Stop immediately paused again | Reset the actual per-session watchdog map on authorized resumption | [Failing resume regression](resume-before-fix.log), [passing resume regression](resume-after-fix.log) |
| F-16 | Invalid overlong session identity could be rejected after task initialization | Validate session length before activation | `test_invalid_session_rejected_before_task_activation` in final suite; this test was added with the repair, not a claimed before/after reproduction |

All entries above are resolved in the packaged implementation with regression coverage. The declared environmental and trust-boundary limitations in [validation](VALIDATION.md) and [security](../references/security.md) are not represented as implemented capabilities.

F-17: Final inspection found that state writes could outgrow the reader's 16 MiB bound, leaving an unreadable next state. Writes now check the same bound before replacement and preserve the prior readable record. `test_oversized_state_never_replaces_readable_record` exercises the failure boundary with a reduced test-only limit; it is not presented as an executed 16 MiB stress benchmark. Temporary test paths are canonicalized to avoid system temporary-directory aliases; macOS execution remains untested.

## 0.4.0 defect review

A review of the 0.3.0 package on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7) found the following. Each was
reproduced by hand before the fix and is covered by a regression in `tests/test_fixes.py`.

| ID | Reproduced failure | Repair |
|---|---|---|
| C-01 | A task reached `COMPLETE` against "Ship feature X, fix all bugs, add tests" with no code written and no command executed, using only `--method manual` checks | Non-command checks require `--attested-because`; a requirement whose entire accepted acceptance set is self-attested is refused unless the operator records `req attest-only` |
| C-02 | `render()` printed self-attested and executed checks identically, so C-01 was invisible in the report | Every check line carries `EXECUTED`/`SELF-ATTESTED` and its method; the report opens with an acceptance-basis count; `gate` returns the same counts |
| C-03 | `DMD_STATE` anywhere under `/tmp` or `$TMPDIR` failed with `unsafe state directory: /var`, because `state_root` used `abspath` while `private_dir` rejects symlinked ancestors | Canonicalize the state root with `realpath` before the ancestor check; `base_dir` containment becomes strictly more correct |
| C-04 | A check leaving a background process returned `exit 0, matched true, failure TIMEOUT` after burning its full timeout, because descendants held the inherited output pipes | Complete on the command's own exit status after a bounded output drain; record `background_holders` in the receipt |
| C-05 | `--regression` was one-way and no work item or check could be corrected once added, so an agent typo became a permanent obligation | `--no-regression` plus `work remove`/`check remove`, which supersede with a mandatory rationale, refuse to orphan dependencies, findings or a requirement's last check, and stay in the report |
| C-06 | One damaged record made `dmd list` fail entirely, hiding every healthy task | Per-record error rows and a summary line on stderr |
| C-07 | The repeated-attempt safeguard compared only the immediately previous attempt and was never read back | Detect equivalent attempts across recent history and surface them in `next` and the report, without ever gating completion |
| C-08 | An expired approval said only "no current inspected approval", though PATH drift alone invalidates it | The approval is split into definition/inputs/environment and the failure names which moved |
| C-09 | Every mutation rewrote the whole event history into both `task.json` and `events.jsonl`, growing quadratically toward a ceiling that permanently wedges the record | Append one line per mutation; keep a bounded 200-event tail in `task.json`. Measured at 402 mutations: 102 KiB record, complete 402-line history |
| C-10 | `ValueError`/`KeyError`/`TypeError` from a defect in dmd were printed as ordinary `dmd: <message>` usage errors | Usage errors stay exit 2; internal defects exit 3 with a traceback. Genuinely user-facing parse failures were wrapped at their source first |
| C-11 | `--request-file` had none of the symlink, size or decoding guards applied to every other operator-supplied file | One `read_operator_file` guard shared by the request file and evidence artifacts |
| C-12 | The bare-success-printer guard matched four bare tokens and missed `/bin/echo` | Compare the basename, and document it in the source as a lint rather than a boundary |
| C-13 | A final review could only ever be recorded once the gate was already green, so a review that found problems left no trace | Rejected reviews are recorded in a review log with their outstanding reasons and rendered in the report |
| C-14 | Stale session bindings accumulated in the state directory forever | Prune bindings whose task directory no longer exists on each bind |

Not changed, deliberately: whole-project content hashing is left uncached (measured 0.38s at 5,621 tracked files) because
caching it would reintroduce the very weakness 0.3.0 closed, and the double fingerprint in `review` and `check set` is a
before/after race check, not redundancy. Attempt history never gates completion. `create_task` still refuses to proceed
past an unreadable prior record, since that record may hold real obligations.

A follow-up audit of the files the original critique had not read (`hooks/install.py`, `tests/test_model.py`, `tests/test_adoption.py`, the reference docs) found the installer carried its own copy of C-03: `--state-dir` under `$TMPDIR` failed with `unsafe state directory: /var`, and a symlinked settings home — the ordinary dotfiles layout — was refused outright. The containing directory is now canonicalized while a symlinked settings *file* is still refused, and uninstall recognizes its own registrations even when the manifest is lost. Two reference statements that the 0.4.0 changes made untrue were corrected. All five registered hook events, `PostToolUseFailure` included, were confirmed valid against the current Claude Code hook documentation.

A final review of these fixes found two more, both repaired and covered: the attested-only gate reason also fired for a requirement with nothing accepted yet (misleading on every in-progress task), and `check set --status PASS` accepted a receipt on a not-yet-reauthored imported check that the gate could never honor.

Upgrading an existing 0.3.0 task record: the contract digest changes, so `dmd gate` will ask for `coverage assert` and a
fresh final review once. No record is rewritten or invalidated, and no historical evidence is discarded.

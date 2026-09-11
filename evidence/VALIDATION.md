# Validation report

Release: **Done Means Done 0.4.1**, prepared September 10, 2026. The source repository was inspected at `bd9139c43a2afb370e8d78d00e3f9c84f55d8cc2`. This is the consolidated local package, not a claim that this revision was pushed to GitHub.

## Executed verification

| Check | Actual result | Evidence |
|---|---|---|
| `python3 -B tests/run.py` | **178 tests passed; 0 failures; 0 skipped.** A nonempty clean run is required for its success marker. | [Final test transcript](tests-final.log) |
| `python3 -B examples/local-demo.py` | Actual subprocess CLI workflow: unapproved execution rejected; intentional red baseline; root-cause repair; green verification; finding closure; final review/gate/report; same-size preserved-mtime source mutation rejected; final repair/reverification; a solely attested requirement refused, then completed under recorded operator authority with the acceptance basis disclosed in the report. | [Local demonstration transcript](local-demo.log) |
| `python3 -B tests/validate_package.py` | Python syntax parsed using the Python 3.10 grammar; required files, release identity, local Markdown links, code fences, skill size, real files/no symlinks and no bytecode-cache artifacts checked. | [Static package transcript](package-validation.log) |
| YAML frontmatter parsed with PyYAML in the build environment | Correct single skill name, standard fields and version. PyYAML is not a runtime dependency. | [Metadata result](frontmatter-validation.log) |
| `python3 -B bin/dmd --version` and subcommand help | Version 0.4.1; every documented command entrypoint accepts its help request. No acceptance command or hook installation executed by help checks. | [CLI surface transcript](cli-help-validation.log) |
| Final archive and SHA-256 manifest | ZIP CRC/member checks and every payload checksum verified; one `done-means-done/` root. No archived files from other skill packages, no symlinks or bytecode caches. | Distribution manifest `SHA256SUMS`; archive validation emitted during packaging |

The unit suite includes **synthetic predicate fixtures** and **actual CLI/filesystem/process/hook-contract/installer/migration tests**. Synthetic records test the acceptance predicate, not independent truth of software behavior. Hook payload fixtures test documented control responses, not a live host session.

All installer tests used isolated temporary settings/state. Pre-fix failure transcripts (`adoption-before-fix.log`, `resume-before-fix.log`) are 0.3.0 artifacts, intentionally retained as regression evidence and explained in [remediations](REMEDIATIONS.md); they are not current failures. The transcripts in the table above were regenerated on macOS for 0.4.0.

## 0.4.0 defect-review verification

Executed on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7), the platform 0.3.0 declared untested:

| Check | Actual result |
|---|---|
| Hand reproductions of C-01 through C-06 (C-07 to C-14 are covered by the suite, not by hand) | Each failed on 0.3.0 and behaves correctly on 0.4.0; transcripts summarized in [remediations](REMEDIATIONS.md) |
| Event growth at 402 mutations | 102 KiB `task.json` with a 200-event tail; complete 402-line `events.jsonl` |
| Whole-project fingerprint cost | 0.38s at 5,621 tracked files; linear and deliberately uncached |

## Build environment

0.3.0 was prepared on Linux x86_64, kernel 6.18.35, glibc 2.41, Python 3.13.5. The 0.4.0 defect review and every regenerated transcript above ran on macOS (Darwin 25.6.0, Apple Silicon, Python 3.14.7). Commands and fixture processes ran locally. The stated Python 3.10 minimum is syntax-checked, not a separate executed Python 3.10 test run. Executed runtimes are now Python 3.13.5 (Linux) and 3.14.7 (macOS). Temporary project and state directories in the demo are removed after it completes; its transcript is retained, not live temporary-path download links.

## Explicitly not verified or implemented

- No live Claude Code session was started; installed host versions, hook reload behavior, tool authorization UX, and session compaction integration were not exercised on the operator's machine.
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

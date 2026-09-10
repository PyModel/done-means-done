# Changelog

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

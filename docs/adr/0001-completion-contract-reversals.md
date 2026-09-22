# ADR 0001: What lets an agent stop, and what a finished task still governs

Status: accepted (0.7.0, 2026-09-22)

## Context

The Stop hook exists so an agent cannot end its turn while authorized work remains. The 0.6.0 audits found that three of its tested behaviors worked against that purpose. Each one either let the agent out early or held unrelated work hostage. A future release is likely to flip them back, because each old behavior looks locally safer.

## Decisions

1. **A stored `COMPLETE` is final for the hooks.** Hooks do not bind, record, or enforce anything for a finished task. `dmd gate` still recomputes honestly, and a contract change through the CLI (`amend`, `req add`, a new check or finding) sets the task back to `ACTIVE`.
   - *Rejected:* re-gating on every Stop. After an edit, a finished assignment blocked every later, unrelated session in that worktree, and the watchdog then rewrote the finished task to `PAUSED`.
2. **The no-progress safeguard releases one stop; it never pauses the task.** The task stays `ACTIVE`, a `released` note names the session and count, and the next SessionStart repeats it. Progress means that the set of satisfied obligations gained a member.
   - *Rejected:* pausing. Seven quick stops ended enforcement for every later session until an operator resumed it, which gave the agent a one-step exit from the whole assignment.
3. **`init --new` moves the worktree's session bindings to the new task.**
   - *Rejected:* leaving them on the superseded task. That task is paused, the hooks do not enforce paused tasks, and the session could then stop freely.

## Invariant

The Stop hook lets a turn end only for `COMPLETE`, `BLOCKED` (every open reason waits on a recorded blocker), a suspended task, or a single no-progress release. It never lets a turn end because another path failed quietly.

## Enforcing tests

`tests/test_completion.py`:
- `BlockedMeansNothingActionable`
- `StopGovernance`
- `RunnerSurvivesItsParent`

`tests/test_runtime.py`:
- `test_watchdog_releases_the_stop_but_never_the_obligations`
- `test_complete_is_final_for_hooks_but_the_gate_stays_honest`
- `test_later_session_leaves_a_complete_task_untouched`
- `test_new_obligation_reopens_a_complete_task`
- `test_init_new_moves_the_session_to_the_new_assignment`

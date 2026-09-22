# ADR 0002: One task store behind the CLI, the hooks and migration

Status: accepted (0.7.0, 2026-09-22)

## Context

Up to 0.7.0 the task store lived inside `cli.py`: locating a task, loading and saving it, session binding and hook configuration. `hooks` and `migration` imported the CLI back to reach them, which made two import cycles. There were four copies of the state scan, three of session pruning and four places that validated the hook mode. The 0.6.0 and 0.7.0 hook bugs sat where those copies diverged.

## Decision

`dmdlib/store.py` owns everything below:

| Concern | Store API |
|---|---|
| Locating tasks | `state_root`, `base_dir`, `locate`, `sibling_tasks` |
| Loading and changing records | `load_task`, `task_records`, `transaction` |
| Creating tasks | `create_task` |
| Session binding | `bind_session`, `rebind_sessions`, `bindings`, `prune_sessions`, `remember_session` |
| Hook configuration | `load_config`, `save_config`, `config_errors` |
| Run liveness | `pid_alive`, `other_runs` |

The report sections moved into `dmdlib/report.py`. The CLI only parses arguments, calls the store and the model, and prints.

- *Rejected:* keeping lazy imports of `cli` from `hooks`. The cycles hid the duplicates, and every hook fix had to be made twice.

## Invariant

No `dmdlib` module imports `dmdlib.cli`. A mutation is saved only through `transaction`, which:
- validates the record;
- reopens a `COMPLETE` task whose contract changed;
- caps the audit lists.

## Enforcing tests

- `tests/test_completion.py::StoreBoundary` checks the import direction.
- The whole suite exercises the store through the CLI and the hooks.

# Adoption, migration and rollback

## Dependencies and first installation

Use Python 3.10+ on macOS or Linux. `fcntl`, process-group signals, `/bin/sh` and POSIX directory synchronization are part of the implementation. There are no pip or Node dependencies. Git improves source identity and is required by the development tests; keep the `python3` command available for fixture checks.

Keep the entire extracted directory together. From its parent, run:

```bash
python3 -B done-means-done/tests/run.py
python3 -B done-means-done/tests/validate_package.py
python3 -B done-means-done/examples/local-demo.py
```

For a first installation only, ensure the target is absent before copying:

```bash
test ! -e "$HOME/.claude/skills/done-means-done" && \
  test ! -L "$HOME/.claude/skills/done-means-done" && \
  mkdir -p "$HOME/.claude/skills" && \
  cp -R done-means-done "$HOME/.claude/skills/done-means-done"
```

Do not nest the new package inside an existing skill directory. Use an explicit backup/upgrade instead. Keep the installation path stable after hook registration because hook commands contain its absolute runtime path.

```bash
python3 "$HOME/.claude/skills/done-means-done/hooks/install.py" --link-bin              # preview: ~/.local/bin/dmd -> bin/dmd, plus hook registrations
python3 "$HOME/.claude/skills/done-means-done/hooks/install.py" --link-bin --apply      # explicit settings change and PATH link
dmd --version
dmd config --mode enforce
```

`--link-bin [DIR]` symlinks `bin/dmd` into `DIR` (default `~/.local/bin`) so `dmd` resolves in every shell and every agent tool call without a per-command shell function. It refuses to replace a file or a foreign symlink at that path. `--link-bin --no-hooks` manages only the link and leaves `settings.json` alone; `--remove` removes the link it recorded along with the hook registrations. If `DIR` is not on `PATH`, the installer says so; `export PATH="$HOME/.local/bin:$PATH"` in the shell profile, or `export PATH="$HOME/.claude/skills/done-means-done/bin:$PATH"` to skip the link.

The last command enables blocking; `observe` is the default. Inspect the resulting `.claude/settings.json`, then start a new host session. Invoke `/done-means-done` with the actual assignment. A current session needs `init --session ACTUAL_ID` or `bind-session ACTUAL_ID`; do not assume a shell variable supplies it.

For alternate paths, use installer `--settings /physical/path/settings.json` and `--state-dir /physical/private/state`. Continue using the same `DMD_STATE` for CLI calls. Hook command paths are machine-local, not portable shared settings.

## Upgrade from an existing installation

Finish or checkpoint active checks and obtain a clean handoff before changing runtime files. Preserve the old installation and private state. Review existing registrations and remove the previous installation's exact managed commands before installing replacements. Registrations from releases without a manifest cannot be safely identified by a broad substring; inspect and remove only the known exact commands with operator consent.

Move the old installation to an operator-chosen backup outside all skill discovery directories, then copy the new complete directory into the original target path. Do not destroy old task records. Validate the new package and inspect hook previews before application. Avoid activating duplicate skill copies from user/project/shared discovery locations.

## Schema-1 import

This release uses schema 2 under a separate `v2/` namespace. It does not silently interpret or overwrite older records. Locate the actual old `task.json`; use its concrete path rather than guessing an ID.

```bash
dmd --cwd /absolute/project migrate \
  --from-task /absolute/legacy/task.json \
  --authority "Operator approved migration of this assignment"
```

If the destination worktree already has a separate unfinished task, the import refuses to supersede it unless the operator explicitly uses `--new`. A valid replacement preserves that old assignment as paused. Invalid legacy graphs are rejected before replacing the active task or pausing its work.

Import retains the original file unchanged and archives a redacted snapshot. Requirements/work/checks/findings are copied as obligations, not assumed achievements. Historical PASS records do not satisfy current checks; every check starts NOT_RUN and needs reauthoring. Confirmed findings reopen even if previously labeled reported, minor, or out of scope. Old paused/cancelled execution states stay suspended.

Review the request, old amendments, imported observations and mappings. Set real commands/cwd/success matches with `check edit`, explicitly map work, inspect scripts, approve and rerun. Recreate regression links and current manual artifacts, supplying `--attested-because` for any non-command method. Reconcile coverage and perform a new final review. Only then request the completion gate.

Migration authority does not imply reactivating a cancelled task. Use the separately recorded operator restart instruction when applicable. Import never claims an old deployment actually occurred.

## Operational checks and observability

After installation, verify `dmd --version`, `dmd config`, the exact registered hook commands, and a disposable local assignment. Inspect actual host logs and source/check evidence, not only a model report. Test mapped TaskCompleted refusal, a Stop continuation, a cancellation, a restart/restore and an unrelated task before relying on global enforcement.

Canonical task events include run start/finish/failure, gate reasons, scope changes, finding transitions, hook observations/blocks, watchdog pause and migration. Reports include every recorded obligation and actual evidence paths. Measure missing-deliverable re-prompts, stale-evidence rejections, false blocks, recovery failures, and task duration/cost over representative assignments. No efficacy benchmark is claimed by the unit tests.

The local build did not install hooks on the operator's machine, test their installed Claude Code version, or validate on Apple Silicon. Those are host acceptance checks, not silently completed actions.

## Rollback

To stop enforcement without deleting state:

```bash
dmd config --mode off
```

To remove this installation's hook registrations:

```bash
python3 "$HOME/.claude/skills/done-means-done/hooks/install.py" --remove
python3 "$HOME/.claude/skills/done-means-done/hooks/install.py" --remove --apply   # hooks and the recorded PATH link
```

Preserve private task records, evidence, saved handoffs and all source changes. To revert runtime files, remove its exact hooks first, restore the preserved old installation, and review the old settings individually. Do not overwrite unrelated settings changes with a whole backup. Schema-2 records are not downgraded in place; retain them and use original schema-1 files with the old runtime when necessary.

Rollback changes enforcement, never the truth about unfinished work. Never reset the project or erase a findings log as part of disabling the skill.

# ADR 0007: Untracked host-agent state is not the candidate

Status: accepted (unreleased, 2026-10-19).

## Context

The fingerprint hashes tracked files and every untracked file Git does not ignore. Host agents write their own state into the project's `.claude/`: a skill-learning hook lands `.claude/skills/<name>/` (SKILL.md, ledgers, evidence) at session end, `settings.local.json` changes, and agent worktrees sit under `.claude/worktrees/`. In pythinker-bot a skill landed 10 minutes after a green `dmd run A-01..A-09`. All nine receipts went stale, the Stop hook blocked, and the next session's reflection wrote again. The session blamed the DMG build's `.build/`, which Git already ignored. At least six active repos carry untracked `.claude/` state.

## Decision

1. Untracked paths under any `.claude/` directory are left out of the snapshot listing (`git ls-files --exclude=.claude/`), the untracked set used for generated outputs and leftovers, the concurrent-writer scan, and the non-Git walk (`source.HOST_STATE`).
2. Tracked files under `.claude/` are still hashed. A declared `--input` is hashed wherever it lives, `.claude/` included.
3. The name is a constant, not a config knob: a knob would be a way to drop source from the candidate.

## Consequences

- A host writing its own state no longer stales evidence or refuses a run as a concurrent writer.
- A check that reads an untracked file under `.claude/` without declaring it as `--input` is not invalidated when that file changes. The same is already true of gitignored files.
- When a repo already has untracked `.claude/` files, its existing receipts go stale once after this change; rerun them. Fingerprints of repos without them are unchanged.

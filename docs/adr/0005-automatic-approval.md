# ADR 0005: Contract decisions are approved automatically

Status: accepted (unreleased, 2026-10-17). Supersedes the confirmation parts of [ADR 0003](0003-operator-decisions.md) and the 0.8.0 operator-confirmed approvals.

## Context

0.8.0 made every `AU-*` decision, including each verifier approval, wait for the operator: a PreToolUse hook returned `permissionDecision: "ask"` (which prompts even when permissions are bypassed) and the CLI also tried a `/dev/tty` prompt. The matcher flagged any command naming the state root, the runtime or a settings file, so calling `bin/dmd` by its full path, or a read-only `sed` on the repo, prompted. In practice the operator was asked on routine `dmd approve` / `dmd run` commands, and declining or removing the hook left decisions unconfirmed, so the gate kept owing them.

## Decision

1. `authority.record` confirms a decision when it is recorded (`confirmed: {"via": "auto"}`). Nothing asks: no PreToolUse hook, no terminal prompt, no pending config change.
2. The decision log stays: what changed, the text recorded, the prior state. The report lists it, and `dmd authority withdraw` reverses a decision.
3. The installer no longer registers PreToolUse; `--apply` removes a registration left by 0.8.0 (it is still recognized as a managed event). `dmd hook pre-tool-use` is a no-op, so a stale registration never prompts or fails.
4. Decisions recorded unconfirmed by 0.8.0 still block the gate. `dmd authority confirm AU-XX` approves one directly.
5. Evidence binding is unchanged: a verifier runs only with an approval for its exact definition, inputs and environment; a changed verifier needs a new approval; receipts stay fingerprinted.

## Consequences

- The agent can approve its own verifiers and change the contract (cancel, defer, pause, lower enforcement) without the operator. The adversarial audit behind 0.8.0 named exactly this. The operator chose not to be asked; the log and the report are the control now.
- Removed code: the host ask/confirm channel (`ask_operator`, `record_approval`, confirmation tickets and their `gc` pruning), the shell-command matcher (`authority.detect`), the terminal prompt and the pending-config mechanism.

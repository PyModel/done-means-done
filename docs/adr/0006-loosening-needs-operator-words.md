# ADR 0006: Loosening the contract needs the operator's own later words

Status: accepted (0.9.0, 2026-10-17). Hardens [ADR 0005](0005-automatic-approval.md).

## Context

ADR 0005 removed every operator prompt. That left the agent free to cancel a requirement, defer a defect, accept attestation, mark request items as context, switch to self review, pause or cancel the task, supersede it, drop regression protection or lower hook enforcement, all on its own typed `--authority`. Pausing and lowering the mode also release the Stop hook. The operator still must not be asked.

## Decision

1. The UserPromptSubmit hook already captures each prompt the operator submits (automatic turns excluded). It now also appends it to a bounded per-worktree history: the last 50 prompts, 64 KiB each, in `prompts/root-<digest>.json`. The per-session latest-prompt file used by `init --from-host` is unchanged.
2. A loosening decision is refused, with nothing recorded, unless its `--authority` is an exact, word-bounded quote of at least 4 non-space characters from a prompt captured in the task's worktree after the task was created (`store.operator_quote`). For `config`, the worktree is the cwd, and "after" means after the task there, if any.
3. The original request and amendments are not sources. The request names every requirement, so quoting it would authorize dropping any of them; amendments are agent-typed, so allowing them would let `amend` launder a quote.
4. Loosening ops: `req cancel`, `req attest-only`, `finding defer`, `coverage context`, `review-policy self`, `state PAUSED|CANCELLED`, `init --new` / `migrate --new` over an unfinished task, `check edit --no-regression`, a lower `config --mode` or `--max-no-progress`. Tightening needs no quote. Ops already gated by executed evidence keep that gate: `blocker --item task`, a confirmed duplicate, `review --kind independent`, `check baseline`.
5. `check.approve` is unchanged: whether a verifier is hollow can't be judged mechanically beyond the existing refusal of a match token printed by the command itself.

## Consequences

- Nothing prompts. When the agent needs to loosen the contract, it asks in the conversation and quotes the answer, so the operator's words sit next to each decision in the report.
- Without the UserPromptSubmit hook no loosening decision is possible. The installer registers it by default.
- A quote can be lifted out of context, and the capture is local state a same-user process can edit. This is a cooperative control, not authentication.

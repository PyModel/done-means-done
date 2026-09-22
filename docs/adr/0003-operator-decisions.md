# ADR 0003: Operator decisions are confirmed by the host's permission prompt

Status: accepted (0.7.0, 2026-09-22)

## Context

A handful of commands change the contract the agent is held to:
- cancelling or attest-onlying a requirement;
- deferring a defect;
- pausing or cancelling the task;
- recording an independent review;
- superseding the task with `init --new`;
- lowering hook enforcement.

Until 0.7.0 each one took an `--authority "..."` flag, which is text the agent types. Five of them were a one-command way to reach `COMPLETE` or to stop being enforced. The runtime runs as the same local user as the agent, so it cannot authenticate a human by itself. The host can: a permission prompt is answered by the person at the keyboard.

## Decision

1. A PreToolUse hook (matcher `Bash`, enforce mode only) matches the commands that change the contract and returns `permissionDecision: "ask"`.
   - The host then asks the operator. This was verified live on Claude Code 2.1.280: the prompt appears even under `--dangerously-skip-permissions`, and in headless mode, where nobody can answer, the command is denied.
   - The reason text names what the command would do. For `authority confirm` it also names the decisions themselves.
   - The hook writes a ticket keyed by `tool_use_id`.
2. The CLI logs every such decision as `AU-XX`, with what it replaced.
3. PostToolUse for the same `tool_use_id` means the operator approved and the command ran. The hook marks the decisions that command recorded as confirmed (`host-prompt`), and only in this session's own tasks: the bound task, the task at its cwd or at a `--cwd` the command named, and tasks that recorded the session. An approval in one project never confirms another agent's decision elsewhere. The live test found that bleed, and a regression test covers it. The operator can also confirm on their own terminal (`terminal`).
4. With a confirmation channel (enforce mode, and PreToolUse registered by the installer), an unconfirmed decision does not count:
   - the gate owes `AU-XX` as an actionable reason that no blocker hides;
   - an unconfirmed pause does not suspend enforcement;
   - an unconfirmed config change leaves the hooks on their previous settings.

   The agent can ask again (`authority confirm`) or undo the decision (`authority withdraw`).
5. Without a channel, decisions apply at once and are reported as never confirmed.

## Alternatives rejected

- **Static `permissions.ask` rules** from the installer. They do prompt under bypass mode, but they match prefixes only. `/path/to/dmd req cancel`, `sh -c '...'` and an argv list in a script all escape them. They also give the CLI nothing to record: whether the prompt was ever shown cannot be known afterwards.
- **Quoting the operator's prompt** (UserPromptSubmit logging, with `--authority` required to be a substring of it). The hook payload does not say who produced a turn, and other sources can start one. It also means storing every prompt the operator types, and it cannot tell "the operator said this" from "the operator agreed to this".
- **Refusing in the CLI until confirmed.** The CLI runs before PostToolUse, so it cannot know the prompt was approved. A ticket written before approval would be consumed even after a denial.

## Invariant

With a channel, the gate can reach `COMPLETE`, and the Stop hook can release the agent, only through decisions the operator approved at the host prompt or on their own terminal. A decision recorded any other way leaves an actionable reason.

## Limits

This is not a security boundary. An agent that forges hook payloads, drives the hook module directly, or edits state files by hand is outside it. The matcher covers the usual invocations, not every possible program, and anything it misses is recorded unconfirmed: a missed prompt fails safe. Shell segments that only mention the words (`echo`, `grep`, `git log --grep`) are not asked about, unless their text is piped into an interpreter. See `references/security.md`.

## Enforcing tests

`tests/test_completion.py::OperatorDecisions` covers:
- the matcher;
- an unconfirmed cancel, including withdrawing it;
- approval through the host prompt;
- no cross-confirmation between commands;
- pause and config downgrade;
- supersede and withdrawing it;
- the no-channel disclosure;
- the decision named in the confirm prompt.

The live-host runs are in `evidence/live-host.log`.

# Verification policy

## Acceptance design

For each required outcome, state the observable invariant, success case, failure cases, integration boundary, and actual artifact under test. Map checks to the work they observe. File existence, a successful build, and a printed marker do not automatically prove behavior.

Use existing project instructions and check configuration. The following are planning categories, not invented commands or blanket requirements for every project:

| Changed surface | Required verification to consider |
|---|---|
| Logic and contracts | Positive/negative assertions, boundary values, malformed inputs, callers and generated contracts |
| Concurrency and state | Race/interleaving cases, duplicate requests, idempotency, cancellation, transactions, retries and recovery |
| Storage or migrations | Old/new schema compatibility, representative data, migration execution in a safe environment, rollback/forward-repair evidence |
| Authentication or access | Rejection paths, tenant/user isolation, privilege boundaries, secret/PII handling |
| UI and native apps | Actual interaction, responsive/platform behavior, accessibility, loading/error/empty states; screenshots or observations when applicable |
| Performance-sensitive code | Reproducible workloads, declared hardware/runtime/data, measured acceptance thresholds |
| Operational changes | Build, health checks, rollout configuration, observable errors/metrics, environment-specific integration, rollback |
| Documentation/research | Exact source coverage, grounded claims, working examples, links and actual deliverable completeness |

Record applicable categories as requirements/work/checks before implementation. Record a reason when a category does not apply. This is not permission to downgrade an already-required failing check to a skip.

## Command evidence

A command check must be inspected and approved before execution. The declaration binds command, cwd, expected behavior, literal success match, work/requirement mapping, time/output limits, regression settings, and declared input files. Approval additionally binds the runtime shell identity, PATH and declared input identity. It is cooperative approval, not an authenticated operator signature.

The runner captures actual exit, combined output, failure mode, timing and source/definition identity. A successful `--match` must be an observation emitted after the intended assertions and test discovery. A suite wrapper should assert a nonzero intended test count and no unexplained skips before emitting its success marker. Parse structured runner results when available; do not use a loose `grep PASS` over mixed failed/passed output.

`--max-output` bounds capture, not the duration of the assignment. Overflow fails. Reduce noisy logging or use a verifier that emits a bounded structured result and records relevant detailed logs outside the source tree. Do not swallow failures or redirect critical assertions away from the verifier. Secrets should never enter captured output; redaction is a fallback with known limits.

A check that times out, exits unexpectedly, matches no declared success observation, changes the candidate while running, or has a changed definition cannot pass. A red run is accepted only for its declared intentional failure exit/message. Unavailable dependencies, syntax errors, import errors and crashes are not useful red evidence unless they are precisely the intended defect under test and the declaration/reproducer demonstrates that fact.

## Freshness and artifact identity

The candidate digest reads file contents and mode, not just timestamps or sizes. With Git, it includes HEAD, tracked files and nonignored untracked paths, using NUL-delimited filenames. Declared additional `--input` files include otherwise ignored or external inputs. Without Git, regular files beneath the root are read conservatively.

Ignored directories and external configuration are not discovered by magic. Declare relevant files. Symlink target strings are hashed; external referent contents require explicit inputs. Toolchain binaries, remote databases/services, mutable container tags, time, randomness and arbitrary environment variables are not all automatically snapshotted. Record their identity in the verifier/review and require checks that verify consequential environmental invariants. Prefer locked dependencies and immutable environment identities.

Whole-project content hashing is deliberately conservative. Any included source change invalidates previous green evidence, including checks that might not actually depend on that change. This trades rerun cost for avoiding optimistic dependency inference. No file-count shortcut silently weakens it. For large repos, maintain legitimate build-output ignore rules and keep evidence outside the tree; do not exclude source merely to avoid stale results.

Before final acceptance, stop concurrent writers, settle external operations, integrate all work, record the candidate, run the checks, then review that exact result. The runner compares before/after fingerprints; it is not a filesystem snapshot or sandbox. A mutation reverted between observations or undeclared external change is outside its structural guarantee. Isolate the final candidate when stronger assurance is required.

Evidence artifacts carry SHA-256 and byte-count integrity references. Editing/deleting a captured artifact invalidates acceptance. A user/agent that can rewrite the runtime and entire private state can also forge matching metadata; these are local audit records, not cryptographic attestation against the same user.

## Regression and final review

For every fixed finding, map its actual remediation and regression check. Retain the intentional baseline proof and final green. A documented baseline limitation requires a real artifact describing the missing capability, supporting evidence, and alternative reasoning; it never waives the final behavioral test.

The final review is captured only after the other gates are satisfied. Review the original request, coverage, test meaning, actual diff, callers, compatibility, integrated result, operational requirements and all findings. Record who reviewed and whether the review was independent. A requirement for independent review cannot be fulfilled by another pass by the same agent.

New source, check definitions, findings, evidence, or obligation changes invalidate affected acceptance/final review. Re-run and re-review, rather than retaining a stale success claim. Report no unresolved observed defects in the reviewed scope; do not claim proof that all possible defects have been found.

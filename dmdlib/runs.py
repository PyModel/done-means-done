"""One verification run: select the checks, refuse a tree someone is still writing, hold
named resources, execute each check in one fingerprint window, and write the receipts.
The CLI only resolves the task directory and hands over the arguments."""
from __future__ import annotations
import contextlib
import json
import os
import signal
import socket
import sys
import time
import uuid
from pathlib import Path
from .storage import DmdError, digest, evidence, ident, lock, lock_wait, now, private_dir, read_json, save
from .source import drift, recent_writes, output_match, untracked
from .model import (SUSPENDED, check_candidate, check_definition, get, live, source_for, task_snapshot)
from .runner import approval_reason, approval_signature, execute
from .store import index_run, load_task, other_runs, state_root, unindex_run

QUIET_WINDOW_SECONDS = 3.0
# Longest a run will wait for a fresh write to settle before refusing outright.
SETTLE_SECONDS = 3.0
# Bounded run history per check; every run's evidence file stays on disk.
MAX_RUNS_PER_CHECK = 50
EXCLUSIVE_WAIT_SECONDS = 600.0
# The finish block writes executed evidence; it outwaits a hook or CLI command on the record.
FINISH_LOCK_WAIT_SECONDS = 60.0


def preflight(t, checks, window):
    """Refuse a run that would test a tree someone is still writing. Cheaper than burning
    an integration run and rejecting its receipt afterwards."""
    candidates = sorted({check_candidate(c, t["root"]) for c in checks})
    problems = []
    for cand in candidates:
        writes = recent_writes(cand, window)
        if writes:
            # The normal agent turn is edit-then-verify. Wait (bounded by SETTLE_SECONDS)
            # for the newest write to age past the window, then re-check; refuse only a
            # tree still being written.
            wait = window - writes[0]["age_s"]
            if 0 < wait <= SETTLE_SECONDS:
                time.sleep(wait + 0.05)
                writes = recent_writes(cand, window)
        if writes:
            named = ", ".join(f"{w['path']} ({w['age_s']}s ago)" for w in writes[:5])
            more = f" and {len(writes) - 5} more" if len(writes) > 5 else ""
            problems.append(f"concurrent writer: {cand} has dirty files modified within {window:g}s: {named}{more}")
    for r in other_runs(t["task_id"], candidates):
        state = {True: "is alive", False: "is not alive; recover it there with dmd recover-run", None: "is on another host"}[r["alive"]]
        problems.append(f"another dmd run ({r['task_id']} check {r['check']}, pid {r['pid']}) holds {', '.join(r['candidates'])}; that runner {state}")
    if problems:
        raise DmdError("; ".join(problems) + f". Wait for the writer to finish, or rerun with --quiet-window 0 to skip the dirty-file check")
    return candidates


@contextlib.contextmanager
def exclusive(tags, wait):
    """Serialize checks that share a named resource (a database, a port) across every task,
    worktree and session on this machine. Tags are taken in sorted order so two checks
    sharing several never deadlock."""
    directory = private_dir(state_root() / "locks")
    with contextlib.ExitStack() as stack:
        for tag in sorted(set(tags)):
            try:
                stack.enter_context(lock(directory, ident(tag) + ".lock", wait=wait))
            except DmdError as exc:
                raise DmdError(f"exclusive resource '{tag}' is held by another check: {exc}") from exc
        yield


def select_checks(t, ids, everything):
    if everything and ids:
        raise DmdError("give check IDs or --all, not both")
    if everything:
        checks = [c for c in live(t["checks"]) if c["method"] == "command" and not c.get("needs_review")]
        if not checks:
            raise DmdError("no runnable command checks")
        return checks
    if not ids:
        raise DmdError("name at least one check ID, or use --all")
    if len(ids) != len(set(ids)):
        raise DmdError("duplicate check IDs in one run")
    return [get(t["checks"], cid) for cid in ids]


@contextlib.contextmanager
def interruptible():
    """SIGTERM and SIGHUP (a host tool timeout, a closed terminal) take the same path as
    Ctrl-C: the check's process group is terminated and the run is recorded as interrupted,
    instead of the runner dying and leaving the check executing unsupervised."""
    def stop(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def generated_outputs(planned, checks, before, after):
    """Creation-only exceptions to in-run drift, never exclusions from a receipt. A
    previously generated file can be rewritten only while its recorded content is intact.
    Pre-existing untracked source and tracked paths cannot become generated outputs."""
    found = {}
    for c in checks:
        cand = check_candidate(c, planned["root"])
        b, a = before[cand]["files"], after[cand]["files"]
        loose = untracked(cand)
        eligible = set(a) if loose is None else loose
        prior = (c.get("receipt") or {}).get("outputs") or {}
        allowed = found.setdefault(cand, {})
        for path, value in a.items():
            file = Path(cand) / path
            if (path not in eligible or not output_match(path, c.get("writes") or []) or value == "volatile"
                    or not file.is_file() or file.is_symlink()):
                continue
            if path not in b or (prior.get(path) == b[path] and (c.get("receipt") or {}).get("assurance_version") == 2):
                allowed[path] = value
    return found


def run_checks(directory, ids=(), everything=False, red=False, quiet_window=None, wait_exclusive=None):
    """Run the named checks (or every runnable one) for the task at `directory`. Returns the
    process exit code: 0 when every check passed, 1 otherwise; refusals raise DmdError."""
    with interruptible():
        return _run(directory, list(ids), everything, red, quiet_window, wait_exclusive)


def _run(directory, ids, everything, red, quiet_window, wait_exclusive):
    # The task lock is held only to read or change the record. Observing the project tree
    # (preflight, both snapshots) runs outside it: on a large checkout a snapshot takes
    # seconds, and a hook or CLI command waiting on the lock must not fail meanwhile.
    with lock(directory, ".run.lock"):
        with lock(directory):
            t = load_task(directory)
            if t["state"] in SUSPENDED:
                raise DmdError("execution is suspended; operator-authorized activation required")
            if t.get("running"):
                raise DmdError("previous run has an unknown outcome; use recover-run after inspecting it")
            checks = select_checks(t, ids, everything)
            plan = []
            for c in checks:
                if c["method"] != "command" or c.get("needs_review") or c.get("removed"):
                    raise DmdError(f"{c['id']}: only fully authored, live command checks can run")
                signature = approval_signature(c)
                reason = approval_reason(t, c)
                if reason:
                    raise DmdError(f"{c['id']} {reason}")
                if red and (not c.get("regression") or not c.get("red_match")):
                    raise DmdError(f"{c['id']}: --red requires a regression check with an intentional failure match")
                plan.append((c, signature, digest(check_definition(c))))
        planned = t
        window = QUIET_WINDOW_SECONDS if quiet_window is None else quiet_window
        candidates = preflight(planned, checks, window)
        # One fingerprint window for the whole list: snapshot every candidate once here,
        # once after the last check. Receipts bind to the start state; drift is diffed.
        before = task_snapshot(planned)
        fps = {path: snap["fingerprint"] for path, snap in before.items()}
        token = uuid.uuid4().hex
        with lock(directory):
            t = load_task(directory)
            if t["state"] in SUSPENDED:
                raise DmdError("execution is suspended; operator-authorized activation required")
            for c, signature, definition in plan:
                current = get(t["checks"], c["id"])
                if approval_reason(t, current) or approval_signature(current) != signature:
                    raise DmdError("approval changed during preflight; inspect and retry")
            t["running"] = {"token": token, "checks": [c["id"] for c in checks], "check": checks[0]["id"],
                            "started": now(), "source": fps, "candidates": candidates,
                            "pid": os.getpid(), "host": socket.gethostname(), "red": bool(red)}
            save(directory, t, "run.start", checks=[c["id"] for c in checks], red=red)
            index_run(directory, t["running"])
        poll = {"at": 0.0, "value": False}
        def cancelled():
            # Re-read at most once per second; a transient read failure is not a cancel.
            if time.monotonic() - poll["at"] < 1.0:
                return poll["value"]
            poll["at"] = time.monotonic()
            try:
                current = read_json(directory / "task.json")
            except (DmdError, OSError):
                return poll["value"]
            poll["value"] = current.get("state") in SUSPENDED or (current.get("running") or {}).get("token") != token
            return poll["value"]
        results = []
        refused = None
        try:
            for index, (c, _, _) in enumerate(plan):
                if index:
                    with lock(directory):
                        t = load_task(directory)
                        if (t.get("running") or {}).get("token") == token:
                            t["running"]["check"] = c["id"]
                            save(directory, t, "run.next", check=c["id"])
                wait = EXCLUSIVE_WAIT_SECONDS if wait_exclusive is None else wait_exclusive
                try:
                    holder = exclusive(c.get("exclusive") or [], wait)
                    holder.__enter__()
                except DmdError as exc:
                    # Nothing ran for this check: a held resource is a clean refusal, not an
                    # interrupted run. Earlier results in the list are kept.
                    refused = f"{c['id']}: {exc}"
                    break
                # Progress goes to stderr so stdout stays one JSON row per result; a
                # watcher can tell a 20-minute integration run from a hang.
                print(json.dumps({"check": c["id"], "event": "start", "candidate": check_candidate(c, planned["root"]),
                                  "timeout": c["timeout"], "at": now()}), file=sys.stderr, flush=True)
                try:
                    with lock(directory):
                        current_task = load_task(directory)
                        current = get(current_task["checks"], c["id"])
                        if approval_reason(current_task, current) or approval_signature(current) != plan[index][1]:
                            raise DmdError("approval changed while waiting for execution; inspect and retry")
                    results.append(execute(c, cancelled))
                finally:
                    holder.__exit__(None, None, None)
                print(json.dumps({"check": c["id"], "event": "finish", "exit": results[-1]["exit"],
                                  "duration_s": results[-1]["duration_s"], "failure": results[-1]["failure"]}), file=sys.stderr, flush=True)
                if results[-1]["failure"] == "CANCELLED":
                    break
        except BaseException:
            # The supervisor has cleaned up its local process group. Preserve interrupted
            # evidence as unknown, never silently retry external effects. A lock that cannot
            # be taken right now must not replace the interrupt: the record still says
            # `running`, which is what recover-run reads.
            with contextlib.suppress(DmdError):
                with lock(directory):
                    t = load_task(directory)
                    if (t.get("running") or {}).get("token") == token:
                        t["running"]["interrupted"] = True
                        save(directory, t, "run.interrupted", checks=[c["id"] for c, _, _ in plan], completed=len(results))
            raise
        # The trees the planned checks tested, as they are now. A candidate a check removed
        # (its own scratch worktree) is drift, not a reason to lose every result.
        after = task_snapshot(planned, missing_ok=True)
        outputs = generated_outputs(planned, [c for c, _, _ in plan[:len(results)]], before, after)
        moved = {}
        for path in before:
            # Only exact output paths created (or unchanged since a prior capture) in this
            # run may differ. The final receipt hashes them all, including their contents.
            allowed = outputs.get(path, {})
            b = dict(before[path], files={k: v for k, v in before[path]["files"].items() if k not in allowed})
            a = dict(after[path], files={k: v for k, v in after[path]["files"].items() if k not in allowed})
            delta = drift(b, a)
            if delta["changed_total"] or delta["head_before"] != delta["head_after"] or after[path]["fingerprint"].startswith("missing:"):
                moved[path] = delta
        final_fps = {path: snap["fingerprint"] for path, snap in after.items()}
        # Executed evidence is at stake here: wait longer than a routine mutation would.
        with lock(directory, wait=max(lock_wait(), FINISH_LOCK_WAIT_SECONDS)):
            t = load_task(directory)
            suspended = (t.get("running") or {}).get("token") != token or t["state"] in SUSPENDED
            summary = []
            all_ok = True
            for (c, signature, definition), result in zip(plan, results):
                current = get(t["checks"], c["id"])
                cand = check_candidate(c, t["root"])
                candidate_drift = moved.get(cand) or next((moved[p] for p in moved if Path(p) in Path(cand).parents), None)
                try:
                    approval_moved = approval_signature(current) != signature or bool(approval_reason(t, current))
                except DmdError:
                    approval_moved = True  # a declared input vanished: the approved definition no longer exists as inspected
                definition_changed = digest(check_definition(current)) != definition or approval_moved
                changed = suspended or definition_changed or candidate_drift is not None
                failure = result["failure"] or ("CANDIDATE_OR_DEFINITION_CHANGED" if changed else None)
                match = c["red_match"] if red else c["match"]
                matched = bool(match and match in result["output"])
                expected_exit = c["red_exit"] if red else 0
                ran_ok = result["exit"] == expected_exit and matched and result["failure"] is None
                success = ran_ok and failure is None
                # A command that passed against a tree that then moved is STALE, not FAILED:
                # the receipt is rejected, and the reader learns what moved without opening
                # the evidence file.
                stale = ran_ok and changed
                reason = None
                if stale:
                    reason = {"suspended": suspended, "definition_changed": definition_changed, "candidate": cand, "drift": candidate_drift}
                metadata = {k: v for k, v in result.items() if k != "output"}
                art = evidence(directory, json.dumps({"check": c["id"], "definition": check_definition(c),
                                                     "candidate": cand, "head": before[cand]["head"] if cand in before else None,
                                                     "source": fps.get(cand), "started": (t.get("running") or {}).get("started"),
                                                     "metadata": metadata, "stale": reason}, indent=2) + "\n\n" + result["output"], "command")
                receipt = {"kind": "command", "source": source_for(fps if changed else final_fps, c), "candidate": cand,
                           "assurance_version": 2, "approval": signature,
                           "outputs": {p: d for p, d in outputs.get(cand, {}).items() if output_match(p, c.get("writes") or [])},
                           "head": before[cand]["head"] if cand in before else None,
                           "definition": definition, "artifact": art,
                           "exit": result["exit"], "matched": matched, "failure": failure,
                           "background_holders": result["background_holders"],
                           "duration_s": result["duration_s"], "at": now()}
                if stale:
                    receipt["stale"] = reason
                if red:
                    current["red"] = receipt if success else None
                    label = "RED-OK" if success else ("RED-STALE" if stale else "RED-INVALID")
                else:
                    current["status"] = "PASS" if success else "FAIL"
                    current["receipt"] = receipt
                    label = "PASS" if success else ("STALE" if stale else "FAIL")
                runs = current.setdefault("runs", [])
                runs.append({"at": now(), "red": red, "artifact": art, "result": label})
                if len(runs) > MAX_RUNS_PER_CHECK:
                    del runs[:-MAX_RUNS_PER_CHECK]
                if not success:
                    history = t.setdefault("attempts", {}).setdefault(c["id"], [])
                    failure_identity = {"exit": result["exit"], "failure": failure, "matched": matched,
                                        "output": digest(result["output"])}
                    history.append({"at": now(), "signature": digest(failure_identity), "origin": "runner",
                                    "failure": failure or ("expected red baseline not observed" if red else "exit or success match failed"),
                                    "strategy": c["command"], "artifact": art})
                    del history[:-MAX_RUNS_PER_CHECK]
                all_ok = all_ok and success
                row = {"check": c["id"], "result": label, "exit": result["exit"], "matched": matched,
                       "failure": failure, "duration_s": result["duration_s"], "candidate": cand,
                       "head": receipt["head"], "evidence": str(directory / art["path"])}
                if stale:
                    row["stale"] = reason
                summary.append(row)
            skipped = [c["id"] for c, _, _ in plan[len(results):]]
            if (t.get("running") or {}).get("token") == token:
                # Only this run's own record is cleared; a recovered-and-restarted one is not ours.
                t["running"] = None
            unindex_run(token)
            save(directory, t, "run.finish", results=[(r["check"], r["result"]) for r in summary], skipped=skipped)
        for row in summary:
            print(json.dumps(row))
        if len(plan) > 1 or skipped:
            print(json.dumps({"batch": [r["check"] for r in summary], "skipped": skipped, "refused": refused,
                              "results": {r["check"]: r["result"] for r in summary},
                              "window": {"started": fps, "moved": moved}}))
        if refused:
            raise DmdError(refused + (f"; {len(summary)} earlier check(s) recorded" if summary else ""))
        return 0 if all_ok and not skipped else 1

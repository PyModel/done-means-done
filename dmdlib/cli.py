"""Done Means Done command surface. No third-party Python dependencies."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import signal
import socket
import sys
import time
import uuid
from pathlib import Path
from . import __version__
from .storage import DmdError, atomic, digest, evidence, ident, lock, now, private_dir, read_json, redact, save
from .source import candidate_root, drift, recent_writes
from .model import (FINDING_STATES, OPERATOR_FINDING_STATES, WORK_STATES, acceptance_reason, attested, check_candidate, check_definition, missing_inputs,
                    contract_digest, gate, get, live, new_id, repeated_attempts, review_signature, source_digest,
                    source_for, task_fingerprint, task_snapshot, work_ok)
from .runner import approval_current, approval_drift, approval_parts, approval_signature, execute, SHELL
from .store import (StateInsideProject, base_dir, bind_session, bindings, config_errors, create_task, load_config, load_task, locate, other_runs,
                    pid_alive, prune_sessions, remember_session, require_text, save_config, sibling_tasks, state_root, task_records, transaction)
from .report import SECTIONS, render

QUIET_WINDOW_SECONDS = 3.0
# Longest a run will wait for a fresh write to settle before refusing outright.
SETTLE_SECONDS = 3.0
# Bounded run history per check; every run's evidence file stays on disk.
MAX_RUNS_PER_CHECK = 50
EXCLUSIVE_WAIT_SECONDS = 600.0








def need(args):
    directory = locate(args.cwd, args.task)
    if directory is None:
        hint = "; ".join(f"{tid} is {state} in worktree {root} (run dmd --cwd {root} ... there, or init here)"
                         for tid, state, root in sibling_tasks(args.cwd))
        raise DmdError("no active task in this worktree; invoke init with the authorized assignment"
                       + (". Related: " + hint if hint else ""))
    return directory

@contextlib.contextmanager
def edit(args, kind):
    directory = need(args)
    # A handler's confirmation is printed only once the mutation is saved, never for a
    # change that validation then rejects.
    out = io.StringIO()
    with transaction(directory, kind, allow_cancelled=kind == "state") as t:
        with contextlib.redirect_stdout(out):
            yield directory, t
    sys.stdout.write(out.getvalue())











def artifact_from_file(directory, path):
    if path is None:
        raise DmdError("--evidence is required: record the actual observation artifact")
    return evidence(directory, read_operator_file(path, "--evidence"), "review")




def read_operator_file(path, name):
    """Operator-supplied files get one set of guards, wherever they enter the runtime."""
    p = Path(path).expanduser()
    if p.is_symlink() or not p.is_file():
        raise DmdError(f"{name} must be an existing regular file, not a symlink: {p}")
    if not 0 < p.stat().st_size <= 1048576:
        raise DmdError(f"{name} must be non-empty and at most 1 MiB: {p}")
    return p.read_text(errors="replace")


def init(args):
    message = read_operator_file(args.request_file, "--request-file") if args.request_file else args.message
    directory, t = create_task(args.cwd, args.task, require_text(message, "request (-m TEXT or --request-file PATH)"),
                               require_text(args.authority, "--authority"), session=args.session, new=args.new,
                               require_review=args.independent_review)
    print(t["task_id"])


def req(args):
    if args.action == "list":
        return listing(args, "requirements")
    with edit(args, "req." + args.action) as (_, t):
        if args.action == "add":
            rid = new_id(t["requirements"], "R")
            t["requirements"].append({"id": rid, "text": require_text(args.text, "requirement"),
                                      "anchor": require_text(args.anchor, "--anchor to the original request"), "status": "active"})
            print(rid)
        elif args.action == "attest-only":
            r = get(t["requirements"], args.id)
            r["attest_only"] = True
            r["attest_only_authority"] = require_text(
                args.authority, "explicit operator instruction accepting attested-only acceptance")
            t["amendments"].append({"at": now(), "text": "attested-only acceptance authorized: " + r["attest_only_authority"],
                                    "requirement": r["id"]})
            print(r["id"] + " may be accepted on attestation alone under recorded operator authority")
        else:
            r = get(t["requirements"], args.id)
            mine = {w["id"] for w in t["work"] if w["req"] == r["id"]}
            stranded = [w["id"] for w in live(t["work"]) if w["req"] != r["id"] and set(w["deps"]) & mine
                        and get(t["requirements"], w["req"])["status"] == "active"]
            if stranded:
                raise DmdError(f"{', '.join(stranded)} depend on work under {r['id']}; replan them first "
                               f"(dmd work set --id W-XX --clear-deps, then --dep as needed)")
            r["status"] = "removed"
            r["authority"] = require_text(args.authority, "explicit operator removal instruction")
            t["amendments"].append({"at": now(), "text": r["authority"], "requirement": r["id"]})


def declared_input(cwd, item):
    """A declared input is an existing regular file. A directory, an empty string or a
    missing path would poison every fingerprint of the task until repaired."""
    if not str(item).strip():
        raise DmdError("--input must name a file; an empty value is not an input (use --clear-inputs to remove all)")
    path = (Path(cwd) / Path(item).expanduser()).resolve()
    if not path.exists():
        raise DmdError(f"--input does not exist: {path}")
    if not path.is_file():
        raise DmdError(f"--input must be a regular file, not a directory or special file: {path}")
    return str(path)


def trivial_command(command):
    """A lint against the laziest vacuous check, not a security boundary: an agent that
    wants a meaningless check can always write one. The gate's defence is the red baseline
    and the operator's inspection at approval time, not this list."""
    if not command:
        return False
    first = command.strip().split()[0] if command.strip() else ""
    return Path(first).name in ("echo", "printf", "true", ":") or first == ":"


def supersede(t, record, kind, note):
    """Retire an agent-authored record. Requirements are operator-owned and never take this path."""
    reason = require_text(note, f"--note explaining why this {kind} is superseded")
    if record.get("removed"):
        raise DmdError(f"{record['id']} is already superseded")
    dependents = [w["id"] for w in live(t["work"]) if record["id"] in w.get("deps", [])] if kind == "work" else []
    if dependents:
        raise DmdError(f"{record['id']} is a prerequisite of {', '.join(dependents)}; replan those first")
    holders = [f["id"] for f in t["findings"]
               if record["id"] in f.get("work" if kind == "work" else "checks", [])
               and f["status"] in ("suspected", "confirmed", "fixed-unverified", "fixed-verified")]
    if holders:
        raise DmdError(f"{record['id']} carries remediation for {', '.join(holders)}; resolve those findings first")
    record["removed"] = True
    record["removed_reason"] = reason
    record["removed_at"] = now()


def listing(args, group):
    """Read-only view of one record group. `dmd status` is the whole ledger; close-out
    usually needs one group."""
    t = load_task(need(args))
    rows = t[group]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    for row in rows:
        if group == "requirements":
            print(f"{row['id']} [{row['status']}] {row['text']}")
        elif group == "work":
            state = "superseded" if row.get("removed") else row["status"]
            deps = ", ".join(row.get("deps") or []) or "none"
            print(f"{row['id']} [{state}] {row['text']} -> {row['req']}; deps: {deps}")
        elif group == "checks":
            state = "superseded" if row.get("removed") else row["status"]
            print(f"{row['id']} [{state}] ({row['method']}) {row['expect']} -> {row['req']} / {', '.join(row['work'])}")
        elif group == "findings":
            print(f"{row['id']} [{row['status']}; {row['origin']}] {row['location']}: {row['text']}")
        elif group == "blockers":
            print(f"{row['id']} [{'resolved' if row['resolved'] else 'OPEN'}] {row['item']}: {row['text']}")
        elif group == "uncertain":
            print(f"{row['id']} [{'resolved' if row['resolved'] else 'OPEN'}] {row['text']}")


def work(args):
    if args.action == "list":
        return listing(args, "work")
    with edit(args, "work." + args.action) as (directory, t):
        if args.action == "remove":
            w = get(t["work"], args.id)
            supersede(t, w, "work", args.note)
            print(w["id"] + " superseded")
            return
        if args.action == "add":
            if not args.req:
                raise DmdError("--req is required and must name an existing requirement (R-XX); see dmd req list")
            get(t["requirements"], args.req)
            for dep in args.dep or []:
                get(t["work"], dep)
            wid = new_id(t["work"], "W")
            t["work"].append({"id": wid, "req": args.req, "text": require_text(args.text, "work item"),
                              "status": "todo", "deps": args.dep or [], "owns": args.owns or [], "note": ""})
            print(wid)
        else:
            w = get(t["work"], args.id)
            if args.replace:
                require_text(args.note, "replanning rationale --note")
                w.setdefault("history", []).append({"at": now(), "text": w["text"], "reason": args.note})
                w["text"] = args.replace
                w["status"] = "todo"
            if args.clear_deps:
                w["deps"] = []
            if args.dep is not None:
                w["deps"] = args.dep
            if args.owns is not None:
                w["owns"] = args.owns
            if args.note:
                w["note"] = args.note
            if args.status:
                if w.get("removed"):
                    raise DmdError(f"{w['id']} is superseded; add a replacement work item instead")
                fp = task_fingerprint(t)
                if args.status in ("doing", "verified") and any(not work_ok(directory, t, get(t["work"], d), fp) for d in w["deps"]):
                    raise DmdError("dependencies must have current verification before work starts/closes")
                if args.status == "verified":
                    cs = [c for c in live(t["checks"]) if w["id"] in c["work"]]
                    owed = {c["id"]: acceptance_reason(directory, c, fp, t["root"]) for c in cs}
                    owed = {k: v for k, v in owed.items() if v}
                    if not cs or owed:
                        detail = "; ".join(f"{k}: {v}" for k, v in owed.items()) or "no mapped check"
                        raise DmdError("work cannot be verified without current mapped acceptance evidence: " + detail)
                w["status"] = args.status
            print(w["id"] + " " + w["status"])


def check(args):
    if args.action == "list":
        return listing(args, "checks")
    with edit(args, "check." + args.action) as (directory, t):
        if args.regression and args.no_regression:
            raise DmdError("choose either --regression or --no-regression")
        if args.action == "remove":
            c = get(t["checks"], args.id)
            remaining = [x for x in live(t["checks"]) if x["req"] == c["req"] and x["id"] != c["id"]]
            if not remaining:
                raise DmdError(f"{c['req']} would have no acceptance check left; add its replacement first")
            supersede(t, c, "check", args.note)
            print(c["id"] + " superseded")
            return
        if args.action in ("add", "edit"):
            if args.action == "add":
                cid = new_id(t["checks"], "A")
                c = {"id": cid, "req": args.req, "work": args.work or [], "method": args.method or "command",
                     "command": args.cmd, "cwd": str(Path(args.run_cwd or t["root"]).resolve()),
                     "expect": args.expect, "match": args.match, "timeout": 300 if args.timeout is None else args.timeout,
                     "max_output": 1048576 if args.max_output is None else args.max_output, "inputs": args.input or [],
                     "regression": bool(args.regression), "red_match": args.red_match, "red_exit": 1 if args.red_exit is None else args.red_exit,
                     "attested_because": args.attested_because, "candidate": args.candidate,
                     "exclusive": args.exclusive or [],
                     "status": "NOT_RUN", "receipt": None, "red": None, "baseline": None}
                t["checks"].append(c)
            else:
                c = get(t["checks"], args.id)
                c.setdefault("history", []).append({"definition": check_definition(c), "receipt": c.get("receipt"), "at": now()})
                for flag, key in [("req", "req"), ("work", "work"), ("method", "method"), ("cmd", "command"),
                                  ("run_cwd", "cwd"), ("expect", "expect"), ("match", "match"), ("timeout", "timeout"),
                                  ("max_output", "max_output"), ("input", "inputs"), ("red_match", "red_match"),
                                  ("red_exit", "red_exit"), ("attested_because", "attested_because"),
                                  ("candidate", "candidate"), ("exclusive", "exclusive")]:
                    value = getattr(args, flag)
                    if value is not None:
                        c[key] = value
                if args.regression:
                    c["regression"] = True
                if args.no_regression:
                    # Correcting the flag is recorded in history above, never silently cleared.
                    c["regression"] = False
                    c["red_match"] = None
                    c["red"] = None
                c["status"] = "NOT_RUN"
                c["receipt"] = None
                c["red"] = None
                c["baseline"] = None
                c.pop("needs_review", None)
            c["cwd"] = str(Path(c["cwd"]).expanduser().resolve(strict=True))
            if args.clear_inputs:
                c["inputs"] = []
            if args.clear_exclusive:
                c["exclusive"] = []
            c["inputs"] = [declared_input(c["cwd"], x) for x in c["inputs"]]
            # The candidate is the tree this check's evidence is bound to. Default: the task
            # root when the check runs inside it, else the checkout its cwd belongs to.
            explicit = c.get("candidate") if args.action == "edit" and args.candidate is None else args.candidate
            if explicit:
                cand = Path(explicit).expanduser().resolve(strict=True)
                if not cand.is_dir():
                    raise DmdError("--candidate must be a directory")
                c["candidate"] = str(cand)
            else:
                c["candidate"] = candidate_root(c["cwd"], t["root"])
            for tag in c.get("exclusive") or []:
                ident(tag)
            if attested(c) and not str(c.get("attested_because") or "").strip():
                raise DmdError(f"--attested-because is required for a {c['method']} check: state why no "
                               "command can observe this behavior. Attested checks are reported as self-attested "
                               "and cannot alone accept a requirement.")
            if c["method"] == "command":
                if str(c.get("attested_because") or "").strip():
                    raise DmdError("--attested-because applies only to manual, review or browser checks")
                if trivial_command(c["command"]):
                    raise DmdError("a bare success printer is not an acceptance check")
                if c.get("match") and c["match"] in (c.get("command") or ""):
                    # `tests && echo TOKEN` or `python3 -c 'print("TOKEN")'` reduces to an exit
                    # status, which the literal match exists to go beyond.
                    raise DmdError(f"--match {c['match']!r} appears in the command text, so the command prints it without "
                                   "observing anything. Match a string the verifier's own output emits after its assertions "
                                   "(a test runner's summary line, a script's final print)")
            if args.approve is not None:
                # One call for author-and-approve. The inspection it attests is the same as
                # dmd approve; an edit still clears it, so edited text never runs uninspected.
                if c["method"] != "command":
                    raise DmdError("--approve applies only to command checks")
                t["approvals"][c["id"]] = {"signature": digest(approval_parts(c)), "parts": approval_parts(c),
                                           "note": require_text(args.approve, "--approve note naming what you inspected"), "at": now()}
            print(c["id"] + (" (approved)" if args.approve is not None else ""))
        elif args.action == "set":
            c = get(t["checks"], args.id)
            if c.get("needs_review") and args.status == "PASS":
                raise DmdError("this imported check is not fully authored; reauthor it with check edit before recording a result")
            if c["method"] == "command" and args.status == "PASS":
                raise DmdError("command checks can pass only through dmd run")
            if args.status not in ("PASS", "FAIL", "NOT_RUN"):
                raise DmdError("required skips and NOT_APPLICABLE cannot satisfy a check")
            require_text(args.note, "--note describing the observation")
            c["status"] = args.status
            if args.status == "PASS":
                art = artifact_from_file(directory, args.evidence)
                fp = task_fingerprint(t)
                c["receipt"] = {"kind": c["method"], "source": source_for(fp, c), "candidate": check_candidate(c, t["root"]),
                                "definition": digest(check_definition(c)),
                                "artifact": art, "note": args.note, "at": now()}
            else:
                c["receipt"] = None
        else:
            c = get(t["checks"], args.id)
            if not c.get("regression"):
                raise DmdError("baseline limitation applies only to a regression check")
            c["baseline"] = {"definition": digest(check_definition(c)), "reason": require_text(args.note, "baseline limitation --note"),
                             "artifact": artifact_from_file(directory, args.evidence), "at": now()}


def approve(args):
    with edit(args, "approve") as (_, t):
        c = get(t["checks"], args.id)
        if c.get("needs_review") or c["method"] != "command":
            raise DmdError("check must be a fully authored command check")
        parts = approval_parts(c)
        t["approvals"][c["id"]] = {"signature": digest(parts), "parts": parts,
                                   "note": require_text(args.note, "inspected approval --note"), "at": now()}
        print(c["id"] + " approved for the exact displayed definition and environment")


def preview(args):
    t = load_task(need(args))
    c = get(t["checks"], args.id)
    print(json.dumps({"definition": check_definition(c), "shell": SHELL,
                      "path_fingerprint": digest(os.environ.get("PATH", "")),
                      "instruction": "Inspect the command and every called script. Approval is an operator-authorized action, not implied by a ledger."}, indent=2))






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


def run(args):
    with interruptible():
        return _run(args)


def _run(args):
    directory = need(args)
    with lock(directory, ".run.lock"):
        with lock(directory):
            t = load_task(directory)
            if t["state"] in ("PAUSED", "CANCELLED"):
                raise DmdError("execution is suspended; operator-authorized activation required")
            if t.get("running"):
                raise DmdError("previous run has an unknown outcome; use recover-run after inspecting it")
            checks = select_checks(t, args.ids, args.all)
            plan = []
            for c in checks:
                if c["method"] != "command" or c.get("needs_review") or c.get("removed"):
                    raise DmdError(f"{c['id']}: only fully authored, live command checks can run")
                signature = approval_signature(c)
                recorded = t["approvals"].get(c["id"]) or {}
                if not approval_current(c, recorded.get("signature")):
                    drifted = approval_drift(c, recorded.get("parts"))
                    raise DmdError(f"{c['id']} has no current inspected approval: " + "; ".join(drifted) +
                                   f". Re-inspect and run: dmd approve {c['id']} --note '<what you inspected>'")
                if args.red and (not c.get("regression") or not c.get("red_match")):
                    raise DmdError(f"{c['id']}: --red requires a regression check with an intentional failure match")
                plan.append((c, signature, digest(check_definition(c))))
            window = QUIET_WINDOW_SECONDS if args.quiet_window is None else args.quiet_window
            candidates = preflight(t, checks, window)
            # One fingerprint window for the whole list: snapshot every candidate once here,
            # once after the last check. Receipts bind to the start state; drift is diffed.
            before = task_snapshot(t)
            fps = {path: snap["fingerprint"] for path, snap in before.items()}
            token = uuid.uuid4().hex
            t["running"] = {"token": token, "checks": [c["id"] for c in checks], "check": checks[0]["id"],
                            "started": now(), "source": fps, "candidates": candidates,
                            "pid": os.getpid(), "host": socket.gethostname(), "red": bool(args.red)}
            save(directory, t, "run.start", checks=[c["id"] for c in checks], red=args.red)
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
            poll["value"] = current.get("state") in ("PAUSED", "CANCELLED") or (current.get("running") or {}).get("token") != token
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
                wait = EXCLUSIVE_WAIT_SECONDS if args.wait_exclusive is None else args.wait_exclusive
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
                print(json.dumps({"check": c["id"], "event": "start", "candidate": check_candidate(c, t["root"]),
                                  "timeout": c["timeout"], "at": now()}), file=sys.stderr, flush=True)
                try:
                    results.append(execute(c, cancelled))
                finally:
                    holder.__exit__(None, None, None)
                print(json.dumps({"check": c["id"], "event": "finish", "exit": results[-1]["exit"],
                                  "duration_s": results[-1]["duration_s"], "failure": results[-1]["failure"]}), file=sys.stderr, flush=True)
                if results[-1]["failure"] == "CANCELLED":
                    break
        except BaseException:
            # The supervisor has cleaned up its local process group. Preserve
            # interrupted evidence as unknown, never silently retry external effects.
            with lock(directory):
                t = load_task(directory)
                if (t.get("running") or {}).get("token") == token:
                    t["running"]["interrupted"] = True
                    save(directory, t, "run.interrupted", checks=[c["id"] for c, _, _ in plan], completed=len(results))
            raise
        with lock(directory):
            t = load_task(directory)
            after = task_snapshot(t)
            moved = {path: drift(before[path], after[path]) for path in before if path in after and before[path]["fingerprint"] != after[path]["fingerprint"]}
            suspended = (t.get("running") or {}).get("token") != token or t["state"] in ("PAUSED", "CANCELLED")
            summary = []
            all_ok = True
            for (c, signature, definition), result in zip(plan, results):
                current = get(t["checks"], c["id"])
                cand = check_candidate(c, t["root"])
                candidate_drift = moved.get(cand) or next((moved[p] for p in moved if Path(p) in Path(cand).parents), None)
                definition_changed = digest(check_definition(current)) != definition or approval_signature(current) != signature
                changed = suspended or definition_changed or candidate_drift is not None
                failure = result["failure"] or ("CANDIDATE_OR_DEFINITION_CHANGED" if changed else None)
                match = c["red_match"] if args.red else c["match"]
                matched = bool(match and match in result["output"])
                expected_exit = c["red_exit"] if args.red else 0
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
                receipt = {"kind": "command", "source": source_for(fps, c), "candidate": cand,
                           "head": before[cand]["head"] if cand in before else None,
                           "definition": definition, "artifact": art,
                           "exit": result["exit"], "matched": matched, "failure": failure,
                           "background_holders": result["background_holders"],
                           "duration_s": result["duration_s"], "at": now()}
                if stale:
                    receipt["stale"] = reason
                if args.red:
                    current["red"] = receipt if success else None
                    label = "RED-OK" if success else ("RED-STALE" if stale else "RED-INVALID")
                else:
                    current["status"] = "PASS" if success else "FAIL"
                    current["receipt"] = receipt
                    label = "PASS" if success else ("STALE" if stale else "FAIL")
                runs = current.setdefault("runs", [])
                runs.append({"at": now(), "red": args.red, "artifact": art, "result": label})
                if len(runs) > MAX_RUNS_PER_CHECK:
                    del runs[:-MAX_RUNS_PER_CHECK]
                all_ok = all_ok and success
                row = {"check": c["id"], "result": label, "exit": result["exit"], "matched": matched,
                       "failure": failure, "duration_s": result["duration_s"], "candidate": cand,
                       "head": receipt["head"], "evidence": str(directory / art["path"])}
                if stale:
                    row["stale"] = reason
                summary.append(row)
            skipped = [c["id"] for c, _, _ in plan[len(results):]]
            t["running"] = None
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


def finding(args):
    if args.action == "list":
        return listing(args, "findings")
    with edit(args, "finding." + args.action) as (directory, t):
        if args.action == "add":
            fid = new_id(t["findings"], "F")
            status = args.status or "suspected"
            if status not in ("suspected", "confirmed"):
                raise DmdError("new findings start suspected or confirmed")
            t["findings"].append({"id": fid, "text": require_text(args.text, "finding"),
                                  "location": require_text(args.location, "--location"), "origin": args.origin or "unknown",
                                  "status": status, "work": [], "checks": [], "note": args.note or ""})
            if status == "confirmed":
                t["findings"][-1]["confirmed_at"] = now()
            print(fid)
        elif args.action == "defer":
            f = get(t["findings"], args.id)
            if f["status"] in ("fixed-verified", "disproved", "duplicate"):
                raise DmdError(f"{f['id']} is already resolved as {f['status']}")
            f["status"] = "deferred"
            f["note"] = require_text(args.note, "--note explaining why this defect is deferred rather than fixed")
            f["authority"] = require_text(args.authority, "explicit operator instruction deferring this finding (--authority)")
            f["deferred_at"] = now()
            t["amendments"].append({"at": now(), "text": "finding deferred under operator authority: " + f["authority"], "finding": f["id"]})
            print(f["id"] + " deferred under recorded operator authority; it is disclosed in every report")
        else:
            f = get(t["findings"], args.id)
            if args.status in OPERATOR_FINDING_STATES:
                raise DmdError(f"{args.status} requires operator authority: use dmd finding defer --id {f['id']} --authority '...' --note '...'")
            if args.status:
                f["status"] = args.status
                if args.status == "confirmed":
                    f.setdefault("confirmed_at", now())
            if args.origin:
                f["origin"] = args.origin
            if args.work is not None:
                f["work"] = args.work
            if args.check is not None:
                f["checks"] = args.check
            if args.note:
                f["note"] = args.note
            if f["status"] in ("fixed-verified", "disproved", "duplicate"):
                require_text(args.note, "evidence-backed resolution --note")
            if f["status"] == "disproved":
                f["artifact"] = artifact_from_file(directory, args.evidence)
                f["source"] = source_digest(task_fingerprint(t))
                from .model import assess
                reason = assess(directory, t, task_fingerprint(t)).findings.get(f["id"])
                if reason:
                    raise DmdError(reason)
            if f["status"] == "duplicate":
                target = require_text(args.duplicate, "--duplicate canonical finding ID")
                get(t["findings"], target)
                f["duplicate"] = target
                seen, cursor = set(), f["id"]
                while cursor:
                    if cursor in seen:
                        raise DmdError("duplicate finding cycle")
                    seen.add(cursor)
                    row = get(t["findings"], cursor)
                    cursor = row.get("duplicate") if row["status"] == "duplicate" else None
            if f["status"] == "fixed-verified":
                from .model import unresolved_findings
                reason = unresolved_findings(directory, t, task_fingerprint(t)).get(f["id"])
                if reason:
                    raise DmdError(reason)
            print(f["id"] + " " + f["status"])


def blocker(args):
    if args.action == "list":
        return listing(args, "blockers")
    with edit(args, "blocker." + args.action) as (_, t):
        if args.action == "add":
            item = require_text(args.item, "--item naming 'task' or the R/W/A/F ID that is blocked")
            if item != "task":
                known = {row["id"] for group in ("requirements", "work", "checks", "findings") for row in t[group]}
                if item not in known:
                    raise DmdError(f"--item must be 'task' or an existing R/W/A/F ID, not {item}")
            bid = new_id(t["blockers"], "B")
            t["blockers"].append({"id": bid, "item": item, "text": require_text(args.text, "concrete missing prerequisite"),
                                  "owner": require_text(args.owner, "--owner"), "unblock": require_text(args.unblock, "--unblock"),
                                  "proof": require_text(args.proof, "--proof of the unavailable prerequisite"), "resolved": False})
            print(bid)
        else:
            b = get(t["blockers"], args.id)
            b["resolution"] = require_text(args.proof, "--proof the blocker was resolved")
            b["resolved"] = True


def uncertain(args):
    if args.action == "list":
        return listing(args, "uncertain")
    with edit(args, "uncertain." + args.action) as (_, t):
        if args.action == "add":
            uid = new_id(t["uncertain"], "U")
            t["uncertain"].append({"id": uid, "text": require_text(args.text, "unknown operation"), "resolved": False})
            print(uid)
        else:
            u = get(t["uncertain"], args.id)
            u["proof"] = require_text(args.proof, "--proof of the reconciled outcome")
            u["resolved"] = True


def coverage(args):
    if args.action == "show":
        t = load_task(need(args))
        print(json.dumps({"original_request": t["original_request"], "amendments": t["amendments"],
                          "requirements": t["requirements"], "work": t["work"],
                          "checks": [check_definition(c) for c in t["checks"]], "findings": t["findings"]}, indent=2))
    else:
        with edit(args, "coverage.assert") as (_, t):
            if not t["requirements"]:
                raise DmdError("cannot assert coverage of an empty requirement inventory")
            t["coverage"] = {"digest": contract_digest(t), "note": require_text(args.note, "request-to-record mapping --note"), "at": now()}
        print("coverage recorded; semantic completeness still requires source-request review")


def review(args):
    rejected = None
    with edit(args, "review") as (directory, t):
        fp = task_fingerprint(t)
        g = gate(directory, t, fp, require_review=False)
        if g["status"] != "COMPLETE":
            # A review that finds outstanding work is part of the audit trail, not a no-op.
            t.setdefault("review_log", []).append(
                {"at": now(), "outcome": "rejected", "kind": args.kind,
                 "reviewer": require_text(args.reviewer, "--reviewer identity"),
                 "note": require_text(args.note, "final review --note"),
                 "artifact": artifact_from_file(directory, args.evidence),
                 "outstanding": g["reasons"][:20]})
            rejected = g["reasons"]
        else:
            # One fingerprint per review. The signature binds the review to this source
            # state; a later gate detects any edit, so a second hash here adds only a race.
            artifact = artifact_from_file(directory, args.evidence)
            t["review"] = {"signature": review_signature(t, fp), "kind": args.kind, "reviewer": require_text(args.reviewer, "--reviewer identity"),
                           "note": require_text(args.note, "final review --note"), "artifact": artifact, "at": now()}
            t.setdefault("review_log", []).append({"at": now(), "outcome": "accepted", "kind": args.kind,
                                                   "reviewer": t["review"]["reviewer"], "note": t["review"]["note"]})
    if rejected:
        raise DmdError("review recorded as rejected; obligations remain: " + "; ".join(rejected))
    print("final review recorded")







def inspect_task(args):
    directory = need(args)
    with lock(directory):
        t = load_task(directory)
        g = gate(directory, t)
        if args.command == "gate":
            if g["status"] in ("ACTIVE", "BLOCKED", "COMPLETE"):
                t["state"] = g["status"]
            save(directory, t, "gate", result=g["status"], reason_ids=[x.split(":")[0] for x in g["reasons"]])
        text = render(directory, t, g)
        if args.command in ("handoff", "reconcile", "gate"):
            atomic(directory / "handoff.md", text)
        if args.command == "report" and args.save:
            atomic(directory / "report.md", text)
        if getattr(args, "only", None):
            text = render(directory, t, g, only=set(args.only))
    if args.brief and "source" in g:
        # The map is one line per candidate; a digest identifies the same state in one.
        g = dict(g, source_digest=source_digest(g["source"]), candidates=len(g["source"]))
        del g["source"]
    if args.command == "status" and args.json:
        print(json.dumps({"task_dir": str(directory), "task": t, "gate": g}, indent=2))
    elif args.command in ("gate", "next"):
        print(json.dumps(g, indent=2))
    else:
        print(text, end="")
    return (0 if g["status"] == "COMPLETE" else 1) if args.command == "gate" else 0


def other(args):
    with edit(args, args.command) as (directory, t):
        if args.command == "amend":
            t["amendments"].append({"at": now(), "text": require_text(args.text, "operator amendment")})
        elif args.command == "state":
            require_text(args.reason, "--reason recording the operator instruction or real interruption")
            if args.status == "ACTIVE" and t["state"] == "CANCELLED" and not args.authority:
                raise DmdError("reactivation after cancellation requires explicit --authority")
            t["state"] = args.status
            t["state_reason"] = args.reason
            if args.status == "ACTIVE":
                t["watchdogs"] = {}
        elif args.command == "attempt":
            groups = {"R": "requirements", "W": "work", "A": "checks", "F": "findings"}
            prefix = args.item.split("-", 1)[0]
            if prefix not in groups:
                raise DmdError("attempt names a requirement, work item, check or finding ID (R-/W-/A-/F-)")
            get(t[groups[prefix]], args.item)
            history = t["attempts"].setdefault(args.item, [])
            signature = digest(require_text(args.signature, "observed failure signature").strip())
            history.append({"at": now(), "signature": signature, "failure": redact(args.signature), "strategy": args.strategy})
            # Alternating between two failing approaches is still no new information.
            print("Change diagnostic strategy before another equivalent attempt; obligation remains active."
                  if repeated_attempts(history) else "Attempt recorded.")
        elif args.command == "bind-session":
            remember_session(t, args.session)
            bind_session(directory, args.session)
        elif args.command == "map-host-task":
            get(t["work"], args.work)
            old = t["host_map"].get(args.host_id)
            if old and old != args.work:
                raise DmdError("host task is already mapped to another work item")
            t["host_map"][require_text(args.host_id, "--host-id")] = args.work
        elif args.command == "recover-run":
            # An OS-released flock proves the local runner is gone, not that an
            # external deployment or payment did/didn't happen. Require reconciliation.
            r = t.get("running")
            if not r:
                raise DmdError("no interrupted run is recorded; nothing to recover")
            require_text(args.proof, "--proof reconciling the interrupted operation")
            with lock(directory, ".run.lock", wait=0):
                alive = pid_alive(r.get("pid"), r.get("host"))
                found = {"check": r.get("check"), "checks": r.get("checks") or [r.get("check")], "started": r.get("started"),
                         "pid": r.get("pid"), "host": r.get("host"), "this_host": socket.gethostname(),
                         "runner_alive": alive, "interrupted_flag": bool(r.get("interrupted")), "run_lock": "free"}
                if alive is True:
                    raise DmdError(f"runner pid {r.get('pid')} is still alive on this host; stop it or wait, do not recover over it: "
                                   + json.dumps(found))
                if alive is False:
                    found["accepted_because"] = f"run lock was free and runner pid {r.get('pid')} no longer exists on this host"
                elif r.get("pid") is None:
                    found["accepted_because"] = "run lock was free; the record predates PID tracking, so liveness was not checkable"
                else:
                    found["accepted_because"] = f"run lock was free; the runner was on host {r.get('host')}, so its liveness was not checkable here"
                found["external_effects"] = "not proven by this command; your --proof must reconcile them"
                t["running"] = None
                t.setdefault("recovery", []).append({"at": now(), "proof": args.proof, "found": found})
                print(json.dumps(found, indent=2))


def doctor(args):
    """One read-only health pass over the things that otherwise surface as an error in the
    middle of some other command. Exit 1 when anything needs attention."""
    report = {"state_root": None, "hook_mode": None, "problems": [], "notes": []}
    try:
        root = state_root()
        report["state_root"] = str(root)
    except DmdError as exc:
        report["problems"].append(f"state root: {exc}")
        print(json.dumps(report, indent=2)); return 1
    try:
        config = load_config(strict=False)
        report["hook_mode"] = config.get("mode", "observe") if isinstance(config, dict) else None
        report["problems"] += [f"config.json: {e}" for e in config_errors(config)]
    except DmdError as exc:
        report["problems"].append(f"config.json: {exc}")
    if os.environ.get("PATH") and not any((Path(d) / "dmd").exists() for d in os.environ["PATH"].split(os.pathsep) if d):
        report["notes"].append("no dmd on PATH; run hooks/install.py --link-bin or call bin/dmd by path")
    dead = 0
    for binding, target in bindings():
        if target is None:
            report["problems"].append(f"unreadable session binding: {binding.name}")
        elif not (target / "task.json").is_file():
            dead += 1
    if dead:
        report["notes"].append(f"{dead} dead session binding(s); dmd gc removes them")
    try:
        directory = locate(args.cwd)
    except (DmdError, OSError) as exc:
        report["problems"].append(f"this worktree: {exc}")
        directory = None
    if directory is None:
        report["notes"].append("no active task in this worktree")
        try:
            related = sibling_tasks(args.cwd)
        except (DmdError, OSError):
            related = []
        if related:
            report["notes"].append("related: " + "; ".join(f"{tid} is {state} in {r}" for tid, state, r in related))
    else:
        t = load_task(directory)
        report["task"] = {"task_id": t["task_id"], "state": t["state"], "root": t["root"], "dir": str(directory)}
        if not Path(t["root"]).is_dir():
            report["problems"].append(f"task root is missing: {t['root']}; dmd relocate --to <path> --authority '...'")
        for c in live(t["checks"]):
            lost = missing_inputs(c)
            if lost:
                report["problems"].append(f"{c['id']}: declared input missing: {', '.join(lost)}; check edit --id {c['id']} --input <file> or --clear-inputs")
            if not Path(c["cwd"]).is_dir():
                report["problems"].append(f"{c['id']}: cwd is missing: {c['cwd']}")
        running = t.get("running")
        if running:
            alive = pid_alive(running.get("pid"), running.get("host"))
            if alive is False:
                report["problems"].append(f"stranded run of {running.get('checks')}: runner pid {running.get('pid')} is gone; dmd recover-run --proof '...'")
            else:
                report["notes"].append(f"run in progress: {running.get('checks')} (pid {running.get('pid')})")
        try:
            g = gate(directory, t)
            report["gate"] = {"status": g["status"], "headline": (g.get("summary") or {}).get("headline")}
        except DmdError as exc:
            report["problems"].append(f"gate: {exc}")
    print(json.dumps(report, indent=2))
    return 1 if report["problems"] else 0


def gc(args):
    """Remove dead session bindings and list finished task records. Task directories are
    never deleted here: evidence is the audit trail, and removal is the operator's call."""
    removed = prune_sessions()
    finished = []
    for directory, t in task_records():
        if isinstance(t, dict) and t["state"] in ("COMPLETE", "CANCELLED"):
            finished.append({"task_id": t["task_id"], "state": t["state"], "root": t["root"], "path": str(directory)})
    print(json.dumps({"session_bindings_removed": removed, "finished_tasks": finished,
                      "note": "finished task directories are listed, not deleted; remove them explicitly if their evidence is no longer needed"}, indent=2))
    return 0


def relocate(args):
    """Re-point a task whose checkout moved. Every receipt is bound to the old tree, so all
    command evidence returns to NOT_RUN; nothing is silently carried across."""
    import shutil
    new_root = Path(args.to).expanduser().resolve(strict=True)
    if not new_root.is_dir():
        raise DmdError("--to must be an existing directory")
    require_text(args.authority, "--authority recording the operator instruction to relocate")
    matches = []
    for directory, t in task_records():
        if not isinstance(t, dict):
            continue
        if args.task:
            if t["task_id"] == args.task:
                matches.append(directory)
        elif t["state"] not in ("COMPLETE", "CANCELLED") and not Path(t["root"]).is_dir():
            matches.append(directory)
    if len(matches) != 1:
        raise DmdError(f"{len(matches)} task(s) matched; name exactly one with --task <id> (dmd list shows roots)")
    old_dir = matches[0]
    with lock(old_dir):
        t = load_task(old_dir)
        old_root = t["root"]
        if old_root == str(new_root):
            raise DmdError("task already lives at that root")
        base, root = base_dir(new_root)
        if str(root) != str(new_root):
            raise DmdError(f"--to must be the checkout root ({root}), not a subdirectory")
        new_dir = base / t["task_id"]
        if new_dir.exists():
            raise DmdError(f"a task record already exists at {new_dir}")
        def moved(path):
            p = Path(path)
            if p == Path(old_root) or Path(old_root) in p.parents:
                return str(new_root / p.relative_to(old_root))
            return str(p)
        t["root"] = str(new_root)
        for c in t["checks"]:
            c["cwd"] = moved(c["cwd"])
            c["inputs"] = [moved(x) for x in c.get("inputs", [])]
            if c.get("candidate"):
                c["candidate"] = moved(c["candidate"])
            if c.get("status") == "PASS":
                c["status"] = "NOT_RUN"
        t["amendments"].append({"at": now(), "text": f"relocated from {old_root} to {new_root}: " + args.authority})
        t.setdefault("relocations", []).append({"at": now(), "from": old_root, "to": str(new_root), "authority": args.authority})
        shutil.copytree(old_dir, new_dir, symlinks=False, ignore=shutil.ignore_patterns(".lock", ".run.lock"))
        save(new_dir, t, "relocate", **{"from": old_root, "to": str(new_root)})
        atomic(base / "active.json", json.dumps({"task_id": t["task_id"]}))
        old_marker = old_dir.parent / "active.json"
        if old_marker.exists() and read_json(old_marker).get("task_id") == t["task_id"]:
            old_marker.unlink()
        shutil.rmtree(old_dir)
    print(json.dumps({"task_id": t["task_id"], "from": old_root, "to": str(new_root), "dir": str(new_dir),
                      "note": "all command evidence is now NOT_RUN; approve and rerun checks against the new tree"}))
    return 0


def configuration(args):
    with lock(state_root()):
        data = load_config(strict=False)
        if args.mode:
            data["mode"] = args.mode
        if args.max_no_progress is not None:
            if not 1 <= args.max_no_progress <= 6:
                raise DmdError("max-no-progress must be 1..6; do not bypass host loop safeguards")
            data["max_no_progress"] = args.max_no_progress
        save_config(data)
    print(json.dumps(data))


HELP = {
    "init": "Create the durable task record for an authorized assignment",
    "req": "Add, cancel, list, or mark attest-only a requirement",
    "work": "Add, edit, remove, or list work items and set their status",
    "check": "Add, edit, list, or attest acceptance checks; record a red baseline",
    "preview": "Print exactly what a check will execute, for approval",
    "approve": "Approve a check's command after inspecting its preview",
    "run": "Execute checks (IDs or --all) in one fingerprint window",
    "finding": "Record, update, or list defects found in the project",
    "blocker": "Record, clear, or list concrete external blockers",
    "uncertain": "Record or reconcile an external operation with unknown outcome",
    "coverage": "Show the contract or assert request-to-inventory coverage",
    "review": "Record the final request/diff/integration review",
    "status": "Print the report (--json for task and gate)",
    "next": "Gate JSON with the next executable actions",
    "gate": "Compute and store the gate; exit 0 only on COMPLETE",
    "report": "Print the full report (--save writes report.md)",
    "handoff": "Write handoff.md for the next session",
    "reconcile": "Re-derive the handoff on resume and print it",
    "state": "Set the task ACTIVE, PAUSED, or CANCELLED with a reason",
    "amend": "Record an operator amendment to the request",
    "attempt": "Record a failed attempt signature for a requirement, work item, check or finding",
    "bind-session": "Bind a host session ID to this task",
    "map-host-task": "Map a native host todo ID to a work item",
    "recover-run": "Clear an interrupted run after checking the runner",
    "config": "Set the hook mode (off, observe, enforce) and watchdog limit",
    "hook": "Entry point for installed host lifecycle hooks",
    "list": "List every task record in the state directory (--json, --state)",
    "doctor": "Health check: state root, hook mode, bindings, this worktree's task",
    "gc": "Remove dead session bindings; list finished task records",
    "relocate": "Re-point a task whose checkout moved (all evidence becomes NOT_RUN)",
    "migrate": "Import a schema-1 record into a new schema-2 task",
}

def parser():
    p = argparse.ArgumentParser(prog="dmd", description="Persistent obligations, strict remediation, verified completion")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--task")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    def command(name, fn):
        s = sub.add_parser(name, help=HELP[name], description=HELP[name]); s.set_defaults(func=fn); return s
    s = command("init", init); s.add_argument("-m", "--message"); s.add_argument("--request-file"); s.add_argument("--authority", required=True); s.add_argument("--session"); s.add_argument("--new", action="store_true"); s.add_argument("--independent-review", action="store_true")
    s = command("req", req); s.add_argument("action", choices=["add", "cancel", "attest-only", "list"]); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--anchor"); s.add_argument("--authority"); s.add_argument("--json", action="store_true")
    s = command("work", work); s.add_argument("action", choices=["add", "set", "remove", "list"]); s.add_argument("text", nargs="?"); s.add_argument("--req"); s.add_argument("--id"); s.add_argument("--dep", action="append"); s.add_argument("--owns", action="append"); s.add_argument("--status", choices=sorted(WORK_STATES)); s.add_argument("--note"); s.add_argument("--replace"); s.add_argument("--clear-deps", action="store_true"); s.add_argument("--json", action="store_true")
    s = command("check", check); s.add_argument("action", choices=["add", "edit", "set", "baseline", "remove", "list"])
    for flag in ["req", "id", "cmd", "run-cwd", "expect", "match", "red-match", "status", "note", "evidence", "candidate", "approve"]:
        s.add_argument("--" + flag)
    s.add_argument("--method", choices=["command", "manual", "review", "browser"]); s.add_argument("--work", action="append"); s.add_argument("--input", action="append"); s.add_argument("--clear-inputs", action="store_true"); s.add_argument("--clear-exclusive", action="store_true"); s.add_argument("--exclusive", action="append"); s.add_argument("--timeout", type=float); s.add_argument("--max-output", type=int); s.add_argument("--regression", action="store_true"); s.add_argument("--no-regression", action="store_true"); s.add_argument("--red-exit", type=int); s.add_argument("--attested-because"); s.add_argument("--json", action="store_true")
    s = command("preview", preview); s.add_argument("id")
    s = command("approve", approve); s.add_argument("id"); s.add_argument("--note", required=True)
    s = command("run", run); s.add_argument("ids", nargs="*"); s.add_argument("--all", action="store_true"); s.add_argument("--red", action="store_true"); s.add_argument("--quiet-window", type=float); s.add_argument("--wait-exclusive", type=float)
    s = command("finding", finding); s.add_argument("action", choices=["add", "set", "defer", "list"]); s.add_argument("--authority"); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--location"); s.add_argument("--status", choices=sorted(FINDING_STATES)); s.add_argument("--origin", choices=["introduced", "pre-existing", "dependency", "unknown"]); s.add_argument("--note"); s.add_argument("--work", action="append"); s.add_argument("--check", action="append"); s.add_argument("--duplicate"); s.add_argument("--evidence"); s.add_argument("--json", action="store_true")
    s = command("blocker", blocker); s.add_argument("action", choices=["add", "clear", "list"]); s.add_argument("text", nargs="?"); s.add_argument("--json", action="store_true")
    for flag in ["id", "item", "owner", "unblock", "proof"]: s.add_argument("--" + flag)
    s = command("uncertain", uncertain); s.add_argument("action", choices=["add", "resolve", "list"]); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--proof"); s.add_argument("--json", action="store_true")
    s = command("coverage", coverage); s.add_argument("action", choices=["show", "assert"]); s.add_argument("--note")
    s = command("review", review); s.add_argument("--kind", choices=["self", "independent"], required=True); s.add_argument("--reviewer", required=True); s.add_argument("--note", required=True); s.add_argument("--evidence", required=True)
    for name in ["status", "next", "gate", "report", "handoff", "reconcile"]:
        s = command(name, inspect_task); s.add_argument("--json", action="store_true"); s.add_argument("--save", action="store_true"); s.add_argument("--only", action="append", choices=SECTIONS, help="render one report section")
        s.add_argument("--brief", action="store_true", help="omit the per-candidate source map from gate JSON")
    s = command("state", other); s.add_argument("status", choices=["ACTIVE", "PAUSED", "CANCELLED"]); s.add_argument("--reason", required=True); s.add_argument("--authority")
    s = command("amend", other); s.add_argument("text")
    s = command("attempt", other); s.add_argument("item"); s.add_argument("signature"); s.add_argument("--strategy")
    s = command("bind-session", other); s.add_argument("session")
    s = command("map-host-task", other); s.add_argument("--host-id", required=True); s.add_argument("--work", required=True)
    s = command("recover-run", other); s.add_argument("--proof", required=True)
    s = command("config", configuration); s.add_argument("--mode", choices=["off", "observe", "enforce"]); s.add_argument("--max-no-progress", type=int)
    s = command("hook", None); s.add_argument("event", choices=["session-start", "stop", "task-completed", "post-tool-use", "post-tool-failure"])
    s = command("list", None); s.add_argument("--json", action="store_true"); s.add_argument("--state", action="append")
    command("doctor", doctor)
    command("gc", gc)
    s = command("relocate", relocate); s.add_argument("--to", required=True); s.add_argument("--authority", required=True)
    s = command("migrate", None); s.add_argument("--from-task", required=True); s.add_argument("--authority", required=True); s.add_argument("--new", action="store_true")
    return p


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.command == "hook":
            from .hooks import handle
            return handle(args)
        if args.command == "migrate":
            from .migration import migrate
            return migrate(args)
        if args.command == "list":
            damaged = 0
            rows = []
            for directory, t in task_records():
                # One unreadable record must never hide every healthy assignment.
                path = directory / "task.json"
                if isinstance(t, dict):
                    row = {"task_id": t["task_id"], "stored_state": t["state"], "root": t["root"], "path": str(path),
                           "updated_at": t.get("updated_at")}
                else:
                    damaged += 1
                    row = {"task_id": None, "stored_state": "UNREADABLE", "path": str(path), "error": str(t)}
                if args.state and row["stored_state"] not in args.state:
                    continue
                rows.append(row)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    print(json.dumps(row))
            if damaged:
                print(f"dmd: {damaged} unreadable record(s) listed above; repair or migrate them explicitly", file=sys.stderr)
            return 0
        return args.func(args) or 0
    except (DmdError, OSError) as exc:
        print("dmd: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("dmd: interrupted; unfinished obligations are preserved", file=sys.stderr)
        return 130
    except Exception:
        # A defect in dmd is not a usage error. Say so, and keep the traceback for diagnosis
        # rather than flattening it into a message that looks like operator input was wrong.
        import traceback
        print("dmd: internal error; the task record was not advanced. Report this trace:", file=sys.stderr)
        traceback.print_exc()
        return 3

if __name__ == "__main__":
    raise SystemExit(main())

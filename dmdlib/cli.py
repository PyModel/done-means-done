"""Done Means Done command surface. No third-party Python dependencies."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
import sys
import uuid
from pathlib import Path
from . import __version__
from .storage import DmdError, atomic, digest, evidence, ident, lock, now, private_dir, read_json, redact, save
from .source import identity
from .model import (SCHEMA, FINDING_STATES, WORK_STATES, accepted, attested, check_definition, contract_digest,
                    gate, get, live, new_id, repeated_attempts, review_signature, task_fingerprint,
                    validation_errors, work_ok)
from .runner import approval_drift, approval_parts, approval_signature, execute, SHELL


def state_root():
    path = Path(os.environ.get("DMD_STATE", str(Path.home() / ".local/state/done-means-done"))).expanduser()
    # Canonicalize before the symlinked-ancestor check: /var and /tmp are symlinks on
    # macOS, so an abspath would reject every state directory under the system temp root.
    return private_dir(Path(os.path.realpath(path)))

def base_dir(cwd):
    root, project, worktree = identity(cwd)
    state = state_root()
    if state == root or state.is_relative_to(root):
        raise DmdError("DMD_STATE must remain outside the project being verified")
    return private_dir(state / "v2" / project / worktree), root

def load_task(directory):
    t = read_json(directory / "task.json")
    try:
        errors = validation_errors(t)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise DmdError("malformed task record; preserve it and repair or migrate explicitly") from exc
    if errors:
        raise DmdError("; ".join(errors))
    return t

def locate(cwd, task_id=None):
    base, root = base_dir(cwd)
    if task_id is None:
        if not (base / "active.json").exists():
            return None
        task_id = read_json(base / "active.json").get("task_id")
    directory = base / ident(task_id)
    if not (directory / "task.json").exists():
        return None
    t = load_task(directory)
    if t["root"] != str(root):
        raise DmdError("task belongs to another worktree")
    return directory

def sibling_tasks(cwd):
    """Unfinished tasks recorded for other worktrees of the same repository. State is keyed
    by physical worktree, so a task started in the main checkout is invisible from a linked
    worktree; naming it beats a bare 'no active task'."""
    root, project, worktree = identity(cwd)
    found = []
    for pointer in sorted((state_root() / "v2" / project).glob("*/active.json")):
        if pointer.parent.name == worktree:
            continue
        try:
            t = load_task(pointer.parent / ident(read_json(pointer).get("task_id")))
        except (DmdError, OSError):
            continue
        if t["state"] not in ("COMPLETE", "CANCELLED"):
            found.append((t["task_id"], t["state"], t["root"]))
    return found

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
    with lock(directory):
        t = load_task(directory)
        if t["state"] == "CANCELLED" and kind not in ("state",):
            raise DmdError("task is cancelled; explicit operator-authorized reactivation is required")
        yield directory, t
        errors = validation_errors(t)
        if errors:
            raise DmdError("; ".join(errors))
        save(directory, t, kind)


def bind_session(directory, session):
    if not session or len(session) > 512:
        raise DmdError("a host session ID of 1..512 characters is required")
    sessions = private_dir(state_root() / "sessions")
    # Bindings whose task no longer exists are dead weight; drop them as we go.
    for stale in sessions.glob("*.json"):
        try:
            target = Path(read_json(stale).get("task_dir", ""))
            if not (target / "task.json").is_file():
                stale.unlink()
        except (DmdError, OSError):
            with contextlib.suppress(OSError):
                stale.unlink()
    atomic(sessions / (digest(session) + ".json"), json.dumps({"task_dir": str(directory), "session_hash": digest(session)}))


def create_task(args, request, authorization, require_review=False, prepare=None):
    if getattr(args, "session", None) is not None and not 1 <= len(args.session) <= 512:
        raise DmdError("a host session ID of 1..512 characters is required")
    base, root = base_dir(args.cwd)
    task_id = ident(args.task or "T-" + uuid.uuid4().hex)
    directory = base / task_id
    t = {"schema": SCHEMA, "version": __version__, "task_id": task_id, "root": str(root),
         "state": "ACTIVE", "created_at": now(), "original_request": redact(request.strip()),
         "authorization": redact(authorization), "amendments": [], "requirements": [], "work": [],
         "checks": [], "findings": [], "blockers": [], "uncertain": [], "events": [], "sessions": [],
         "approvals": {}, "coverage": None, "review": None, "running": None, "attempts": {},
         "require_independent_review": require_review, "host_map": {}}
    if getattr(args, "session", None):
        t["sessions"].append(args.session)
    if prepare is not None:
        prepare(t)
        errors = validation_errors(t)
        if errors:
            raise DmdError("import rejected before activation: " + "; ".join(errors))
    with lock(base):
        if directory.exists():
            raise DmdError("task ID already exists; initialization never overwrites a task")
        old = locate(args.cwd)
        if old:
            prior = load_task(old)
            if prior["state"] not in ("COMPLETE", "CANCELLED") and not getattr(args, "new", False):
                raise DmdError("unfinished assignment exists; resume it or explicitly use --new")
            if prior["state"] not in ("COMPLETE", "CANCELLED"):
                with lock(old):
                    prior = load_task(old)
                    if prior.get("running"):
                        raise DmdError("cannot supersede an assignment while its check is running")
                    prior["state"] = "PAUSED"
                    save(old, prior, "superseded-active-pointer", new_task_id=task_id)
        private_dir(directory)
        save(directory, t, "init")
        atomic(base / "active.json", json.dumps({"task_id": task_id}))
    if getattr(args, "session", None):
        bind_session(directory, args.session)
    return directory, t


def artifact_from_file(directory, path):
    if path is None:
        raise DmdError("--evidence is required: record the actual observation artifact")
    return evidence(directory, read_operator_file(path, "--evidence"), "review")


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DmdError(f"{name} is required")
    return value.strip()


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
    directory, t = create_task(args, require_text(message, "request"), require_text(args.authority, "--authority"), args.independent_review)
    print(t["task_id"])


def req(args):
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
            r["status"] = "removed"
            r["authority"] = require_text(args.authority, "explicit operator removal instruction")
            t["amendments"].append({"at": now(), "text": r["authority"], "requirement": r["id"]})


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


def work(args):
    with edit(args, "work." + args.action) as (directory, t):
        if args.action == "remove":
            w = get(t["work"], args.id)
            supersede(t, w, "work", args.note)
            print(w["id"] + " superseded")
            return
        if args.action == "add":
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
                    if not cs or not all(accepted(directory, c, fp) for c in cs):
                        raise DmdError("work cannot be verified without current mapped acceptance evidence")
                w["status"] = args.status
            print(w["id"] + " " + w["status"])


def check(args):
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
                     "attested_because": args.attested_because,
                     "status": "NOT_RUN", "receipt": None, "red": None, "baseline": None}
                t["checks"].append(c)
            else:
                c = get(t["checks"], args.id)
                c.setdefault("history", []).append({"definition": check_definition(c), "receipt": c.get("receipt"), "at": now()})
                for flag, key in [("req", "req"), ("work", "work"), ("method", "method"), ("cmd", "command"),
                                  ("run_cwd", "cwd"), ("expect", "expect"), ("match", "match"), ("timeout", "timeout"),
                                  ("max_output", "max_output"), ("input", "inputs"), ("red_match", "red_match"),
                                  ("red_exit", "red_exit"), ("attested_because", "attested_because")]:
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
            c["inputs"] = [str((Path(c["cwd"]) / x).resolve(strict=True)) for x in c["inputs"]]
            if attested(c) and not str(c.get("attested_because") or "").strip():
                raise DmdError(f"--attested-because is required for a {c['method']} check: state why no "
                               "command can observe this behavior. Attested checks are reported as self-attested "
                               "and cannot alone accept a requirement.")
            if c["method"] == "command":
                if str(c.get("attested_because") or "").strip():
                    raise DmdError("--attested-because applies only to manual, review or browser checks")
                if trivial_command(c["command"]):
                    raise DmdError("a bare success printer is not an acceptance check")
            print(c["id"])
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
                fp = task_fingerprint(t)
                art = artifact_from_file(directory, args.evidence)
                if task_fingerprint(t) != fp:
                    raise DmdError("source changed while recording manual evidence")
                c["receipt"] = {"kind": c["method"], "source": fp, "definition": digest(check_definition(c)),
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


def run(args):
    directory = need(args)
    with lock(directory, ".run.lock"):
        with lock(directory):
            t = load_task(directory)
            if t["state"] in ("PAUSED", "CANCELLED"):
                raise DmdError("execution is suspended; operator-authorized activation required")
            if t.get("running"):
                raise DmdError("previous run has an unknown outcome; use recover-run after inspecting it")
            c = get(t["checks"], args.id)
            if c["method"] != "command" or c.get("needs_review"):
                raise DmdError("only fully authored command checks can run")
            signature = approval_signature(c)
            recorded = t["approvals"].get(c["id"]) or {}
            if recorded.get("signature") != signature:
                drift = approval_drift(c, recorded.get("parts"))
                raise DmdError("check has no current inspected approval: " + "; ".join(drift) +
                               f". Re-inspect and run: dmd approve {c['id']} --note '<what you inspected>'")
            if args.red and (not c.get("regression") or not c.get("red_match")):
                raise DmdError("--red requires a regression check with an intentional failure match")
            fp = task_fingerprint(t)
            definition = digest(check_definition(c))
            token = uuid.uuid4().hex
            t["running"] = {"token": token, "check": c["id"], "started": now(), "source": fp}
            save(directory, t, "run.start", check=c["id"], red=args.red)
        def cancelled():
            current = load_task(directory)
            return current["state"] in ("PAUSED", "CANCELLED") or (current.get("running") or {}).get("token") != token
        try:
            result = execute(c, cancelled)
        except BaseException:
            # The supervisor has cleaned up its local process group. Preserve
            # interrupted evidence as unknown, never silently retry external effects.
            with lock(directory):
                t = load_task(directory)
                if (t.get("running") or {}).get("token") == token:
                    t["running"]["interrupted"] = True
                    save(directory, t, "run.interrupted", check=c["id"])
            raise
        with lock(directory):
            t = load_task(directory)
            current = get(t["checks"], args.id)
            changed = ((t.get("running") or {}).get("token") != token or
                       digest(check_definition(current)) != definition or
                       approval_signature(current) != signature or task_fingerprint(t) != fp or
                       t["state"] in ("PAUSED", "CANCELLED"))
            failure = result["failure"] or ("CANDIDATE_OR_DEFINITION_CHANGED" if changed else None)
            match = c["red_match"] if args.red else c["match"]
            matched = bool(match and match in result["output"])
            expected_exit = c["red_exit"] if args.red else 0
            success = result["exit"] == expected_exit and matched and failure is None
            metadata = {k: v for k, v in result.items() if k != "output"}
            art = evidence(directory, json.dumps({"check": args.id, "definition": check_definition(c),
                                                 "source": fp, "started": (t.get("running") or {}).get("started"),
                                                 "metadata": metadata}, indent=2) + "\n\n" + result["output"], "command")
            receipt = {"kind": "command", "source": fp, "definition": definition, "artifact": art,
                       "exit": result["exit"], "matched": matched, "failure": failure,
                       "background_holders": result["background_holders"],
                       "duration_s": result["duration_s"], "at": now()}
            if args.red:
                current["red"] = receipt if success else None
                label = "RED-OK" if success else "RED-INVALID"
            else:
                current["status"] = "PASS" if success else "FAIL"
                current["receipt"] = receipt
                label = current["status"]
            current.setdefault("runs", []).append({"at": now(), "red": args.red, "artifact": art, "result": label})
            t["running"] = None
            save(directory, t, "run.finish", check=args.id, result=label, failure=failure)
        print(json.dumps({"check": args.id, "result": label, "exit": result["exit"], "matched": matched,
                          "failure": failure, "duration_s": result["duration_s"], "evidence": str(directory / art["path"])}))
        return 0 if success else 1


def finding(args):
    with edit(args, "finding." + args.action) as (directory, t):
        if args.action == "add":
            fid = new_id(t["findings"], "F")
            status = args.status or "suspected"
            if status not in ("suspected", "confirmed"):
                raise DmdError("new findings start suspected or confirmed")
            t["findings"].append({"id": fid, "text": require_text(args.text, "finding"),
                                  "location": require_text(args.location, "--location"), "origin": args.origin or "unknown",
                                  "status": status, "work": [], "checks": [], "note": args.note or ""})
            print(fid)
        else:
            f = get(t["findings"], args.id)
            if args.status:
                f["status"] = args.status
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
                f["source"] = task_fingerprint(t)
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
    with edit(args, "blocker." + args.action) as (_, t):
        if args.action == "add":
            bid = new_id(t["blockers"], "B")
            t["blockers"].append({"id": bid, "item": args.item, "text": require_text(args.text, "concrete missing prerequisite"),
                                  "owner": require_text(args.owner, "--owner"), "unblock": require_text(args.unblock, "--unblock"),
                                  "proof": require_text(args.proof, "--proof of the unavailable prerequisite"), "resolved": False})
            print(bid)
        else:
            b = get(t["blockers"], args.id)
            b["resolution"] = require_text(args.proof, "--proof the blocker was resolved")
            b["resolved"] = True


def uncertain(args):
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
            artifact = artifact_from_file(directory, args.evidence)
            if task_fingerprint(t) != fp:
                raise DmdError("source changed during final review")
            t["review"] = {"signature": review_signature(t, fp), "kind": args.kind, "reviewer": require_text(args.reviewer, "--reviewer identity"),
                           "note": require_text(args.note, "final review --note"), "artifact": artifact, "at": now()}
            t.setdefault("review_log", []).append({"at": now(), "outcome": "accepted", "kind": args.kind,
                                                   "reviewer": t["review"]["reviewer"], "note": t["review"]["note"]})
    if rejected:
        raise DmdError("review recorded as rejected; obligations remain: " + "; ".join(rejected))
    print("final review recorded")


def render(directory, t, g):
    counts = g.get("attestation") or {}
    lines = [f"# Done Means Done | {t['task_id']} | {g['status']}", "", "## Assignment", t["original_request"], "",
             "## Acceptance basis",
             f"- Accepted checks executed by dmd: {counts.get('executed', 0)}",
             f"- Accepted checks SELF-ATTESTED by the agent (no command was run): {counts.get('self_attested', 0)}",
             "", "## Requirements"]
    for r in t["requirements"]:
        line = f"- {r['id']} [{r['status']}] {r['text']} (source: {r['anchor']})"
        if r.get("attest_only"):
            line += " — attested-only by operator authority: " + r["attest_only_authority"]
        lines.append(line)
    lines += ["", "## Work"]
    for w in t["work"]:
        state = "superseded" if w.get("removed") else w["status"]
        lines.append(f"- {w['id']} [{state}] {w['text']} -> {w['req']}; dependencies: {', '.join(w['deps']) or 'none'}")
        if w.get("removed"):
            lines.append("  Superseded: " + w["removed_reason"])
    lines += ["", "## Checks"]
    for c in t["checks"]:
        current = accepted(directory, c, g.get("source"))
        if c.get("removed"):
            basis = "superseded"
        else:
            basis = "SELF-ATTESTED" if attested(c) else "EXECUTED"
        lines.append(f"- {c['id']} [{'ACCEPTED' if current else 'UNVERIFIED'} · {basis}] ({c['method']}) {c['expect']}")
        if attested(c) and c.get("attested_because"):
            lines.append("  Attested because: " + c["attested_because"])
        if c.get("removed"):
            lines.append("  Superseded: " + c["removed_reason"])
        if c.get("receipt"):
            lines.append("  Evidence: " + str(directory / c["receipt"]["artifact"]["path"]))
            if c["receipt"].get("background_holders"):
                lines.append("  Note: background processes still held the output pipes when this check finished.")
        if c.get("baseline"):
            lines.append("  Baseline limitation: " + c["baseline"]["reason"])
    lines += ["", "## Findings (all severities and origins)"]
    for f in t["findings"]:
        lines.append(f"- {f['id']} [{f['status']}; {f['origin']}] {f['location']}: {f['text']}; {f.get('note', '')}")
    lines += ["", "## Blockers and external outcomes"]
    for b in t["blockers"]:
        lines.append(f"- {b['id']} [{'resolved' if b['resolved'] else 'OPEN'}] {b['text']}; unblock: {b['unblock']}; owner: {b['owner']}")
    for u in t["uncertain"]:
        lines.append(f"- {u['id']} [{'resolved' if u['resolved'] else 'UNKNOWN'}] {u['text']}")
    repeats = {item: history for item, history in (t.get("attempts") or {}).items() if repeated_attempts(history)}
    if repeats:
        lines += ["", "## Attempts requiring a different strategy"]
        for item, history in sorted(repeats.items()):
            lines.append(f"- {item}: {len(history)} recorded attempts; last strategy: {history[-1].get('strategy') or 'none recorded'}")
    log = t.get("review_log") or []
    if log:
        lines += ["", "## Review history"]
        for entry in log:
            lines.append(f"- {entry['at']} [{entry['outcome']}] {entry['kind']} review by {entry['reviewer']}: {entry['note']}")
    lines += ["", "## Still owed"] + ["- " + x for x in g["reasons"]]
    lines += ["", "## Next actions"] + [f"- {x['id']}: {x['action']}" for x in g["next"]]
    lines += ["", "Review: " + ((t.get("review") or {}).get("kind", "not recorded")),
              "Source: " + str(g.get("source", "not measured while suspended")),
              "Evidence is local auditability, not tamper-proof attestation."]
    return "\n".join(lines) + "\n"


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
            get(t["work"], args.item)
            history = t["attempts"].setdefault(args.item, [])
            signature = digest(require_text(args.signature, "observed failure signature").strip())
            history.append({"at": now(), "signature": signature, "failure": redact(args.signature), "strategy": args.strategy})
            # Alternating between two failing approaches is still no new information.
            print("Change diagnostic strategy before another equivalent attempt; obligation remains active."
                  if repeated_attempts(history) else "Attempt recorded.")
        elif args.command == "bind-session":
            if args.session not in t["sessions"]:
                t["sessions"].append(args.session)
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
            with lock(directory, ".run.lock"):
                require_text(args.proof, "--proof reconciling the interrupted operation")
                t["running"] = None
                t.setdefault("recovery", []).append({"at": now(), "proof": args.proof})


def configuration(args):
    root = state_root()
    with lock(root):
        path = root / "config.json"
        data = read_json(path) if path.exists() else {"mode": "observe", "max_no_progress": 6}
        if args.mode:
            data["mode"] = args.mode
        if args.max_no_progress is not None:
            if not 1 <= args.max_no_progress <= 6:
                raise DmdError("max-no-progress must be 1..6; do not bypass host loop safeguards")
            data["max_no_progress"] = args.max_no_progress
        atomic(path, json.dumps(data, indent=2))
    print(json.dumps(data))


def parser():
    p = argparse.ArgumentParser(prog="dmd", description="Persistent obligations, strict remediation, verified completion")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--task")
    sub = p.add_subparsers(dest="command", required=True)
    def command(name, fn):
        s = sub.add_parser(name); s.set_defaults(func=fn); return s
    s = command("init", init); s.add_argument("-m", "--message"); s.add_argument("--request-file"); s.add_argument("--authority", required=True); s.add_argument("--session"); s.add_argument("--new", action="store_true"); s.add_argument("--independent-review", action="store_true")
    s = command("req", req); s.add_argument("action", choices=["add", "cancel", "attest-only"]); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--anchor"); s.add_argument("--authority")
    s = command("work", work); s.add_argument("action", choices=["add", "set", "remove"]); s.add_argument("text", nargs="?"); s.add_argument("--req"); s.add_argument("--id"); s.add_argument("--dep", action="append"); s.add_argument("--owns", action="append"); s.add_argument("--status", choices=sorted(WORK_STATES)); s.add_argument("--note"); s.add_argument("--replace")
    s = command("check", check); s.add_argument("action", choices=["add", "edit", "set", "baseline", "remove"])
    for flag in ["req", "id", "cmd", "run-cwd", "expect", "match", "red-match", "status", "note", "evidence"]:
        s.add_argument("--" + flag)
    s.add_argument("--method", choices=["command", "manual", "review", "browser"]); s.add_argument("--work", action="append"); s.add_argument("--input", action="append"); s.add_argument("--timeout", type=float); s.add_argument("--max-output", type=int); s.add_argument("--regression", action="store_true"); s.add_argument("--no-regression", action="store_true"); s.add_argument("--red-exit", type=int); s.add_argument("--attested-because")
    s = command("preview", preview); s.add_argument("id")
    s = command("approve", approve); s.add_argument("id"); s.add_argument("--note", required=True)
    s = command("run", run); s.add_argument("id"); s.add_argument("--red", action="store_true")
    s = command("finding", finding); s.add_argument("action", choices=["add", "set"]); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--location"); s.add_argument("--status", choices=sorted(FINDING_STATES)); s.add_argument("--origin", choices=["introduced", "pre-existing", "dependency", "unknown"]); s.add_argument("--note"); s.add_argument("--work", action="append"); s.add_argument("--check", action="append"); s.add_argument("--duplicate"); s.add_argument("--evidence")
    s = command("blocker", blocker); s.add_argument("action", choices=["add", "clear"]); s.add_argument("text", nargs="?")
    for flag in ["id", "item", "owner", "unblock", "proof"]: s.add_argument("--" + flag)
    s = command("uncertain", uncertain); s.add_argument("action", choices=["add", "resolve"]); s.add_argument("text", nargs="?"); s.add_argument("--id"); s.add_argument("--proof")
    s = command("coverage", coverage); s.add_argument("action", choices=["show", "assert"]); s.add_argument("--note")
    s = command("review", review); s.add_argument("--kind", choices=["self", "independent"], required=True); s.add_argument("--reviewer", required=True); s.add_argument("--note", required=True); s.add_argument("--evidence", required=True)
    for name in ["status", "next", "gate", "report", "handoff", "reconcile"]:
        s = command(name, inspect_task); s.add_argument("--json", action="store_true"); s.add_argument("--save", action="store_true")
    s = command("state", other); s.add_argument("status", choices=["ACTIVE", "PAUSED", "CANCELLED"]); s.add_argument("--reason", required=True); s.add_argument("--authority")
    s = command("amend", other); s.add_argument("text")
    s = command("attempt", other); s.add_argument("item"); s.add_argument("signature"); s.add_argument("--strategy")
    s = command("bind-session", other); s.add_argument("session")
    s = command("map-host-task", other); s.add_argument("--host-id", required=True); s.add_argument("--work", required=True)
    s = command("recover-run", other); s.add_argument("--proof", required=True)
    s = command("config", configuration); s.add_argument("--mode", choices=["off", "observe", "enforce"]); s.add_argument("--max-no-progress", type=int)
    s = command("hook", None); s.add_argument("event", choices=["session-start", "stop", "task-completed", "post-tool-use", "post-tool-failure"])
    command("list", None)
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
            for path in sorted(state_root().glob("v2/*/*/*/task.json")):
                # One unreadable record must never hide every healthy assignment.
                try:
                    t = load_task(path.parent)
                    row = {"task_id": t["task_id"], "stored_state": t["state"], "root": t["root"], "path": str(path)}
                except (DmdError, OSError) as exc:
                    damaged += 1
                    row = {"task_id": None, "stored_state": "UNREADABLE", "path": str(path), "error": str(exc)}
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

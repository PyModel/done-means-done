"""Pure state validation plus current-evidence checks. Only this module computes completion."""
from __future__ import annotations
import re
from collections import deque
from pathlib import Path
from .storage import DmdError, digest, evidence_ok
from .source import snapshot, MissingCandidate

SCHEMA = 2
TASK_STATES = {"ACTIVE", "PAUSED", "BLOCKED", "CANCELLED", "COMPLETE"}
WORK_STATES = {"todo", "doing", "implemented", "verified"}
FINDING_STATES = {"suspected", "confirmed", "fixed-unverified", "fixed-verified", "disproved", "duplicate", "deferred"}
# Findings the agent may not resolve on its own judgement: `deferred` exists only under
# recorded operator authority (dmd finding defer), mirrors req attest-only, and is disclosed
# by name in every report and gate summary.
OPERATOR_FINDING_STATES = ("deferred",)
CHECK_FIELDS = ("id", "req", "work", "method", "command", "cwd", "expect", "match", "timeout", "max_output", "inputs", "regression", "red_match", "red_exit", "attested_because")
# Added in 0.5.0. Part of the definition only when set, so a record written by an earlier
# release keeps its definition digest, approvals and receipts across the upgrade.
OPTIONAL_CHECK_FIELDS = ("candidate", "exclusive")
# Only a command check produces machine-verifiable acceptance. Every other method is an
# agent or operator attestation: recorded, rendered and counted as such, never disguised.
ATTESTED_METHODS = ("manual", "review", "browser")

def live(items):
    """Records the agent superseded stay in the ledger and the report, but owe nothing."""
    return [x for x in items if not x.get("removed")]

def attested(c):
    return c.get("method") in ATTESTED_METHODS

def get(items, key):
    item = next((x for x in items if x["id"] == key), None)
    if item is None:
        raise DmdError(f"unknown ID: {key}")
    return item

def new_id(items, prefix):
    return f"{prefix}-{max([int(x['id'].split('-')[1]) for x in items] or [0]) + 1:02d}"

def check_definition(c):
    definition = {key: c.get(key) for key in CHECK_FIELDS}
    for key in OPTIONAL_CHECK_FIELDS:
        if c.get(key):
            definition[key] = c[key]
    return definition

def check_candidate(c, root=None):
    """The tree a check tests. Explicit `candidate` wins; a legacy check without one tests
    the task root when its cwd lies inside it, otherwise its own cwd."""
    if c.get("candidate"):
        return c["candidate"]
    cwd = Path(c["cwd"])
    if root is not None:
        root = Path(root)
        if cwd == root or root in cwd.parents:
            return str(root)
    return str(cwd)

def source_for(fps, c):
    """The fingerprint a check's receipt must match: its candidate's entry, or the nearest
    enclosing candidate when a legacy check names a subdirectory."""
    if not isinstance(fps, dict):
        return fps
    cand = Path(check_candidate(c))
    if str(cand) in fps:
        return fps[str(cand)]
    for parent in cand.parents:
        if str(parent) in fps:
            return fps[str(parent)]
    return None

def contract(task):
    return {
        "request": task["original_request"], "amendments": task["amendments"],
        "requirements": task["requirements"],
        "work": [{k: w.get(k) for k in ("id", "req", "text", "deps", "owns", "removed")} for w in task["work"]],
        "checks": [dict(check_definition(c), removed=bool(c.get("removed"))) for c in task["checks"]],
        "attest_only": [r["id"] for r in task["requirements"] if r.get("attest_only")],
        "findings": [{k: f.get(k) for k in ("id", "text", "location", "origin")} for f in task["findings"]],
    }

def contract_digest(task):
    return digest(contract(task))

def task_candidates(task):
    """Every tree the task's evidence is bound to, with the declared inputs of the checks
    that test it. The task root is always present."""
    root = str(Path(task["root"]))
    found = {root: set()}
    for c in live(task["checks"]):
        found.setdefault(check_candidate(c, root), set()).update(c.get("inputs", []))
    return {path: sorted(inputs) for path, inputs in found.items()}

def task_snapshot(task):
    return {path: snapshot(Path(path), inputs) for path, inputs in task_candidates(task).items()}

def task_fingerprint(task):
    """Per-candidate fingerprints keyed by absolute path. A check's evidence is bound to
    the tree it actually tested, so an edit in a shared checkout does not invalidate green
    runs taken against a worktree."""
    return {path: snap["fingerprint"] for path, snap in task_snapshot(task).items()}

def source_digest(fps):
    return digest(fps)

def validation_errors(t):
    errors = []
    if not isinstance(t, dict) or t.get("schema") != SCHEMA:
        return ["unsupported task schema; use the explicit migration command"]
    required = ["task_id", "root", "state", "original_request", "authorization", "amendments", "requirements", "work", "checks", "findings", "blockers", "uncertain", "sessions", "events"]
    if any(k not in t for k in required):
        return ["task is missing required fields"]
    if t["state"] not in TASK_STATES:
        errors.append("invalid task state")
    if not isinstance(t["root"], str) or not Path(t["root"]).is_absolute():
        errors.append("invalid project root")
    if not str(t["original_request"]).strip() or not str(t["authorization"]).strip():
        errors.append("original request and activation authority are required")
    groups = {"requirements": "R", "work": "W", "checks": "A", "findings": "F", "blockers": "B", "uncertain": "U"}
    for group, prefix in groups.items():
        rows = t[group]
        if not isinstance(rows, list):
            return [f"invalid {group} array"]
        ids = []
        for row in rows:
            if not isinstance(row, dict) or not re.fullmatch(prefix + r"-\d{2,}", str(row.get("id", ""))):
                return [f"invalid {group} record/ID"]
            ids.append(row["id"])
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate IDs in {group}")
    if errors:
        return errors
    reqs = {r["id"] for r in t["requirements"]}
    works = {w["id"] for w in t["work"]}
    checks = {c["id"] for c in t["checks"]}
    findings = {f["id"] for f in t["findings"]}
    for r in t["requirements"]:
        if not str(r.get("text", "")).strip() or not str(r.get("anchor", "")).strip():
            errors.append(f"{r['id']}: text and request anchor required")
        if r.get("status") not in ("active", "removed"):
            errors.append(f"{r['id']}: invalid requirement state")
        if r.get("status") == "removed" and not r.get("authority"):
            errors.append(f"{r['id']}: removal has no operator authority")
        if r.get("attest_only") and not str(r.get("attest_only_authority", "")).strip():
            errors.append(f"{r['id']}: attested-only acceptance has no operator authority")
    deps = {}
    for w in t["work"]:
        if w.get("req") not in reqs or w.get("status") not in WORK_STATES or not str(w.get("text", "")).strip():
            errors.append(f"{w['id']}: invalid requirement, work state, or text")
        if w.get("removed") and not str(w.get("removed_reason", "")).strip():
            errors.append(f"{w['id']}: superseded work has no recorded rationale")
        d = w.get("deps", [])
        if not isinstance(d, list) or any(x not in works for x in d):
            errors.append(f"{w['id']}: missing dependency")
            d = []
        if len(d) != len(set(d)):
            errors.append(f"{w['id']}: duplicate dependencies")
        deps[w["id"]] = set(d)
    # Iterative dependency validation; long task graphs must not overflow a stack.
    dependents = {w: [] for w in works}
    degrees = {w: len(ds) for w, ds in deps.items()}
    for w, ds in deps.items():
        for d in ds:
            dependents[d].append(w)
    q = deque(w for w, n in degrees.items() if not n)
    visited = 0
    while q:
        w = q.popleft(); visited += 1
        for child in dependents[w]:
            degrees[child] -= 1
            if degrees[child] == 0:
                q.append(child)
    if visited != len(works):
        errors.append("work dependency cycle")
    for c in t["checks"]:
        if c.get("req") not in reqs or c.get("method") not in ("command", "review", "browser", "manual"):
            errors.append(f"{c['id']}: invalid check owner or method")
        if not isinstance(c.get("work"), list) or not c["work"] or any(w not in works for w in c["work"]):
            errors.append(f"{c['id']}: explicit work mapping required")
        elif any(get(t["work"], w)["req"] != c["req"] for w in c["work"]):
            errors.append(f"{c['id']}: work belongs to another requirement")
        if not str(c.get("expect", "")).strip():
            errors.append(f"{c['id']}: missing behavioral expectation")
        if c.get("removed") and not str(c.get("removed_reason", "")).strip():
            errors.append(f"{c['id']}: superseded check has no recorded rationale")
        if c.get("method") == "command" and str(c.get("attested_because") or "").strip():
            errors.append(f"{c['id']}: a command check does not take an attestation rationale")
        if c.get("method") == "command":
            if not c.get("needs_review") and (not str(c.get("command") or "").strip() or not str(c.get("match") or "").strip()):
                errors.append(f"{c['id']}: command and literal success match required")
            if not isinstance(c.get("timeout"), (int, float)) or isinstance(c.get("timeout"), bool) or not 0 < c["timeout"] <= 604800:
                errors.append(f"{c['id']}: invalid timeout")
            if not isinstance(c.get("max_output"), int) or not 1024 <= c["max_output"] <= 1048576:
                errors.append(f"{c['id']}: invalid output limit")
            if c.get("regression") and (not str(c.get("red_match", "")).strip() or not 1 <= c.get("red_exit", 0) <= 125):
                errors.append(f"{c['id']}: regression requires an intentional failure match and exit 1..125")
        if c.get("candidate") is not None and (not isinstance(c["candidate"], str) or not Path(c["candidate"]).is_absolute()):
            errors.append(f"{c['id']}: candidate must be an absolute path")
        tags = c.get("exclusive") or []
        if not isinstance(tags, list) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", x) for x in tags):
            errors.append(f"{c['id']}: exclusive resource tags must be identifiers")
        if c.get("status") not in ("NOT_RUN", "PASS", "FAIL"):
            errors.append(f"{c['id']}: unsupported check state (required skips cannot pass)")
    for f in t["findings"]:
        if f.get("status") not in FINDING_STATES:
            errors.append(f"{f['id']}: invalid finding disposition")
        if not str(f.get("location", "")).strip() or not str(f.get("text", "")).strip():
            errors.append(f"{f['id']}: finding text and location required")
        if f.get("status") == "duplicate" and f.get("duplicate") not in findings:
            errors.append(f"{f['id']}: missing canonical finding")
        if any(x not in works for x in f.get("work", [])) or any(x not in checks for x in f.get("checks", [])):
            errors.append(f"{f['id']}: missing remediation work or check")
    all_ids = reqs | works | checks | findings | {"task"}
    for b in t["blockers"]:
        if b.get("item") not in all_ids or not all(str(b.get(x, "")).strip() for x in ("text", "owner", "unblock", "proof")):
            errors.append(f"{b['id']}: incomplete concrete blocker")
    return errors

def legacy_receipt(c):
    """A receipt written before 0.5.0: it carries no candidate, and its source is the task
    root fingerprint of that release, whatever tree the check's cwd named."""
    r = c.get("receipt") or {}
    return bool(r) and "candidate" not in r

def _legacy_source_current(r, fp, root):
    """Whether a pre-0.5.0 receipt still holds the guarantee that release gave it: the task
    root is unchanged. Root-bound checks rebind exactly; a worktree check keeps 0.4.x
    semantics (blind to the worktree) until it is rerun, and the report says so."""
    if not isinstance(fp, dict):
        return r.get("source") == fp
    if root is not None:
        return r.get("source") == fp.get(str(Path(root)))
    return r.get("source") in fp.values()

def missing_inputs(c):
    """Declared inputs of one check that no longer exist on disk."""
    found = []
    for item in c.get("inputs", []) or []:
        path = Path(item)
        if not path.is_absolute():
            path = Path(c["cwd"]) / path
        if not path.exists() and not path.is_symlink():
            found.append(str(path))
    return found

def acceptance_reason(task_dir, c, fp, root=None):
    """None when the check's evidence is currently accepted; otherwise one specific reason.
    A reader must never have to guess between not run, failed, stale, unapproved and
    predates-the-upgrade: each is a different next action."""
    if c.get("removed"):
        return "superseded"
    if c.get("needs_review"):
        return "definition edited; inspect, approve and rerun"
    lost = missing_inputs(c)
    if lost:
        return (f"declared input missing: {', '.join(lost)}; restore it, or dmd check edit --id {c['id']} "
                "--input <file> (or --clear-inputs) and rerun")
    r = c.get("receipt") or {}
    if c.get("status") == "NOT_RUN":
        return "not run"
    if c.get("status") != "PASS":
        if r.get("stale") or r.get("failure") == "CANDIDATE_OR_DEFINITION_CHANGED":
            return "STALE: passed while its candidate or definition moved; rerun"
        return "FAIL: " + (str(r.get("failure")) if r.get("failure") else "exit or match failed") + "; fix and rerun"
    if r.get("definition") != digest(check_definition(c)):
        return "definition edited since the receipt; approve and rerun"
    if r.get("source") != source_for(fp, c):
        cand = check_candidate(c, root)
        if legacy_receipt(c):
            if not _legacy_source_current(r, fp, root):
                return f"receipt predates 0.5.0 candidate binding and the task root has since changed; rerun to bind it to {cand}"
        else:
            head = r.get("head") or "no-head"
            return f"stale: {cand} changed since the receipt (tested @ {head}); rerun"
    if not evidence_ok(task_dir, r.get("artifact")):
        return "evidence artifact missing or altered; rerun"
    if c["method"] == "command":
        if not (r.get("kind") == "command" and r.get("exit") == 0 and r.get("matched") is True and r.get("failure") is None):
            return "receipt is not a clean command pass; rerun"
    elif not (r.get("kind") == c["method"] and r.get("note")):
        return "attestation has no recorded observation"
    return None

def accepted(task_dir, c, fp, root=None):
    return acceptance_reason(task_dir, c, fp, root) is None

def red_ok(task_dir, c):
    red = c.get("red") or {}
    if red.get("definition") == digest(check_definition(c)) and red.get("matched") is True and red.get("failure") is None and red.get("exit") == c.get("red_exit") and evidence_ok(task_dir, red.get("artifact")):
        return True
    baseline = c.get("baseline") or {}
    return bool(baseline.get("reason")) and baseline.get("definition") == digest(check_definition(c)) and evidence_ok(task_dir, baseline.get("artifact"))

def work_ok(task_dir, t, w, fp):
    if w.get("removed"):
        return False
    cs = [c for c in live(t["checks"]) if w["id"] in c["work"]]
    return w["status"] == "verified" and bool(cs) and all(accepted(task_dir, c, fp, t["root"]) for c in cs)

def work_owed(task_dir, t, w, fp):
    """The IDs of the mapped checks whose evidence is not current for this work item."""
    return [c["id"] for c in live(t["checks"]) if w["id"] in c["work"] and not accepted(task_dir, c, fp, t["root"])]

def requirement_attestation(task_dir, t, rid, fp):
    """Split a requirement's currently accepted checks into executed and attested."""
    cs = [c for c in live(t["checks"]) if c["req"] == rid and accepted(task_dir, c, fp, t["root"])]
    return [c for c in cs if not attested(c)], [c for c in cs if attested(c)]

EVIDENCE_IDENTITY = ("kind", "source", "definition", "candidate", "exit", "matched", "failure", "note", "stale", "reason")

def evidence_identity(record):
    """What a review actually vouched for in a receipt, red run or baseline: the tree and
    definition it was bound to and the outcome. Not the timestamp, duration or log path:
    rerunning the same command on a byte-identical candidate is the same evidence."""
    if not record:
        return None
    return {k: record.get(k) for k in EVIDENCE_IDENTITY if record.get(k) is not None}

def review_signature(t, fp):
    """The review stays valid while the contract, the source map and the accepted evidence
    are what it was recorded against. A rerun that reproduces the same pass on the same
    fingerprints does not owe a second review; a moved tree or a changed outcome does."""
    return digest({"contract": contract_digest(t), "source": fp,
                   "work": [{"id": w["id"], "status": w["status"], "removed": bool(w.get("removed"))} for w in t["work"]],
                   "checks": [{"id": c["id"], "status": c["status"], "removed": bool(c.get("removed")),
                               "receipt": evidence_identity(c.get("receipt")), "red": evidence_identity(c.get("red")),
                               "baseline": evidence_identity(c.get("baseline"))} for c in t["checks"]],
                   "findings": t["findings"], "blockers": t["blockers"], "uncertain": t["uncertain"]})

def unresolved_findings(task_dir, t, fp):
    unresolved = {}
    resolved = set()
    rows = {f["id"]: f for f in t["findings"]}
    for fid in rows:
        chain, seen, current = [], set(), fid
        reason = None
        while current not in resolved:
            if current in unresolved:
                reason = unresolved[current]; break
            if current in seen:
                reason = "duplicate cycle"; break
            seen.add(current); chain.append(current)
            f = rows.get(current)
            if not f:
                reason = "missing duplicate target"; break
            status = f["status"]
            if status == "duplicate":
                if not f.get("note"):
                    reason = "duplicate needs rationale"; break
                current = f.get("duplicate"); continue
            if status == "deferred":
                if not f.get("note") or not str(f.get("authority") or "").strip():
                    reason = "deferral needs a rationale and recorded operator authority"
                break
            if status == "disproved":
                bound = (source_digest(fp), fp.get(str(Path(t["root"])))) if isinstance(fp, dict) else (fp,)
                if not f.get("note") or f.get("source") not in bound or not evidence_ok(task_dir, f.get("artifact")):
                    reason = "disproof needs evidence"
                break
            if status == "fixed-verified":
                ws, cs = f.get("work", []), f.get("checks", [])
                if not ws or not cs or not f.get("note"):
                    reason = "fix needs explicit remediation work, checks and rationale"; break
                if not all(work_ok(task_dir, t, get(t["work"], w), fp) for w in ws):
                    reason = "remediation work not currently verified"; break
                if not all(accepted(task_dir, get(t["checks"], c), fp, t["root"]) for c in cs):
                    reason = "remediation checks not currently accepted"; break
                regressions = [get(t["checks"], c) for c in cs if get(t["checks"], c).get("regression")]
                if not regressions or not all(red_ok(task_dir, c) for c in regressions):
                    reason = "regression needs a meaningful red baseline or documented evidence-backed limitation"
                break
            reason = f"finding remains {status}"; break
        for entry in chain:
            if reason:
                unresolved[entry] = reason
            else:
                resolved.add(entry)
    return unresolved

def next_actions(task_dir, t, fp):
    blocked = {b["item"] for b in t["blockers"] if not b.get("resolved")}
    if t["state"] in ("PAUSED", "CANCELLED") or "task" in blocked:
        return []
    result = []
    for u in t["uncertain"]:
        if not u.get("resolved"):
            result.append({"id": u["id"], "action": "reconcile external outcome before retry"})
    active = {r["id"] for r in t["requirements"] if r["status"] == "active"}
    attempts = t.get("attempts") or {}
    for w in live(t["work"]):
        if w["req"] not in active or w["id"] in blocked or w["req"] in blocked:
            continue
        if any(not work_ok(task_dir, t, get(t["work"], d), fp) for d in w["deps"]):
            continue
        if not work_ok(task_dir, t, w, fp):
            owed = work_owed(task_dir, t, w, fp)
            if w["status"] != "verified":
                action = "implement/verify" + (f" (checks owed: {', '.join(owed)})" if owed else "")
            else:
                action = "rerun stale evidence: " + (", ".join(owed) or "no mapped check")
            if repeated_attempts(attempts.get(w["id"], [])):
                action += "; equivalent attempts already failed here — change diagnostic strategy before retrying"
            result.append({"id": w["id"], "action": action})
    for f in t["findings"]:
        if f["id"] not in blocked and f["status"] in ("suspected", "confirmed", "fixed-unverified"):
            result.append({"id": f["id"], "action": "investigate/remediate and verify"})
    for rid in sorted(active):
        executed, attested_checks = requirement_attestation(task_dir, t, rid, fp)
        if attested_checks and not executed and not get(t["requirements"], rid).get("attest_only"):
            result.append({"id": rid, "action": "add an executed acceptance check, or record operator authority via req attest-only"})
    return result

def repeated_attempts(history):
    """Two equivalent failures anywhere in the recent history, not only back to back."""
    recent = [a.get("signature") for a in history[-6:]]
    return any(recent.count(s) >= 2 for s in set(recent) if s)

def gate(task_dir, t, fp=None, require_review=True):
    errors = validation_errors(t)
    if errors:
        return {"status": "INVALID", "reasons": errors, "next": []}
    if t["state"] in ("CANCELLED", "PAUSED"):
        return {"status": t["state"], "reasons": ["execution suspended; obligations are not complete"], "next": [],
                "summary": {"headline": f"task is {t['state']}" + (f": {t.get('state_reason')}" if t.get("state_reason") else ""),
                            "rerun": None}}
    try:
        fp = fp or task_fingerprint(t)
    except MissingCandidate as exc:
        return {"status": "BLOCKED", "reasons": [str(exc)], "next": [],
                "summary": {"headline": "a candidate tree is missing", "rerun": None}}
    reasons = []
    cov = t.get("coverage") or {}
    if cov.get("digest") != contract_digest(t):
        reasons.append("coverage: reconcile the original request and current obligation inventory")
    active = {r["id"] for r in t["requirements"] if r["status"] == "active"}
    if not active:
        reasons.append("no active requirements; an empty ledger cannot complete")
    executed_total = attested_total = 0
    for rid in sorted(active):
        ws = [w for w in live(t["work"]) if w["req"] == rid]
        cs = [c for c in live(t["checks"]) if c["req"] == rid]
        if not ws or not cs:
            reasons.append(f"{rid}: missing work or acceptance mapping")
        executed, attested_checks = requirement_attestation(task_dir, t, rid, fp)
        executed_total += len(executed)
        attested_total += len(attested_checks)
        # Only fires when attestation is what carried the requirement. A requirement with
        # nothing accepted yet already reports its own stale/missing evidence reasons.
        if attested_checks and not executed and not get(t["requirements"], rid).get("attest_only"):
            reasons.append(f"{rid}: every accepted check is self-attested; add an executed check "
                           f"or record operator authority with req attest-only")
        for w in ws:
            if not work_ok(task_dir, t, w, fp):
                owed = work_owed(task_dir, t, w, fp)
                if w["status"] != "verified":
                    reasons.append(f"{w['id']}: status {w['status']}, not verified" + (f"; checks owed: {', '.join(owed)}" if owed else ""))
                elif owed:
                    reasons.append(f"{w['id']}: verified, but evidence is not current for {', '.join(owed)}")
                else:
                    reasons.append(f"{w['id']}: verified with no mapped acceptance check")
            if any(not work_ok(task_dir, t, get(t["work"], d), fp) for d in w["deps"]):
                reasons.append(f"{w['id']}: dependency is not currently verified")
        for c in cs:
            reason = acceptance_reason(task_dir, c, fp, t["root"])
            if reason:
                reasons.append(f"{c['id']}: {reason}")
            if c.get("regression") and not red_ok(task_dir, c):
                reasons.append(f"{c['id']}: meaningful baseline evidence missing")
    for fid, reason in unresolved_findings(task_dir, t, fp).items():
        reasons.append(f"{fid}: {reason}")
    for b in t["blockers"]:
        if not b.get("resolved"):
            reasons.append(f"{b['id']}: unresolved prerequisite for {b['item']}")
    for u in t["uncertain"]:
        if not u.get("resolved"):
            reasons.append(f"{u['id']}: unknown external outcome")
    if t.get("running"):
        reasons.append("running: verification still active or its outcome needs recovery")
    if require_review:
        r = t.get("review") or {}
        if r.get("signature") != review_signature(t, fp) or not evidence_ok(task_dir, r.get("artifact")):
            reasons.append("review: final request/diff/integration review missing or stale")
        elif t.get("require_independent_review") and r.get("kind") != "independent":
            reasons.append("review: independent review required; self-review is not independent")
    actions = next_actions(task_dir, t, fp)
    blocked = bool([b for b in t["blockers"] if not b.get("resolved")])
    status = "COMPLETE" if not reasons else ("BLOCKED" if blocked and not actions else "ACTIVE")
    review_owed = any(r.startswith("review:") for r in reasons)
    return {"status": status, "reasons": reasons, "next": actions, "source": fp,
            "attestation": {"executed": executed_total, "self_attested": attested_total},
            "summary": gate_summary(task_dir, t, fp, review_owed)}

def gate_summary(task_dir, t, fp, review_owed):
    """The gate's reasons grouped by cause, so twenty-five stale lines read as one fact
    and the reader gets the command that discharges it."""
    groups = {"accepted": [], "stale": [], "failed": [], "not_run": [], "unapproved": [], "legacy": [], "missing_inputs": [], "other": []}
    for c in live(t["checks"]):
        reason = acceptance_reason(task_dir, c, fp, t["root"])
        if reason is None:
            groups["legacy" if legacy_receipt(c) else "accepted"].append(c["id"])
        elif reason == "not run":
            groups["not_run"].append(c["id"])
        elif reason.startswith("declared input missing"):
            groups["missing_inputs"].append(c["id"])
        elif reason.startswith("FAIL"):
            groups["failed"].append(c["id"])
        elif "approve" in reason:
            groups["unapproved"].append(c["id"])
        elif reason.startswith(("stale", "STALE", "receipt predates")):
            groups["stale"].append(c["id"])
        else:
            groups["other"].append(c["id"])
    rerun = groups["stale"] + groups["failed"] + groups["not_run"]
    unverified = [w["id"] for w in live(t["work"]) if not work_ok(task_dir, t, w, fp)]
    open_findings = sorted(unresolved_findings(task_dir, t, fp))
    open_blockers = [b["id"] for b in t["blockers"] if not b.get("resolved")]
    parts = []
    if groups["stale"]:
        parts.append(f"{len(groups['stale'])} check(s) stale")
    if groups["failed"]:
        parts.append(f"{len(groups['failed'])} check(s) failed")
    if groups["not_run"]:
        parts.append(f"{len(groups['not_run'])} check(s) not run")
    if groups["unapproved"]:
        parts.append(f"{len(groups['unapproved'])} check(s) need approval")
    if groups["missing_inputs"]:
        parts.append(f"{len(groups['missing_inputs'])} check(s) declare a missing input")
    if groups["other"]:
        parts.append(f"{len(groups['other'])} check(s) lack evidence")
    if unverified and not rerun and not groups["unapproved"] and not groups["other"]:
        parts.append(f"{len(unverified)} work item(s) not verified")
    if open_findings:
        parts.append(f"{len(open_findings)} finding(s) open")
    deferred = [f["id"] for f in t["findings"] if f["status"] == "deferred"]
    if deferred:
        parts.append(f"{len(deferred)} finding(s) deferred under operator authority")
    if open_blockers:
        parts.append(f"{len(open_blockers)} blocker(s) open")
    if review_owed:
        parts.append("final review owed")
    headline = "; ".join(parts) or "all obligations satisfied"
    return {"headline": headline, "checks": groups, "work_unverified": unverified,
            "findings_open": open_findings, "findings_deferred": deferred, "blockers_open": open_blockers,
            "review": "owed" if review_owed else "current",
            "rerun": ("dmd run " + " ".join(rerun)) if rerun else None}

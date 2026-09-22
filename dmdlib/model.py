"""Pure state validation plus current-evidence checks. Only this module computes completion."""
from __future__ import annotations
import re
from collections import deque
from pathlib import Path
from .storage import DmdError, digest, evidence_ok
from .source import file_digest, snapshot, MissingCandidate

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
OPTIONAL_CHECK_FIELDS = ("candidate", "exclusive", "writes")
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

# An explicit list item in the request: "- x", "* x", "1. x", "2) x", "a) x".
LIST_ITEM = re.compile(r"^\s{0,8}(?:[-*+\u2022]|\d{1,3}[.)]|[a-z]\))\s+(\S.*)$")
MAX_CLAUSES = 100

def extract_clauses(text, first=1, source="request"):
    """The explicit list items of a request or amendment, each a candidate outcome the
    operator enumerated. Fenced code is skipped; prose is not segmented."""
    found, fenced = [], False
    for line in str(text).splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = None if fenced else LIST_ITEM.match(line)
        if m and len(found) < MAX_CLAUSES:
            found.append({"id": f"C-{first + len(found):02d}", "text": m.group(1).strip()[:300], "source": source})
    return found

def unmapped_clauses(t):
    """Request list items with no requirement covering them and no context note."""
    covered = {cid for r in t["requirements"] for cid in r.get("covers") or []}
    return [c for c in t.get("clauses") or [] if c["id"] not in covered and not str(c.get("context") or "").strip()]

def contract(task):
    extra = {}
    if "clauses" in task:
        # Present only on tasks created since 0.7.0, so older contracts keep their digest.
        extra["clauses"] = [{k: c.get(k) for k in ("id", "text", "context")} for c in task["clauses"]]
    return {**extra,
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

def candidate_outputs(task):
    """Output globs declared by live checks, per candidate. They leave the candidate's
    fingerprint for every check on it: a generated report is not source."""
    found = {}
    for c in live(task["checks"]):
        if c.get("writes"):
            found.setdefault(check_candidate(c, task["root"]), set()).update(c["writes"])
    return {path: sorted(globs) for path, globs in found.items()}

def task_snapshot(task):
    outputs = candidate_outputs(task)
    return {path: snapshot(Path(path), inputs, exclude=outputs.get(path, ()))
            for path, inputs in task_candidates(task).items()}

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
    if not isinstance(t["amendments"], list):
        return ["invalid amendments array"]
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
        if not isinstance(c.get("work"), list) or any(w not in works for w in c["work"]):
            errors.append(f"{c['id']}: invalid work mapping")
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
        writes = c.get("writes") or []
        if not isinstance(writes, list) or any(not isinstance(x, str) or not x.strip() for x in writes):
            errors.append(f"{c['id']}: output globs must be non-empty strings")
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
    clauses = t.get("clauses", [])
    if not isinstance(clauses, list) or any(not isinstance(c, dict) or not re.fullmatch(r"C-\d{2,}", str(c.get("id", "")))
                                            or not str(c.get("text", "")).strip() for c in clauses):
        errors.append("invalid request list items")
    else:
        clause_ids = {c["id"] for c in clauses}
        for r in t["requirements"]:
            covers = r.get("covers", [])
            if not isinstance(covers, list) or any(x not in clause_ids for x in covers):
                errors.append(f"{r['id']}: covers an unknown request item")
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

def check_verdict(task_dir, c, fp, root=None):
    """(None, None) when the check's evidence is currently accepted; otherwise a typed cause
    and one specific reason. A reader must never have to guess between not run, failed,
    stale, edited and predates-the-upgrade: each is a different next action."""
    if c.get("removed"):
        return "superseded", "superseded"
    if c.get("needs_review"):
        return "edited", "definition edited; inspect, approve and rerun"
    lost = missing_inputs(c)
    if lost:
        return "missing_inputs", (f"declared input missing: {', '.join(lost)}; restore it, or dmd check edit --id {c['id']} "
                                  "--input <file> (or --clear-inputs) and rerun")
    r = c.get("receipt") or {}
    if c.get("status") == "NOT_RUN":
        return "not_run", "not run"
    if c.get("status") != "PASS":
        if r.get("stale") or r.get("failure") == "CANDIDATE_OR_DEFINITION_CHANGED":
            return "stale", "STALE: passed while its candidate or definition moved; rerun"
        return "failed", "FAIL: " + (str(r.get("failure")) if r.get("failure") else "exit or match failed") + "; fix and rerun"
    if r.get("definition") != digest(check_definition(c)):
        return "edited", "definition edited since the receipt; approve and rerun"
    if r.get("source") != source_for(fp, c):
        cand = check_candidate(c, root)
        if legacy_receipt(c):
            if not _legacy_source_current(r, fp, root):
                return "stale", f"receipt predates 0.5.0 candidate binding and the task root has since changed; rerun to bind it to {cand}"
        else:
            head = r.get("head") or "no-head"
            return "stale", f"stale: {cand} changed since the receipt (tested @ {head}); rerun"
    if not evidence_ok(task_dir, r.get("artifact")):
        return "other", "evidence artifact missing or altered; rerun"
    if c["method"] == "command":
        if not (r.get("kind") == "command" and r.get("exit") == 0 and r.get("matched") is True and r.get("failure") is None):
            return "other", "receipt is not a clean command pass; rerun"
    elif not (r.get("kind") == c["method"] and r.get("note")):
        return "other", "attestation has no recorded observation"
    return None, None

def acceptance_reason(task_dir, c, fp, root=None):
    return check_verdict(task_dir, c, fp, root)[1]

def accepted(task_dir, c, fp, root=None):
    return acceptance_reason(task_dir, c, fp, root) is None

def red_ok(task_dir, c):
    red = c.get("red") or {}
    if red.get("definition") == digest(check_definition(c)) and red.get("matched") is True and red.get("failure") is None and red.get("exit") == c.get("red_exit") and evidence_ok(task_dir, red.get("artifact")):
        return True
    baseline = c.get("baseline") or {}
    return bool(baseline.get("reason")) and baseline.get("definition") == digest(check_definition(c)) and evidence_ok(task_dir, baseline.get("artifact"))

def work_ok(task_dir, t, w, fp):
    return assess(task_dir, t, fp).work_ok(w["id"])

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

def repeated_attempts(history):
    """Two equivalent failures anywhere in the recent history, not only back to back."""
    recent = [a.get("signature") for a in history[-6:]]
    return any(recent.count(s) >= 2 for s in set(recent) if s)

SUSPENDED = ("PAUSED", "CANCELLED")
OPEN_FINDING_STATES = ("suspected", "confirmed", "fixed-unverified")


class Assessment:
    """One evaluation of a task against one source map. Every check, red baseline, work
    item and finding is judged exactly once; the gate verdict, `next`, the summary, the
    report and the Stop hook's progress digest are all views of this object, so they cannot
    disagree about what is owed.

    Each open reason is owned by one item. A reason whose owner waits on an unresolved
    blocker (directly, through its requirement, or through a prerequisite) is `waiting`;
    every other reason is actionable. BLOCKED means an open blocker exists and nothing is
    actionable. That is the only state besides COMPLETE in which the Stop hook lets an
    agent end its turn, so it must never hide executable work."""

    def __init__(self, task_dir, t, fp):
        self.task_dir, self.t, self.fp = task_dir, t, fp
        root = t["root"]
        self.verdicts = {c["id"]: check_verdict(task_dir, c, fp, root) for c in t["checks"]}
        self.red = {c["id"]: red_ok(task_dir, c) for c in live(t["checks"]) if c.get("regression")}
        self.active = sorted(r["id"] for r in t["requirements"] if r["status"] == "active")
        self._work_ok = {}
        for w in t["work"]:
            self.work_ok(w["id"])
        self.findings = self._findings()
        self.blocked = self._blocked_items()
        self.reasons = []  # (owner, text); owner "review" is appended last
        self._collect()

    # Per-item predicates -------------------------------------------------------------
    def accepted(self, cid):
        return self.verdicts[cid][0] is None

    def mapped_checks(self, wid):
        return [c for c in live(self.t["checks"]) if wid in c["work"]]

    def work_owed(self, wid):
        return [c["id"] for c in self.mapped_checks(wid) if not self.accepted(c["id"])]

    def work_ok(self, wid):
        """A work item is done when it has at least one mapped check and every mapped check
        is currently accepted. Its stored status is the agent's progress note, not proof."""
        if wid not in self._work_ok:
            w = get(self.t["work"], wid)
            cs = self.mapped_checks(wid)
            self._work_ok[wid] = not w.get("removed") and bool(cs) and all(self.accepted(c["id"]) for c in cs)
        return self._work_ok[wid]

    def requirement_attestation(self, rid):
        cs = [c for c in live(self.t["checks"]) if c["req"] == rid and self.accepted(c["id"])]
        return [c for c in cs if not attested(c)], [c for c in cs if attested(c)]

    def _findings(self):
        """Unresolved findings and why. Duplicates resolve through their canonical finding."""
        t = self.t
        unresolved, resolved = {}, set()
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
                    fp = self.fp
                    binding = (f.get("binding") or {}).get("files")
                    if binding:
                        # Bound to the files it examined: an edit elsewhere leaves it standing.
                        moved = [Path(p).name for p, d in sorted(binding.items()) if file_digest(p) != d]
                    else:
                        # Recorded before 0.7.0, or with no resolvable location: the whole tree.
                        bound = (source_digest(fp), fp.get(str(Path(t["root"])))) if isinstance(fp, dict) else (fp,)
                        moved = [] if f.get("source") in bound else ["the source tree"]
                    if not f.get("note") or not evidence_ok(self.task_dir, f.get("artifact")):
                        reason = "disproof needs evidence"
                    elif moved:
                        reason = f"disproof is stale: {', '.join(moved)} changed since it was recorded; re-examine and record it again"
                    elif f.get("confirmed_at") and not any(self.accepted(c) and not attested(get(t["checks"], c)) for c in f.get("checks", [])):
                        # Confirmation was evidence of a defect; retracting it takes an executed
                        # check showing the invariant holds, not a note and an arbitrary file.
                        reason = "a confirmed finding is disproved only by an accepted executed check (finding set --check A-XX)"
                    break
                if status == "fixed-verified":
                    ws, cs = f.get("work", []), f.get("checks", [])
                    if not cs or not f.get("note"):
                        reason = "fix needs explicit remediation checks and rationale"; break
                    if not all(self.work_ok(w) for w in ws):
                        reason = "remediation work not currently verified"; break
                    if not all(self.accepted(c) for c in cs):
                        reason = "remediation checks not currently accepted"; break
                    regressions = [c for c in cs if get(t["checks"], c).get("regression")]
                    if not regressions or not all(self.red.get(c) for c in regressions):
                        reason = "regression needs a meaningful red baseline or documented evidence-backed limitation"
                    break
                reason = f"finding remains {status}"; break
            for entry in chain:
                if reason:
                    unresolved[entry] = reason
                else:
                    resolved.add(entry)
        return unresolved

    def _blocked_items(self):
        """Items that wait on an unresolved blocker: blocked directly, through their
        requirement, or through a prerequisite (transitively)."""
        t = self.t
        direct = {b["item"] for b in t["blockers"] if not b.get("resolved")}
        if "task" in direct:
            return {"*"}
        waiting = set(direct)
        works = {w["id"]: w for w in t["work"]}
        changed = True
        while changed:
            changed = False
            for w in works.values():
                if w["id"] not in waiting and (w["req"] in waiting or any(d in waiting for d in w["deps"])):
                    waiting.add(w["id"]); changed = True
        for c in t["checks"]:
            if c["req"] in waiting or any(w in waiting for w in c["work"]):
                waiting.add(c["id"])
        for f in t["findings"]:
            if any(w in waiting for w in f.get("work", [])) or any(c in waiting for c in f.get("checks", [])):
                waiting.add(f["id"])
        return waiting

    def waits(self, owner):
        return "*" in self.blocked or owner in self.blocked

    # Reasons ---------------------------------------------------------------------------
    def _add(self, owner, text):
        self.reasons.append((owner, text))

    def _collect(self):
        t = self.t
        cov = t.get("coverage") or {}
        loose = unmapped_clauses(t)
        if loose:
            named = "; ".join(f"{c['id']} {c['text'][:60]!r}" for c in loose[:5]) + (f" (+{len(loose) - 5} more)" if len(loose) > 5 else "")
            self._add("coverage", f"coverage: request items with no requirement: {named}")
        elif cov.get("digest") != contract_digest(t):
            self._add("coverage", "coverage: reconcile the original request and current obligation inventory")
        if not self.active:
            self._add("task", "no active requirements; an empty ledger cannot complete")
        self.executed_total = self.attested_total = 0
        for rid in self.active:
            ws = [w for w in live(t["work"]) if w["req"] == rid]
            cs = [c for c in live(t["checks"]) if c["req"] == rid]
            if not cs:
                self._add(rid, f"{rid}: missing acceptance check")
            executed, attested_checks = self.requirement_attestation(rid)
            self.executed_total += len(executed)
            self.attested_total += len(attested_checks)
            # Only fires when attestation is what carried the requirement. A requirement with
            # nothing accepted yet already reports its own stale/missing evidence reasons.
            if attested_checks and not executed and not get(t["requirements"], rid).get("attest_only"):
                self._add(rid, f"{rid}: every accepted check is self-attested; add an executed check "
                               f"or record operator authority with req attest-only")
            for w in ws:
                if not self.work_ok(w["id"]):
                    owed = self.work_owed(w["id"])
                    self._add(w["id"], f"{w['id']}: checks owed: {', '.join(owed)}" if owed
                              else f"{w['id']}: no mapped acceptance check; map one with check add/edit --work {w['id']}, or supersede it")
                if any(not self.work_ok(d) for d in w["deps"]):
                    self._add(w["id"], f"{w['id']}: dependency is not currently verified")
            for c in cs:
                code, reason = self.verdicts[c["id"]]
                if reason:
                    self._add(c["id"], f"{c['id']}: {reason}")
                if c.get("regression") and not self.red.get(c["id"]):
                    self._add(c["id"], f"{c['id']}: meaningful baseline evidence missing")
        for fid, reason in self.findings.items():
            self._add(fid, f"{fid}: {reason}")
        for b in t["blockers"]:
            if not b.get("resolved"):
                self._add(b["id"], f"{b['id']}: unresolved prerequisite for {b['item']}")
        for u in t["uncertain"]:
            if not u.get("resolved"):
                self._add(u["id"], f"{u['id']}: unknown external outcome")
        if t.get("running"):
            self._add("running", "running: verification still active or its outcome needs recovery")

    def review_reason(self, require_review=True):
        if not require_review:
            return None
        r = self.t.get("review") or {}
        if r.get("signature") != review_signature(self.t, self.fp) or not evidence_ok(self.task_dir, r.get("artifact")):
            return "review: final request/diff/integration review missing or stale"
        if self.t.get("require_independent_review") and r.get("kind") != "independent":
            return "review: independent review required; self-review is not independent"
        return None

    def actionable(self):
        """Owners of reasons the agent can act on now. Blocker records themselves are what
        waits; the final review is actionable only once nothing else is open."""
        open_blockers = {b["id"] for b in self.t["blockers"] if not b.get("resolved")}
        return [(owner, text) for owner, text in self.reasons if owner not in open_blockers and not self.waits(owner)]

    # Views -----------------------------------------------------------------------------
    def next_actions(self):
        """Dependency-ready actions, one per item, for every actionable reason."""
        t = self.t
        if t["state"] in SUSPENDED or self.waits("task"):
            return []
        owners = []
        for owner, _ in self.actionable():
            if owner not in owners:
                owners.append(owner)
        attempts = t.get("attempts") or {}
        actionable_owners = set(owners)
        result = []
        for owner in owners:
            if owner == "coverage":
                loose = unmapped_clauses(t)
                if loose:
                    first = loose[0]["id"]
                    action = (f"account for request items {', '.join(c['id'] for c in loose)}: dmd req add '<outcome>' --anchor "
                              f"'<quote>' --covers {first}, or dmd coverage map {first} --req R-XX, or dmd coverage context "
                              f"{first} --note '<why it is not an outcome>' (dmd coverage items lists them)")
                else:
                    action = ("reread the original request against dmd coverage show, then "
                              "dmd coverage assert --note '<which request outcomes map to which R-XX>'")
            elif owner == "task":
                action = "record each requested outcome: dmd req add '<outcome>' --anchor '<quote from the request>'"
            elif owner == "running":
                action = "inspect the interrupted run's effects, then dmd recover-run --proof '<what you verified>'"
            elif owner.startswith("U-"):
                action = f"verify what actually happened before any retry, then dmd uncertain resolve --id {owner} --proof '<evidence>'"
            elif owner.startswith("R-"):
                reqtext = " ".join(text for o, text in self.reasons if o == owner)
                add = (f"dmd check add --req {owner} --cmd '<verifier>' --expect '<behavior>' --match '<token printed after "
                       "its assertions>' --approve '<what you inspected>'")
                action = (add if "missing acceptance check" in reqtext
                          else f"add an executed check ({add}), or record the operator's words: "
                               f"dmd req attest-only --id {owner} --authority '<quote>'")
            elif owner.startswith("F-"):
                action = self.finding_action(owner)
            elif owner.startswith("W-"):
                w = get(t["work"], owner)
                if any(not self.work_ok(d) for d in w["deps"]):
                    continue  # not dependency-ready; its prerequisite carries the action
                owed = self.work_owed(owner)
                if owed and w["status"] == "verified":
                    action = f"rerun stale evidence: dmd run {' '.join(owed)}"
                elif owed:
                    action = f"implement {owner}, then dmd run {' '.join(owed)}"
                else:
                    action = (f"map a check: dmd check add --req {w['req']} --work {owner} ..., "
                              f"or dmd work remove --id {owner} --note '<why it is superseded>'")
            elif owner.startswith("A-"):
                c = get(t["checks"], owner)
                if self.verdicts[owner][0] is not None and any(w in actionable_owners for w in c["work"]):
                    continue  # the mapped work item already names this check
                action = self.check_action(c)
            else:
                continue
            if repeated_attempts(attempts.get(owner, [])):
                action += "; equivalent attempts already failed here — change diagnostic strategy before retrying"
            result.append({"id": owner, "action": action})
        return result

    def check_action(self, c):
        cid = c["id"]
        code, reason = self.verdicts[cid]
        if code in ("not_run", "stale"):
            action = f"dmd run {cid}"
        elif code == "failed":
            action = f"fix the cause ({reason.split(';')[0]}), then dmd run {cid}"
        elif code == "edited":
            action = f"inspect it (dmd preview {cid}), then dmd approve {cid} --note '<what you inspected>' and dmd run {cid}"
        elif code is not None:
            action = reason
        else:
            action = ""
        if c.get("regression") and not self.red.get(cid):
            red = (f"record the red baseline against the faulty code: dmd run {cid} --red (or, when that code is gone, "
                   f"dmd check baseline --id {cid} --note '<why>' --evidence <file>)")
            action = f"{action}; {red}" if action else red
        return action

    def finding_action(self, fid):
        f = get(self.t["findings"], fid)
        reason = self.findings.get(fid, "")
        if f["status"] == "suspected":
            return (f"investigate, then dmd finding set --id {fid} --status confirmed (with the evidence), or --status disproved "
                    f"--note '<what shows the invariant holds>' --evidence <file>")
        if f["status"] in ("confirmed", "fixed-unverified"):
            return (f"fix it with a regression check: dmd check add --req R-XX --cmd '<test>' --expect '<invariant>' --match '<pass token>' "
                    f"--regression --red-match '<failure token>' --approve '<inspected>'; dmd run A-XX --red; fix the root cause; "
                    f"dmd run A-XX; dmd finding set --id {fid} --status fixed-verified --check A-XX --note '<root cause fixed>'")
        return f"resolve {fid}: {reason}"

    def status(self, reasons):
        if not reasons:
            return "COMPLETE"
        blocked = any(not b.get("resolved") for b in self.t["blockers"])
        return "BLOCKED" if blocked and not self.actionable() else "ACTIVE"

    def summary(self, review_owed):
        """The reasons grouped by typed cause, so twenty-five stale lines read as one fact
        and the reader gets the command that discharges it."""
        t = self.t
        groups = {"accepted": [], "stale": [], "failed": [], "not_run": [], "edited": [], "legacy": [], "missing_inputs": [], "other": []}
        for c in live(t["checks"]):
            code = self.verdicts[c["id"]][0]
            if code is None:
                groups["legacy" if legacy_receipt(c) else "accepted"].append(c["id"])
            else:
                groups[code].append(c["id"])
        rerun = groups["stale"] + groups["failed"] + groups["not_run"]
        unverified = [w["id"] for w in live(t["work"]) if w["req"] in self.active and not self.work_ok(w["id"])]
        open_findings = sorted(self.findings)
        open_blockers = [b["id"] for b in t["blockers"] if not b.get("resolved")]
        deferred = [f["id"] for f in t["findings"] if f["status"] == "deferred"]
        parts = []
        loose = unmapped_clauses(t)
        if loose:
            parts.append(f"{len(loose)} request item(s) unmapped")
        elif (t.get("coverage") or {}).get("digest") != contract_digest(t):
            parts.append("coverage not asserted")
        uncovered = [rid for rid in self.active if not any(c["req"] == rid for c in live(t["checks"]))]
        if uncovered:
            parts.append(f"{len(uncovered)} requirement(s) without a check")
        for key, label in (("stale", "stale"), ("failed", "failed"), ("not_run", "not run"), ("edited", "edited, need approval"),
                           ("missing_inputs", "declare a missing input"), ("other", "lack evidence")):
            if groups[key]:
                parts.append(f"{len(groups[key])} check(s) {label}")
        if unverified and not rerun and not groups["edited"] and not groups["other"]:
            parts.append(f"{len(unverified)} work item(s) not verified")
        if open_findings:
            parts.append(f"{len(open_findings)} finding(s) open")
        if deferred:
            parts.append(f"{len(deferred)} finding(s) deferred under operator authority")
        if open_blockers:
            parts.append(f"{len(open_blockers)} blocker(s) open")
        if review_owed:
            parts.append("final review owed")
        return {"headline": "; ".join(parts) or "all obligations satisfied", "checks": groups, "work_unverified": unverified,
                "findings_open": open_findings, "findings_deferred": deferred, "blockers_open": open_blockers,
                "review": "owed" if review_owed else "current",
                "rerun": ("dmd run " + " ".join(rerun)) if rerun else None}

    def progress(self):
        """What is currently satisfied, for the no-progress safeguard. Progress means this
        set gained a member; adding records, rewording notes or new failures never count."""
        t = self.t
        done = [c["id"] for c in live(t["checks"]) if self.accepted(c["id"])]
        done += [w["id"] for w in live(t["work"]) if self.work_ok(w["id"])]
        done += [f["id"] for f in t["findings"] if f["id"] not in self.findings]
        done += [x["id"] for x in t["blockers"] + t["uncertain"] if x.get("resolved")]
        if (t.get("coverage") or {}).get("digest") == contract_digest(t):
            done.append("coverage")
        return sorted(done)


def assess(task_dir, t, fp):
    return Assessment(task_dir, t, fp)

def unresolved_findings(task_dir, t, fp):
    return assess(task_dir, t, fp).findings

def gate(task_dir, t, fp=None, require_review=True):
    return evaluate(task_dir, t, fp, require_review)[0]

def evaluate(task_dir, t, fp=None, require_review=True):
    """The gate verdict and the Assessment behind it (None when the task could not be
    assessed: invalid, suspended, or a candidate tree is missing)."""
    errors = validation_errors(t)
    if errors:
        return {"status": "INVALID", "reasons": errors, "next": []}, None
    if t["state"] in SUSPENDED:
        return {"status": t["state"], "reasons": ["execution suspended; obligations are not complete"], "next": [],
                "summary": {"headline": f"task is {t['state']}" + (f": {t.get('state_reason')}" if t.get("state_reason") else ""),
                            "rerun": None}}, None
    try:
        fp = fp or task_fingerprint(t)
    except MissingCandidate as exc:
        return {"status": "BLOCKED", "reasons": [str(exc)], "next": [],
                "summary": {"headline": "a candidate tree is missing", "rerun": None}}, None
    a = assess(task_dir, t, fp)
    reasons = [text for _, text in a.reasons]
    # The final review is recorded only against an otherwise complete task, so it is owed
    # last; it is the next action only when nothing else is.
    review = a.review_reason(require_review)
    if review:
        reasons.append(review)
    nxt = a.next_actions()
    if review and not a.reasons:
        nxt.append({"id": "review", "action": "review the request, the final diff and the integrated result, then dmd review --kind self "
                    "--reviewer '<who reviewed>' --note '<what was reviewed>' --evidence /abs/review.txt"})
    status = "COMPLETE" if not reasons else ("ACTIVE" if nxt else a.status(reasons))
    if status == "ACTIVE" and not nxt:
        # Open reasons with no dependency-ready item mean the plan itself is stuck (a cycle
        # through failed work, a dependency on superseded scope). That is still the agent's
        # to fix, and it must be told so rather than blocked with nothing to do.
        nxt.append({"id": "plan", "action": "no dependency-ready item; replan around: " + "; ".join(reasons[:3])})
    return {"status": status, "reasons": reasons, "next": nxt, "source": fp,
            "attestation": {"executed": a.executed_total, "self_attested": a.attested_total},
            "summary": a.summary(bool(review))}, a

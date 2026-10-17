"""Contract decisions (AU-*), recorded and approved automatically.

Every decision is confirmed when it is recorded: nothing waits on the operator. The log
stays the audit trail of what the agent changed in the contract, and `dmd authority
withdraw` reverses a decision. A record from before automatic approval may still be
unconfirmed; `dmd authority confirm` approves it.
"""
from __future__ import annotations
import copy
from .storage import DmdError, now


def record(t, op, target, text, prior, binding=None):
    log = t.setdefault("authority", [])
    number = max([int(e["id"].split("-")[1]) for e in log] or [0]) + 1
    entry = {"id": f"AU-{number:02d}", "op": op, "target": target, "text": text, "at": now(),
             "confirmed": {"via": "auto", "at": now()}, "prior": copy.deepcopy(prior), "binding": binding}
    log.append(entry)
    return entry


def unconfirmed(t):
    return [e for e in t.get("authority") or [] if not e.get("confirmed") and not e.get("withdrawn")]


def decision_ok(t, entry_id, op, target, binding=None):
    return any(e.get("id") == entry_id and e.get("op") == op and e.get("target") == target
               and e.get("confirmed") and not e.get("withdrawn") and e.get("binding") == binding
               for e in t.get("authority") or [])


def find(t, entry_id):
    entry = next((e for e in t.get("authority") or [] if e["id"] == entry_id), None)
    if entry is None:
        raise DmdError(f"unknown operator decision: {entry_id}")
    return entry


def withdraw(t, entry):
    """Reverse decisions newest-first, never overwrite a later decision."""
    if entry.get("withdrawn"):
        raise DmdError(f"{entry['id']} is already withdrawn")
    if any(e["at"] > entry["at"] and e["target"] == entry["target"] and not e.get("withdrawn")
           for e in t.get("authority") or []):
        raise DmdError("withdraw later decisions for this target first")
    prior, op = entry.get("prior") or {}, entry["op"]
    if op in ("req.cancel", "req.attest-only", "finding.defer", "finding.duplicate", "check.no-regression"):
        group = "findings" if op.startswith("finding.") else "checks" if op.startswith("check.") else "requirements"
        index = next(i for i, row in enumerate(t[group]) if row["id"] == entry["target"])
        t[group][index] = copy.deepcopy(prior["record"])
        t["amendments"] = [a for a in t["amendments"] if a.get("at") != prior.get("amendment_at")]
    elif op == "state":
        t["state"], t["state_reason"] = prior["state"], prior.get("state_reason")
    elif op == "review.independent":
        t["review"] = copy.deepcopy(prior.get("review"))
    elif op == "check.approve":
        t["approvals"].pop(entry["target"], None)
    elif op == "check.baseline":
        next(c for c in t["checks"] if c["id"] == entry["target"])["baseline"] = prior.get("baseline")
    elif op == "coverage.assert":
        t["coverage"] = prior.get("coverage")
    elif op == "coverage.context":
        for old in prior["clauses"]:
            t["clauses"][next(i for i, c in enumerate(t["clauses"]) if c["id"] == old["id"])] = old
    elif op == "amend":
        t["amendments"] = [a for a in t["amendments"] if a.get("at") != prior["amendment_at"]]
        t["clauses"] = [c for c in t["clauses"] if c.get("source") != prior["source"]]
    elif op == "review-policy":
        t["require_independent_review"] = prior["require_independent_review"]
    elif op == "blocker.task":
        next(b for b in t["blockers"] if b["id"] == entry["target"]).update(resolved=True, resolution="suspension withdrawn")
    elif op != "init.new":
        raise DmdError(f"{entry['id']} ({op}) cannot be withdrawn here")
    entry["withdrawn"] = now()

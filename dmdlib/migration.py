"""Explicit copy-only import of schema-1 assignments. No historical green is trusted."""
import re
from pathlib import Path
from .storage import DmdError, lock, read_json, save, evidence
import json
from .model import new_id, validation_errors

def migrate(args):
    from .cli import create_task, require_text
    source = Path(args.from_task).expanduser()
    old = read_json(source)
    if not isinstance(old, dict) or old.get("schema") != 1:
        raise DmdError("migration accepts schema 1 only; the original file is never modified")
    request = require_text(old.get("original_request"), "legacy original_request")
    for name in ("requirements", "work_items", "checks", "findings", "blockers"):
        if not isinstance(old.get(name), list):
            raise DmdError(f"legacy {name} must be an array")
    # Validate IDs and references before creating a replacement record.
    for name, prefix in [("requirements", "R"), ("work_items", "W"), ("checks", "A"), ("findings", "F"), ("blockers", "B")]:
        ids = [row.get("id") for row in old[name] if isinstance(row, dict)]
        if len(ids) != len(old[name]) or len(set(ids)) != len(ids) or any(not re.fullmatch(prefix + r"-\d{2,}", str(i)) for i in ids):
            raise DmdError(f"legacy {name} has invalid or duplicate IDs")
    require_text(args.authority, "--authority")
    def prepare(t):
        t["migration"] = {"source": str(source.resolve()), "legacy_id": old.get("task_id")}
        for r in old["requirements"]:
            t["requirements"].append({"id": r["id"], "text": r["text"], "anchor": "Imported request: reread original and reconcile",
                                      "status": "active", "legacy_status": r.get("status")})
        reqs = {r["id"] for r in t["requirements"]}
        def fallback():
            rid = new_id(t["requirements"], "R")
            t["requirements"].append({"id": rid, "text": "Reconcile previously unmapped required work", "anchor": "Legacy follow-up inventory", "status": "active"})
            reqs.add(rid)
            return rid
        for w in old["work_items"]:
            rid = w.get("req") if w.get("req") in reqs else fallback()
            t["work"].append({"id": w["id"], "req": rid, "text": w["text"], "status": "todo", "deps": w.get("deps", []),
                              "owns": [], "note": "Imported unverified; old status=" + str(w.get("status"))})
        for c in old["checks"]:
            rid = c.get("req") if c.get("req") in reqs else fallback()
            ws = [w["id"] for w in t["work"] if w["req"] == rid]
            if not ws:
                wid = new_id(t["work"], "W")
                t["work"].append({"id": wid, "req": rid, "text": "Revalidate imported acceptance", "status": "todo", "deps": [], "owns": [], "note": ""})
                ws = [wid]
            t["checks"].append({"id": c["id"], "req": rid, "work": ws, "method": c.get("method", "command"),
                               "command": c.get("command"), "cwd": str(Path(t["root"]).resolve()), "expect": c.get("expect") or "Reauthor legacy expectation",
                               "match": None, "timeout": 300, "max_output": 1048576, "inputs": [], "regression": False,
                               "red_match": None, "red_exit": 1, "status": "NOT_RUN", "receipt": None, "red": None, "baseline": None, "needs_review": True})
        for f in old["findings"]:
            t["findings"].append({"id": f["id"], "text": f["text"], "location": f.get("location") or "legacy record: locate exact artifact",
                                  "status": "confirmed" if f.get("confidence") == "confirmed" else "suspected",
                                  "origin": "introduced" if f.get("scope") == "task-caused" else "unknown", "work": [], "checks": [],
                                  "note": "Imported as an unresolved obligation; original disposition retained in archived record"})
        valid_items = {"task"} | {x["id"] for key in ("requirements", "work", "checks", "findings") for x in t[key]}
        for b in old["blockers"]:
            if not b.get("cleared_at"):
                t["blockers"].append({"id": b["id"], "item": b.get("item") if b.get("item") in valid_items else "task",
                                      "text": b.get("text") or "Reconcile legacy blocker", "owner": "Identify the original prerequisite owner",
                                      "unblock": "Recheck the original prerequisite and record proof", "proof": "Imported legacy record; reconfirm externally", "resolved": False})
        for u in old.get("uncertain", []):
            if not u.get("resolved"):
                t["uncertain"].append({"id": new_id(t["uncertain"], "U"), "text": u.get("text") or "Reconcile interrupted legacy operation", "resolved": False})
        if old.get("status") in ("PAUSED", "CANCELLED"):
            t["state"] = old["status"]
        t["amendments"] = old.get("amendments", [])
    def guarded(t):
        # A malformed legacy row is bad input, not a defect in dmd.
        try:
            prepare(t)
        except (KeyError, TypeError, ValueError) as exc:
            raise DmdError(f"legacy record is malformed: {exc!r}") from exc
    # Transform and validate in memory before replacing the active pointer or
    # pausing any pre-existing assignment. An invalid import has no task effects.
    directory, t = create_task(args, request, args.authority, prepare=guarded)
    with lock(directory):
        t["migration"]["archived_record"] = evidence(directory, json.dumps(old, indent=2), "legacy")
        save(directory, t, "migration.import", accepted_historical_checks=0)
    print(t["task_id"] + ": imported; reauthor checks, reconfirm scope/findings, approve and rerun. No historical PASS accepted.")
    return 0

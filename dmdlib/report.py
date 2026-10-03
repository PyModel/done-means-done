"""The human-readable report: named sections rendered from a task and its gate."""
from __future__ import annotations
from pathlib import Path
from .model import acceptance_reason, attested, check_candidate, legacy_receipt, live, repeated_attempts
from .runner import approval_reason
from .source import untracked


SECTIONS = ("assignment", "acceptance", "requirements", "work", "checks", "findings", "blockers", "decisions", "attempts", "reviews", "leftovers", "owed", "next", "footer")

def sections(directory, t, g):
    """The report as named sections, so a reader can ask for one instead of the dump."""
    counts = g.get("attestation") or {}
    out = {}
    provenance = t.get("request_provenance") or {"kind": "legacy-agent-transcribed"}
    out["assignment"] = ["## Assignment", t["original_request"], "Request provenance: " + provenance["kind"]]
    out["acceptance"] = ["## Acceptance basis",
                         f"- Accepted checks executed by dmd: {counts.get('executed', 0)}",
                         f"- Accepted checks SELF-ATTESTED by the agent (no command was run): {counts.get('self_attested', 0)}",
                         "- Command capture is not semantic proof. Inspect actual assertions and the full request-to-outcome mapping.",
                         "- Supplied review/blocker files and stdin are agent-transcribed notes, not authenticated external observations."]
    lines = ["## Requirements"]
    for r in t["requirements"]:
        line = f"- {r['id']} [{r['status']}] {r['text']} (source: {r['anchor']})"
        if r.get("attest_only"):
            line += " — attested-only by operator authority: " + r["attest_only_authority"]
        lines.append(line)
    out["requirements"] = lines
    lines = ["## Work"]
    for w in t["work"]:
        state = "superseded" if w.get("removed") else w["status"]
        lines.append(f"- {w['id']} [{state}] {w['text']} -> {w['req']}; dependencies: {', '.join(w['deps']) or 'none'}")
        if w.get("removed"):
            lines.append("  Superseded: " + w["removed_reason"])
    out["work"] = lines
    lines = ["## Checks"]
    for c in t["checks"]:
        reason = acceptance_reason(directory, c, g.get("source"), t["root"], task=t)
        current = reason is None
        if c.get("removed"):
            basis = "superseded"
        else:
            basis = "SELF-ATTESTED" if attested(c) else ("COMMAND CAPTURE" if c.get("receipt") else "NOT EXECUTED")
        lines.append(f"- {c['id']} [{'ACCEPTED' if current else 'UNVERIFIED'} · {basis}] ({c['method']}) {c['expect']}")
        if attested(c) and c.get("attested_because"):
            lines.append("  Attested because: " + c["attested_because"])
        if c.get("removed"):
            lines.append("  Superseded: " + c["removed_reason"])
        if c.get("exclusive"):
            lines.append("  Exclusive: " + ", ".join(c["exclusive"]))
        if c["method"] == "command" and not c.get("removed"):
            why = approval_reason(t, c)
            lines.append("  Verifier: " + (why or "operator-confirmed definition and declared inputs"))
        if c.get("writes"):
            lines.append("  Allowed generated outputs (contents remain fingerprinted): " + ", ".join(c["writes"]))
        if c.get("receipt"):
            r = c["receipt"]
            lines.append("  Evidence: " + str(directory / r["artifact"]["path"]))
            if r.get("candidate"):
                lines.append(f"  Tested: {r['candidate']} @ {r.get('head') or 'no-head'}")
            elif legacy_receipt(c) and current:
                lines.append(f"  Tested: task root (receipt predates 0.5.0 candidate binding); rerun to bind it to {check_candidate(c, t['root'])}")
            if r.get("stale"):
                d = r["stale"].get("drift") or {}
                why = "definition changed" if r["stale"].get("definition_changed") else "candidate moved"
                moved = ", ".join(d.get("changed", [])[:5]) or "none listed"
                lines.append(f"  STALE: {why}; HEAD {d.get('head_before') or 'no-head'} -> {d.get('head_after') or 'no-head'}; changed: {moved}")
            if not current and not c.get("removed"):
                lines.append("  Unverified because: " + reason)
            if r.get("background_holders"):
                lines.append("  Note: background processes still held the output pipes when this check finished.")
        if c.get("baseline"):
            lines.append("  Baseline limitation: " + c["baseline"]["reason"])
    out["checks"] = lines
    lines = ["## Findings (all severities and origins)"]
    for f in t["findings"]:
        lines.append(f"- {f['id']} [{f['status']}; {f.get('origin') or 'unknown'}] {f['location']}: {f['text']}; {f.get('note', '')}")
    out["findings"] = lines
    lines = ["## Blockers and external outcomes"]
    for b in t["blockers"]:
        lines.append(f"- {b['id']} [{'resolved' if b.get('resolved') else 'OPEN'}] {b['text']}; unblock: {b['unblock']}; owner: {b['owner']}")
    for u in t["uncertain"]:
        lines.append(f"- {u['id']} [{'resolved' if u.get('resolved') else 'UNKNOWN'}] {u['text']}")
    out["blockers"] = lines
    out["decisions"] = []
    if t.get("authority"):
        out["decisions"] = ["## Operator decisions"]
        for e in t["authority"]:
            state = ("withdrawn" if e.get("withdrawn") else f"confirmed via {e['confirmed']['via']}" if e.get("confirmed")
                     else "AWAITING THE OPERATOR'S CONFIRMATION" if e.get("channel")
                     else "AWAITING CONFIRMATION: use a controlling terminal or install enforcing hooks")
            out["decisions"].append(f"- {e['id']} [{state}] {e['op']} {e['target']}: {e['text']}")
    repeats = {item: history for item, history in (t.get("attempts") or {}).items() if repeated_attempts(history)}
    out["attempts"] = []
    if repeats:
        out["attempts"] = ["## Attempts requiring a different strategy"] + [
            f"- {item}: {len(history)} recorded attempts; last strategy: {history[-1].get('strategy') or 'none recorded'}"
            for item, history in sorted(repeats.items())]
    log = t.get("review_log") or []
    out["reviews"] = []
    if log:
        out["reviews"] = ["## Review history"] + [
            f"- {entry['at']} [{entry['outcome']}] {entry['kind']} review by {entry['reviewer']}: {entry['note']}" for entry in log]
    out["leftovers"] = leftovers(t)
    out["owed"] = ["## Still owed"] + ["- " + x for x in g["reasons"]]
    out["next"] = ["## Next actions"] + [f"- {x['id']}: {x['action']}" for x in g["next"]]
    source = g.get("source")
    if isinstance(source, dict):
        source_lines = ["Source:"] + [f"- {path}: {fp}" for path, fp in sorted(source.items())]
    else:
        source_lines = ["Source: " + str(source or "not measured while suspended")]
    out["footer"] = ["Review: " + ((t.get("review") or {}).get("kind", "not recorded")), *source_lines,
                     "Evidence is local auditability, not tamper-proof attestation."]
    return out


def leftovers(t, limit=20):
    """What a finished job may have left behind: untracked files in the task root that no
    check declared as its output, and checks whose command left processes running. Commit
    what is a deliverable; remove the rest."""
    root = t["root"]
    outputs = {p for c in live(t["checks"]) if check_candidate(c, root) == root
               for p in (c.get("receipt") or {}).get("outputs", {})}
    loose = sorted(p for p in (untracked(root) or set()) if p not in outputs) if Path(root).is_dir() else []
    holders = [c["id"] for c in live(t["checks"]) if (c.get("receipt") or {}).get("background_holders")]
    if not loose and not holders:
        return []
    lines = ["## Possible leftovers (commit deliverables, remove the rest)"]
    lines += [f"- untracked: {p}" for p in loose[:limit]]
    if len(loose) > limit:
        lines.append(f"- ... and {len(loose) - limit} more untracked files")
    lines += [f"- {cid}: its command left background processes running; stop them" for cid in holders]
    return lines


def render(directory, t, g, only=None):
    parts = sections(directory, t, g)
    chosen = [name for name in SECTIONS if not only or name in only]
    lines = [f"# Done Means Done | {t['task_id']} | {g['status']}"]
    for name in chosen:
        if parts[name]:
            lines += [""] + parts[name]
    return "\n".join(lines) + "\n"

"""Operator decisions, not agent-written authority quotes.

Every decision stays pending until a controlling-terminal response or a matching host
permission event confirms it. These are cooperative local signals, not authenticated
human identity or protection from an actor with same-user filesystem/PTY access.
"""
from __future__ import annotations
import copy
import os
import re
import select
import shlex
import time
from pathlib import Path
from .storage import DmdError, now, safe_text

OPS = {
    "req.cancel": r"\breq\s+cancel\b",
    "req.attest-only": r"\breq\s+attest-only\b",
    "finding.defer": r"\bfinding\s+defer\b",
    "finding.duplicate": r"\bfinding\s+set\b[^;&|\n]*--status(?:\s+|=)duplicate\b",
    "check.approve": r"\bapprove\b|\bcheck\s+(?:add|edit)\b[^;&|\n]*--approve\b",
    "check.baseline": r"\bcheck\s+baseline\b",
    "check.no-regression": r"\bcheck\s+edit\b[^;&|\n]*--no-regression\b",
    "coverage.context": r"\bcoverage\s+context\b",
    "coverage.assert": r"\bcoverage\s+assert\b",
    "amend": r"\bamend\b",
    "review-policy": r"\breview-policy\b",
    "blocker.task": r"\bblocker\s+add\b[^;&|\n]*--item(?:\s+|=)task\b",
    "state": r"\bstate\s+(?:ACTIVE|PAUSED|CANCELLED)\b",
    "review.independent": r"\breview\b[^;&|\n]*--kind(?:\s+|=)independent\b",
    "init.new": r"\b(?:init|migrate)\b[^;&|\n]*--new\b",
    "config": r"\bconfig\b[^;&|\n]*--(?:mode|max-no-progress)\b",
    "authority.confirm": r"\bauthority\s+confirm\b",
    "relocate": r"\brelocate\b[^;&|\n]*--to\b",
    "hook": r"\bhook\s+(?:user-prompt-submit|session-start|stop|task-completed|pre-tool-use|post-tool-use|post-tool-failure)\b",
    "uninstall": r"install\.py\b",
}
DESCRIPTIONS = {
    "req.cancel": "cancel a requirement", "req.attest-only": "accept a requirement on attestation alone",
    "finding.defer": "defer a defect instead of fixing it", "finding.duplicate": "resolve a confirmed defect as a duplicate",
    "check.approve": "authorize the exact verifier and declared inputs (inspect their assertions)",
    "check.baseline": "waive an executed regression baseline", "check.no-regression": "remove regression protection",
    "coverage.context": "exclude request text as context", "coverage.assert": "confirm the entire request-to-outcome mapping",
    "amend": "amend the assignment", "review-policy": "change the independent-review policy",
    "blocker.task": "suspend all work on an evidence-backed prerequisite",
    "state": "pause, cancel or resume the assignment", "review.independent": "record an independent review",
    "init.new": "replace the unfinished assignment with a new one", "config": "change hook enforcement",
    "authority.confirm": "confirm an earlier operator decision", "relocate": "re-point the task at another checkout",
    "hook": "run a lifecycle hook by hand", "uninstall": "change lifecycle hook installation",
    "protected-write": "modify protected governance state, runtime or settings",
}
MENTIONS = {"echo", "printf", "grep", "egrep", "rg", "cat", "less", "man", "head", "tail"}


def protected_path(path, protected, cwd):
    if not isinstance(path, str) or not path:
        return False
    p = Path(os.path.expandvars(path)).expanduser()
    p = (Path(cwd) / p).resolve() if not p.is_absolute() else p.resolve()
    return any(p == root or root in p.parents for root in map(lambda x: Path(x).resolve(), protected))


def detect(command, protected=(), cwd=None):
    """Conservative interception of visible shell operations. Not a shell sandbox: an
    opaque program can perform writes with no path or operation in its command text."""
    found, here = [], cwd or os.getcwd()
    text = str(command or "")
    executed = re.search(r"\|\s*(?:\S*/)?(?:sh|bash|zsh|dash|python3?|perl|ruby|node|xargs|eval|source)\b", text)
    for segment in re.split(r"[;&|\n]+", text):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        if not words:
            continue
        first = words[0].rsplit("/", 1)[-1]
        if first == "cd" and len(words) == 2:
            here = str((Path(here) / Path(words[1]).expanduser()).resolve())
        readonly = first in MENTIONS or (first == "sed" and not re.search(r"\s-\S*i", segment)) or first == "git"
        writes = bool(re.search(r">|\bsed\s+[^\n]*-\S*i|\b(?:tee|mv|cp|rm|chmod|chown|touch|install)\b", segment))
        if protected and (writes or not readonly or executed):
            tokens = re.findall(r"[^\s<>='\"()]+", segment)
            if any(protected_path(w, protected, here) for w in words + tokens):
                found.append("protected-write")
        if readonly and not writes and not executed:
            continue
        if not re.search(r"(?:^|[\s/'\"=(,\[])dmd\b|install\.py", segment):
            continue
        flat = re.sub(r"[\"',\[\]]", " ", segment)
        found += [op for op, pattern in OPS.items() if re.search(pattern, flat)]
    return list(dict.fromkeys(found))


def channel(config, installed_events):
    return effective(config).get("mode") == "enforce" and "pre-tool-use" in installed_events


def effective(config):
    pending = config.get("pending") if isinstance(config, dict) else None
    if isinstance(pending, dict) and isinstance(pending.get("prior"), dict):
        return {**config, **pending["prior"]}
    return config


def terminal_confirms(description, timeout=30):
    """Bounded /dev/tty I/O without seek-dependent TextIOWrapper. A PTY can simulate
    this response; the signal is explicitly not a proof of human identity."""
    if os.environ.get("DMD_NO_TTY") or not os.isatty(0):
        return False
    fd = None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
        os.write(fd, f"Done Means Done: operator confirmation: {safe_text(description, 2000)} [y/N] ".encode())
        deadline, answer = time.monotonic() + timeout, b""
        while len(answer) < 256:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return False
            chunk = os.read(fd, 256 - len(answer))
            if not chunk:
                return False
            answer += chunk
            if b"\n" in answer:
                return answer.strip().lower() in (b"y", b"yes")
        return False
    except (OSError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def record(t, op, target, text, prior, has_channel, binding=None):
    log = t.setdefault("authority", [])
    number = max([int(e["id"].split("-")[1]) for e in log] or [0]) + 1
    entry = {"id": f"AU-{number:02d}", "op": op, "target": target, "text": text, "at": now(),
             "channel": bool(has_channel), "confirmed": None, "prior": copy.deepcopy(prior), "binding": binding}
    if terminal_confirms(f"{DESCRIPTIONS[op]} ({target}): {text!r}"):
        entry["confirmed"] = {"via": "terminal", "at": now()}
    log.append(entry)
    return entry


def unconfirmed(t):
    return [e for e in t.get("authority") or [] if not e.get("confirmed") and not e.get("withdrawn")]


def undisclosed(t):
    return [e for e in unconfirmed(t) if not e.get("channel")]


def decision_ok(t, entry_id, op, target, binding=None):
    return any(e.get("id") == entry_id and e.get("op") == op and e.get("target") == target
               and e.get("confirmed") and not e.get("withdrawn") and e.get("binding") == binding
               for e in t.get("authority") or [])


def confirm(t, ops, since, via):
    done = []
    for e in unconfirmed(t):
        touched = max(e.get("at") or "", e.get("confirm_requested_at") or "")
        requested = e["op"] in ops or ("authority.confirm" in ops and (e.get("confirm_requested_at") or "") >= since)
        if requested and touched >= since:
            e["confirmed"] = {"via": via, "at": now()}
            done.append(e["id"])
    return done


def find(t, entry_id):
    entry = next((e for e in t.get("authority") or [] if e["id"] == entry_id), None)
    if entry is None:
        raise DmdError(f"unknown operator decision: {entry_id}")
    return entry


def withdraw(t, entry):
    """Reverse pending decisions newest-first, never overwrite a later decision."""
    if entry.get("withdrawn") or entry.get("confirmed"):
        raise DmdError(f"{entry['id']} is already confirmed or withdrawn")
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

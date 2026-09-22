"""Operator decisions: the commands that change the contract the agent is held to.

Typing `--authority "..."` records the operator's words; it cannot prove the operator said
them. When a confirmation channel exists (hooks installed with PreToolUse, mode enforce),
the host asks the operator before any of these commands runs, and the decision takes
effect for the gate only once that approval is recorded. Without a channel the decision
applies at once and every report says it was never confirmed.

Local files are still local files: an agent that forges hook payloads or edits state by
hand is outside what this prevents (references/security.md)."""
from __future__ import annotations
import copy
import os
import re
from .storage import DmdError, now

# Operation key -> pattern over a shell command (quotes, commas and brackets normalised to
# spaces, so an argv list in a script matches too). `dmd` must also appear in the command.
OPS = {
    "req.cancel": r"\breq\s+cancel\b",
    "req.attest-only": r"\breq\s+attest-only\b",
    "finding.defer": r"\bfinding\s+defer\b",
    "state": r"\bstate\s+(?:ACTIVE|PAUSED|CANCELLED)\b",
    "review.independent": r"\breview\b[^;&|\n]*--kind(?:\s+|=)independent\b",
    "init.new": r"\b(?:init|migrate)\b[^;&|\n]*--new\b",
    "config": r"\bconfig\b[^;&|\n]*--(?:mode|max-no-progress)\b",
    "authority.confirm": r"\bauthority\s+confirm\b",
    "relocate": r"\brelocate\b[^;&|\n]*--to\b",
    # Running a hook entry by hand could forge a confirmation; removing the hooks ends them.
    "hook": r"\bhook\s+(?:session-start|stop|task-completed|pre-tool-use|post-tool-use|post-tool-failure)\b",
    "uninstall": r"install\.py\b[^;&|\n]*--remove\b",
}
DESCRIPTIONS = {
    "req.cancel": "cancel a requirement", "req.attest-only": "accept a requirement on attestation alone",
    "finding.defer": "defer a defect instead of fixing it", "state": "pause, cancel or resume the assignment",
    "review.independent": "record an independent review", "init.new": "replace the unfinished assignment with a new one",
    "config": "change hook enforcement", "authority.confirm": "confirm an earlier operator decision",
    "relocate": "re-point the task at another checkout", "hook": "run a lifecycle hook by hand",
    "uninstall": "remove the lifecycle hooks",
}


# Commands that only mention text. A segment they start (`echo ...`, `grep ...`) is not
# scanned, so reading or searching for these words does not put a prompt to the operator.
MENTIONS = {"echo", "printf", "grep", "egrep", "rg", "git", "cat", "less", "man", "sed", "awk", "head", "tail"}


def detect(command):
    """The operator-decision operations a shell command would perform. Each shell segment
    (split on ; & | and newlines) is scanned unless it starts with a command that only
    mentions text; quotes, commas and brackets become spaces, so an argv list in a
    script is caught too."""
    found = []
    text = str(command or "")
    # Text piped into an interpreter is executed, not mentioned.
    executed = re.search(r"\|\s*(?:\S*/)?(?:sh|bash|zsh|dash|python3?|perl|ruby|node|xargs|eval|source)\b", text)
    for segment in re.split(r"[;&|\n]+", text):
        words = segment.split()
        if not words or (words[0].rsplit("/", 1)[-1] in MENTIONS and not executed):
            continue
        if not re.search(r"(?:^|[\s/'\"=(,\[])dmd\b|install\.py", segment):
            continue
        flat = re.sub(r"[\"',\[\]]", " ", segment)
        found += [op for op, pattern in OPS.items() if re.search(pattern, flat) and op not in found]
    return found


def channel(config, installed_events):
    """A confirmation channel exists when the hooks enforce and PreToolUse is registered."""
    return effective(config).get("mode") == "enforce" and "pre-tool-use" in installed_events


def effective(config):
    """Hook settings in force: an unconfirmed change keeps the values it would replace."""
    pending = config.get("pending") if isinstance(config, dict) else None
    if isinstance(pending, dict) and isinstance(pending.get("prior"), dict):
        return {**config, **pending["prior"]}
    return config


def terminal_confirms(description):
    """Ask on the controlling terminal, the operator's own. A host tool call has none."""
    if os.environ.get("DMD_NO_TTY") or not os.isatty(0):
        return False
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(f"Done Means Done: as the operator, confirm: {description} [y/N] ")
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        return False


def record(t, op, target, text, prior, has_channel):
    """Log an operator decision on the task. Returns the entry; it is confirmed on the spot
    when the operator answers on their own terminal."""
    log = t.setdefault("authority", [])
    number = max([int(e["id"].split("-")[1]) for e in log] or [0]) + 1
    entry = {"id": f"AU-{number:02d}", "op": op, "target": target, "text": text, "at": now(),
             "channel": bool(has_channel), "confirmed": None, "prior": copy.deepcopy(prior)}
    if has_channel and terminal_confirms(f"{DESCRIPTIONS[op]} ({target}): {text!r}"):
        entry["confirmed"] = {"via": "terminal", "at": now()}
    log.append(entry)
    return entry


def unconfirmed(t):
    """Decisions that need the operator's confirmation and do not have it yet."""
    return [e for e in t.get("authority") or [] if e.get("channel") and not e.get("confirmed") and not e.get("withdrawn")]


def undisclosed(t):
    """Decisions recorded with no channel to confirm them: applied, and reported as such."""
    return [e for e in t.get("authority") or [] if not e.get("channel") and not e.get("withdrawn")]


def confirm(t, ops, since, via):
    """Mark decisions confirmed by an approved host prompt. `since` is when the prompt was
    raised: only entries recorded (or re-requested) after it, for one of its operations."""
    done = []
    for e in unconfirmed(t):
        touched = max(e.get("at") or "", e.get("confirm_requested_at") or "")
        requested = e["op"] in ops or ("authority.confirm" in ops and e.get("confirm_requested_at"))
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
    """Undo a decision the operator did not confirm, restoring what it replaced."""
    if entry.get("withdrawn"):
        raise DmdError(f"{entry['id']} is already withdrawn")
    if entry.get("confirmed"):
        raise DmdError(f"{entry['id']} was confirmed by the operator; only they can reverse it")
    prior = entry.get("prior") or {}
    op = entry["op"]
    if op in ("req.cancel", "req.attest-only", "finding.defer"):
        group = "findings" if op == "finding.defer" else "requirements"
        index = next(i for i, row in enumerate(t[group]) if row["id"] == entry["target"])
        t[group][index] = copy.deepcopy(prior["record"])
        t["amendments"] = [a for a in t["amendments"] if a.get("at") != prior.get("amendment_at")]
    elif op == "state":
        t["state"] = prior["state"]
        t["state_reason"] = prior.get("state_reason")
    elif op == "review.independent":
        t["review"] = copy.deepcopy(prior.get("review"))
    elif op != "init.new":  # a supersede is undone by the store, across two task records
        raise DmdError(f"{entry['id']} ({op}) cannot be withdrawn here")
    entry["withdrawn"] = now()

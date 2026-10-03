"""The task store: where records live, how they are opened, changed and bound to host
sessions, and the hook configuration. The CLI, the hooks and migration all go through it."""
from __future__ import annotations
import contextlib
import json
import os
import socket
import time
import uuid
from pathlib import Path
from . import __version__
from .storage import DmdError, atomic, digest, ident, lock, now, private_dir, read_json, redact, save
from .source import git_root, identity
from .model import FINISHED, SCHEMA, contract_digest, extract_clauses, validation_errors
from . import authority


def state_root():
    path = Path(os.environ.get("DMD_STATE", str(Path.home() / ".local/state/done-means-done"))).expanduser()
    # Canonicalize before the symlinked-ancestor check: /var and /tmp are symlinks on
    # macOS, so an abspath would reject every state directory under the system temp root.
    return private_dir(Path(os.path.realpath(path)))

class StateInsideProject(DmdError):
    """The state root lies inside the tree that would be verified. No task can exist for
    that tree by construction, so hooks treat it as 'no task here' rather than a failure."""

def base_dir(cwd, create=False):
    """The worktree's record directory. Only creating or relocating a task makes it: a
    lookup from a folder with no task leaves nothing behind in the state root."""
    root, project, worktree = identity(cwd)
    state = state_root()
    if state == root or state.is_relative_to(root):
        why = ("" if git_root(root) else f"; {root} is not a Git checkout, so it is treated as the project root")
        raise StateInsideProject(f"DMD_STATE ({state}) must remain outside the project being verified ({root}){why}. "
                                 "Run dmd inside the project checkout, or point DMD_STATE elsewhere")
    path = state / "v2" / project / worktree
    return (private_dir(path) if create or path.exists() else path), root

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

def task_records():
    """Every task record in the state root as (directory, task or the load error). One
    unreadable record never hides the healthy ones."""
    for path in sorted(state_root().glob("v2/*/*/*/task.json")):
        try:
            yield path.parent, load_task(path.parent)
        except (DmdError, OSError) as exc:
            yield path.parent, exc

def sibling_tasks(cwd):
    """Unfinished tasks recorded for other worktrees of the same repository. State is keyed
    by physical worktree, so a task started in the main checkout is invisible from a linked
    worktree; naming it beats a bare 'no active task'."""
    root, project, worktree = identity(cwd)
    found = []
    pointers = sorted((state_root() / "v2" / project).glob("*/active.json"))
    if not pointers:
        # A pruned or relocated worktree lands in a different project bucket; a task whose
        # recorded root contains or equals this cwd is still worth naming.
        pointers = sorted(state_root().glob("v2/*/*/active.json"))
    for pointer in pointers:
        if pointer.parent.name == worktree and pointer.parent.parent.name == project:
            continue
        try:
            t = load_task(pointer.parent / ident(read_json(pointer).get("task_id")))
        except (DmdError, OSError):
            continue
        related = pointer.parent.parent.name == project or Path(t["root"]) == root or Path(t["root"]) in root.parents or root in Path(t["root"]).parents
        if related and t["state"] not in FINISHED:
            found.append((t["task_id"], t["state"], t["root"]))
    return found


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DmdError(f"{name} is required")
    return value.strip()


# Audit lists a long task keeps appending to. Every evidence file stays on disk and
# events.jsonl keeps the full sequence; the record keeps the recent tail.
MAX_HISTORY = 50

def cap_histories(t):
    for row in t["checks"] + t["work"]:
        if len(row.get("history") or []) > MAX_HISTORY:
            del row["history"][:-MAX_HISTORY]
    for key in ("review_log", "recovery"):
        if len(t.get(key) or []) > MAX_HISTORY:
            del t[key][:-MAX_HISTORY]
    for history in (t.get("attempts") or {}).values():
        if len(history) > MAX_HISTORY:
            del history[:-MAX_HISTORY]


@contextlib.contextmanager
def transaction(directory, kind, allow_cancelled=False):
    """One validated mutation of a task record under its lock. The record is saved only
    when the change validates; a contract change reopens a finished task."""
    with lock(directory):
        t = load_task(directory)
        if t["state"] == "CANCELLED" and not allow_cancelled:
            raise DmdError("task is cancelled; explicit operator-authorized reactivation is required")
        before = contract_digest(t)
        yield t
        if t["state"] == "COMPLETE" and contract_digest(t) != before:
            # New or changed obligations reopen a finished task, so the hooks govern it again.
            t["state"] = "ACTIVE"
        errors = validation_errors(t)
        if errors:
            raise DmdError("; ".join(errors))
        cap_histories(t)
        save(directory, t, kind)


# Host sessions -----------------------------------------------------------------------

def sessions_dir():
    return private_dir(state_root() / "sessions")

def binding_path(session):
    return state_root() / "sessions" / (digest(session) + ".json")

def bindings():
    """(binding file, recorded task dir or None when unreadable) for every session binding."""
    directory = state_root() / "sessions"
    for binding in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            yield binding, Path(read_json(binding).get("task_dir", ""))
        except (DmdError, OSError):
            # Unreadable is not the same as dead; another session may be mid-write.
            yield binding, None

def prune_sessions():
    """Remove bindings whose task no longer exists. Returns how many were removed."""
    removed = 0
    with lock(sessions_dir(), ".prune.lock", wait=1.0):
        for binding, target in bindings():
            if target is not None and str(target) and not (target / "task.json").is_file():
                with contextlib.suppress(OSError):
                    binding.unlink(); removed += 1
    return removed

# Bounded per-task bookkeeping: a long-lived task must never grow its record until the
# 16 MiB save limit turns every later mutation into a failure.
MAX_SESSIONS = 50

def remember_session(task, session):
    """Record the session by hash, bounded. Raw host session IDs are not needed in the record."""
    key = digest(session)
    sessions = task.setdefault("sessions", [])
    if session in sessions or key in sessions:
        return
    sessions.append(key)
    if len(sessions) > MAX_SESSIONS:
        del sessions[:-MAX_SESSIONS]

def valid_session(session):
    if not isinstance(session, str) or not 1 <= len(session) <= 512:
        raise DmdError("a host session ID of 1..512 characters is required")
    return session

def bind_session(directory, session):
    valid_session(session)
    # Bindings whose task no longer exists are dead weight; drop them as we go. Pruning is
    # housekeeping: a contended prune lock or a vanished file must not fail the binding.
    with contextlib.suppress(DmdError, OSError):
        prune_sessions()
    atomic(binding_path(session), json.dumps({"task_dir": str(directory), "session_hash": digest(session)}))

def rebind_sessions(old, new):
    """Sessions bound to a superseded task follow the worktree to its new assignment;
    otherwise they would stay on a paused task that no hook enforces."""
    for binding, target in bindings():
        if target is not None and str(target) == str(old):
            atomic(binding, json.dumps(dict(read_json(binding), task_dir=str(new))))


# Hook configuration ------------------------------------------------------------------

DEFAULT_CONFIG = {"mode": "enforce", "max_no_progress": 6}
HOOK_MODES = ("off", "observe", "enforce")

def config_errors(config):
    errors = []
    if not isinstance(config, dict):
        return ["config.json must be an object"]
    if config.get("mode", DEFAULT_CONFIG["mode"]) not in HOOK_MODES:
        errors.append(f"invalid hook mode {config.get('mode')!r}; choose off, observe or enforce")
    cap = config.get("max_no_progress", 6)
    if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 6:
        errors.append("invalid no-progress safeguard configuration; max_no_progress must be 1..6")
    return errors

def load_config(strict=True):
    """The hook configuration. strict raises on an invalid file; otherwise the raw data
    is returned for a caller that reports problems itself (dmd doctor)."""
    path = state_root() / "config.json"
    data = read_json(path) if path.exists() else dict(DEFAULT_CONFIG)
    if strict:
        errors = config_errors(data)
        if errors:
            raise DmdError("; ".join(errors))
    return data

def installed_events():
    """Hook events registered by an installer run against this state root."""
    events = set()
    directory = state_root() / "installations"
    for manifest in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        with contextlib.suppress(DmdError, OSError, AttributeError):
            events |= {c.rsplit(" ", 1)[-1] for c in read_json(manifest).get("commands") or [] if isinstance(c, str)}
    return events

def authority_channel():
    """Whether the host can ask the operator to confirm a contract change (authority.py)."""
    try:
        config = load_config(strict=False)
    except DmdError:
        return False
    return not config_errors(config) and authority.channel(config, installed_events())

def save_config(data):
    errors = config_errors(data)
    if errors:
        raise DmdError("; ".join(errors))
    atomic(state_root() / "config.json", json.dumps(data, indent=2))


# Task creation -----------------------------------------------------------------------

def prompt_path(session, root):
    return state_root() / "prompts" / (digest([valid_session(session), str(root)]) + ".json")


def capture_prompt(session, cwd, prompt):
    root, _, _ = identity(cwd)
    text = redact(require_text(prompt, "host prompt"))
    if len(text.encode()) > 1048576:
        raise DmdError("host prompt exceeds 1 MiB")
    atomic(prompt_path(session, root), json.dumps({"root": str(root), "session": digest(session),
           "text": text, "digest": digest(text), "at": now()}))


def captured_prompt(session, root):
    if not session:
        return None
    path = prompt_path(session, root)
    if not path.exists():
        return None
    data = read_json(path)
    if data.get("root") != str(root) or data.get("session") != digest(session) or digest(data.get("text")) != data.get("digest"):
        raise DmdError("host prompt provenance is inconsistent")
    return data


def create_task(cwd, task_id, request, authorization, session=None, new=False, require_review=True, prepare=None, from_host=False):
    """Create and activate a task record for the worktree at cwd. An unfinished task there
    is superseded (paused) only when `new` is set; its sessions follow the new task."""
    if session is not None:
        valid_session(session)
    base, root = base_dir(cwd, create=True)
    captured = captured_prompt(session, root)
    if from_host and captured is None:
        raise DmdError("no captured host request for this session and worktree")
    if captured:
        if not from_host and redact(request.strip()) != captured["text"]:
            raise DmdError("request differs from the captured host prompt; use init --from-host with the same --session")
        request = captured["text"]
    provenance = ({"kind": "host-captured", "session": captured["session"], "digest": captured["digest"], "at": captured["at"]}
                  if captured else {"kind": "agent-transcribed", "digest": digest(redact(request.strip()))})
    task_id = ident(task_id or "T-" + uuid.uuid4().hex)
    directory = base / task_id
    t = {"schema": SCHEMA, "version": __version__, "task_id": task_id, "root": str(root),
         "state": "ACTIVE", "created_at": now(), "original_request": redact(request.strip()),
         "authorization": redact(authorization), "request_provenance": provenance,
         "amendments": [], "requirements": [], "work": [],
         "checks": [], "findings": [], "blockers": [], "uncertain": [], "events": [], "sessions": [],
         "approvals": {}, "coverage": None, "review": None, "running": None, "attempts": {},
         "require_independent_review": require_review, "host_map": {}}
    # The request's explicit list items: each must map to a requirement or be marked context.
    t["clauses"] = extract_clauses(t["original_request"])
    if session:
        t["sessions"].append(digest(session))
    if prepare is not None:
        prepare(t)
        errors = validation_errors(t)
        if errors:
            raise DmdError("import rejected before activation: " + "; ".join(errors))
    with lock(base):
        if directory.exists():
            raise DmdError("task ID already exists; initialization never overwrites a task")
        old = locate(cwd)
        if old:
            prior = load_task(old)
            if prior["state"] not in FINISHED and not new:
                raise DmdError("unfinished assignment exists; resume it or explicitly use --new")
            if prior["state"] not in FINISHED:
                with lock(old):
                    prior = load_task(old)
                    if prior.get("running"):
                        raise DmdError("cannot supersede an assignment while its check is running")
                    authority.record(t, "init.new", prior["task_id"], authorization,
                                     {"dir": str(old), "state": prior["state"], "state_reason": prior.get("state_reason")},
                                     authority_channel())
                    prior["state"] = "PAUSED"
                    save(old, prior, "superseded-active-pointer", new_task_id=task_id)
        private_dir(directory)
        save(directory, t, "init")
        atomic(base / "active.json", json.dumps({"task_id": task_id}))
        if old:
            rebind_sessions(old, directory)
    if session:
        bind_session(directory, session)
    return directory, t


def withdraw_supersede(directory, t, entry):
    """Undo an unconfirmed `init --new`: the superseded task resumes as it was, the worktree
    and its sessions point back at it, and the replacement is cancelled."""
    prior = entry["prior"]
    old = Path(prior["dir"])
    with lock(old):
        previous = load_task(old)
        previous["state"] = prior["state"]
        previous["state_reason"] = prior.get("state_reason")
        save(old, previous, "supersede-withdrawn", by_task_id=t["task_id"])
    atomic(directory.parent / "active.json", json.dumps({"task_id": previous["task_id"]}))
    rebind_sessions(directory, old)
    t["state"] = "CANCELLED"
    t["state_reason"] = f"withdrew {entry['id']}: {previous['task_id']} resumed"
    authority.withdraw(t, entry)


# Verification runs on this machine ---------------------------------------------------

def pid_alive(pid, host):
    """True/False for a PID on this host; None when it belongs to another host."""
    if host != socket.gethostname() or not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def runs_dir():
    directory = private_dir(state_root() / "runs")
    seeded = directory / ".seeded"
    if not seeded.exists():
        # Runs recorded before the index existed (an upgrade mid-run) are indexed once.
        for task_dir, t in task_records():
            if isinstance(t, dict) and (t.get("running") or {}).get("token"):
                with contextlib.suppress(DmdError, OSError):
                    atomic(directory / (ident(t["running"]["token"]) + ".json"),
                           json.dumps({"task_dir": str(task_dir), "token": t["running"]["token"]}))
        atomic(seeded, "")
    return directory

def index_run(directory, running):
    """Record a started run in the machine-wide run index, so a preflight reads the few
    runs in flight instead of every task record in the state root."""
    atomic(runs_dir() / (ident(running["token"]) + ".json"), json.dumps({"task_dir": str(directory), "token": running["token"]}))

def unindex_run(token):
    if token:
        with contextlib.suppress(OSError, DmdError):
            (runs_dir() / (ident(token) + ".json")).unlink()

def prune_run_index():
    """Drop index entries whose task no longer records that run. Returns how many."""
    removed = 0
    for entry in sorted(runs_dir().glob("*.json")):
        try:
            ref = read_json(entry)
            running = read_json(Path(ref["task_dir"]) / "task.json").get("running") or {}
            live = running.get("token") == ref.get("token")
        except (DmdError, OSError, KeyError, TypeError, AttributeError):
            live = False
        if not live:
            with contextlib.suppress(OSError):
                entry.unlink(); removed += 1
    return removed

# A confirmation ticket outlives its tool call only when the operator denied the prompt
# (no PostToolUse consumes it). A day is far longer than any prompt waits for an answer.
TICKET_TTL_SECONDS = 86400

def prune_tickets(max_age=TICKET_TTL_SECONDS):
    removed = 0
    directory = state_root() / "pending"
    for ticket in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        with contextlib.suppress(OSError):
            if time.time() - ticket.stat().st_mtime > max_age:
                ticket.unlink(); removed += 1
    return removed

# What a worktree directory holds besides its task records: the active pointer and the lock.
RECORD_DIR_BOOKKEEPING = ("active.json", ".lock")

def prune_empty_record_dirs(min_age=60):
    """Remove project and worktree directories that hold no task record: the empty ones
    versions before 0.7.1 created on every lookup, and those whose tasks were deleted
    (only the active pointer and lock remain). A directory younger than `min_age` seconds
    may belong to a task being created right now and is kept."""
    removed = 0
    top = state_root() / "v2"
    for project in sorted(top.iterdir()) if top.is_dir() else []:
        if not project.is_dir() or project.is_symlink():
            continue
        # Ages are read first: removing a worktree directory refreshes its project's mtime.
        old = [d for d in [*sorted(project.iterdir()), project]
               if d.is_dir() and not d.is_symlink() and time.time() - d.stat().st_mtime > min_age]
        for directory in old:
            with contextlib.suppress(OSError):
                if directory != project and all(p.name in RECORD_DIR_BOOKKEEPING and p.is_file() for p in directory.iterdir()):
                    for name in RECORD_DIR_BOOKKEEPING:
                        (directory / name).unlink(missing_ok=True)
                directory.rmdir(); removed += 1  # refused, and skipped, unless empty
    return removed

def other_runs(task_id, candidates):
    """Runs recorded by other tasks on this machine that touch one of our candidates. A
    live one is a concurrent writer; a dead one is a leftover the other task must recover.
    An index entry whose task no longer records that run is an orphan and is pruned."""
    found = []
    for entry in sorted(runs_dir().glob("*.json")):
        ref = None
        try:
            ref = read_json(entry)
            t = read_json(Path(ref["task_dir"]) / "task.json")
        except (DmdError, OSError, KeyError, TypeError):
            t = None
        r = t.get("running") if isinstance(t, dict) else None
        if not r or r.get("token") != (ref or {}).get("token"):
            with contextlib.suppress(OSError):
                entry.unlink()
            continue
        if t.get("task_id") == task_id:
            continue
        overlap = sorted(set(r.get("candidates") or []) & set(candidates))
        if overlap:
            found.append({"task_id": t.get("task_id"), "check": r.get("check"), "pid": r.get("pid"),
                          "host": r.get("host"), "alive": pid_alive(r.get("pid"), r.get("host")), "candidates": overlap})
    return found

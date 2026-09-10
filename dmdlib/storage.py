"""Private, atomically replaced local state. POSIX advisory locks; no security sandbox."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
import random
import re
import stat
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_STATE_BYTES = 16 * 1024 * 1024
# Default bounded wait for a contended state lock. Hooks and CLI commands on the same
# task overlap routinely (a PostToolUse event firing during `dmd run`); a short wait
# absorbs that without turning the lock into an indefinite block.
LOCK_WAIT_SECONDS = 5.0
# task.json carries a bounded tail; events.jsonl is the complete appended history.
EVENT_TAIL = 200

class DmdError(Exception):
    pass

def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
        raise DmdError("invalid identifier; use 1..96 letters, numbers, dots, underscores or hyphens")
    return value

def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DmdError(f"not an unlinked regular file: {path}")
    return info

def private_dir(path):
    path = Path(os.path.abspath(path))
    # Reject symlinked state ancestors instead of following user-controlled links.
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            if part.is_symlink() or not part.is_dir():
                raise DmdError(f"unsafe state directory: {part}")
        else:
            part.mkdir(mode=0o700)
    st = path.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise DmdError(f"state directory must be owned by this user and mode 0700: {path}")
    return path

def atomic(path, data):
    path = Path(path)
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        regular(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data if isinstance(data, bytes) else data.encode())
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def append_line(path, text):
    """Append one history line. The projection is recoverable, so replace-atomicity is unnecessary."""
    path = Path(path)
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        regular(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise DmdError(f"refusing linked history file: {path}")
        os.write(fd, text.encode() if isinstance(text, str) else text)
        os.fsync(fd)
    finally:
        os.close(fd)

def read_json(path, limit=None):
    limit = MAX_STATE_BYTES if limit is None else limit
    info = regular(Path(path))
    if info.st_size > limit:
        raise DmdError(f"state exceeds {limit} bytes: {path}")
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, UnicodeError) as exc:
        raise DmdError(f"invalid JSON: {path}") from exc

def lock_wait():
    raw = os.environ.get("DMD_LOCK_WAIT")
    if raw is None:
        return LOCK_WAIT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise DmdError("DMD_LOCK_WAIT must be a number of seconds") from exc
    if not 0 <= value <= 600:
        raise DmdError("DMD_LOCK_WAIT must be 0..600 seconds")
    return value

def acquire(fd, wait):
    """Retry a non-blocking flock with capped exponential backoff and jitter until `wait`
    seconds have elapsed. Non-blocking plus retry, rather than a blocking flock, keeps the
    deadline exact and leaves no lock waiter to strand if the holder never returns."""
    deadline = time.monotonic() + wait
    delay = 0.02
    attempts = 0
    while True:
        attempts += 1
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return attempts
        except BlockingIOError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DmdError(f"another operation owns this lock; waited {wait:g}s over {attempts} attempt(s). "
                               "Retry after it finishes, or raise DMD_LOCK_WAIT") from exc
            time.sleep(min(remaining, delay * (0.5 + random.random())))
            delay = min(delay * 2, 0.25)

@contextlib.contextmanager
def lock(directory, name=".lock", wait=None):
    private_dir(directory)
    path = directory / name
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise DmdError("refusing linked lock file")
        acquire(fd, lock_wait() if wait is None else wait)
        yield
    finally:
        os.close(fd)

# Redaction is intentionally labelled best-effort, not a guarantee for arbitrary secrets.
_PATTERNS = [
    (re.compile(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|token|password|passwd)[\"']?\s*[:=]\s*)[\"']?[^\s,;\"']+[\"']?"), r"\1[REDACTED]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,})\b"), "[REDACTED]"),
]
def redact(text):
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, str(text))
    return text

def safe_text(text, limit=2000):
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", redact(str(text)))[:limit]

def save(task_dir, task, kind, **fields):
    task["updated_at"] = now()
    task["sequence"] = task.get("sequence", 0) + 1
    event = {"sequence": task["sequence"], "at": now(), "kind": kind, **fields}
    # task.json keeps a bounded tail so an unbounded history cannot wedge the record.
    # events.jsonl is appended once per mutation and holds the complete sequence.
    events = task.setdefault("events", [])
    events.append(event)
    if len(events) > EVENT_TAIL:
        del events[:-EVENT_TAIL]
    serialized = json.dumps(task, ensure_ascii=True, indent=2) + "\n"
    if len(serialized.encode()) > MAX_STATE_BYTES:
        raise DmdError("task record exceeds the readable state limit; original record preserved, archive/migrate explicitly")
    atomic(task_dir / "task.json", serialized)
    append_line(task_dir / "events.jsonl", json.dumps(event, ensure_ascii=True) + "\n")

def evidence(task_dir, text, kind="artifact"):
    directory = task_dir / "evidence"
    private_dir(directory)
    name = f"{kind}-{uuid.uuid4().hex}.txt"
    data = redact(text).encode()
    atomic(directory / name, data)
    return {"path": "evidence/" + name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

def evidence_ok(task_dir, record):
    if not isinstance(record, dict):
        return False
    rel = record.get("path", "")
    if not isinstance(rel, str) or not re.fullmatch(r"evidence/[A-Za-z0-9_-]+\.txt", rel):
        return False
    try:
        path = task_dir / rel
        if path.parent.is_symlink():
            return False
        st = regular(path)
        if st.st_size != record.get("bytes") or st.st_size > 2 * 1024 * 1024:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == record.get("sha256")
    except (OSError, DmdError):
        return False

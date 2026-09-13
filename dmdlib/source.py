"""Content fingerprints include dirty and non-ignored untracked files; never execute repo code."""
from __future__ import annotations
import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path
from .storage import DmdError, digest

class MissingCandidate(DmdError):
    """A tree the task's evidence is bound to no longer exists at its recorded path."""

class _Tee:
    """Feed the tree hash and one per-file hash from a single read, so a snapshot can
    name which paths moved without changing the fingerprint scheme."""
    def __init__(self, tree):
        self.tree = tree
        self.own = hashlib.sha256()
    def update(self, data):
        self.tree.update(data)
        self.own.update(data)

def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None

def git_root(path):
    """Top-level directory of the checkout containing path, or None outside Git."""
    top = git(path, "rev-parse", "--show-toplevel")
    return Path(os.fsdecode(top).rstrip("\n")).resolve() if top else None

def identity(cwd):
    cwd = Path(cwd).expanduser().resolve(strict=True)
    if not cwd.is_dir():
        raise DmdError("--cwd must be a directory")
    root = git_root(cwd)
    if root is None:
        # A checkout whose Git discovery fails must not be re-keyed as a plain directory:
        # that silently moves its task into a different namespace and hides it.
        for probe in (cwd, *cwd.parents):
            if (probe / ".git").exists():
                raise DmdError(f"Git metadata exists at {probe} but Git discovery failed; check that git is on PATH and the checkout is intact")
        root = cwd
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None and (root / ".git").exists():
        raise DmdError(f"Git metadata exists at {root} but Git discovery failed; check that git is on PATH and the checkout is intact")
    project = digest(os.fsdecode(common).rstrip("\n") if common else str(root))[:24]
    return root, project, digest(str(root))[:24]

def _feed_file(tree, path, label, files=None):
    h = _Tee(tree)
    h.update(os.fsencode(label) + b"\0")
    try:
        _feed_body(h, path)
    finally:
        if files is not None:
            files[label] = h.own.hexdigest()

def _feed_body(h, path):
    try:
        before = path.lstat()
    except FileNotFoundError:
        h.update(b"missing\0")
        return
    h.update(str(stat.S_IMODE(before.st_mode)).encode() + b"\0")
    if stat.S_ISLNK(before.st_mode):
        # A symlink's destination content is an external input, not silently followed.
        h.update(b"link\0" + os.fsencode(os.readlink(path)) + b"\0")
    elif stat.S_ISREG(before.st_mode):
        h.update(b"file\0")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            # An unreadable file is visible drift (the digest names it), not a reason for
            # every command and hook on the task to fail.
            h.update(b"unreadable\0" + str(exc.errno).encode() + b"\0")
            return
        try:
            opened = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise DmdError(f"source changed while opening: {path}")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise DmdError(f"source changed during fingerprint: {path}")
        finally:
            os.close(fd)
        h.update(b"\0")
    else:
        # Sockets, fifos and devices left in a tree (a dev server's socket) are hashed by
        # type so they register as drift instead of aborting fingerprinting.
        h.update(b"special\0" + str(stat.S_IFMT(before.st_mode)).encode() + b"\0")

def head(root):
    """Current HEAD commit of the checkout at root, or None outside Git."""
    raw = git(root, "rev-parse", "HEAD")
    return os.fsdecode(raw).strip() if raw else None

def fingerprint(root, inputs=(), depth=0):
    return snapshot(root, inputs, depth)["fingerprint"]

def snapshot(root, inputs=(), depth=0):
    """One walk yields the content fingerprint (unchanged scheme), the HEAD commit and a
    per-path digest map, so drift can be diffed instead of merely detected."""
    root = Path(root)
    if depth > 8:
        raise DmdError("nested repository depth exceeds 8")
    if not root.is_dir():
        raise MissingCandidate(f"candidate root is missing: {root}; restore it, or re-point the task with dmd relocate")
    h = hashlib.sha256()
    h.update(b"dmd-source-v2\0")
    digests = {}
    head_raw = git(root, "rev-parse", "HEAD")
    h.update((head_raw or b"no-head") + b"\0")
    listing = git(root, "ls-files", "-z", "-c", "-o", "--exclude-standard")
    if listing is None:
        # A .git entry with failed discovery is not a trustworthy non-Git fallback.
        if (root / ".git").exists():
            raise DmdError("Git metadata exists but Git source discovery failed")
        files = []
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for d in list(dirs):
                p = Path(current) / d
                if p.is_symlink():
                    files.append(str(p.relative_to(root)))
                    dirs.remove(d)
            files.extend(str((Path(current) / n).relative_to(root)) for n in names)
    else:
        files = [os.fsdecode(p) for p in listing.split(b"\0") if p]
    for rel in sorted(set(files)):
        path = root / rel
        if path.is_dir() and not path.is_symlink():
            inner = snapshot(path, depth=depth+1)
            h.update(os.fsencode(rel) + b"\0submodule\0" + inner["fingerprint"].encode())
            digests[rel] = inner["fingerprint"]
        else:
            _feed_file(h, path, rel, digests)
    missing = []
    for item in sorted(set(inputs)):
        path = Path(item)
        if not path.is_absolute():
            path = root / path
        if not path.exists() and not path.is_symlink():
            # A declared input that vanished (a deleted session scratchpad, a renamed
            # fixture) is drift for the checks that declared it. It is reported per check
            # by the gate; it must never make fingerprinting the whole task raise.
            missing.append(str(path))
            h.update(os.fsencode("external:" + str(path)) + b"\0missing-input\0")
            digests["external:" + str(path)] = "missing"
            continue
        _feed_file(h, path, "external:" + str(path), digests)
    return {"fingerprint": h.hexdigest(), "head": os.fsdecode(head_raw).strip() if head_raw else None,
            "files": digests, "missing_inputs": missing}

def drift(before, after, limit=50):
    """What moved between two snapshots of the same candidate: HEAD and changed paths."""
    b, a = before.get("files", {}), after.get("files", {})
    changed = sorted(set(b) ^ set(a) | {k for k in b.keys() & a.keys() if b[k] != a[k]})
    return {"head_before": before.get("head"), "head_after": after.get("head"),
            "changed": changed[:limit], "changed_total": len(changed)}

def candidate_root(cwd, task_root):
    """Default candidate for a check: the task root when the check runs inside it, otherwise
    the checkout the check's cwd belongs to (a sibling worktree, another repository), or
    the cwd itself outside Git."""
    cwd, task_root = Path(cwd).resolve(), Path(task_root).resolve()
    if cwd == task_root or task_root in cwd.parents:
        return str(task_root)
    top = git(cwd, "rev-parse", "--show-toplevel")
    return str(Path(os.fsdecode(top).rstrip("\n")).resolve()) if top else str(cwd)

def recent_writes(root, window):
    """Dirty or untracked paths modified within the last `window` seconds. A file still
    being written by another session is a concurrent writer, not a candidate to test.
    Outside Git nothing is reported."""
    if window <= 0:
        return []
    listing = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if listing is None:
        return []
    entries = [e for e in listing.split(b"\0") if e]
    now = time.time()
    found = []
    i = 0
    while i < len(entries):
        entry = entries[i]; i += 1
        code, rel = entry[:2], os.fsdecode(entry[3:])
        if code[:1] in (b"R", b"C"):
            i += 1  # the rename source follows as its own field
        try:
            age = now - (Path(root) / rel).lstat().st_mtime
        except OSError:
            continue
        if age < window:
            found.append({"path": rel, "age_s": round(max(age, 0), 3)})
    return sorted(found, key=lambda x: x["age_s"])

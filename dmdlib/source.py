"""Content fingerprints include dirty and non-ignored untracked files; never execute repo code."""
from __future__ import annotations
import hashlib
import os
import stat
import subprocess
from pathlib import Path
from .storage import DmdError, digest

def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None

def identity(cwd):
    cwd = Path(cwd).expanduser().resolve(strict=True)
    if not cwd.is_dir():
        raise DmdError("--cwd must be a directory")
    top = git(cwd, "rev-parse", "--show-toplevel")
    root = Path(os.fsdecode(top).rstrip("\n")).resolve() if top else cwd
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    project = digest(os.fsdecode(common).rstrip("\n") if common else str(root))[:24]
    return root, project, digest(str(root))[:24]

def _feed_file(h, path, label):
    h.update(os.fsencode(label) + b"\0")
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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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
        raise DmdError(f"unsupported source input (not a file or symlink): {path}")

def fingerprint(root, inputs=(), depth=0):
    root = Path(root)
    if depth > 8:
        raise DmdError("nested repository depth exceeds 8")
    h = hashlib.sha256()
    h.update(b"dmd-source-v2\0")
    head = git(root, "rev-parse", "HEAD")
    h.update((head or b"no-head") + b"\0")
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
            h.update(os.fsencode(rel) + b"\0submodule\0" + fingerprint(path, depth=depth+1).encode())
        else:
            _feed_file(h, path, rel)
    for item in sorted(set(inputs)):
        path = Path(item)
        if not path.is_absolute():
            path = root / path
        if not path.exists() and not path.is_symlink():
            raise DmdError(f"declared input is missing: {path}")
        _feed_file(h, path, "external:" + str(path))
    return h.hexdigest()

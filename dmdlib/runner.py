"""Inspected command execution with bounded output, cancellation and held-group cleanup."""
from __future__ import annotations
import hashlib
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from .model import check_definition
from .storage import DmdError, digest
from .source import fingerprint

SHELL = str(Path("/bin/sh").resolve())
# How long to keep reading after the command exits, for output its descendants still hold.
DRAIN_SECONDS = 2.0

def approval_parts(c):
    """The approval is split so an expired one can say exactly what changed.
    PATH and interpreter stay in scope: approving `pytest` must not approve a
    different `pytest` resolved later from a different PATH."""
    inputs = []
    for p in c.get("inputs", []):
        path = Path(p)
        if not path.is_absolute():
            path = Path(c["cwd"]) / path
        if not path.is_file() or path.is_symlink():
            raise DmdError(f"approval input must be a regular file: {path}")
        inputs.append([str(path), hashlib.sha256(path.read_bytes()).hexdigest()])
    return {"definition": digest(check_definition(c)),
            "inputs": digest(inputs),
            "environment": digest({"shell": SHELL, "path": os.environ.get("PATH", ""),
                                   "platform": sys.platform, "python": sys.version})}

def approval_signature(c):
    return digest(approval_parts(c))

def approval_drift(c, recorded):
    """Name the components that moved since approval, most actionable first."""
    if not recorded:
        return ["never approved"]
    current = approval_parts(c)
    labels = {"definition": "the check definition changed",
              "inputs": "a declared input file changed",
              "environment": "the execution environment changed (shell, PATH, platform or interpreter)"}
    return [labels[k] for k in ("definition", "inputs", "environment") if recorded.get(k) != current[k]] or ["approval is stale"]

def execute(c, cancelled=lambda: False):
    start = time.monotonic()
    limit = c["max_output"]
    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)
    supervisor = Path(__file__).with_name("runner_child.py")
    proc = None
    selector = selectors.DefaultSelector()
    chunks = {"stdout": bytearray(), "stderr": bytearray(), "status": bytearray()}
    total = 0
    failure = None
    status_closed = None
    background = False
    try:
        proc = subprocess.Popen([sys.executable, str(supervisor), str(write_fd), SHELL, c["command"]],
                                cwd=c["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd); write_fd = -1
        for stream, name in [(proc.stdout, "stdout"), (proc.stderr, "stderr"), (read_fd, "status")]:
            fd = stream if isinstance(stream, int) else stream.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        while selector.get_map():
            if cancelled():
                failure = "CANCELLED"; break
            remaining = c["timeout"] - (time.monotonic() - start)
            if remaining <= 0:
                failure = "TIMEOUT"; break
            # The command itself has finished once the status pipe closes. Descendants it
            # left running still hold the inherited stdout/stderr pipes, so waiting for EOF
            # on those would time out a check that actually passed. Drain briefly, then stop
            # and record that background holders remained.
            if status_closed is not None:
                drain_left = DRAIN_SECONDS - (time.monotonic() - status_closed)
                if drain_left <= 0:
                    background = True; break
                remaining = min(remaining, drain_left)
            for key, _ in selector.select(min(0.1, remaining)):
                data = os.read(key.fd, 65536)
                name = key.data
                if not data:
                    selector.unregister(key.fd)
                    if name == "status":
                        status_closed = time.monotonic()
                    continue
                if name == "status":
                    if len(chunks[name]) + len(data) > 32:
                        failure = "INVALID_EXIT_STATUS"; break
                else:
                    total += len(data)
                    if total > limit:
                        failure = "OUTPUT_LIMIT"; break
                chunks[name].extend(data)
            if failure:
                break
    except BaseException:
        failure = "INTERRUPTED"
        raise
    finally:
        selector.close()
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
        if proc:
            # Do not reap the group leader before cleanup. It is either alive
            # in its hold loop or an unreaped child: its PID cannot be reused.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    stream.close()
    try:
        code = int(chunks["status"].decode())
    except ValueError:
        code = None
        failure = failure or "MISSING_EXIT_STATUS"
    out = chunks["stdout"].decode("utf-8", errors="replace") + "\n" + chunks["stderr"].decode("utf-8", errors="replace")
    if len(out.encode()) > limit:
        failure = "OUTPUT_LIMIT"
    # Over-limit output never turns into a successful truncated match.
    out = out.encode()[:limit].decode("utf-8", errors="replace")
    return {"exit": code, "output": out, "failure": failure, "background_holders": background,
            "duration_s": round(time.monotonic() - start, 6), "captured_bytes": total}

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
from .authority import decision_ok, unconfirmed

SHELL = str(Path("/bin/sh").resolve())
# How long to keep reading after the command exits, for output its descendants still hold.
DRAIN_SECONDS = 2.0
# Grace between SIGTERM and SIGKILL when a check is timed out or cancelled, so cleanup
# handlers (containers, temp dirs, DB rows) get a chance to run.
TERM_GRACE_SECONDS = 2.0

def approval_parts(c, legacy=False):
    """The approval is split so an expired one can say exactly what changed.
    PATH and interpreter stay in scope: approving `pytest` must not approve a
    different `pytest` resolved later from a different PATH. The interpreter is
    identified by major.minor: a patch upgrade of the Python running dmd changes
    nothing a check executes. `legacy` reproduces the pre-0.7.0 full-version form
    so approvals recorded before the upgrade stay valid."""
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
                                   "platform": sys.platform,
                                   "python": sys.version if legacy else "%d.%d" % sys.version_info[:2]})}

def approval_signature(c, legacy=False):
    return digest(approval_parts(c, legacy))

def approval_current(c, recorded_signature):
    return recorded_signature == approval_signature(c)


def approval_reason(t, c):
    recorded = t.get("approvals", {}).get(c["id"]) or {}
    try:
        current = approval_current(c, recorded.get("signature"))
    except DmdError as exc:
        return str(exc)
    if not current:
        return ("has no current inspected approval: " + "; ".join(approval_drift(c, recorded.get("parts")))
                + f". Re-inspect: dmd approve {c['id']} --note '<what you inspected>'")
    if not decision_ok(t, recorded.get("decision"), "check.approve", c["id"], recorded.get("signature")):
        return "verifier approval awaits operator confirmation; inspection notes alone cannot authorize execution"
    if any(e["op"] == "check.no-regression" and e["target"] == c["id"] for e in unconfirmed(t)):
        return "regression downgrade awaits operator confirmation"
    return None

def approval_drift(c, recorded):
    """Name the components that moved since approval, most actionable first."""
    if not recorded:
        return ["never approved"]
    current = approval_parts(c)
    labels = {"definition": "the check definition changed",
              "inputs": "a declared input file changed",
              "environment": "the execution environment changed (shell, PATH, platform or interpreter)"}
    return [labels[k] for k in ("definition", "inputs", "environment") if recorded.get(k) != current[k]] or ["approval is stale"]

def _signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # EPERM on a descendant that changed credentials: the group cannot be signalled
        # from here; report it rather than raising out of a finally block.
        return False


def _release_group(proc, graceful):
    """Terminate the held process group. A timed-out or cancelled command gets SIGTERM and
    a short grace before SIGKILL so its cleanup handlers can run; a finished command's
    supervisor is simply released."""
    if graceful and _signal_group(proc.pid, signal.SIGTERM):
        deadline = time.monotonic() + TERM_GRACE_SECONDS
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
    _signal_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


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
        try:
            proc = subprocess.Popen([sys.executable, str(supervisor), str(write_fd), SHELL, c["command"]],
                                    cwd=c["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, pass_fds=(write_fd,), start_new_session=True)
        except OSError as exc:
            # Nothing ran (the cwd is gone, the interpreter is unusable). That is a clean
            # FAIL with a named cause, not an interrupted run that wedges the ledger.
            failure = "SPAWN_FAILED"
            chunks["stderr"].extend(f"SPAWN_FAILED: {exc}".encode())
        if proc:
            os.close(write_fd); write_fd = -1
            for stream, name in [(proc.stdout, "stdout"), (proc.stderr, "stderr"), (read_fd, "status")]:
                fd = stream if isinstance(stream, int) else stream.fileno()
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, name)
        term_at = None
        while proc and selector.get_map():
            if term_at is None and cancelled():
                failure = "CANCELLED"
            remaining = c["timeout"] - (time.monotonic() - start)
            if term_at is None and remaining <= 0:
                failure = "TIMEOUT"
            if failure in ("CANCELLED", "TIMEOUT") and term_at is None:
                # Ask the group to stop and keep reading through the grace period, so a
                # cleanup handler's output lands in the evidence before SIGKILL.
                term_at = time.monotonic()
                _signal_group(proc.pid, signal.SIGTERM)
            if term_at is not None:
                grace_left = TERM_GRACE_SECONDS - (time.monotonic() - term_at)
                if grace_left <= 0:
                    break
                remaining = grace_left
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
            # A timeout or cancel keeps reading through the grace period (bounded above), so
            # cleanup output lands in the evidence; only a capture failure stops at once.
            if failure and failure not in ("TIMEOUT", "CANCELLED"):
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
            _release_group(proc, graceful=failure == "INTERRUPTED")
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
    try:
        code = int(chunks["status"].decode())
    except ValueError:
        code = None
        failure = failure or "MISSING_EXIT_STATUS"
    if failure == "SPAWN_FAILED":
        code = None
    # The limit is decided once, on raw captured bytes, inside the read loop. Re-deriving it
    # from the decoded text (where a separator or a replaced byte adds length) turned a
    # genuine pass at exactly the limit into OUTPUT_LIMIT.
    out = chunks["stdout"].decode("utf-8", errors="replace") + "\n" + chunks["stderr"].decode("utf-8", errors="replace")
    return {"exit": code, "output": out, "failure": failure, "background_holders": background,
            "duration_s": round(time.monotonic() - start, 6), "captured_bytes": total}

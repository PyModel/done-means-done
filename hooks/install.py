#!/usr/bin/env python3
"""Preview/apply exact hook registrations. Preserve unrelated settings; explicit --apply only."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import shlex
import stat
import sys
import tempfile
import uuid
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dmdlib.storage import DmdError, atomic, digest, lock, private_dir, read_json

# Event names this installer registers; also used to recognize its own orphaned registrations.
EVENTS = {"session-start", "stop", "task-completed", "post-tool-use", "post-tool-failure"}


def validate(data):
    if not isinstance(data, dict):
        raise DmdError("settings root must be an object")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise DmdError("settings hooks must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise DmdError(f"hooks.{event} must be an array")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or any(not isinstance(h, dict) for h in group["hooks"]):
                raise DmdError("invalid hook group; existing settings were not changed")


def write_settings(path, data):
    parent = path.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise DmdError("settings parent must be a real directory owned by this user")
    mode = 0o600
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DmdError("settings must be an unlinked regular file")
        mode = stat.S_IMODE(info.st_mode)
        backup = parent / (path.name + ".dmd-backup-" + uuid.uuid4().hex)
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
        print("backup: " + str(backup))
    fd, temporary = tempfile.mkstemp(prefix=".dmd-settings-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--settings", default=str(Path.home() / ".claude/settings.json"))
    p.add_argument("--state-dir", default=os.environ.get("DMD_STATE", str(Path.home() / ".local/state/done-means-done")))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--remove", action="store_true")
    args = p.parse_args(argv)
    # Canonicalize the containing directory so a legitimately symlinked settings home
    # (a dotfiles checkout, or /tmp and /var on macOS) resolves to its physical path.
    # The settings file itself is still refused if it is a symlink: writing through it
    # would replace the link and land in an unexpected target.
    supplied = Path(args.settings).expanduser()
    path = Path(os.path.realpath(supplied.parent)) / supplied.name
    state = Path(os.path.realpath(Path(args.state_dir).expanduser()))
    runtime = Path(__file__).resolve().parents[1] / "bin/dmd"
    command = shlex.join(["env", "DMD_STATE=" + str(state), sys.executable, str(runtime), "hook"])
    desired = [
        ("SessionStart", None, command + " session-start", 30),
        ("Stop", None, command + " stop", 30),
        ("TaskCompleted", None, command + " task-completed", 30),
        ("PostToolUse", "Edit|Write|Bash", command + " post-tool-use", 10),
        ("PostToolUseFailure", "Bash", command + " post-tool-failure", 10),
    ]
    manifest_path = state / "installations" / (digest(str(path)) + ".json")
    # Preview is read-only, including state and settings directories.
    def owned(cmd):
        """A registration this package installed, even if the manifest or interpreter moved."""
        return isinstance(cmd, str) and str(runtime) in cmd and cmd.rstrip().rsplit(" ", 1)[-1] in EVENTS

    def perform():
        if path.is_symlink():
            raise DmdError("refusing a symlinked settings file; point --settings at the physical file")
        raw = path.read_bytes() if path.exists() else None
        data = json.loads(raw) if raw is not None else {}
        validate(data)
        previous = read_json(manifest_path) if manifest_path.exists() else {"commands": []}
        managed = set(previous.get("commands", [])) | {x[2] for x in desired}
        hooks = data.setdefault("hooks", {})
        for event, groups in list(hooks.items()):
            kept = []
            for group in groups:
                remaining = [h for h in group["hooks"]
                             if h.get("command") not in managed and not owned(h.get("command"))]
                if remaining:
                    kept.append({**group, "hooks": remaining})
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if not args.remove:
            for event, matcher, cmd, timeout in desired:
                group = {"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}
                if matcher is not None:
                    group["matcher"] = matcher
                hooks.setdefault(event, []).append(group)
        if not hooks:
            data.pop("hooks", None)
        if not args.apply:
            print(json.dumps({"preview": True, "settings": str(path), "remove": args.remove, "result": data}, indent=2))
            return 0
        # Detect ordinary concurrent changes before replacement; external writers
        # that ignore this installer lock are not a transactionally isolated service.
        if (path.read_bytes() if path.exists() else None) != raw:
            raise DmdError("settings changed during installation; retry after reconciling")
        original = json.loads(raw) if raw is not None else {}
        if data != original:
            write_settings(path, data)
        else:
            print("settings unchanged")
        private_dir(manifest_path.parent)
        atomic(manifest_path, json.dumps({"settings": str(path), "commands": [] if args.remove else [x[2] for x in desired]}, indent=2))
        print("hook registrations removed; state preserved" if args.remove else "hook registrations installed; use dmd config to select observe/enforce")
        return 0
    try:
        if args.apply:
            with lock(private_dir(state), ".installer.lock"):
                return perform()
        return perform()
    except (DmdError, OSError, ValueError) as exc:
        print("dmd installer: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

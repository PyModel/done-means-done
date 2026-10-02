"""A run's finish path never strands its own results; the tree is observed outside the task
lock; hooks fail open on a deleted cwd, a racing mkdir, a contended prune or a corrupt
binding; a JSON null is not text; relocate refuses a live run and moves the sessions."""
import json
import os
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dmdlib import model as m
from dmdlib.cli import locate, load_task
from dmdlib.storage import DmdError, digest, lock, private_dir
from dmdlib.store import bind_session, binding_path, sessions_dir
from test_candidates import GitFixture, QUIET
from test_hardening import HookHarness
from test_runtime import DmdFixture


def probe_free(directory):
    """Raise unless this process could take the task lock right now (flock conflicts across
    file descriptions even inside one process, so a held lock is visible here)."""
    with lock(directory, wait=0):
        pass


class FinishPathCase(GitFixture):
    def test_check_that_deletes_its_own_candidate_is_recorded_not_stranded(self):
        self.setup_task()
        scratch = self.home / "scratch"; scratch.mkdir()
        (scratch / "nuke.py").write_text("import os, shutil\nshutil.rmtree(os.getcwd())\nprint('ACCEPTANCE_PASS:' + str(1))\n")
        cid = self.add_check(scratch, command="python3 -B nuke.py")
        out, err = self.cmd("run", cid, *QUIET, code=1)
        row = json.loads(out.strip().splitlines()[0])
        self.assertEqual(row["result"], "STALE", (out, err))
        self.assertIn(str(scratch), row["stale"]["candidate"])
        t = load_task(locate(self.repo))
        self.assertIsNone(t["running"], "the finished run must not need recover-run")
        self.assertEqual(next(c for c in t["checks"] if c["id"] == cid)["status"], "FAIL")

    def test_vanished_approval_input_is_stale_not_stranded(self):
        self.setup_task()
        helper = self.home / "helper.txt"; helper.write_text("fixture\n")
        (self.repo / "drop.py").write_text(f"import os\nos.unlink({str(helper)!r})\nprint('ACCEPTANCE_PASS:' + str(1))\n")
        cid = self.add_check(self.repo, command="python3 -B drop.py", extra=("--input", str(helper)))
        out, err = self.cmd("run", cid, *QUIET, code=1)
        self.assertEqual(json.loads(out.strip().splitlines()[0])["result"], "STALE", (out, err))
        self.assertIsNone(load_task(locate(self.repo))["running"])

    def test_tree_is_observed_outside_the_task_lock(self):
        self.setup_task()
        real = m.task_snapshot
        task_dir = locate(self.repo)
        def observed(task, *args, **kwargs):
            probe_free(task_dir)
            return real(task, *args, **kwargs)
        with patch("dmdlib.runs.task_snapshot", side_effect=observed) as snap:
            self.cmd("run", "A-01", *QUIET)
        self.assertEqual(snap.call_count, 2)

    def test_interrupt_under_a_held_lock_still_exits_130(self):
        self.setup_task()
        task_dir = locate(self.repo)
        held = threading.Event()
        def holder():
            with lock(task_dir, wait=0):
                held.set(); time.sleep(1.5)
        def interrupted(c, cancelled=lambda: False):
            threading.Thread(target=holder).start(); held.wait(5)
            raise KeyboardInterrupt("test")
        with patch("dmdlib.runs.execute", interrupted), patch.dict(os.environ, {"DMD_LOCK_WAIT": "0.3"}):
            self.cmd("run", "A-01", *QUIET, code=130)
        time.sleep(1.6)
        self.assertIsNotNone(load_task(task_dir)["running"], "the interrupted run stays recorded for recover-run")


class GateOutsideLockCase(DmdFixture):
    def test_gate_fingerprints_before_taking_the_lock(self):
        self.setup_task()
        task_dir = locate(self.repo)
        real = m.task_fingerprint
        def observed(task):
            probe_free(task_dir)
            return real(task)
        with patch("dmdlib.cli.task_fingerprint", side_effect=observed) as fp:
            self.cmd("gate", code=1)
        self.assertEqual(fp.call_count, 1)


class HookFailOpenCase(HookHarness):
    def test_fast_path_survives_a_deleted_cwd(self):
        self.setup_task()
        with patch("os.getcwd", side_effect=FileNotFoundError(2, "No such file or directory")):
            out, err = self.hook("stop")
        self.assertEqual(err, "")
        self.assertIn("Done Means Done", out)

    def test_stop_hook_fingerprints_outside_the_task_lock(self):
        self.setup_task(); self.enforce()
        task_dir = locate(self.repo)
        real = m.task_fingerprint
        def observed(task):
            probe_free(task_dir)
            return real(task)
        with patch("dmdlib.hooks.task_fingerprint", side_effect=observed) as fp:
            out, _ = self.hook("stop")
        self.assertEqual(fp.call_count, 1)
        self.assertEqual(json.loads(out).get("decision"), "block", out)

    def test_corrupt_session_binding_is_dropped(self):
        self.setup_task(); self.enforce()
        binding = binding_path("session-test"); private_dir(binding.parent); binding.write_text("{not json")
        out, _ = self.hook("stop")
        self.assertNotIn("could not evaluate", out)
        self.assertEqual(json.loads(out).get("decision"), "block", out)
        self.assertEqual(Path(json.loads(binding.read_text())["task_dir"]), locate(self.repo))

    def test_bind_session_survives_a_contended_prune_lock(self):
        self.setup_task()
        task_dir = locate(self.repo)
        held = threading.Event()
        def holder():
            with lock(sessions_dir(), ".prune.lock", wait=0):
                held.set(); time.sleep(1.5)
        thread = threading.Thread(target=holder); thread.start(); held.wait(5)
        try:
            bind_session(task_dir, "session-contended")
        finally:
            thread.join()
        self.assertEqual(Path(json.loads(binding_path("session-contended").read_text())["task_dir"]), task_dir)

    def test_private_dir_tolerates_a_concurrent_creator(self):
        target = self.state / "racing"; target.mkdir(mode=0o700, parents=True)
        real_exists = Path.exists
        def exists(self_path):
            return False if self_path == target else real_exists(self_path)
        with patch.object(Path, "exists", exists):
            self.assertEqual(private_dir(target), target)


class NullTextCase(unittest.TestCase):
    def task(self):
        root = Path(os.getcwd())
        return {"schema": 2, "task_id": "T", "root": str(root), "state": "ACTIVE", "original_request": "r", "authorization": "a",
                "amendments": [], "requirements": [{"id": "R-01", "text": "t", "anchor": "a", "status": "active"}],
                "work": [{"id": "W-01", "req": "R-01", "text": "w", "status": "todo", "deps": [], "owns": []}],
                "checks": [{"id": "A-01", "req": "R-01", "work": ["W-01"], "method": "command", "command": "x", "cwd": str(root), "expect": "e",
                            "match": "M", "timeout": 1, "max_output": 1024, "inputs": [], "regression": False, "red_match": None, "red_exit": 1,
                            "status": "NOT_RUN", "receipt": None, "red": None, "baseline": None}],
                "findings": [{"id": "F-01", "text": "f", "location": "l", "status": "suspected", "origin": "unknown", "work": [], "checks": []}],
                "blockers": [{"id": "B-01", "item": "task", "text": "b", "owner": "o", "unblock": "u", "proof": "p", "resolved": False}],
                "uncertain": [], "sessions": [], "events": []}

    def test_a_json_null_is_not_text(self):
        clean = self.task()
        self.assertEqual(m.validation_errors(clean), [])
        for group, index, key in (("requirements", 0, "text"), ("requirements", 0, "anchor"), ("work", 0, "text"), ("checks", 0, "expect"),
                                  ("findings", 0, "location"), ("findings", 0, "text"), ("blockers", 0, "owner")):
            t = self.task(); t[group][index][key] = None
            self.assertTrue(m.validation_errors(t), (group, key))

    def test_a_regression_check_needs_a_real_red_match(self):
        t = self.task(); t["checks"][0]["regression"] = True; t["checks"][0]["red_match"] = None
        self.assertTrue(any("regression requires" in e for e in m.validation_errors(t)))


class RelocateCase(GitFixture):
    def test_relocate_refuses_during_a_live_run(self):
        self.setup_task(); d = locate(self.repo); t = load_task(d)
        t["running"] = {"token": "x", "check": "A-01", "checks": ["A-01"], "started": "now", "source": {}, "candidates": [],
                        "pid": os.getpid(), "host": socket.gethostname()}
        (d / "task.json").write_text(json.dumps(t))
        moved = self.home / "moved"; moved.mkdir()
        _, err = self.cmd("--task", t["task_id"], "relocate", "--to", str(moved), "--authority", "operator moved it", code=2)
        self.assertIn("running", err)
        self.assertTrue((d / "task.json").exists())

    def test_relocate_moves_the_session_bindings(self):
        self.setup_task()
        old = locate(self.repo)
        bind_session(old, "session-test")
        moved = self.home / "moved"; self.repo.rename(moved)
        self.cmd("relocate", "--to", str(moved), "--authority", "operator moved it")
        new = Path(json.loads(binding_path("session-test").read_text())["task_dir"])
        self.assertNotEqual(new, old)
        self.assertTrue((new / "task.json").is_file())


if __name__ == "__main__":
    unittest.main()

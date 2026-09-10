"""Regressions for 0.4.1: lock contention is absorbed, hooks stay quiet under it, and a task
recorded for a sibling worktree is named instead of hidden behind 'no active task'."""
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dmdlib.cli import main
from dmdlib.storage import DmdError, lock
from test_runtime import DmdFixture


class LockRetryCase(DmdFixture):
    def hold(self, directory, seconds):
        released = threading.Event()
        def worker():
            with lock(directory, wait=0):
                released.set()
                time.sleep(seconds)
        thread = threading.Thread(target=worker); thread.start()
        released.wait(5)
        return thread

    def test_contended_lock_is_acquired_once_the_holder_releases(self):
        directory = self.state / "lockdir"; directory.mkdir(mode=0o700, parents=True)
        thread = self.hold(directory, 0.4)
        started = time.monotonic()
        with lock(directory, wait=5):
            waited = time.monotonic() - started
        thread.join()
        self.assertGreaterEqual(waited, 0.3)
        self.assertLess(waited, 3)

    def test_zero_wait_fails_immediately_and_names_the_remedy(self):
        directory = self.state / "lockdir"; directory.mkdir(mode=0o700, parents=True)
        thread = self.hold(directory, 0.5)
        with self.assertRaises(DmdError) as ctx:
            with lock(directory, wait=0):
                pass
        thread.join()
        self.assertIn("DMD_LOCK_WAIT", str(ctx.exception))

    def test_wait_expires_after_the_configured_deadline(self):
        directory = self.state / "lockdir"; directory.mkdir(mode=0o700, parents=True)
        thread = self.hold(directory, 2)
        started = time.monotonic()
        with self.assertRaises(DmdError):
            with lock(directory, wait=0.3):
                pass
        elapsed = time.monotonic() - started
        thread.join()
        self.assertGreaterEqual(elapsed, 0.3); self.assertLess(elapsed, 1.5)

    def test_environment_override_is_validated(self):
        directory = self.state / "lockdir"; directory.mkdir(mode=0o700, parents=True)
        with patch.dict(os.environ, {"DMD_LOCK_WAIT": "abc"}):
            with self.assertRaises(DmdError):
                with lock(directory):
                    pass
        with patch.dict(os.environ, {"DMD_LOCK_WAIT": "0"}):
            with lock(directory):
                pass

    def test_cli_mutation_waits_out_a_concurrent_holder(self):
        self.setup_task()
        task_dir = self.state / "v2"
        task_dir = next(task_dir.glob("*/*/T-*"))
        thread = self.hold(task_dir, 0.4)
        self.cmd("amend", "operator clarified scope")
        thread.join()


class HookContentionCase(DmdFixture):
    def test_post_tool_use_is_silent_when_the_task_lock_is_held(self):
        self.setup_task()
        task_dir = next((self.state / "v2").glob("*/*/T-*"))
        released = threading.Event()
        def worker():
            with lock(task_dir, wait=0):
                released.set(); time.sleep(1.6)
        thread = threading.Thread(target=worker); thread.start(); released.wait(5)
        out, err = self.cmd("hook", "post-tool-use", stdin=self.payload(tool_name="Edit"))
        thread.join()
        self.assertEqual(out, ""); self.assertEqual(err, "")

    def test_post_tool_use_is_recorded_when_the_lock_is_free(self):
        self.setup_task()
        self.cmd("hook", "post-tool-use", stdin=self.payload(tool_name="Edit"))
        out, _ = self.cmd("status", "--json")
        kinds = [e["kind"] for e in json.loads(out)["task"]["events"]]
        self.assertIn("hook.post-tool-use", kinds)


class SiblingWorktreeCase(DmdFixture):
    def git(self, *args, cwd=None):
        subprocess.run(["git", "-C", str(cwd or self.repo), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})

    def linked_worktree(self):
        self.git("init", "-q"); self.git("add", "."); self.git("commit", "-q", "-m", "base")
        other = self.home / "linked"
        self.git("worktree", "add", "-q", str(other), "-b", "feature")
        return other.resolve()

    def test_no_active_task_names_the_sibling_worktree_task(self):
        other = self.linked_worktree()
        self.setup_task()
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            code = main(["--cwd", str(other), "status"])
        self.assertEqual(code, 2)
        self.assertIn("no active task in this worktree", err.getvalue())
        self.assertIn("T-", err.getvalue())
        self.assertIn(str(self.repo.resolve()), err.getvalue())
        self.assertIn("--cwd", err.getvalue())

    def test_finished_sibling_tasks_are_not_suggested(self):
        other = self.linked_worktree()
        self.setup_task(); self.finalize()
        err = io.StringIO()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", err):
            main(["--cwd", str(other), "status"])
        self.assertNotIn("Related:", err.getvalue())

    def test_plain_no_task_message_without_siblings(self):
        err = io.StringIO()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", err):
            main(["--cwd", str(self.repo), "status"])
        self.assertIn("no active task in this worktree", err.getvalue())
        self.assertNotIn("Related:", err.getvalue())


if __name__ == "__main__":
    unittest.main()

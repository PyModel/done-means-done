"""Regressions for the completion contract: the agent may stop only when nothing it can act on remains."""
import json
import unittest
from dmdlib import model as m
from dmdlib.cli import load_task, locate
from dmdlib.storage import digest
import test_model
import test_runtime


class BlockedMeansNothingActionable(test_model.ModelFixture):
    """BLOCKED lets the Stop hook release the agent, so it must mean every open reason
    waits on a recorded blocker. An unblocked requirement with owed work is ACTIVE."""
    def block_second_requirement(self):
        self.t["requirements"].append({"id": "R-02", "text": "other outcome", "anchor": "request", "status": "active"})
        self.t["work"].append({"id": "W-02", "req": "R-02", "text": "other", "status": "todo", "deps": [], "owns": []})
        self.t["checks"].append(dict(self.c, id="A-02", req="R-02", work=["W-02"], status="NOT_RUN", receipt=None))
        self.t["blockers"].append({"id": "B-01", "item": "R-02", "text": "needs credentials", "owner": "operator",
                                   "unblock": "provide credentials", "proof": "HTTP 401", "resolved": False})

    def test_stale_coverage_on_an_unblocked_task_is_actionable(self):
        self.block_second_requirement(); self.seal()
        self.t["amendments"].append({"at": "now", "text": "new scope"})
        g = self.gate()
        self.assertEqual(g["status"], "ACTIVE", g)
        self.assertTrue(any(a["id"] == "coverage" for a in g["next"]), g["next"])

    def test_missing_red_baseline_on_an_unblocked_requirement_is_actionable(self):
        self.block_second_requirement()
        self.c["regression"] = True; self.c["red_match"] = "EXPECTED"
        self.c["receipt"]["definition"] = digest(m.check_definition(self.c)); self.seal()
        g = self.gate()
        self.assertEqual(g["status"], "ACTIVE", g)
        self.assertTrue(any(a["id"] == "A-01" for a in g["next"]), g["next"])

    def test_failing_fixed_verified_finding_is_actionable(self):
        self.block_second_requirement()
        self.t["findings"].append({"id": "F-01", "text": "defect", "location": "x:1", "status": "fixed-verified", "origin": "introduced",
                                   "work": ["W-01"], "checks": ["A-01"], "note": "claimed"})
        self.seal()
        g = self.gate()
        self.assertEqual(g["status"], "ACTIVE", g)
        self.assertTrue(any(a["id"] == "F-01" for a in g["next"]), g["next"])

    def test_everything_waiting_on_the_blocker_is_blocked(self):
        self.block_second_requirement(); self.seal()
        g = self.gate()
        self.assertEqual(g["status"], "BLOCKED", g)
        self.assertEqual(g["next"], [])

    def test_work_depending_on_blocked_work_waits_too(self):
        self.t["work"].append({"id": "W-02", "req": "R-01", "text": "prereq", "status": "todo", "deps": [], "owns": []})
        self.t["work"].append({"id": "W-03", "req": "R-01", "text": "after", "status": "todo", "deps": ["W-02"], "owns": []})
        self.c["work"] = ["W-01", "W-02", "W-03"]; self.c["receipt"]["definition"] = digest(m.check_definition(self.c))
        self.t["blockers"].append({"id": "B-01", "item": "W-02", "text": "vendor outage", "owner": "vendor",
                                   "unblock": "vendor restores API", "proof": "HTTP 503", "resolved": False})
        self.seal()
        self.assertEqual(self.gate()["status"], "BLOCKED", self.gate())


class SuspendedTaskHooks(test_runtime.DmdFixture):
    def test_task_completed_on_a_paused_task_does_not_reject_verified_work(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce"); self.cmd("map-host-task", "--host-id", "n42", "--work", "W-01")
        self.cmd("run", "A-01"); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("state", "PAUSED", "--reason", "operator pause")
        self.cmd("hook", "task-completed", stdin=self.payload(task_id="n42"), code=0)


class StopGovernance(test_runtime.DmdFixture):
    def enforce(self, cap="6"):
        self.cmd("config", "--mode", "enforce", "--max-no-progress", cap)

    def stop(self, **payload):
        out, _ = self.cmd("hook", "stop", stdin=self.payload(**payload))
        return json.loads(out) if out else {}

    def test_cd_into_a_subdirectory_keeps_the_task_governing(self):
        self.setup_task(); self.enforce(); sub = self.repo / "pkg"; sub.mkdir()
        self.assertEqual(self.stop(cwd=str(sub)).get("decision"), "block")

    def test_sibling_worktree_follows_its_own_task_but_not_an_empty_one(self):
        import subprocess
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(git + ["-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(git + ["-C", str(self.repo), "commit", "-qm", "base"], check=True)
        self.setup_task(); self.enforce()
        empty, busy = self.home / "wt-empty", self.home / "wt-busy"
        for wt in (empty, busy):
            subprocess.run(git + ["-C", str(self.repo), "worktree", "add", "-q", str(wt)], check=True)
        # No task of its own: the bound assignment keeps governing there.
        self.assertEqual(self.stop(cwd=str(empty)).get("decision"), "block")
        self.cmd("--cwd", str(busy), "init", "-m", "other assignment", "--authority", "operator")
        out, _ = self.cmd("hook", "stop", stdin=self.payload(cwd=str(busy)))
        lines = [json.loads(x) for x in out.splitlines()]
        self.assertIn("binding was released", lines[0]["systemMessage"])
        self.assertEqual(lines[-1].get("decision"), "block")  # now governed by the sibling's own task

    def test_unrelated_project_releases_the_binding(self):
        self.setup_task(); self.enforce(); other = self.home / "elsewhere"; other.mkdir()
        self.assertNotIn("decision", self.stop(cwd=str(other)))

    def test_adding_records_is_not_progress(self):
        self.setup_task(); self.enforce("2")
        self.stop(); self.stop()
        self.cmd("finding", "add", "noise", "--location", "subject.py:1", "--status", "suspected", "--origin", "introduced")
        self.assertNotIn("decision", self.stop(), "a new record must not reset the no-progress count")

    def test_accepted_evidence_is_progress(self):
        self.setup_task(); self.enforce("2")
        self.stop(); self.stop(); self.cmd("run", "A-01")
        self.assertEqual(self.stop().get("decision"), "block")

    def test_a_live_run_is_not_a_no_progress_loop(self):
        self.setup_task(); self.enforce("1")
        d = locate(self.repo); t = load_task(d); t["running"] = {"token": "x", "checks": ["A-01"], "check": "A-01"}
        from dmdlib.storage import save
        save(d, t, "test.running")
        for _ in range(3):
            out = self.stop(); self.assertEqual(out.get("decision"), "block"); self.assertIn("in progress", out["reason"])


class RunnerSurvivesItsParent(test_runtime.DmdFixture):
    """A killed `dmd run` must not leave its check executing unsupervised."""
    def start_run(self):
        import os, subprocess, sys, time
        from pathlib import Path
        pid_file = self.home / "check.pid"
        self.setup_task(command=f"sh -c 'echo $$ > {pid_file}; exec sleep 30'")
        dmd = Path(__file__).resolve().parents[1] / "bin" / "dmd"
        proc = subprocess.Popen([sys.executable, "-B", str(dmd), "--cwd", str(self.repo), "run", "A-01", "--quiet-window", "0"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        return proc, int(pid_file.read_text())

    def gone_within(self, pid, seconds):
        import os, time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def test_sigterm_terminates_the_check_and_records_the_interruption(self):
        import signal
        proc, pid = self.start_run()
        proc.send_signal(signal.SIGTERM); proc.wait(timeout=10)
        self.assertEqual(proc.returncode, 130)
        self.assertTrue(self.gone_within(pid, 5), "check survived SIGTERM of dmd run")
        self.assertTrue(load_task(locate(self.repo))["running"]["interrupted"])

    def test_sigkill_of_the_runner_still_stops_the_check(self):
        import signal
        proc, pid = self.start_run()
        proc.send_signal(signal.SIGKILL); proc.wait(timeout=10)
        self.assertTrue(self.gone_within(pid, 6), "check kept running after its runner was killed")

    def supervise(self, command, close_status):
        import os, subprocess, sys
        from pathlib import Path
        child = Path(__file__).resolve().parents[1] / "dmdlib" / "runner_child.py"
        r, w = os.pipe(); os.set_inheritable(w, True)
        if close_status:
            os.close(r)
        p = subprocess.Popen([sys.executable, "-B", str(child), str(w), "/bin/sh", command], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(w,), start_new_session=True)
        os.close(w)
        self.addCleanup(lambda: (p.poll() is None and os.killpg(p.pid, 9), p.wait(), p.stdout.close(), p.stderr.close(),
                                 p.stdin.close(), None if close_status else os.close(r)))
        return p

    def test_supervisor_holds_when_the_parent_stopped_listening(self):
        """The parent closes the status pipe on an interrupt or output-limit break; the
        command's exit then hit EPIPE, the interpreter finalized under the stdin watcher
        thread and aborted (SIGABRT, a macOS crash report) instead of holding the group."""
        import time
        p = self.supervise("sleep 0.2", close_status=True)
        time.sleep(2.5)  # shutdown waits ~1 s for the reader lock before aborting
        self.assertIsNone(p.poll(), f"supervisor exited ({p.returncode}): {p.stderr.read1(300) if p.poll() is not None else b''}")

    def test_supervisor_exits_cleanly_when_orphaned(self):
        import signal, time
        p = self.supervise("sleep 0.1", close_status=False)
        time.sleep(0.5)
        p.stdin.close()  # the parent is gone: the watcher terminates the whole group
        p.wait(timeout=5)
        self.assertNotEqual(p.returncode, -signal.SIGABRT)
        self.assertNotIn(b"Fatal Python error", p.stderr.read())


class LessCeremonySameGuarantees(test_runtime.DmdFixture):
    def test_author_and_approve_in_one_call(self):
        self.cmd("init", "-m", "Deliver value 42", "--authority", "operator", "--session", "session-test")
        self.cmd("req", "add", "Value is 42", "--anchor", "request")
        # No work item: the requirement is accepted directly by its check.
        out, _ = self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "VALUE=42 asserted",
                          "--match", "ACCEPTANCE_PASS:1", "--approve", "read verify.py: one assertion, token after it")
        self.assertIn("approved", out)
        self.cmd("run", "A-01", "--quiet-window", "0")
        self.cmd("coverage", "assert", "--note", "one outcome, one check")
        self.cmd("review", "--kind", "self", "--reviewer", "agent", "--note", "reviewed", "--evidence", str(self.review))
        self.assertEqual(json.loads(self.cmd("gate")[0])["status"], "COMPLETE")

    def test_edit_after_one_call_approval_still_needs_inspection(self):
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--cmd", "python3 -B verify.py --x", "--approve", "inspected")
        self.cmd("check", "edit", "--id", "A-01", "--cmd", 'python3 -c "open(\'danger\',\'w\')"')
        self.cmd("run", "A-01", code=2); self.assertFalse((self.repo / "danger").exists())


class NoCheapExits(test_runtime.DmdFixture):
    def test_a_match_token_inside_the_command_is_refused(self):
        self.cmd("init", "-m", "x", "--authority", "operator")
        self.cmd("req", "add", "Tests pass", "--anchor", "request")
        for command in ('python3 -c "print(\'DONE\')"', "npm test && echo DONE", "env echo DONE"):
            _, err = self.cmd("check", "add", "--req", "R-01", "--cmd", command, "--expect", "x", "--match", "DONE", code=2)
            self.assertIn("appears in the command text", err)

    def test_confirmed_finding_needs_an_executed_check_to_be_disproved(self):
        self.setup_task(); art = self.home / "anything.txt"; art.write_text("I looked")
        self.cmd("finding", "add", "VALUE wrong", "--location", "subject.py:1", "--status", "confirmed")
        _, err = self.cmd("finding", "set", "--id", "F-01", "--status", "disproved", "--note", "fine", "--evidence", str(art), code=2)
        self.assertIn("accepted executed check", err)
        self.cmd("run", "A-01", "--quiet-window", "0")
        self.cmd("finding", "set", "--id", "F-01", "--status", "disproved", "--check", "A-01", "--note", "A-01 asserts VALUE=42", "--evidence", str(art))

    def test_suspected_finding_is_disproved_by_evidence(self):
        self.setup_task(); art = self.home / "trace.txt"; art.write_text("traced: invariant holds")
        self.cmd("finding", "add", "maybe wrong", "--location", "subject.py:1", "--status", "suspected")
        self.cmd("finding", "set", "--id", "F-01", "--status", "disproved", "--note", "traced", "--evidence", str(art))

    def test_cancelling_a_requirement_cannot_strand_dependent_work(self):
        self.setup_task()
        self.cmd("req", "add", "Second", "--anchor", "request")
        self.cmd("work", "add", "after W-01", "--req", "R-02", "--dep", "W-01")
        _, err = self.cmd("req", "cancel", "--id", "R-01", "--authority", "operator dropped it", code=2)
        self.assertIn("W-02", err)
        self.cmd("work", "set", "--id", "W-02", "--clear-deps")
        self.cmd("req", "cancel", "--id", "R-01", "--authority", "operator dropped it")

    def test_a_stuck_plan_is_an_action_not_a_dead_end(self):
        self.setup_task(); d = locate(self.repo); t = load_task(d)
        t["requirements"].append({"id": "R-02", "text": "x", "anchor": "r", "status": "active"})
        t["work"].append({"id": "W-02", "req": "R-02", "text": "old", "status": "todo", "deps": [], "owns": [], "removed": True, "removed_reason": "gone"})
        t["work"].append({"id": "W-03", "req": "R-02", "text": "new", "status": "todo", "deps": ["W-02"], "owns": []})
        t["checks"].append(dict(t["checks"][0], id="A-02", req="R-02", work=["W-03"], status="NOT_RUN", receipt=None))
        from dmdlib.storage import save
        save(d, t, "test.stuck")
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertEqual(g["status"], "ACTIVE"); self.assertTrue(g["next"], g)


class VolatileSource(unittest.TestCase):
    def test_a_file_that_never_settles_is_drift_not_a_crash(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from dmdlib import source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "stable.txt").write_text("a"); (root / "server.log").write_text("b")
            clean = source.snapshot(root)
            real = source._feed_body
            def racing(h, path):
                if path.name == "server.log":
                    raise source._Changed("source changed during fingerprint")
                return real(h, path)
            with patch.object(source, "_feed_body", racing):
                noisy = source.snapshot(root)
            self.assertEqual(noisy["files"]["server.log"], "volatile")
            self.assertEqual(noisy["files"]["stable.txt"], clean["files"]["stable.txt"])
            self.assertNotEqual(noisy["fingerprint"], clean["fingerprint"])


if __name__ == "__main__":
    unittest.main()

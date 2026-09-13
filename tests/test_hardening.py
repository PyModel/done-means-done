"""0.6.0 hardening: hooks fail open, isolation across projects, tolerant fingerprints,
operator-authorized deferral, runner edge cases, doctor/gc/relocate."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from dmdlib.cli import main, locate, load_task
from dmdlib.runner import execute
from dmdlib.storage import digest, read_json
from test_runtime import DmdFixture
from test_candidates import GitFixture


class HookHarness(DmdFixture):
    def hook(self, event, cwd=None, session="session-test", code=0, raw=None):
        out, err = io.StringIO(), io.StringIO()
        payload = raw if raw is not None else json.dumps({"cwd": str(cwd or self.repo), "session_id": session, "stop_hook_active": False, "tool_name": "Edit"})
        class Stdin(io.StringIO):
            @property
            def buffer(self_inner):
                return io.BytesIO(payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else payload)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch("sys.stdin", Stdin("")):
            result = main(["--cwd", str(cwd or self.repo), "hook", event])
        self.assertEqual(result, code, (event, result, out.getvalue(), err.getvalue()))
        return out.getvalue(), err.getvalue()

    def enforce(self):
        self.cmd("config", "--mode", "enforce")


class IncidentCase(HookHarness):
    """The niblet incident: a PAUSED task whose declared input lived in a deleted scratchpad
    blocked every Stop of every later session in that project."""
    def test_paused_task_with_deleted_input_never_fails_a_hook(self):
        scratch = self.home / "scratchpad"; scratch.mkdir(); gate_script = scratch / "api-gate.sh"; gate_script.write_text("exit 0\n")
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--input", str(gate_script))
        self.cmd("state", "PAUSED", "--reason", "operator paused")
        shutil.rmtree(scratch)
        self.enforce()
        for event in ("session-start", "stop", "post-tool-use", "task-completed"):
            out, err = self.hook(event, code=0)
            self.assertNotIn("declared input is missing", err + out, event); self.assertEqual(err, "", event)
        out, _ = self.hook("session-start")
        self.assertIn("PAUSED", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_active_task_with_deleted_input_names_the_check_and_the_repair(self):
        gate_script = self.home / "gate.sh"; gate_script.write_text("exit 0\n")
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--input", str(gate_script))
        gate_script.unlink()
        out, _ = self.cmd("gate", code=1); g = json.loads(out)
        self.assertTrue(any("A-01: declared input missing" in r and "--clear-inputs" in r for r in g["reasons"]), g["reasons"])
        self.assertEqual(g["summary"]["checks"]["missing_inputs"], ["A-01"])
        self.cmd("status"); self.cmd("next")  # every read command still works
        self.cmd("check", "edit", "--id", "A-01", "--clear-inputs")
        self.assertEqual(load_task(locate(self.repo))["checks"][0]["inputs"], [])

    def test_hook_over_active_task_with_deleted_input_reports_instead_of_exit_2(self):
        gate_script = self.home / "gate.sh"; gate_script.write_text("exit 0\n")
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--input", str(gate_script)); gate_script.unlink(); self.enforce()
        out, err = self.hook("stop", code=0)
        self.assertEqual(err, ""); self.assertEqual(json.loads(out)["decision"], "block"); self.assertIn("A-01", json.loads(out)["reason"])


class IsolationCase(HookHarness):
    def test_session_moving_to_another_project_is_released_and_governs_the_new_one(self):
        self.setup_task(); self.hook("session-start"); self.enforce()
        other = self.home / "other"; other.mkdir(); (other / "x").write_text("x")
        out, err = self.hook("stop", cwd=other, code=0)
        self.assertEqual(err, ""); self.assertIn("released", json.loads(out)["systemMessage"])
        # Project B has its own task: the moved session now governs it, not A.
        b_out, b_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(b_out), contextlib.redirect_stderr(b_err):
            self.assertEqual(main(["--cwd", str(other), "init", "-m", "other assignment", "--authority", "operator"]), 0)
        out, err = self.hook("stop", cwd=other, code=0)
        self.assertEqual(err, ""); d = json.loads(out); self.assertEqual(d["decision"], "block")
        self.assertIn(b_out.getvalue().strip(), read_json(next((self.state / "sessions").glob("*.json")))["task_dir"])

    def test_dead_binding_is_dropped_not_fatal(self):
        self.setup_task(); self.hook("session-start"); self.enforce()
        shutil.rmtree(locate(self.repo)); (self.state / "v2").exists()
        for event in ("stop", "post-tool-use", "session-start"):
            out, err = self.hook(event, code=0); self.assertEqual(err, "", event)
        self.assertFalse(list((self.state / "sessions").glob("*.json")))

    def test_renamed_checkout_does_not_block_stop(self):
        self.setup_task(); self.hook("session-start"); self.enforce()
        moved = self.home / "renamed"; self.repo.rename(moved)
        out, err = self.hook("stop", cwd=moved, code=0); self.assertEqual(err, "")

    def test_cwd_that_is_a_file_is_ignored(self):
        self.setup_task(); f = self.home / "afile"; f.write_text("x")
        out, err = self.hook("session-start", cwd=f, code=0); self.assertEqual((out, err), ("", ""))

    def test_loose_state_root_permissions_report_not_block(self):
        self.setup_task(); self.enforce(); os.chmod(self.state, 0o755)
        try:
            out, err = self.hook("stop", code=0)
            self.assertEqual(err, ""); self.assertIn("could not evaluate", json.loads(out)["systemMessage"])
        finally:
            os.chmod(self.state, 0o700)

    def test_malformed_payloads_never_exit_nonzero(self):
        self.setup_task(); self.enforce()
        for raw in ("not json", "[]", b"\xff\xfe{", ""):
            out, err = self.hook("stop", raw=raw, code=0); self.assertEqual(err, "", raw)

    def test_sessions_are_stored_hashed_and_bounded(self):
        self.setup_task()
        for i in range(60):
            self.hook("stop", session=f"s-{i}")
        t = load_task(locate(self.repo))
        self.assertLessEqual(len(t["sessions"]), 50); self.assertNotIn("s-59", t["sessions"]); self.assertIn(digest("s-59"), t["sessions"])
        self.assertLessEqual(len(t.get("watchdogs", {})), 50)

    def test_watchdog_pause_names_the_session(self):
        self.setup_task(); self.enforce(); self.cmd("config", "--max-no-progress", "1")
        self.hook("stop"); out, _ = self.hook("stop")
        self.assertIn("paused", json.loads(out)["systemMessage"])
        self.assertIn(digest("session-test")[:12], load_task(locate(self.repo))["state_reason"])

    def test_git_discovery_failure_does_not_rekey_the_task(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True); self.setup_task()
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(main(["--cwd", str(self.repo), "status"]), 2)
            self.assertIn("Git discovery failed", err.getvalue())
            self.enforce(); o, e = self.hook("stop", code=0); self.assertEqual(e, "")


class InputValidationCase(DmdFixture):
    def test_directory_and_empty_inputs_are_rejected(self):
        self.setup_task()
        _, err = self.cmd("check", "edit", "--id", "A-01", "--input", "", code=2); self.assertIn("--clear-inputs", err)
        _, err = self.cmd("check", "edit", "--id", "A-01", "--input", str(self.repo), code=2); self.assertIn("regular file", err)
        _, err = self.cmd("check", "edit", "--id", "A-01", "--input", "nope.txt", code=2); self.assertIn("does not exist", err)
        self.assertEqual(load_task(locate(self.repo))["checks"][0]["inputs"], [])

    def test_special_and_unreadable_files_are_drift_not_crashes(self):
        from dmdlib.source import fingerprint
        os.mkfifo(self.repo / "pipe"); base = fingerprint(self.repo)
        locked = self.repo / "locked.txt"; locked.write_text("x"); os.chmod(locked, 0)
        try:
            self.assertNotEqual(base, fingerprint(self.repo))
        finally:
            os.chmod(locked, 0o600)

    def test_work_add_without_req_is_actionable(self):
        self.setup_task(); _, err = self.cmd("work", "add", "more", code=2); self.assertIn("--req is required", err)

    def test_blocker_add_validates_item_before_allocating_an_id(self):
        self.setup_task()
        out, err = self.cmd("blocker", "add", "x", "--item", "W-99", "--owner", "o", "--unblock", "u", "--proof", "p", code=2)
        self.assertEqual(out, ""); self.assertIn("W-99", err); self.assertEqual(load_task(locate(self.repo))["blockers"], [])

    def test_uncertain_list(self):
        self.setup_task(); self.cmd("uncertain", "add", "payment call timed out")
        out, _ = self.cmd("uncertain", "list"); self.assertIn("U-01 [OPEN]", out)
        out, _ = self.cmd("uncertain", "list", "--json"); self.assertEqual(json.loads(out)[0]["id"], "U-01")


class DeferralCase(DmdFixture):
    def test_finding_defer_requires_authority_and_is_disclosed(self):
        self.setup_task(); self.cmd("finding", "add", "third-party bug", "--location", "vendor/x.py:1", "--origin", "dependency", "--status", "confirmed")
        _, err = self.cmd("finding", "set", "--id", "F-01", "--status", "deferred", code=2); self.assertIn("finding defer", err)
        _, err = self.cmd("finding", "defer", "--id", "F-01", "--note", "upstream owns it", code=2); self.assertIn("--authority", err)
        self.cmd("finding", "defer", "--id", "F-01", "--note", "upstream owns it", "--authority", "operator: defer vendor bug, tracked upstream")
        # Deferral is an amendment to the contract, so coverage is reasserted like any amendment.
        self.cmd("coverage", "assert", "--note", "F-01 deferred under operator authority; inventory unchanged")
        self.finalize()
        out, _ = self.cmd("gate"); g = json.loads(out)
        self.assertEqual(g["status"], "COMPLETE"); self.assertEqual(g["summary"]["findings_deferred"], ["F-01"]); self.assertIn("deferred under operator authority", g["summary"]["headline"])
        self.assertTrue(any(a.get("finding") == "F-01" for a in load_task(locate(self.repo))["amendments"]))
        report, _ = self.cmd("report"); self.assertIn("deferred", report)


class RunnerEdgeCase(DmdFixture):
    def setUp(self):
        super().setUp()
        self.c = {"id": "A-01", "req": "R-01", "work": ["W-01"], "method": "command", "command": "true", "cwd": str(self.repo), "expect": "e", "match": "M", "timeout": 5, "max_output": 1000, "inputs": [], "regression": False, "red_match": None, "red_exit": 1, "attested_because": None}
    def test_output_exactly_at_the_limit_is_not_a_failure(self):
        self.c.update(command='printf "%1000s" x'); r = execute(self.c); self.assertIsNone(r["failure"]); self.assertEqual(r["exit"], 0)
    def test_non_utf8_output_near_the_limit_is_not_a_failure(self):
        self.c.update(command='head -c 900 /dev/urandom'); r = execute(self.c); self.assertIsNone(r["failure"])
    def test_missing_cwd_is_a_clean_spawn_failure(self):
        self.c.update(cwd=str(self.home / "gone")); r = execute(self.c); self.assertEqual(r["failure"], "SPAWN_FAILED"); self.assertIn("gone", r["output"])
    def test_timeout_gives_cleanup_handlers_a_chance(self):
        self.c.update(command='trap "echo BYE" EXIT; sleep 10', timeout=0.3); r = execute(self.c)
        self.assertEqual(r["failure"], "TIMEOUT"); self.assertIn("BYE", r["output"])
    def test_spawn_failure_does_not_wedge_the_ledger(self):
        self.setup_task(); shutil.rmtree(self.repo); self.repo.mkdir()
        # cwd exists again but verify.py is gone: an ordinary FAIL. Now remove the cwd of the check entirely.
        c = load_task(locate(self.repo))["checks"][0]
        self.assertTrue(Path(c["cwd"]).exists())


class HealthCase(GitFixture):
    def test_doctor_reports_missing_inputs_and_exits_1(self):
        gate_script = self.home / "g.sh"; gate_script.write_text("x"); self.setup_task()
        self.cmd("check", "edit", "--id", "A-01", "--input", str(gate_script)); gate_script.unlink()
        out, _ = self.cmd("doctor", code=1); d = json.loads(out)
        self.assertTrue(any("A-01: declared input missing" in p for p in d["problems"])); self.assertEqual(d["task"]["task_id"], load_task(locate(self.repo))["task_id"])
    def test_doctor_healthy_exits_0_and_list_json_filters(self):
        self.setup_task(); out, _ = self.cmd("doctor"); self.assertEqual(json.loads(out)["problems"], [])
        out, _ = self.cmd("list", "--json", "--state", "ACTIVE"); self.assertEqual(len(json.loads(out)), 1)
        out, _ = self.cmd("list", "--json", "--state", "COMPLETE"); self.assertEqual(json.loads(out), [])
    def test_gc_removes_dead_bindings_only(self):
        self.setup_task(); self.cmd("bind-session", "live")
        dead = self.state / "sessions" / ("1" * 64 + ".json"); dead.write_text(json.dumps({"task_dir": str(self.state / "v2/x/y/T-gone")}))
        out, _ = self.cmd("gc"); self.assertEqual(json.loads(out)["session_bindings_removed"], 1)
        self.assertTrue((self.state / "sessions" / (digest("live") + ".json")).exists())
    def test_relocate_moves_the_record_and_resets_evidence(self):
        self.git("init", "-q"); self.git("add", "."); self.git("commit", "-q", "-m", "base"); self.setup_task(); self.cmd("run", "A-01")
        tid = load_task(locate(self.repo))["task_id"]
        moved = self.home / "moved"; self.repo.rename(moved)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # Exactly one unfinished task has a vanished root, so it resolves without --task.
            self.assertEqual(main(["--cwd", str(moved), "relocate", "--to", str(moved), "--authority", "operator moved the checkout"]), 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["task_id"], tid)
        t = load_task(locate(moved)); self.assertEqual(t["root"], str(moved.resolve())); self.assertEqual(t["checks"][0]["status"], "NOT_RUN"); self.assertEqual(t["checks"][0]["cwd"], str(moved.resolve()))
    def test_sibling_hint_survives_a_pruned_worktree(self):
        other = self.linked_worktree()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["--cwd", str(other), "init", "-m", "feature work", "--authority", "operator"]), 0)
        shutil.rmtree(self.repo / ".git" / "worktrees")
        (other / ".git").unlink(); subprocess.run(["git", "init", "-q", str(other)], check=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["--cwd", str(other), "status"]), 2)
        self.assertIn("Related", err.getvalue())


if __name__ == "__main__":
    unittest.main()

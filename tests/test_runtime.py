"""Actual CLI, file hashing, process execution and hook protocol regression tests."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dmdlib.cli import main, locate, load_task
from dmdlib.model import task_fingerprint
from dmdlib.source import fingerprint
from dmdlib.storage import DmdError, atomic, ident, lock, private_dir, read_json, redact, save
from dmdlib.runner import execute, approval_signature

class DmdFixture(unittest.TestCase):
    """Shared harness: temporary project, isolated state, CLI driver. No tests of its own."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve(); self.repo = self.home / "project"; self.repo.mkdir()
        self.state = self.home / "state"
        self.env = patch.dict(os.environ, {"DMD_STATE": str(self.state), "PYTHONDONTWRITEBYTECODE": "1"}); self.env.start(); self.addCleanup(self.env.stop)
        (self.repo / "subject.py").write_text("VALUE = 42\n")
        (self.repo / "verify.py").write_text('from subject import VALUE\nassert VALUE == 42, "EXPECTED_42"\nprint("ACCEPTANCE_PASS:1")\n')
        self.review = self.home / "review.txt"; self.review.write_text("Reviewed request mapping, actual assertions, final source and integration. Self-review, not independent.\n")
    def cmd(self, *args, code=0, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch("sys.stdin", io.StringIO(stdin or "")):
            result = main(["--cwd", str(self.repo), *args])
        self.assertEqual(result, code, (args, result, out.getvalue(), err.getvalue()))
        return out.getvalue(), err.getvalue()
    def setup_task(self, regression=False, command="python3 -B verify.py", extra=()):
        self.cmd("init", "-m", "Deliver value 42", "--authority", "explicit operator invocation", "--session", "session-test")
        self.cmd("req", "add", "Value is 42", "--anchor", "original request")
        self.cmd("work", "add", "Implement and test value", "--req", "R-01")
        args = ["check", "add", "--req", "R-01", "--work", "W-01", "--cmd", command, "--expect", "one assertion verifies VALUE=42", "--match", "ACCEPTANCE_PASS:1", *extra]
        if regression: args += ["--regression", "--red-match", "EXPECTED_42"]
        self.cmd(*args)
        self.cmd("coverage", "assert", "--note", "Request maps to R-01 with explicit work and assertion")
        self.cmd("approve", "A-01", "--note", "Inspected local script and approved this test")
    def finalize(self):
        self.cmd("run", "A-01")
        self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("review", "--kind", "self", "--reviewer", "test reviewer", "--note", "Reviewed final request and candidate", "--evidence", str(self.review))
        out, _ = self.cmd("gate"); self.assertEqual(json.loads(out)["status"], "COMPLETE")
    def payload(self, **updates):
        p = {"cwd": str(self.repo), "session_id": "session-test", "stop_hook_active": False}; p.update(updates); return json.dumps(p)

class RuntimeCase(DmdFixture):
    def test_actual_complete_workflow_and_report(self):
        self.setup_task(); self.finalize(); out, _ = self.cmd("report", "--save"); self.assertIn("R-01", out); self.assertIn("W-01", out)
    def test_check_expectation_mismatch_fails_even_exit_zero(self):
        self.setup_task(command='python3 -c "print(123)"'); out, _ = self.cmd("run", "A-01", code=1); self.assertFalse(json.loads(out)["matched"])
    def test_unapproved_check_never_executes(self):
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--cmd", 'python3 -c "open(\'danger\',\'w\').write(\'x\')"')
        self.cmd("run", "A-01", code=2); self.assertFalse((self.repo / "danger").exists())
    def test_preview_status_do_not_execute(self):
        self.setup_task(command='python3 -c "open(\'danger\',\'w\').write(\'x\')"')
        self.cmd("preview", "A-01"); self.cmd("status"); self.assertFalse((self.repo / "danger").exists())
    def test_source_mutated_by_check_is_rejected(self):
        self.setup_task(command='python3 -c "open(\'new-source\',\'w\').write(\'x\'); print(\'ACCEPTANCE_PASS:1\')"')
        out, _ = self.cmd("run", "A-01", code=1); self.assertEqual(json.loads(out)["failure"], "CANDIDATE_OR_DEFINITION_CHANGED")
    def test_command_pass_cannot_be_manually_set(self):
        self.setup_task(); self.cmd("check", "set", "--id", "A-01", "--status", "PASS", "--note", "looks fine", code=2)
    def test_required_skip_refused(self): self.setup_task(); self.cmd("check", "set", "--id", "A-01", "--status", "NOT_APPLICABLE", "--note", "skip it", code=2)
    def test_real_red_then_green_and_finding_resolution(self):
        self.setup_task(regression=True)
        (self.repo / "subject.py").write_text("VALUE = 41\n")
        self.cmd("run", "A-01", "--red")
        (self.repo / "subject.py").write_text("VALUE = 42\n")
        self.cmd("finding", "add", "wrong value", "--location", "subject.py:1", "--status", "confirmed", "--origin", "pre-existing")
        self.cmd("run", "A-01"); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("finding", "set", "--id", "F-01", "--status", "fixed-verified", "--work", "W-01", "--check", "A-01", "--note", "Value corrected, intentional failing baseline and regression observed")
        self.cmd("coverage", "assert", "--note", "All requirements and discovered finding accounted for")
        self.cmd("review", "--kind", "self", "--reviewer", "tester", "--note", "Full request and findings reconciled", "--evidence", str(self.review))
        self.cmd("gate")
    def test_red_against_green_rejected(self): self.setup_task(regression=True); self.cmd("run", "A-01", "--red", code=1)
    def test_syntax_failure_does_not_count_as_regression(self):
        self.setup_task(regression=True, command='python3 -c "this is invalid syntax"'); out, _ = self.cmd("run", "A-01", "--red", code=1); self.assertFalse(json.loads(out)["matched"])
    def test_finding_update_does_not_reset_status_or_origin(self):
        self.setup_task(); self.cmd("finding", "add", "broken invariant", "--location", "subject.py:1", "--status", "confirmed", "--origin", "introduced")
        self.cmd("finding", "set", "--id", "F-01", "--note", "more details")
        t = load_task(locate(self.repo)); self.assertEqual(t["findings"][0]["status"], "confirmed"); self.assertEqual(t["findings"][0]["origin"], "introduced")
    def test_finding_fixed_without_evidence_refused(self):
        self.setup_task(); self.cmd("finding", "add", "broken", "--location", "subject.py:1")
        self.cmd("finding", "set", "--id", "F-01", "--status", "fixed-verified", "--note", "fixed", code=2)
    def test_initialization_collision_cannot_overwrite(self):
        self.cmd("--task", "T-fixed", "init", "-m", "original", "--authority", "operator")
        directory = locate(self.repo); before = (directory / "task.json").read_bytes()
        self.cmd("--task", "T-fixed", "init", "-m", "replacement", "--authority", "operator", "--new", code=2)
        self.assertEqual(before, (directory / "task.json").read_bytes())
    def test_new_task_preserves_old_as_paused(self):
        self.setup_task(); old = locate(self.repo); self.cmd("init", "-m", "new assignment", "--authority", "operator", "--new")
        self.assertEqual(load_task(old)["state"], "PAUSED"); self.assertNotEqual(old, locate(self.repo))
    def test_oversized_state_never_replaces_readable_record(self):
        self.setup_task(); directory = locate(self.repo); before = (directory / "task.json").read_bytes()
        with patch("dmdlib.storage.MAX_STATE_BYTES", len(before) + 100):
            self.cmd("amend", "long amendment " * 1000, code=2)
        self.assertEqual(before, (directory / "task.json").read_bytes())
        self.assertTrue(load_task(directory)["original_request"])
    def test_task_path_traversal_rejected(self): self.cmd("--task", "../escape", "init", "-m", "request", "--authority", "operator", code=2)
    def test_unowned_work_refused(self): self.setup_task(); self.cmd("work", "add", "hidden follow-up", code=2)
    def test_zero_timeout_refused(self): self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--timeout", "0", code=2)
    def test_cancel_prevents_execution_and_reactivation_needs_authority(self):
        self.setup_task(); self.cmd("state", "CANCELLED", "--reason", "operator cancelled"); self.cmd("run", "A-01", code=2)
        self.cmd("state", "ACTIVE", "--reason", "agent prefers to continue", code=2)
    def test_content_edit_preserved_mtime_is_detected(self):
        p = self.repo / "subject.py"; before = p.stat(); old = fingerprint(self.repo)
        p.write_text("VALUE = 43\n"); os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns)); self.assertNotEqual(old, fingerprint(self.repo))
    def test_metadata_only_touch_does_not_stale_content(self):
        old = fingerprint(self.repo); p = self.repo / "subject.py"; st = p.stat(); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 100000)); self.assertEqual(old, fingerprint(self.repo))
    def test_git_untracked_filename_with_newline_is_hashed(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        p = self.repo / "odd\nname.txt"; p.write_text("a"); old = fingerprint(self.repo); p.write_text("b"); self.assertNotEqual(old, fingerprint(self.repo))
    def test_git_ignored_external_input_explicitly_hashed(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text("ignored.txt\n"); p = self.repo / "ignored.txt"; p.write_text("a")
        old = fingerprint(self.repo); explicit = fingerprint(self.repo, [str(p)]); p.write_text("b")
        self.assertEqual(old, fingerprint(self.repo)); self.assertNotEqual(explicit, fingerprint(self.repo, [str(p)]))
    def test_missing_declared_input_refused(self):
        with self.assertRaises(DmdError): fingerprint(self.repo, [str(self.repo / "absent")])
    def test_symlink_input_target_is_not_silently_followed(self):
        target = self.home / "external"; target.write_text("a"); link = self.repo / "link"; link.symlink_to(target)
        old = fingerprint(self.repo); target.write_text("b"); self.assertEqual(old, fingerprint(self.repo)); link.unlink(); link.symlink_to("different"); self.assertNotEqual(old, fingerprint(self.repo))
    def test_source_permission_change_detected(self):
        p = self.repo / "subject.py"; old = fingerprint(self.repo); p.chmod(0o700); self.assertNotEqual(old, fingerprint(self.repo))
    def test_state_cannot_live_inside_verified_project(self):
        with patch.dict(os.environ, {"DMD_STATE": str(self.repo / "state")}): self.cmd("init", "-m", "request", "--authority", "operator", code=2)
    def test_evidence_private_permissions(self):
        self.setup_task(); self.cmd("run", "A-01"); directory = locate(self.repo)
        for p in (directory / "evidence").iterdir(): self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
    def test_secret_redaction(self):
        text = 'Authorization: Bearer verysecret123\napi_key="super_secret_value"\n'
        result = redact(text); self.assertNotIn("verysecret123", result); self.assertNotIn("super_secret_value", result)
    def test_second_lock_cannot_enter(self):
        p = private_dir(self.home / "locktest")
        with lock(p):
            with self.assertRaises(DmdError):
                with lock(p): pass
    def test_atomic_state_and_event_projection_agree(self):
        # task.json keeps a bounded tail; events.jsonl keeps the complete appended history.
        self.setup_task(); d = locate(self.repo); t = load_task(d)
        events = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), t["sequence"]); self.assertEqual(t["sequence"], events[-1]["sequence"])
        self.assertEqual(events[-len(t["events"]):], t["events"])
    def test_approval_path_drift_requires_reapproval(self):
        self.setup_task()
        with patch.dict(os.environ, {"PATH": os.environ["PATH"] + ":/different"}): self.cmd("run", "A-01", code=2)
    def test_approval_script_input_drift_requires_reapproval(self):
        self.setup_task(extra=("--input", str(self.repo / "verify.py")))
        with (self.repo / "verify.py").open("a") as f: f.write("# changed script\n")
        self.cmd("run", "A-01", code=2)
    def test_manual_evidence_requires_existing_file(self):
        self.setup_task(); self.cmd("check", "edit", "--id", "A-01", "--method", "manual",
                                    "--attested-because", "the observation is inherently manual")
        self.cmd("check", "set", "--id", "A-01", "--status", "PASS", "--note", "observed", "--evidence", str(self.home / "absent"), code=2)
    def test_uncertain_operation_has_explicit_resolution(self):
        self.setup_task(); self.cmd("uncertain", "add", "Did deployment happen?"); self.cmd("uncertain", "resolve", "--id", "U-01", "--proof", "provider confirms request was not submitted")
        self.assertTrue(load_task(locate(self.repo))["uncertain"][0]["resolved"])
    def test_stop_hook_continues_on_stop_hook_active(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce")
        out, _ = self.cmd("hook", "stop", stdin=self.payload()); self.assertEqual(json.loads(out)["decision"], "block")
        out, _ = self.cmd("hook", "stop", stdin=self.payload(stop_hook_active=True)); self.assertEqual(json.loads(out)["decision"], "block")
    def test_watchdog_pauses_without_claiming_completion(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce", "--max-no-progress", "2")
        for _ in range(3): self.cmd("hook", "stop", stdin=self.payload(stop_hook_active=True))
        self.assertEqual(load_task(locate(self.repo))["state"], "PAUSED")
    def test_explicit_resume_resets_no_progress_watchdogs(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce", "--max-no-progress", "1")
        for _ in range(2): self.cmd("hook", "stop", stdin=self.payload())
        self.assertEqual(load_task(locate(self.repo))["state"], "PAUSED")
        self.cmd("state", "ACTIVE", "--reason", "Operator resumed after diagnostic inspection")
        out, _ = self.cmd("hook", "stop", stdin=self.payload())
        self.assertEqual(json.loads(out).get("decision"), "block")
    def test_invalid_session_rejected_before_task_activation(self):
        self.cmd("init", "-m", "authorized request", "--authority", "operator", "--session", "x" * 513, code=2)
        self.assertIsNone(locate(self.repo))
    def test_off_mode_has_no_hook_effect(self):
        self.setup_task(); self.cmd("config", "--mode", "off"); d = locate(self.repo); before = (d / "task.json").read_bytes()
        out, err = self.cmd("hook", "session-start", stdin=self.payload()); self.assertEqual(out + err, ""); self.assertEqual(before, (d / "task.json").read_bytes())
    def test_observe_mode_does_not_block_or_pause(self):
        self.setup_task(); self.cmd("config", "--mode", "observe", "--max-no-progress", "1")
        for _ in range(3):
            out, _ = self.cmd("hook", "stop", stdin=self.payload()); self.assertNotIn("decision", json.loads(out))
        self.assertEqual(load_task(locate(self.repo))["state"], "ACTIVE")
    def test_task_completed_uses_exit_2_for_mapped_unverified_work(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce"); self.cmd("map-host-task", "--host-id", "native-42", "--work", "W-01")
        out, err = self.cmd("hook", "task-completed", stdin=self.payload(task_id="native-42"), code=2); self.assertIn("W-01", err)
    def test_task_completed_accepts_verified_local_work_before_root_review(self):
        self.setup_task(); self.cmd("config", "--mode", "enforce"); self.cmd("map-host-task", "--host-id", "native-42", "--work", "W-01")
        self.cmd("run", "A-01"); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("hook", "task-completed", stdin=self.payload(task_id="native-42"))
        self.cmd("gate", code=1)
    def test_stop_revalidates_stored_complete_after_source_change(self):
        self.setup_task(); self.finalize(); self.cmd("config", "--mode", "enforce"); (self.repo / "subject.py").write_text("VALUE = 43\n")
        out, _ = self.cmd("hook", "stop", stdin=self.payload()); self.assertEqual(json.loads(out)["decision"], "block")
    def test_session_binding_does_not_follow_another_active_task(self):
        self.setup_task(); old = locate(self.repo); self.cmd("init", "-m", "other", "--authority", "operator", "--new")
        out, _ = self.cmd("hook", "session-start", stdin=self.payload()); self.assertIn(load_task(old)["task_id"], out); self.assertIn("PAUSED", out)
    def test_session_id_cannot_traverse_state_paths(self):
        self.setup_task(); self.cmd("bind-session", "../../escape")
        self.assertTrue((self.state / "sessions").exists()); self.assertFalse((self.home / "escape.json").exists())
    def test_hook_does_not_persist_raw_payload(self):
        self.setup_task(); self.cmd("hook", "post-tool-use", stdin=self.payload(tool_name="Bash", tool_response="private-secret-do-not-log"))
        self.assertNotIn("private-secret-do-not-log", (locate(self.repo) / "task.json").read_text())

class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.c = {"cwd": self.tmp.name, "command": 'python3 -c "print(123)"', "timeout": 2, "max_output": 4096}
    def test_actual_output_and_exit_capture(self):
        r = execute(self.c); self.assertEqual(r["exit"], 0); self.assertIn("123", r["output"]); self.assertIsNone(r["failure"])
    def test_timeout_terminates_check(self):
        self.c.update(command='python3 -c "import time; time.sleep(5)"', timeout=.2)
        r = execute(self.c); self.assertEqual(r["failure"], "TIMEOUT"); self.assertLess(r["duration_s"], 3)
    def test_output_limit_is_failure_not_truncated_success(self):
        self.c.update(command='python3 -c "print(\'PASS\'+\'x\'*100000)"', max_output=1024)
        self.assertEqual(execute(self.c)["failure"], "OUTPUT_LIMIT")
    def test_cancellation_terminates_check(self):
        self.c.update(command='python3 -c "import time; time.sleep(5)"')
        r = execute(self.c, lambda: True); self.assertEqual(r["failure"], "CANCELLED")
    def test_background_descendant_holding_pipes_times_out(self):
        self.c.update(command='python3 -c "import subprocess, sys; subprocess.Popen([sys.executable,\'-c\',\'import time; time.sleep(5)\'])"', timeout=.3)
        r = execute(self.c); self.assertEqual(r["failure"], "TIMEOUT"); self.assertLess(r["duration_s"], 3)

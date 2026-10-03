"""Adversarial CLI/host simulations. No live settings or real task records are touched."""
import contextlib
import json
import os
import pty
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dmdlib import authority, model
from dmdlib.storage import digest
from dmdlib.store import load_task, locate, save_config
from test_runtime import DmdFixture


class SecurityFixture(DmdFixture):
    def setUp(self):
        super().setUp()
        self.no_operator = patch("dmdlib.authority.terminal_confirms", return_value=False)
        self.no_operator.start(); self.addCleanup(self.no_operator.stop)

    @contextlib.contextmanager
    def operator(self):
        # A simulated operator, not authenticated human evidence.
        with patch("dmdlib.authority.terminal_confirms", return_value=True):
            yield

    def start(self, request="Deliver value 42"):
        with self.operator():
            self.cmd("init", "-m", request, "--authority", "operator", "--session", "session-test")
        self.cmd("req", "add", "Value is 42", "--anchor", request)

    def add_check(self, **kw):
        return self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py",
                        "--input", str(self.repo / "verify.py"), "--expect", "value is 42",
                        "--match", "ACCEPTANCE_PASS:1", "--approve", "inspected", **kw)

    def hook(self, event, **extra):
        out, _ = self.cmd("hook", event, stdin=self.payload(**extra))
        return [json.loads(line) for line in out.splitlines()] if out else []

    def enable_channel(self):
        save_config({"mode": "enforce", "max_no_progress": 6})
        manifests = self.state / "installations"; manifests.mkdir(mode=0o700, exist_ok=True)
        (manifests / "fixture.json").write_text(json.dumps({"settings": str(self.home / "settings.json"),
                                                            "commands": ["dmd hook pre-tool-use"]}))

    def gate(self):
        return json.loads(self.cmd("next")[0])


class AuthorityBoundary(SecurityFixture):
    def test_hollow_verifier_cannot_self_approve_even_without_hooks(self):
        self.start()
        (self.repo / "verify.py").write_text('print("ACCEPTANCE_PASS:1")\n')
        self.add_check()
        _, err = self.cmd("run", "A-01", "--quiet-window", "0", code=2)
        self.assertIn("confirmation", err)
        self.assertNotEqual(self.gate()["status"], "COMPLETE")

    def test_quote_without_channel_does_not_cancel_scope(self):
        self.start(); self.cmd("req", "add", "other", "--anchor", "value 42")
        self.cmd("req", "cancel", "--id", "R-02", "--authority", "invented quote")
        self.assertTrue(any("awaits the operator" in r for r in self.gate()["reasons"]))

    def test_changed_verifier_cannot_inherit_operator_confirmation(self):
        self.start()
        with self.operator(): self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        (self.repo / "verify.py").write_text('print("ACCEPTANCE_PASS:1")\n')
        self.cmd("approve", "A-01", "--note", "weakened verifier")
        self.cmd("run", "A-01", "--quiet-window", "0", code=2)

    def test_reapproval_of_identical_definition_reuses_confirmation(self):
        self.start()
        with self.operator(): self.add_check()
        self.cmd("approve", "A-01", "--note", "reread same verifier")
        self.cmd("run", "A-01", "--quiet-window", "0")

    def test_declared_input_drift_invalidates_gate_approval_not_just_run(self):
        self.start()
        with self.operator(): self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        with self.operator(): self.cmd("approve", "A-01", "--note", "same")
        approval = load_task(locate(self.repo))["approvals"]["A-01"]
        self.assertIn("decision", approval)

    def test_report_distinguishes_command_capture_and_agent_notes(self):
        self.start()
        with self.operator(): self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        out, _ = self.cmd("report")
        self.assertIn("operator-confirmed", out)
        self.assertIn("agent-transcribed", out)
        self.assertIn("not semantic proof", out)

    def test_false_anchor_is_rejected(self):
        self.start()
        self.cmd("req", "add", "made up", "--anchor", "quote appears nowhere", code=2)

    def test_context_and_coverage_are_operator_decisions(self):
        self.start("Deliver value 42\n- migrate the database")
        self.cmd("coverage", "context", "C-01", "--note", "optional", "--authority", "invented")
        self.cmd("coverage", "assert", "--note", "everything maps")
        ops = {e["op"] for e in authority.unconfirmed(load_task(locate(self.repo)))}
        self.assertTrue({"coverage.context", "coverage.assert"} <= ops, ops)

    def test_operator_amendment_requires_confirmation(self):
        self.start(); self.cmd("amend", "Actually skip everything", "--authority", "invented")
        self.assertIn("amend", {e["op"] for e in authority.unconfirmed(load_task(locate(self.repo)))})

    def test_independent_review_is_default_and_self_policy_requires_confirmation(self):
        self.start()
        self.assertTrue(load_task(locate(self.repo))["require_independent_review"])
        self.cmd("review-policy", "self", "--authority", "invented")
        self.assertTrue(authority.unconfirmed(load_task(locate(self.repo))))

    def test_pending_decisions_are_not_dropped_after_fifty_mutations(self):
        self.start()
        for i in range(52):
            self.cmd("amend", f"Scope addition {i}", "--authority", "invented")
        self.assertEqual(len(authority.unconfirmed(load_task(locate(self.repo)))), 52)


class HostBoundary(SecurityFixture):
    def test_protected_writes_ask_in_bash_edit_and_write(self):
        self.enable_channel()
        path = str(self.state / "config.json")
        for command in (f"echo '{{}}' > '{path}'", f"sed -i '' s/enforce/off/ '{path}'",
                        f"printf '{{}}' | tee '{path}'", f"cat > '{path}' <<'EOF'\n{{}}\nEOF",
                        f"mv /tmp/other '{path}'", f"chmod 777 '{self.state}'"):
            rows = self.hook("pre-tool-use", tool_name="Bash", tool_use_id="cmd", tool_input={"command": command})
            self.assertEqual(rows[0]["hookSpecificOutput"]["permissionDecision"], "ask", command)
        for tool in ("Edit", "Write"):
            rows = self.hook("pre-tool-use", tool_name=tool, tool_use_id=tool, tool_input={"file_path": path})
            self.assertEqual(rows[0]["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_mismatched_or_failed_post_tool_event_cannot_confirm(self):
        self.start(); self.enable_channel()
        command = "dmd approve A-01 --note inspected"
        self.hook("pre-tool-use", tool_name="Bash", tool_use_id="t1", tool_input={"command": command})
        self.add_check()
        self.hook("post-tool-use", tool_name="Bash", tool_use_id="t1", tool_input={"command": "echo unrelated"})
        self.assertTrue(authority.unconfirmed(load_task(locate(self.repo))))
        self.hook("pre-tool-use", tool_name="Bash", tool_use_id="t2", tool_input={"command": command})
        self.cmd("approve", "A-01", "--note", "inspected")
        self.hook("post-tool-failure", tool_name="Bash", tool_use_id="t2", tool_input={"command": command})
        self.assertTrue(authority.unconfirmed(load_task(locate(self.repo))))

    def test_leaving_worktree_releases_the_session_not_the_obligations(self):
        self.start(); self.enable_channel()
        other = self.home / "other"; other.mkdir()
        rows = self.hook("stop", cwd=str(other))
        text = json.dumps(rows)
        self.assertIn("binding was released", text)
        self.assertIn("remains open", text)
        task = load_task(locate(self.repo))
        self.assertEqual(task["state"], "ACTIVE")
        self.assertNotEqual(self.gate()["status"], "COMPLETE")

    def test_host_prompt_cannot_be_weakened_at_init(self):
        self.enable_channel()
        request = "Fix login, export CSV, and migrate the database."
        self.hook("user-prompt-submit", prompt=request)
        self.cmd("init", "-m", "Fix login", "--authority", "operator", "--session", "session-test", code=2)
        self.cmd("init", "--from-host", "--authority", "operator", "--session", "session-test")
        task = load_task(locate(self.repo))
        self.assertEqual(task["original_request"], request)
        self.assertEqual(task["request_provenance"]["kind"], "host-captured")

    def test_host_prompt_is_scoped_to_session_and_root(self):
        self.enable_channel(); self.hook("user-prompt-submit", prompt="Full request")
        self.cmd("init", "--from-host", "--authority", "operator", "--session", "other", code=2)
        other = self.home / "other"; other.mkdir()
        self.cmd("--cwd", str(other), "init", "--from-host", "--authority", "operator", "--session", "session-test", code=2)


class FindingsAndBlockers(SecurityFixture):
    def test_task_blocker_requires_evidence_and_confirmation(self):
        self.start()
        args = ("blocker", "add", "credentials missing", "--item", "task", "--owner", "operator",
                "--unblock", "supply credentials", "--proof", "invented 401")
        self.cmd(*args, code=2)
        self.cmd(*args, "--evidence", "-", stdin="captured response")
        self.assertEqual(self.gate()["status"], "ACTIVE")
        self.assertIn("blocker.task", {e["op"] for e in authority.unconfirmed(load_task(locate(self.repo)))})

    def test_confirmed_duplicate_requires_common_executed_evidence(self):
        self.start()
        for text in ("data loss", "typo"):
            self.cmd("finding", "add", text, "--location", "subject.py:1", "--status", "confirmed")
        self.cmd("finding", "set", "--id", "F-01", "--status", "duplicate", "--duplicate", "F-02",
                 "--note", "same root cause", code=2)

    def test_baseline_exception_and_regression_downgrade_are_pending(self):
        self.start()
        with self.operator():
            self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                     "--match", "ACCEPTANCE_PASS:1", "--regression", "--red-match", "EXPECTED_42", "--approve", "inspected")
        self.cmd("check", "baseline", "--id", "A-01", "--note", "code gone", "--evidence", "-", stdin="note only")
        self.cmd("check", "edit", "--id", "A-01", "--no-regression")
        ops = {e["op"] for e in authority.unconfirmed(load_task(locate(self.repo)))}
        self.assertTrue({"check.baseline", "check.no-regression"} <= ops)

    def test_failed_runs_record_equivalent_attempts_automatically(self):
        self.start()
        with self.operator(): self.add_check()
        (self.repo / "subject.py").write_text("VALUE = 41\n")
        for _ in range(2): self.cmd("run", "A-01", "--quiet-window", "0", code=1)
        attempts = load_task(locate(self.repo))["attempts"]["A-01"]
        self.assertTrue(model.repeated_attempts(attempts))
        self.assertEqual(attempts[-1]["origin"], "runner")


class OutputFreshness(SecurityFixture):
    def test_preexisting_untracked_source_under_writes_is_not_hidden(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        src = self.repo / "src"; src.mkdir(); source = src / "new.py"; source.write_text("VALUE = 42\n")
        self.start()
        with self.operator():
            self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                     "--match", "ACCEPTANCE_PASS:1", "--writes", "src/*.py", "--approve", "inspected")
        self.cmd("run", "A-01", "--quiet-window", "0")
        source.write_text("VALUE = 0\n")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])

    def test_generated_output_is_allowed_but_subsequent_edits_are_stale(self):
        self.start()
        (self.repo / "verify.py").write_text('from pathlib import Path\nassert 1 == 1\nPath("report.txt").write_text("result")\nprint("ACCEPTANCE_PASS:1")\n')
        with self.operator():
            self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                     "--match", "ACCEPTANCE_PASS:1", "--writes", "report*.txt", "--approve", "inspected")
        self.cmd("run", "A-01", "--quiet-window", "0")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["accepted"])
        (self.repo / "report.txt").write_text("forged later")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])
        (self.repo / "report.txt").write_text("result")
        (self.repo / "report-new.txt").write_text("not generated by the check")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])


class TerminalConfirmation(unittest.TestCase):
    def test_real_pty_yes_and_no_on_supported_python(self):
        # PTY is a functional test; it also demonstrates why this is not identity auth.
        root = str(Path(__file__).resolve().parents[1])
        for response, expected in ((b"y\n", "True"), (b"n\n", "False")):
            master, slave = pty.openpty()
            code = ("import os,sys; from dmdlib.authority import terminal_confirms; "
                    "os.setsid(); import fcntl,termios; fcntl.ioctl(0,termios.TIOCSCTTY,0); "
                    "print('CONFIRMED='+str(terminal_confirms('test only')),flush=True)")
            env = dict(os.environ, PYTHONPATH=root); env.pop("DMD_NO_TTY", None)
            proc = subprocess.Popen([sys.executable, "-B", "-c", code], stdin=slave, stdout=slave, stderr=slave, env=env)
            os.close(slave)
            data = b""; answered = False; deadline = time.monotonic() + 5
            try:
                while time.monotonic() < deadline:
                    if not select.select([master], [], [], .1)[0]:
                        if proc.poll() is not None: break
                        continue
                    try: part = os.read(master, 4096)
                    except OSError: break
                    data += part
                    if b"[y/N]" in data and not answered:
                        os.write(master, response); answered = True
                    if b"CONFIRMED=" in data: break
                self.assertIn(("CONFIRMED=" + expected).encode(), data)
                self.assertTrue(answered, data)
            finally:
                if proc.poll() is None: proc.terminate()
                proc.wait(timeout=5); os.close(master)

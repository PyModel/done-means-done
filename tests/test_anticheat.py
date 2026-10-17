"""Contract decisions are approved automatically, stay logged and withdrawable, and never
loosen evidence binding. No live settings or real task records are touched."""
import json
import subprocess
import unittest

from dmdlib import authority, model
from dmdlib.store import load_task, locate, save_config, transaction
from test_runtime import DmdFixture


class SecurityFixture(DmdFixture):
    def start(self, request="Deliver value 42"):
        self.cmd("init", "-m", request, "--authority", "operator", "--session", "session-test")
        self.cmd("req", "add", "Value is 42", "--anchor", request)

    def add_check(self, **kw):
        return self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py",
                        "--input", str(self.repo / "verify.py"), "--expect", "value is 42",
                        "--match", "ACCEPTANCE_PASS:1", "--approve", "inspected", **kw)

    def hook(self, event, **extra):
        out, _ = self.cmd("hook", event, stdin=self.payload(**extra))
        return [json.loads(line) for line in out.splitlines()] if out else []

    def legacy_registration(self):
        """A pre-tool-use registration left by an installer from before automatic approval."""
        save_config({"mode": "enforce", "max_no_progress": 6})
        manifests = self.state / "installations"; manifests.mkdir(mode=0o700, exist_ok=True)
        (manifests / "fixture.json").write_text(json.dumps({"settings": str(self.home / "settings.json"),
                                                            "commands": ["dmd hook pre-tool-use"]}))

    def task(self):
        return load_task(locate(self.repo))

    def decisions(self, op):
        return [e for e in self.task().get("authority") or [] if e["op"] == op]

    def gate(self):
        return json.loads(self.cmd("next")[0])


class AutomaticApproval(SecurityFixture):
    def test_verifier_approval_is_automatic_and_logged(self):
        self.start(); self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        [entry] = self.decisions("check.approve")
        self.assertEqual(entry["confirmed"]["via"], "auto")
        self.assertEqual(self.task()["approvals"]["A-01"]["decision"], entry["id"])
        self.assertFalse(authority.unconfirmed(self.task()))

    def test_every_contract_decision_is_confirmed_when_recorded(self):
        self.start("Deliver value 42\n- migrate the database")
        self.cmd("req", "add", "other", "--anchor", "value 42")
        self.cmd("req", "cancel", "--id", "R-02", "--authority", "dropped")
        self.cmd("coverage", "context", "C-01", "--note", "optional", "--authority", "context only")
        self.cmd("amend", "Also log the value", "--authority", "scope addition")
        self.cmd("review-policy", "self", "--authority", "self review is enough")
        t = self.task()
        self.assertFalse(authority.unconfirmed(t))
        self.assertTrue({"req.cancel", "coverage.context", "amend", "review-policy"} <= {e["op"] for e in t["authority"]})
        self.assertFalse(any("awaits" in r for r in self.gate()["reasons"]))

    def test_automatic_decision_can_still_be_withdrawn(self):
        self.start(); self.cmd("req", "add", "other", "--anchor", "value 42")
        out, _ = self.cmd("req", "cancel", "--id", "R-02", "--authority", "dropped")
        self.assertIn("approved automatically", out)
        [entry] = self.decisions("req.cancel")
        self.cmd("authority", "withdraw", entry["id"])
        self.assertEqual(next(r for r in self.task()["requirements"] if r["id"] == "R-02")["status"], "active")

    def test_changed_verifier_needs_a_new_approval(self):
        self.start(); self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        (self.repo / "verify.py").write_text('print("ACCEPTANCE_PASS:1")\n')
        self.cmd("run", "A-01", "--quiet-window", "0", code=2)
        first = self.task()["approvals"]["A-01"]
        self.cmd("approve", "A-01", "--note", "weakened verifier")
        second = self.task()["approvals"]["A-01"]
        self.assertNotEqual(first["signature"], second["signature"])
        self.assertNotEqual(first["decision"], second["decision"])
        self.cmd("run", "A-01", "--quiet-window", "0")

    def test_reapproval_of_identical_definition_reuses_the_decision(self):
        self.start(); self.add_check()
        first = self.task()["approvals"]["A-01"]["decision"]
        out, _ = self.cmd("approve", "A-01", "--note", "reread same verifier")
        self.assertIn("approval reused", out)
        self.assertEqual(self.task()["approvals"]["A-01"]["decision"], first)
        self.cmd("run", "A-01", "--quiet-window", "0")

    def test_report_says_approvals_are_automatic_and_not_proof(self):
        self.start(); self.add_check()
        self.cmd("run", "A-01", "--quiet-window", "0")
        out, _ = self.cmd("report")
        self.assertIn("approved automatically", out)
        self.assertIn("agent-transcribed", out)
        self.assertIn("not semantic proof", out)

    def test_false_anchor_is_rejected(self):
        self.start()
        self.cmd("req", "add", "made up", "--anchor", "quote appears nowhere", code=2)

    def test_decisions_are_not_dropped_after_fifty_mutations(self):
        self.start()
        for i in range(52):
            self.cmd("amend", f"Scope addition {i}", "--authority", "invented")
        self.assertEqual(len(self.decisions("amend")), 52)


class LegacyDecisions(SecurityFixture):
    def test_unconfirmed_legacy_decision_blocks_until_confirmed(self):
        self.start(); self.add_check()
        directory = locate(self.repo)
        with transaction(directory, "test.legacy") as t:
            t["authority"][-1]["confirmed"] = None  # as recorded before automatic approval
        self.cmd("run", "A-01", "--quiet-window", "0", code=2)
        entry = self.task()["authority"][-1]["id"]
        self.assertTrue(any(entry in r for r in self.gate()["reasons"]))
        self.cmd("authority", "confirm", entry)
        self.assertEqual(self.task()["authority"][-1]["confirmed"]["via"], "command")
        self.cmd("run", "A-01", "--quiet-window", "0")

    def test_legacy_pre_tool_use_registration_never_asks(self):
        self.start(); self.legacy_registration()
        path = str(self.state / "config.json")
        for command in (f"echo '{{}}' > '{path}'", "dmd approve A-01 --note inspected", "dmd req cancel --id R-01 --authority x"):
            self.assertEqual(self.hook("pre-tool-use", tool_name="Bash", tool_use_id="cmd", tool_input={"command": command}), [])
        for tool in ("Edit", "Write"):
            self.assertEqual(self.hook("pre-tool-use", tool_name=tool, tool_use_id=tool, tool_input={"file_path": path}), [])

    def test_config_change_applies_immediately(self):
        self.cmd("config", "--mode", "observe")
        data = json.loads((self.state / "config.json").read_text())
        self.assertEqual(data["mode"], "observe")
        self.assertNotIn("pending", data)


class HostBoundary(SecurityFixture):
    def test_leaving_worktree_releases_the_session_not_the_obligations(self):
        self.start()
        other = self.home / "other"; other.mkdir()
        text = json.dumps(self.hook("stop", cwd=str(other)))
        self.assertIn("binding was released", text)
        self.assertIn("remains open", text)
        self.assertEqual(self.task()["state"], "ACTIVE")
        self.assertNotEqual(self.gate()["status"], "COMPLETE")

    def test_host_prompt_cannot_be_weakened_at_init(self):
        request = "Fix login, export CSV, and migrate the database."
        self.hook("user-prompt-submit", prompt=request)
        self.cmd("init", "-m", "Fix login", "--authority", "operator", "--session", "session-test", code=2)
        self.cmd("init", "--from-host", "--authority", "operator", "--session", "session-test")
        task = self.task()
        self.assertEqual(task["original_request"], request)
        self.assertEqual(task["request_provenance"]["kind"], "host-captured")

    def test_host_prompt_is_scoped_to_session_and_root(self):
        self.hook("user-prompt-submit", prompt="Full request")
        self.cmd("init", "--from-host", "--authority", "operator", "--session", "other", code=2)
        other = self.home / "other"; other.mkdir()
        self.cmd("--cwd", str(other), "init", "--from-host", "--authority", "operator", "--session", "session-test", code=2)


class FindingsAndBlockers(SecurityFixture):
    def test_task_blocker_requires_evidence(self):
        self.start()
        args = ("blocker", "add", "credentials missing", "--item", "task", "--owner", "operator",
                "--unblock", "supply credentials", "--proof", "invented 401")
        self.cmd(*args, code=2)
        self.cmd(*args, "--evidence", "-", stdin="captured response")
        [entry] = self.decisions("blocker.task")
        self.assertEqual(entry["confirmed"]["via"], "auto")
        self.assertNotEqual(self.gate()["status"], "COMPLETE")

    def test_confirmed_duplicate_requires_common_executed_evidence(self):
        self.start()
        for text in ("data loss", "typo"):
            self.cmd("finding", "add", text, "--location", "subject.py:1", "--status", "confirmed")
        self.cmd("finding", "set", "--id", "F-01", "--status", "duplicate", "--duplicate", "F-02",
                 "--note", "same root cause", code=2)

    def test_baseline_exception_and_regression_downgrade_are_logged(self):
        self.start()
        self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                 "--match", "ACCEPTANCE_PASS:1", "--regression", "--red-match", "EXPECTED_42", "--approve", "inspected")
        self.cmd("check", "baseline", "--id", "A-01", "--note", "code gone", "--evidence", "-", stdin="note only")
        self.cmd("check", "edit", "--id", "A-01", "--no-regression")
        ops = {e["op"] for e in self.task()["authority"] if e.get("confirmed")}
        self.assertTrue({"check.baseline", "check.no-regression"} <= ops)

    def test_failed_runs_record_equivalent_attempts_automatically(self):
        self.start(); self.add_check()
        (self.repo / "subject.py").write_text("VALUE = 41\n")
        for _ in range(2): self.cmd("run", "A-01", "--quiet-window", "0", code=1)
        attempts = self.task()["attempts"]["A-01"]
        self.assertTrue(model.repeated_attempts(attempts))
        self.assertEqual(attempts[-1]["origin"], "runner")


class OutputFreshness(SecurityFixture):
    def test_preexisting_untracked_source_under_writes_is_not_hidden(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        src = self.repo / "src"; src.mkdir(); source = src / "new.py"; source.write_text("VALUE = 42\n")
        self.start()
        self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                 "--match", "ACCEPTANCE_PASS:1", "--writes", "src/*.py", "--approve", "inspected")
        self.cmd("run", "A-01", "--quiet-window", "0")
        source.write_text("VALUE = 0\n")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])

    def test_generated_output_is_allowed_but_subsequent_edits_are_stale(self):
        self.start()
        (self.repo / "verify.py").write_text('from pathlib import Path\nassert 1 == 1\nPath("report.txt").write_text("result")\nprint("ACCEPTANCE_PASS:1")\n')
        self.cmd("check", "add", "--req", "R-01", "--cmd", "python3 -B verify.py", "--expect", "x",
                 "--match", "ACCEPTANCE_PASS:1", "--writes", "report*.txt", "--approve", "inspected")
        self.cmd("run", "A-01", "--quiet-window", "0")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["accepted"])
        (self.repo / "report.txt").write_text("forged later")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])
        (self.repo / "report.txt").write_text("result")
        (self.repo / "report-new.txt").write_text("not generated by the check")
        self.assertIn("A-01", self.gate()["summary"]["checks"]["stale"])


if __name__ == "__main__":
    unittest.main()

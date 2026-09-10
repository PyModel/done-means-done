"""Regressions for the 0.4.0 defect review: attestation visibility, portability, execution and recovery."""
import io
import json
import os
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dmdlib.cli import main
from test_runtime import DmdFixture


class AttestationCase(DmdFixture):
    """C1/C2: self-attested checks are marked, counted, and cannot alone accept a requirement."""

    def attested_task(self, because="No command can observe a rendered browser layout"):
        self.cmd("init", "-m", "Ship feature X, fix all bugs, add tests", "--authority", "operator invoked")
        self.cmd("req", "add", "Feature X works end to end", "--anchor", "original request")
        self.cmd("work", "add", "Build feature X", "--req", "R-01")
        args = ["check", "add", "--req", "R-01", "--work", "W-01", "--method", "manual",
                "--expect", "Feature X works end to end"]
        if because is not None:
            args += ["--attested-because", because]
        return args

    def attest_pass(self, cid="A-01"):
        note = self.home / "observed.txt"
        note.write_text("I ran the app and it worked.\n")
        self.cmd("check", "set", "--id", cid, "--status", "PASS",
                 "--note", "Observed feature X working", "--evidence", str(note))

    def test_non_command_check_requires_a_recorded_reason(self):
        self.cmd(*self.attested_task(because=None), code=2)

    def test_command_check_rejects_an_attestation_reason(self):
        self.cmd("init", "-m", "x", "--authority", "op")
        self.cmd("req", "add", "r", "--anchor", "original request")
        self.cmd("work", "add", "w", "--req", "R-01")
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--cmd", "python3 -B verify.py",
                 "--expect", "e", "--match", "ACCEPTANCE_PASS:1",
                 "--attested-because", "not applicable", code=2)

    def test_fully_attested_requirement_cannot_reach_complete(self):
        self.cmd(*self.attested_task())
        self.attest_pass()
        self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("coverage", "assert", "--note", "mapped")
        self.cmd("review", "--kind", "self", "--reviewer", "me", "--note", "final",
                 "--evidence", str(self.review), code=2)
        out, _ = self.cmd("gate", code=1)
        g = json.loads(out)
        self.assertNotEqual(g["status"], "COMPLETE")
        self.assertTrue(any("self-attested" in r for r in g["reasons"]), g["reasons"])

    def test_operator_authority_permits_an_attested_only_requirement(self):
        self.cmd(*self.attested_task())
        self.attest_pass()
        self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("req", "attest-only", "--id", "R-01",
                 "--authority", "Operator accepts manual acceptance for this outcome")
        self.cmd("coverage", "assert", "--note", "mapped")
        self.cmd("review", "--kind", "self", "--reviewer", "me", "--note", "final",
                 "--evidence", str(self.review))
        out, _ = self.cmd("gate")
        self.assertEqual(json.loads(out)["status"], "COMPLETE")

    def test_gate_counts_attested_versus_executed(self):
        self.cmd(*self.attested_task())
        self.attest_pass()
        out, _ = self.cmd("gate", code=1)
        counts = json.loads(out)["attestation"]
        self.assertEqual((counts["executed"], counts["self_attested"]), (0, 1))

    def test_report_marks_self_attested_and_executed_checks(self):
        self.cmd(*self.attested_task())
        self.attest_pass()
        out, _ = self.cmd("report")
        line = next(x for x in out.splitlines() if x.startswith("- A-01"))
        self.assertIn("SELF-ATTESTED", line)
        self.assertNotIn("EXECUTED", line)
        self.assertIn("No command can observe", out)

    def test_report_marks_an_executed_command_check(self):
        self.setup_task()
        self.cmd("run", "A-01")
        out, _ = self.cmd("report")
        line = next(x for x in out.splitlines() if x.startswith("- A-01"))
        self.assertIn("EXECUTED", line)
        self.assertNotIn("SELF-ATTESTED", line)

    def test_attested_only_authority_is_rendered_in_the_report(self):
        self.cmd(*self.attested_task())
        self.cmd("req", "attest-only", "--id", "R-01", "--authority", "Operator accepted manual acceptance")
        out, _ = self.cmd("report")
        self.assertIn("attested-only by operator authority", out)

    def test_attest_only_requires_explicit_authority(self):
        self.cmd(*self.attested_task())
        self.cmd("req", "attest-only", "--id", "R-01", code=2)

    def test_mixed_acceptance_does_not_trip_the_attested_only_rule(self):
        self.setup_task()
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--method", "manual",
                 "--expect", "operator saw the rendered page",
                 "--attested-because", "rendering cannot be asserted from a command")
        self.attest_pass("A-02")
        self.cmd("coverage", "assert", "--note", "Re-reconciled after adding the manual observation")
        self.finalize()


    def test_unrun_command_check_reports_no_attestation_reason(self):
        """A requirement with nothing accepted owes evidence, not an attestation explanation."""
        self.setup_task()
        out, _ = self.cmd("gate", code=1)
        reasons = json.loads(out)["reasons"]
        self.assertTrue(any("A-01" in r for r in reasons), reasons)
        self.assertFalse(any("self-attested" in r for r in reasons), reasons)

    def test_imported_check_cannot_record_a_result_before_reauthoring(self):
        legacy = self.home / "legacy.json"
        legacy.write_text(json.dumps({
            "schema": 1, "task_id": "T-old", "original_request": "legacy request text",
            "requirements": [{"id": "R-01", "text": "legacy outcome", "status": "active"}],
            "work_items": [{"id": "W-01", "req": "R-01", "text": "legacy work", "status": "done"}],
            "checks": [{"id": "A-01", "req": "R-01", "method": "manual", "expect": "legacy observation"}],
            "findings": [], "blockers": []}))
        self.cmd("migrate", "--from-task", str(legacy), "--authority", "operator authorized import")
        note = self.home / "observed.txt"
        note.write_text("I saw it.\n")
        self.cmd("check", "set", "--id", "A-01", "--status", "PASS",
                 "--note", "I saw it", "--evidence", str(note), code=2)
        out, _ = self.cmd("status", "--json")
        self.assertIsNone(json.loads(out)["task"]["checks"][0]["receipt"])


class PortabilityCase(DmdFixture):
    """C3: a symlinked state ancestor is canonicalized, not rejected."""

    def test_state_directory_under_a_symlinked_ancestor_works(self):
        real = self.home / "real-state"
        real.mkdir()
        link = self.home / "linked"
        link.symlink_to(real, target_is_directory=True)
        with patch.dict(os.environ, {"DMD_STATE": str(link / "state")}):
            self.cmd("init", "-m", "portable state", "--authority", "operator invoked")
            self.assertTrue((real / "state").is_dir())

    def test_state_inside_the_project_is_still_refused(self):
        with patch.dict(os.environ, {"DMD_STATE": str(self.repo / "state")}):
            self.cmd("init", "-m", "x", "--authority", "op", code=2)

    def test_symlinked_state_inside_the_project_is_refused_after_resolution(self):
        link = self.home / "sneaky"
        link.symlink_to(self.repo / "inner", target_is_directory=True)
        with patch.dict(os.environ, {"DMD_STATE": str(link)}):
            self.cmd("init", "-m", "x", "--authority", "op", code=2)


class BackgroundProcessCase(DmdFixture):
    """C4: a check that leaves a background process must not be forced to time out."""

    def test_background_holder_does_not_defeat_a_passing_check(self):
        self.setup_task(command="sleep 45 & echo ACCEPTANCE_PASS:1", extra=["--timeout", "25"])
        out, _ = self.cmd("run", "A-01")
        result = json.loads(out)
        self.assertEqual(result["result"], "PASS")
        self.assertIsNone(result["failure"])
        self.assertLess(result["duration_s"], 30)

    def test_background_holder_is_recorded_in_the_receipt(self):
        self.setup_task(command="sleep 45 & echo ACCEPTANCE_PASS:1", extra=["--timeout", "25"])
        self.cmd("run", "A-01")
        out, _ = self.cmd("status", "--json")
        receipt = json.loads(out)["task"]["checks"][0]["receipt"]
        self.assertTrue(receipt["background_holders"])

    def test_a_genuinely_slow_command_still_times_out(self):
        self.setup_task(command="sleep 30; echo ACCEPTANCE_PASS:1", extra=["--timeout", "3"])
        out, _ = self.cmd("run", "A-01", code=1)
        self.assertEqual(json.loads(out)["failure"], "TIMEOUT")

    def test_exit_status_is_still_taken_from_the_real_command(self):
        self.setup_task(command="sleep 20 & echo ACCEPTANCE_PASS:1; exit 3", extra=["--timeout", "25"])
        out, _ = self.cmd("run", "A-01", code=1)
        self.assertEqual(json.loads(out)["exit"], 3)


class LedgerCorrectionCase(DmdFixture):
    """C5: agent-authored records are correctable without deleting obligations."""

    def test_a_superseded_check_no_longer_blocks_the_gate(self):
        self.setup_task()
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--cmd", "python3 -B verify.py",
                 "--expect", "duplicate added by mistake", "--match", "ACCEPTANCE_PASS:1")
        self.cmd("check", "remove", "--id", "A-02", "--note", "Added by mistake; A-01 already covers it")
        self.cmd("coverage", "assert", "--note", "Re-reconciled after superseding the duplicate check")
        self.finalize()

    def test_a_superseded_check_is_still_reported(self):
        self.setup_task()
        self.cmd("check", "remove", "--id", "A-01", "--note", "duplicate", code=2)

    def test_removing_the_last_check_for_a_requirement_is_refused(self):
        self.setup_task()
        self.cmd("check", "remove", "--id", "A-01", "--note", "no longer needed", code=2)

    def test_a_superseded_work_item_no_longer_blocks_the_gate(self):
        self.setup_task()
        self.cmd("work", "add", "Redundant approach", "--req", "R-01")
        self.cmd("work", "remove", "--id", "W-02", "--note", "Replaced by W-01")
        self.cmd("coverage", "assert", "--note", "Re-reconciled after superseding the redundant work item")
        self.finalize()

    def test_superseded_records_appear_in_the_report(self):
        self.setup_task()
        self.cmd("work", "add", "Redundant approach", "--req", "R-01")
        self.cmd("work", "remove", "--id", "W-02", "--note", "Replaced by W-01")
        out, _ = self.cmd("report")
        self.assertIn("W-02", out)
        self.assertIn("superseded", out)

    def test_removal_requires_a_rationale(self):
        self.setup_task()
        self.cmd("work", "add", "Redundant approach", "--req", "R-01")
        self.cmd("work", "remove", "--id", "W-02", code=2)

    def test_a_superseded_work_item_cannot_satisfy_a_finding(self):
        self.setup_task(regression=True)
        self.cmd("finding", "add", "bad", "--location", "subject.py:1", "--status", "confirmed")
        self.cmd("finding", "set", "--id", "F-01", "--work", "W-01", "--check", "A-01")
        self.cmd("work", "remove", "--id", "W-01", "--note", "x", code=2)

    def test_regression_flag_can_be_corrected(self):
        self.setup_task(regression=True)
        self.cmd("check", "edit", "--id", "A-01", "--no-regression", "--match", "ACCEPTANCE_PASS:1")
        out, _ = self.cmd("status", "--json")
        self.assertFalse(json.loads(out)["task"]["checks"][0]["regression"])

    def test_regression_correction_is_recorded_in_history(self):
        self.setup_task(regression=True)
        self.cmd("check", "edit", "--id", "A-01", "--no-regression", "--match", "ACCEPTANCE_PASS:1")
        out, _ = self.cmd("status", "--json")
        history = json.loads(out)["task"]["checks"][0]["history"]
        self.assertTrue(any(h.get("definition", {}).get("regression") for h in history))

    def test_regression_and_no_regression_together_are_refused(self):
        self.setup_task(regression=True)
        self.cmd("check", "edit", "--id", "A-01", "--regression", "--no-regression", code=2)


class ListRobustnessCase(DmdFixture):
    """C6: one damaged record must not hide every healthy task."""

    def test_list_reports_a_damaged_record_and_still_lists_healthy_ones(self):
        self.setup_task()
        out, _ = self.cmd("list")
        healthy = json.loads(out.strip())["task_id"]
        damaged = self.state / "v2" / "broken" / "worktree" / "T-broken"
        damaged.mkdir(parents=True)
        (damaged / "task.json").write_text('{"schema": 2, "broken": true}')
        out, _ = self.cmd("list")
        rows = [json.loads(line) for line in out.strip().splitlines()]
        self.assertIn(healthy, [r.get("task_id") for r in rows])
        self.assertTrue(any(r.get("error") for r in rows), rows)


class AttemptVisibilityCase(DmdFixture):
    """C7: repeated failures are surfaced to the agent, never used to refuse completion."""

    def test_repeated_attempts_are_surfaced_in_next(self):
        self.setup_task()
        self.cmd("attempt", "W-01", "AssertionError: EXPECTED_42", "--strategy", "retry")
        self.cmd("attempt", "W-01", "AssertionError: EXPECTED_42", "--strategy", "retry again")
        out, _ = self.cmd("next")
        self.assertIn("strategy", json.dumps(json.loads(out)["next"]).lower())

    def test_repeated_attempts_are_surfaced_in_the_report(self):
        self.setup_task()
        self.cmd("attempt", "W-01", "same failure", "--strategy", "retry")
        self.cmd("attempt", "W-01", "same failure", "--strategy", "retry")
        out, _ = self.cmd("report")
        self.assertIn("Attempts", out)

    def test_attempts_never_block_completion(self):
        self.setup_task()
        self.cmd("attempt", "W-01", "same failure", "--strategy", "retry")
        self.cmd("attempt", "W-01", "same failure", "--strategy", "retry")
        self.finalize()

    def test_alternating_failures_still_demand_a_strategy_change(self):
        self.setup_task()
        for signature in ["failure A", "failure B", "failure A", "failure B"]:
            out, _ = self.cmd("attempt", "W-01", signature, "--strategy", "swap")
        self.assertIn("Change diagnostic strategy", out)


class ApprovalDiagnosticsCase(DmdFixture):
    """C8: an expired approval must say what changed."""

    def test_environment_drift_is_named_and_re_approvable(self):
        self.setup_task()
        with patch.dict(os.environ, {"PATH": os.environ["PATH"] + ":/nonexistent-dmd-test"}):
            _, err = self.cmd("run", "A-01", code=2)
            self.assertIn("environment", err.lower())
            self.assertNotIn("definition changed", err.lower())
            self.cmd("approve", "A-01", "--note", "Re-inspected after PATH change")
            self.cmd("run", "A-01")

    def test_definition_change_is_named_distinctly(self):
        self.setup_task()
        self.cmd("check", "edit", "--id", "A-01", "--expect", "a different expectation")
        _, err = self.cmd("run", "A-01", code=2)
        self.assertIn("definition", err.lower())

    def test_input_change_is_named_distinctly(self):
        self.setup_task(extra=["--input", "verify.py"])
        (self.repo / "verify.py").write_text('print("ACCEPTANCE_PASS:1")\n')
        _, err = self.cmd("run", "A-01", code=2)
        self.assertIn("input", err.lower())


class StateGrowthCase(DmdFixture):
    """C9: history is appended, not rewritten, and the record stays bounded."""

    def test_task_record_keeps_a_bounded_event_tail(self):
        self.setup_task()
        for i in range(60):
            self.cmd("finding", "add", f"anomaly {i}", "--location", f"subject.py:{i}")
        out, _ = self.cmd("status", "--json")
        task = json.loads(out)["task"]
        self.assertLessEqual(len(task["events"]), 200)
        self.assertGreater(task["sequence"], 60)

    def test_events_jsonl_keeps_the_complete_history(self):
        self.setup_task()
        for i in range(60):
            self.cmd("finding", "add", f"anomaly {i}", "--location", f"subject.py:{i}")
        out, _ = self.cmd("status", "--json")
        task = json.loads(out)["task"]
        directory = Path(json.loads(out)["task_dir"])
        events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), task["sequence"])
        self.assertEqual(events[-len(task["events"]):], task["events"])

    def test_sequence_numbers_remain_contiguous_after_trimming(self):
        self.setup_task()
        for i in range(60):
            self.cmd("finding", "add", f"anomaly {i}", "--location", f"subject.py:{i}")
        out, _ = self.cmd("status", "--json")
        directory = Path(json.loads(out)["task_dir"])
        events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))


class InternalErrorCase(DmdFixture):
    """C10: a defect in dmd must not masquerade as an ordinary usage error."""

    def test_internal_failure_is_reported_as_internal(self):
        self.setup_task()
        with patch("dmdlib.cli.render", side_effect=KeyError("boom")):
            _, err = self.cmd("status", code=3)
        self.assertIn("internal error", err.lower())
        self.assertIn("KeyError", err)

    def test_usage_errors_stay_exit_two(self):
        self.setup_task()
        self.cmd("finding", "set", "--id", "F-99", "--status", "confirmed", code=2)


class RequestFileCase(DmdFixture):
    """C11: the request file gets the same guards as every other operator-supplied file."""

    def test_symlinked_request_file_is_refused(self):
        target = self.home / "real-request.txt"
        target.write_text("do the thing")
        link = self.home / "request-link.txt"
        link.symlink_to(target)
        self.cmd("init", "--request-file", str(link), "--authority", "op", code=2)

    def test_oversized_request_file_is_refused(self):
        big = self.home / "big-request.txt"
        big.write_text("x" * (1048576 + 1))
        self.cmd("init", "--request-file", str(big), "--authority", "op", code=2)

    def test_undecodable_request_file_does_not_crash(self):
        raw = self.home / "raw-request.bin"
        raw.write_bytes(b"deliver \xff\xfe the feature")
        self.cmd("init", "--request-file", str(raw), "--authority", "op")

    def test_missing_request_file_is_a_usage_error(self):
        self.cmd("init", "--request-file", str(self.home / "absent.txt"), "--authority", "op", code=2)


class ReviewAuditCase(DmdFixture):
    """C15: a review that finds outstanding work is recorded, not discarded."""

    def test_review_attempt_against_an_incomplete_task_is_logged(self):
        self.setup_task()
        self.cmd("review", "--kind", "self", "--reviewer", "me", "--note", "Found gaps",
                 "--evidence", str(self.review), code=2)
        out, _ = self.cmd("status", "--json")
        log = json.loads(out)["task"]["review_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["outcome"], "rejected")

    def test_rejected_review_attempts_are_reported(self):
        self.setup_task()
        self.cmd("review", "--kind", "self", "--reviewer", "me", "--note", "Found gaps",
                 "--evidence", str(self.review), code=2)
        out, _ = self.cmd("report")
        self.assertIn("rejected", out)

    def test_a_rejected_attempt_does_not_satisfy_the_gate(self):
        self.setup_task()
        self.cmd("review", "--kind", "self", "--reviewer", "me", "--note", "Found gaps",
                 "--evidence", str(self.review), code=2)
        self.cmd("run", "A-01")
        self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        out, _ = self.cmd("gate", code=1)
        self.assertTrue(any("review" in r for r in json.loads(out)["reasons"]))


class SessionHygieneCase(DmdFixture):
    """C15b: stale session bindings do not accumulate forever."""

    def test_bindings_for_removed_tasks_are_pruned(self):
        self.setup_task()
        sessions = self.state / "sessions"
        stale = sessions / ("0" * 64 + ".json")
        stale.write_text(json.dumps({"task_dir": str(self.state / "v2" / "gone" / "gone" / "T-gone"),
                                     "session_hash": "0" * 64}))
        self.cmd("bind-session", "another-session")
        self.assertFalse(stale.exists())
        self.assertTrue(any(p.name != stale.name for p in sessions.glob("*.json")))


if __name__ == "__main__":
    unittest.main()

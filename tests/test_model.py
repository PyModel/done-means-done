"""Synthetic state fixtures test the predicate, not agent compliance or real software quality."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from dmdlib import model as m
from dmdlib.storage import digest, evidence, private_dir

class ModelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "project"; self.repo.mkdir()
        (self.repo / "source.txt").write_text("candidate\n")
        self.directory = private_dir(self.root / "state")
        self.art = evidence(self.directory, "SYNTHETIC TEST FIXTURE; not a real test execution")
        self.t = {"schema": 2, "task_id": "T-test", "root": str(self.repo), "state": "ACTIVE", "original_request": "Implement the whole outcome", "authorization": "test fixture", "amendments": [],
                  "requirements": [{"id": "R-01", "text": "whole outcome", "anchor": "original request", "status": "active"}],
                  "work": [{"id": "W-01", "req": "R-01", "text": "implementation", "status": "verified", "deps": [], "owns": []}],
                  "checks": [{"id": "A-01", "req": "R-01", "work": ["W-01"], "method": "command", "command": "python3 verify.py", "cwd": str(self.repo), "expect": "real behavior", "match": "PASS:1", "timeout": 2, "max_output": 1024, "inputs": [], "regression": False, "red_match": None, "red_exit": 1, "status": "PASS", "receipt": None, "red": None, "baseline": None}],
                  "findings": [], "blockers": [], "uncertain": [], "sessions": [], "events": [], "running": None, "require_independent_review": False}
        self.fp = m.task_fingerprint(self.t)
        self.c = self.t["checks"][0]
        self.c["receipt"] = {"kind": "command", "source": self.fp, "definition": digest(m.check_definition(self.c)), "artifact": self.art, "exit": 0, "matched": True, "failure": None}
        self.seal()
    def seal(self):
        self.t["coverage"] = {"digest": m.contract_digest(self.t), "note": "fixture source mapping"}
        self.t["review"] = {"signature": m.review_signature(self.t, self.fp), "artifact": self.art, "kind": "self"}
    def gate(self):
        return m.gate(self.directory, self.t)
    def blocked(self, substring=None):
        g = self.gate()
        self.assertNotEqual(g["status"], "COMPLETE")
        if substring:
            self.assertTrue(any(substring in r for r in g["reasons"]), g)
    def finding(self, **updates):
        f = {"id": "F-01", "text": "observed defect", "location": "source.txt:1", "status": "confirmed", "origin": "pre-existing", "work": [], "checks": [], "note": ""}
        f.update(updates); self.t["findings"].append(f); return f
    def test_clean_fixture_completes(self): self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_sole_attested_check_cannot_accept_a_requirement(self):
        self.c["method"] = "manual"; self.c["attested_because"] = "inherently observed by a person"
        self.c["receipt"] = {"kind": "manual", "source": self.fp, "definition": m.digest(m.check_definition(self.c)),
                             "artifact": self.art, "note": "observed"}
        self.seal(); self.blocked("self-attested")
    def test_operator_authority_allows_an_attested_only_requirement(self):
        self.c["method"] = "manual"; self.c["attested_because"] = "inherently observed by a person"
        self.c["receipt"] = {"kind": "manual", "source": self.fp, "definition": m.digest(m.check_definition(self.c)),
                             "artifact": self.art, "note": "observed"}
        self.t["requirements"][0]["attest_only"] = True
        self.t["requirements"][0]["attest_only_authority"] = "operator accepted manual acceptance"
        self.seal(); self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_attest_only_without_authority_is_invalid(self):
        self.t["requirements"][0]["attest_only"] = True; self.blocked("attested-only acceptance has no operator authority")
    def test_superseded_check_cannot_carry_a_requirement_alone(self):
        # The CLI refuses to supersede a requirement's last check; the predicate refuses
        # the resulting state too, so a hand-edited record cannot complete on nothing.
        self.c["removed"] = True; self.c["removed_reason"] = "replaced"; self.seal()
        self.blocked("missing work or acceptance mapping")
    def test_superseded_work_owes_nothing_but_is_retained(self):
        self.t["work"].append({"id": "W-02", "req": "R-01", "text": "abandoned approach", "status": "todo",
                               "deps": [], "owns": [], "note": "", "removed": True, "removed_reason": "replaced by W-01"})
        self.seal(); self.assertEqual(self.gate()["status"], "COMPLETE")
        self.assertEqual(len(self.t["work"]), 2)
    def test_superseded_record_without_a_rationale_is_invalid(self):
        self.t["work"][0]["removed"] = True; self.blocked("superseded work has no recorded rationale")
    def test_attestation_counts_are_reported(self):
        self.assertEqual(self.gate()["attestation"], {"executed": 1, "self_attested": 0})
    def test_no_requirement_is_not_completion(self):
        self.t["requirements"] = []; self.t["work"] = []; self.t["checks"] = []; self.seal(); self.blocked("no active")
    def test_omitted_new_requirement_invalidates_coverage(self):
        self.t["requirements"].append({"id": "R-02", "text": "second outcome", "anchor": "ask two", "status": "active"}); self.blocked("coverage")
    def test_missing_work_is_not_accepted(self): self.t["work"] = []; self.blocked()
    def test_missing_check_is_not_accepted(self): self.t["checks"] = []; self.seal(); self.blocked("missing work or acceptance")
    def test_orphan_work_is_invalid(self): self.t["work"][0]["req"] = None; self.blocked("invalid requirement")
    def test_duplicate_requirement_ids_invalid(self): self.t["requirements"].append(copy.deepcopy(self.t["requirements"][0])); self.blocked("duplicate")
    def test_dependency_missing_invalid(self): self.t["work"][0]["deps"] = ["W-99"]; self.blocked("missing dependency")
    def test_dependency_cycle_invalid(self): self.t["work"][0]["deps"] = ["W-01"]; self.blocked("cycle")
    def test_long_dependency_graph_no_recursion(self):
        self.t["work"] = [{"id": f"W-{n:04d}", "req": "R-01", "text": "step", "status": "todo", "deps": [f"W-{n-1:04d}"] if n else [], "owns": []} for n in range(1500)]
        self.c["work"] = ["W-0000"]
        self.assertEqual(m.validation_errors(self.t), [])
    def test_explicit_check_to_work_mapping_required(self): self.c["work"] = []; self.blocked("mapping")
    def test_check_work_cannot_cross_requirement(self):
        self.t["requirements"].append({"id": "R-02", "text": "other", "anchor": "ask two", "status": "active"}); self.c["req"] = "R-02"; self.blocked("another requirement")
    def test_unverified_work_blocks(self): self.t["work"][0]["status"] = "implemented"; self.seal(); self.blocked("W-01")
    def test_changed_source_is_stale(self): (self.repo / "source.txt").write_text("changed\n"); self.blocked("A-01")
    def test_changed_definition_is_stale(self): self.c["command"] = "python3 different.py"; self.seal(); self.blocked("A-01")
    def test_missing_receipt_not_accepted(self): self.c["receipt"] = None; self.seal(); self.blocked("A-01")
    def test_missing_fingerprint_not_accepted(self): self.c["receipt"].pop("source"); self.seal(); self.blocked("A-01")
    def test_forged_artifact_digest_not_accepted(self): self.c["receipt"]["artifact"]["sha256"] = "0" * 64; self.seal(); self.blocked("A-01")
    def test_deleted_evidence_not_accepted(self): (self.directory / self.art["path"]).unlink(); self.blocked("A-01")
    def test_edited_evidence_not_accepted(self): (self.directory / self.art["path"]).write_text("tampered"); self.blocked("A-01")
    def test_nonzero_exit_not_accepted(self): self.c["receipt"]["exit"] = 1; self.seal(); self.blocked("A-01")
    def test_unmatched_output_not_accepted(self): self.c["receipt"]["matched"] = False; self.seal(); self.blocked("A-01")
    def test_timeout_not_accepted(self): self.c["receipt"]["failure"] = "TIMEOUT"; self.seal(); self.blocked("A-01")
    def test_ordinary_prose_cannot_be_command_evidence(self): self.c["receipt"]["kind"] = "manual"; self.seal(); self.blocked("A-01")
    def test_required_skip_is_invalid(self): self.c["status"] = "NOT_APPLICABLE"; self.blocked("required skips")
    def test_unconfigured_migrated_check_not_accepted(self): self.c["needs_review"] = True; self.seal(); self.blocked("A-01")
    def test_blocker_cannot_coexist_with_complete(self):
        self.t["blockers"].append({"id": "B-01", "item": "W-01", "text": "Missing external deployment credential", "owner": "operator", "unblock": "provide sandbox credential", "proof": "authentication unavailable", "resolved": False}); self.seal(); self.blocked("B-01")
    def test_unknown_external_outcome_blocks(self): self.t["uncertain"].append({"id": "U-01", "text": "deploy outcome unknown", "resolved": False}); self.seal(); self.blocked("U-01")
    def test_inflight_check_blocks(self): self.t["running"] = {"token": "live"}; self.seal(); self.blocked("running")
    def test_paused_never_completes(self): self.t["state"] = "PAUSED"; self.assertEqual(self.gate()["status"], "PAUSED")
    def test_cancelled_never_completes(self): self.t["state"] = "CANCELLED"; self.assertEqual(self.gate()["status"], "CANCELLED")
    def test_stored_complete_revalidated(self): self.t["state"] = "COMPLETE"; (self.repo / "source.txt").write_text("changed"); self.blocked()
    def test_requirement_removal_needs_authority(self): self.t["requirements"][0]["status"] = "removed"; self.blocked("authority")
    def test_all_requirements_removed_not_success(self): self.t["requirements"][0].update(status="removed", authority="operator cancelled"); self.seal(); self.blocked("no active")
    def test_final_review_required(self): self.t["review"] = None; self.blocked("review")
    def test_final_review_invalidated_by_receipt(self): self.c["receipt"]["at"] = "later rerun"; self.blocked("review")
    def test_independent_review_not_self_review(self): self.t["require_independent_review"] = True; self.blocked("independent")
    def test_independent_review_record_accepted_when_required(self): self.t["require_independent_review"] = True; self.t["review"]["kind"] = "independent"; self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_preexisting_confirmed_finding_blocks(self): self.finding(); self.seal(); self.blocked("F-01")
    def test_suspected_finding_blocks(self): self.finding(status="suspected"); self.seal(); self.blocked("F-01")
    def test_fixed_unverified_finding_blocks(self): self.finding(status="fixed-unverified"); self.seal(); self.blocked("F-01")
    def test_logged_only_disposition_invalid(self): self.finding(status="logged-only"); self.blocked("invalid finding")
    def test_fixed_finding_without_regression_blocks(self): self.finding(status="fixed-verified", work=["W-01"], checks=["A-01"], note="fixed"); self.seal(); self.blocked("regression")
    def test_disproved_needs_artifact(self): self.finding(status="disproved", note="not a bug", source=self.fp); self.seal(); self.blocked("disproof")
    def test_disproved_with_current_evidence_accepted(self): self.finding(status="disproved", note="proved invariant", source=self.fp, artifact=self.art); self.seal(); self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_disproof_source_change_reopens(self): self.finding(status="disproved", note="proved invariant", source="old", artifact=self.art); self.seal(); self.blocked("disproof")
    def test_duplicate_missing_target_invalid(self): self.finding(status="duplicate", duplicate="F-99", note="same defect"); self.blocked("canonical")
    def test_duplicate_cycle_blocks(self): self.finding(status="duplicate", duplicate="F-01", note="same defect"); self.seal(); self.blocked("cycle")
    def test_long_duplicate_chain_no_recursion(self):
        self.finding(status="disproved", note="proved invariant", source=self.fp, artifact=self.art)
        for n in range(2, 1502): self.finding(id=f"F-{n:02d}", status="duplicate", duplicate=f"F-{n-1:02d}", note="same observation")
        self.seal(); self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_regression_requires_meaningful_baseline(self):
        self.c.update(regression=True, red_match="intentional assertion", red_exit=1)
        self.c["receipt"]["definition"] = digest(m.check_definition(self.c)); self.seal(); self.blocked("baseline")
    def test_documented_baseline_limitation_with_artifact(self):
        self.c.update(regression=True, red_match="intentional assertion", red_exit=1)
        definition = digest(m.check_definition(self.c)); self.c["receipt"]["definition"] = definition
        self.c["baseline"] = {"reason": "isolated original hardware unavailable; substitute invariant evidence reviewed", "definition": definition, "artifact": self.art}
        self.finding(status="fixed-verified", work=["W-01"], checks=["A-01"], note="remediation verified")
        self.seal(); self.assertEqual(self.gate()["status"], "COMPLETE")
    def test_red_nonintentional_exit_is_invalid(self):
        self.c.update(regression=True, red_match="intentional assertion", red_exit=1)
        self.c["red"] = {"definition": digest(m.check_definition(self.c)), "matched": True, "failure": None, "exit": 127, "artifact": self.art}
        self.assertFalse(m.red_ok(self.directory, self.c))

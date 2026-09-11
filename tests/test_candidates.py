"""Regressions for 0.5.0: evidence binds to the tree a check tested, drift is diffed and
named STALE, a run list shares one fingerprint window, concurrent writers are refused
up front, named resources serialize across tasks, records can be listed one group at a
time, and recovery says what it found."""
import fcntl
import argparse
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from dmdlib.cli import main, locate, load_task
from dmdlib.model import source_digest, task_fingerprint, check_candidate, source_for
from dmdlib.source import snapshot, drift, recent_writes, candidate_root
from test_runtime import DmdFixture

QUIET = ("--quiet-window", "0")


class GitFixture(DmdFixture):
    def git(self, *args, cwd=None):
        subprocess.run(["git", "-C", str(cwd or self.repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    def linked_worktree(self):
        self.git("init", "-q"); self.git("add", "."); self.git("commit", "-q", "-m", "base")
        other = self.home / "linked"
        self.git("worktree", "add", "-q", str(other), "-b", "feature")
        return other.resolve()

    def add_check(self, cwd, command="python3 -B verify.py", extra=()):
        out, _ = self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--cmd", command, "--run-cwd", str(cwd),
                          "--expect", "verifies VALUE", "--match", "ACCEPTANCE_PASS:1", *extra)
        cid = out.strip()
        self.cmd("approve", cid, "--note", "inspected")
        return cid


class CandidateCase(GitFixture):
    """S1: fingerprint the check's own candidate, not the task root."""
    def test_default_candidate_is_the_task_root_inside_it(self):
        self.setup_task()
        c = load_task(locate(self.repo))["checks"][0]
        self.assertEqual(c["candidate"], str(self.repo.resolve()))

    def test_subdirectory_cwd_still_binds_to_the_task_root(self):
        self.setup_task(); sub = self.repo / "pkg"; sub.mkdir()
        cid = self.add_check(sub, command="python3 -B ../verify.py")
        c = next(x for x in load_task(locate(self.repo))["checks"] if x["id"] == cid)
        self.assertEqual(c["candidate"], str(self.repo.resolve()))

    def test_worktree_cwd_binds_to_that_worktree(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other)
        c = next(x for x in load_task(locate(self.repo))["checks"] if x["id"] == cid)
        self.assertEqual(c["candidate"], str(other))
        fps = task_fingerprint(load_task(locate(self.repo)))
        self.assertIn(str(other), fps); self.assertIn(str(self.repo.resolve()), fps)

    def test_explicit_candidate_is_recorded_and_part_of_the_definition(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(self.repo, extra=("--candidate", str(other)))
        t = load_task(locate(self.repo)); c = next(x for x in t["checks"] if x["id"] == cid)
        self.assertEqual(c["candidate"], str(other))
        out, _ = self.cmd("preview", cid); self.assertEqual(json.loads(out)["definition"]["candidate"], str(other))

    def test_root_edit_does_not_invalidate_a_worktree_green(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other)
        self.cmd("run", cid, *QUIET)
        (self.repo / "subject.py").write_text("VALUE = 43\n")  # a colleague edits the shared checkout
        out, _ = self.cmd("status", "--json")
        g = json.loads(out)["gate"]
        self.assertFalse(any(r.startswith(cid + ":") for r in g["reasons"]), g["reasons"])
        self.assertTrue(any(r.startswith("A-01:") for r in g["reasons"]), g["reasons"])  # the root check is stale

    def test_worktree_edit_invalidates_only_the_worktree_green(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other)
        self.cmd("run", "A-01", cid, *QUIET)
        (other / "subject.py").write_text("VALUE = 43\n")
        g = json.loads(self.cmd("status", "--json")[0])["gate"]
        self.assertTrue(any(r.startswith(cid + ":") for r in g["reasons"]))
        self.assertFalse(any(r.startswith("A-01:") for r in g["reasons"]))

    def test_receipt_records_candidate_and_head(self):
        self.linked_worktree(); self.setup_task()
        out, _ = self.cmd("run", "A-01", *QUIET)
        row = json.loads(out); head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(row["head"], head); self.assertEqual(row["candidate"], str(self.repo.resolve()))
        receipt = load_task(locate(self.repo))["checks"][0]["receipt"]
        self.assertEqual(receipt["head"], head); self.assertEqual(receipt["candidate"], str(self.repo.resolve()))
        report, _ = self.cmd("report"); self.assertIn(f"Tested: {self.repo.resolve()} @ {head}", report)

    def test_legacy_receipt_without_candidate_still_accepted_for_root_checks(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET)
        d = locate(self.repo); t = load_task(d)
        # Simulate a record written before 0.5.0: no candidate field, receipt digest over
        # the old field set. The upgrade must keep that green accepted.
        from dmdlib.model import check_definition, CHECK_FIELDS; from dmdlib.storage import digest
        c = t["checks"][0]; c.pop("candidate"); c.pop("exclusive"); c["receipt"].pop("candidate"); c["receipt"].pop("head")
        c["receipt"]["definition"] = digest({k: c.get(k) for k in CHECK_FIELDS})
        self.assertEqual(digest(check_definition(c)), c["receipt"]["definition"])
        (d / "task.json").write_text(json.dumps(t))
        g = json.loads(self.cmd("status", "--json")[0])["gate"]
        self.assertFalse(any(r.startswith("A-01:") for r in g["reasons"]), g["reasons"])

    def test_source_for_falls_back_to_the_enclosing_candidate(self):
        fps = {"/a/b": "x"}
        self.assertEqual(source_for(fps, {"cwd": "/a/b/c/d"}), "x")
        self.assertIsNone(source_for(fps, {"cwd": "/z"}))
        self.assertEqual(source_for("plain", {"cwd": "/z"}), "plain")

    def test_candidate_root_outside_git_is_the_cwd(self):
        elsewhere = self.home / "elsewhere"; elsewhere.mkdir()
        self.assertEqual(candidate_root(elsewhere, self.repo), str(elsewhere.resolve()))


class StaleCase(GitFixture):
    """S2: a command that passed against a tree that then moved is STALE, with the diff."""
    def test_candidate_moved_is_reported_as_stale_with_drift(self):
        self.linked_worktree()
        self.setup_task(command='python3 -c "open(\'new-source\',\'w\').write(\'x\'); print(\'ACCEPTANCE_PASS:1\')"')
        out, _ = self.cmd("run", "A-01", *QUIET, code=1)
        row = json.loads(out)
        self.assertEqual(row["result"], "STALE"); self.assertEqual(row["failure"], "CANDIDATE_OR_DEFINITION_CHANGED")
        self.assertIn("new-source", row["stale"]["drift"]["changed"])
        self.assertEqual(row["stale"]["drift"]["head_before"], row["stale"]["drift"]["head_after"])
        self.assertEqual(load_task(locate(self.repo))["checks"][0]["status"], "FAIL")
        report, _ = self.cmd("report"); self.assertIn("STALE: candidate moved", report); self.assertIn("new-source", report)

    def test_head_move_during_run_names_both_commits(self):
        self.linked_worktree()
        script = self.repo / "commit_during.py"
        script.write_text("import subprocess\nsubprocess.run(['git','-C','.','-c','user.email=t@t','-c','user.name=t','commit','-q','--allow-empty','-m','moved'],check=True)\nprint('ACCEPTANCE_PASS:1')\n")
        self.git("add", "."); self.git("commit", "-q", "-m", "script")
        before = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.setup_task(command="python3 -B commit_during.py")
        row = json.loads(self.cmd("run", "A-01", *QUIET, code=1)[0])
        self.assertEqual(row["result"], "STALE")
        self.assertEqual(row["stale"]["drift"]["head_before"], before)
        self.assertNotEqual(row["stale"]["drift"]["head_after"], before)

    def test_a_genuinely_failing_command_is_fail_not_stale(self):
        self.setup_task(command='python3 -c "print(123)"')
        self.assertEqual(json.loads(self.cmd("run", "A-01", *QUIET, code=1)[0])["result"], "FAIL")

    def test_red_run_against_a_moving_candidate_is_red_stale(self):
        self.setup_task(regression=True, command='python3 -c "open(\'x\',\'w\').write(\'x\'); print(\'EXPECTED_42\'); raise SystemExit(1)"')
        row = json.loads(self.cmd("run", "A-01", "--red", *QUIET, code=1)[0])
        self.assertEqual(row["result"], "RED-STALE"); self.assertIsNone(load_task(locate(self.repo))["checks"][0]["red"])

    def test_drift_lists_added_removed_and_modified(self):
        a = snapshot(self.repo); (self.repo / "subject.py").write_text("VALUE = 1\n"); (self.repo / "verify.py").unlink(); (self.repo / "new.py").write_text("")
        d = drift(a, snapshot(self.repo))
        self.assertEqual(d["changed"], ["new.py", "subject.py", "verify.py"]); self.assertEqual(d["changed_total"], 3)


class BatchCase(GitFixture):
    """S3: several checks in one fingerprint window."""
    def second_check(self):
        return self.add_check(self.repo, command="python3 -B verify.py")

    def test_named_list_runs_each_and_summarizes(self):
        self.setup_task(); cid = self.second_check()
        out, _ = self.cmd("run", "A-01", cid, *QUIET)
        lines = [json.loads(x) for x in out.strip().splitlines()]
        self.assertEqual([r.get("result") for r in lines[:2]], ["PASS", "PASS"])
        self.assertEqual(lines[2]["batch"], ["A-01", cid]); self.assertEqual(lines[2]["window"]["moved"], {})
        t = load_task(locate(self.repo)); self.assertTrue(all(c["status"] == "PASS" for c in t["checks"]))

    def test_all_runs_every_live_command_check(self):
        self.setup_task(); cid = self.second_check()
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--method", "manual", "--expect", "seen",
                 "--attested-because", "a person looks")
        lines = [json.loads(x) for x in self.cmd("run", "--all", *QUIET)[0].strip().splitlines()]
        self.assertEqual(lines[-1]["batch"], ["A-01", cid])

    def test_all_refuses_before_running_when_one_is_unapproved(self):
        self.setup_task(); self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--cmd", "python3 -B verify.py", "--expect", "x", "--match", "ACCEPTANCE_PASS:1")
        _, err = self.cmd("run", "--all", *QUIET, code=2); self.assertIn("A-02 has no current inspected approval", err)
        self.assertEqual(load_task(locate(self.repo))["checks"][0]["status"], "NOT_RUN")

    def test_ids_and_all_together_are_refused(self):
        self.setup_task(); self.cmd("run", "A-01", "--all", *QUIET, code=2)

    def test_no_ids_is_a_usage_error(self):
        self.setup_task(); self.cmd("run", *QUIET, code=2)

    def test_one_window_makes_a_mid_sequence_move_stale_for_every_check(self):
        self.setup_task()
        cid = self.add_check(self.repo, command='python3 -c "open(\'moved\',\'w\').write(\'x\'); print(\'ACCEPTANCE_PASS:1\')"')
        lines = [json.loads(x) for x in self.cmd("run", "A-01", cid, *QUIET, code=1)[0].strip().splitlines()]
        self.assertEqual([r["result"] for r in lines[:2]], ["STALE", "STALE"])
        self.assertIn(str(self.repo.resolve()), lines[2]["window"]["moved"])

    def test_batch_snapshots_each_candidate_once(self):
        self.setup_task(); self.second_check()
        with patch("dmdlib.cli.task_snapshot", wraps=__import__("dmdlib.model", fromlist=["task_snapshot"]).task_snapshot) as snap:
            self.cmd("run", "--all", *QUIET)
        self.assertEqual(snap.call_count, 2)

    def test_running_record_names_the_whole_batch(self):
        self.setup_task(); cid = self.second_check()
        seen = {}
        real = __import__("dmdlib.runner", fromlist=["execute"]).execute
        def spy(c, cancelled=lambda: False):
            seen.setdefault("running", load_task(locate(self.repo))["running"]); return real(c, cancelled)
        with patch("dmdlib.cli.execute", spy):
            self.cmd("run", "A-01", cid, *QUIET)
        self.assertEqual(seen["running"]["checks"], ["A-01", cid]); self.assertEqual(seen["running"]["pid"], os.getpid())


class PreflightCase(GitFixture):
    """S4: refuse to test a tree someone is still writing."""
    def test_recent_dirty_write_is_refused_with_the_path(self):
        self.linked_worktree(); self.setup_task()
        (self.repo / "subject.py").write_text("VALUE = 42\n# touched\n")
        _, err = self.cmd("run", "A-01", "--quiet-window", "60", code=2)
        self.assertIn("concurrent writer", err); self.assertIn("subject.py", err); self.assertIn("--quiet-window 0", err)
        self.assertEqual(load_task(locate(self.repo))["checks"][0]["status"], "NOT_RUN")
        self.assertIsNone(load_task(locate(self.repo))["running"])

    def test_old_dirty_files_are_not_writers(self):
        self.linked_worktree(); self.setup_task()
        p = self.repo / "subject.py"; p.write_text("VALUE = 42\n# touched\n"); old = time.time() - 120; os.utime(p, (old, old))
        self.cmd("run", "A-01", "--quiet-window", "5")

    def test_zero_window_disables_the_check(self):
        self.linked_worktree(); self.setup_task(); (self.repo / "subject.py").write_text("VALUE = 42\n#\n")
        self.cmd("run", "A-01", "--quiet-window", "0")

    def test_untracked_recent_files_count(self):
        self.linked_worktree(); (self.repo / "scratch.txt").write_text("x")
        self.assertEqual([w["path"] for w in recent_writes(self.repo, 60)], ["scratch.txt"])
        self.assertEqual(recent_writes(self.home / "nothing-here-yet", 60) if False else recent_writes(self.home, 60), [])

    def test_another_tasks_live_run_on_the_same_candidate_is_refused(self):
        other = self.linked_worktree(); self.setup_task()
        self.cmd("--cwd", str(other), "init", "-m", "other", "--authority", "op")
        self.cmd("--cwd", str(other), "req", "add", "r", "--anchor", "a"); self.cmd("--cwd", str(other), "work", "add", "w", "--req", "R-01")
        self.cmd("--cwd", str(other), "check", "add", "--req", "R-01", "--work", "W-01", "--cmd", "python3 -B verify.py", "--run-cwd", str(self.repo), "--expect", "x", "--match", "ACCEPTANCE_PASS:1")
        d = locate(other); t = load_task(d)
        t["running"] = {"token": "x", "check": "A-01", "checks": ["A-01"], "started": "now", "source": {}, "candidates": [str(self.repo.resolve())],
                        "pid": os.getpid(), "host": __import__("socket").gethostname()}
        (d / "task.json").write_text(json.dumps(t))
        _, err = self.cmd("run", "A-01", *QUIET, code=2)
        self.assertIn("another dmd run", err); self.assertIn("is alive", err)
        t["running"]["pid"] = 2 ** 22 + 7; (d / "task.json").write_text(json.dumps(t))
        _, err = self.cmd("run", "A-01", *QUIET, code=2); self.assertIn("not alive", err)


class ExclusiveCase(GitFixture):
    """S5: checks sharing a named resource never run concurrently, across tasks and sessions."""
    def test_exclusive_tag_is_recorded_and_in_the_definition(self):
        self.setup_task(extra=("--exclusive", "integration-db"))
        c = load_task(locate(self.repo))["checks"][0]; self.assertEqual(c["exclusive"], ["integration-db"])
        self.assertEqual(json.loads(self.cmd("preview", "A-01")[0])["definition"]["exclusive"], ["integration-db"])
        self.assertIn("Exclusive: integration-db", self.cmd("report")[0])

    def test_held_resource_blocks_the_run_with_a_bounded_wait(self):
        self.setup_task(extra=("--exclusive", "integration-db"))
        lockfile = self.state / "locks" / "integration-db.lock"; lockfile.parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o600); fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            _, err = self.cmd("run", "A-01", *QUIET, "--wait-exclusive", "0.3", code=2)
            self.assertIn("exclusive resource 'integration-db' is held", err); self.assertLess(time.monotonic() - started, 5)
        finally:
            os.close(fd)
        self.assertIsNone(load_task(locate(self.repo))["running"])
        self.cmd("run", "A-01", *QUIET)

    def test_a_held_resource_mid_batch_keeps_earlier_results(self):
        self.setup_task(); cid = self.add_check(self.repo, extra=("--exclusive", "db"))
        lockfile = self.state / "locks" / "db.lock"; lockfile.parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o600); fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            out, err = self.cmd("run", "A-01", cid, *QUIET, "--wait-exclusive", "0.2", code=2)
        finally:
            os.close(fd)
        rows = [json.loads(x) for x in out.strip().splitlines()]
        self.assertEqual(rows[0]["result"], "PASS"); self.assertEqual(rows[-1]["skipped"], [cid]); self.assertIn("1 earlier check(s) recorded", err)
        t = load_task(locate(self.repo)); self.assertIsNone(t["running"]); self.assertEqual(t["checks"][0]["status"], "PASS")

    def test_invalid_tag_is_refused(self):
        self.cmd("init", "-m", "x", "--authority", "op"); self.cmd("req", "add", "r", "--anchor", "a"); self.cmd("work", "add", "w", "--req", "R-01")
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--cmd", "python3 -B verify.py", "--expect", "x", "--match", "P", "--exclusive", "../bad", code=2)


class ListingCase(GitFixture):
    """S6: one group at a time."""
    def test_group_lists(self):
        self.setup_task()
        self.cmd("finding", "add", "bug", "--location", "subject.py:1", "--status", "confirmed")
        self.cmd("blocker", "add", "need", "--item", "W-01", "--owner", "o", "--unblock", "u", "--proof", "p")
        self.assertEqual(self.cmd("req", "list")[0].strip(), "R-01 [active] Value is 42")
        self.assertTrue(self.cmd("work", "list")[0].startswith("W-01 [todo] Implement and test value -> R-01"))
        self.assertTrue(self.cmd("check", "list")[0].startswith("A-01 [NOT_RUN] (command)"))
        self.assertTrue(self.cmd("finding", "list")[0].startswith("F-01 [confirmed; unknown] subject.py:1: bug"))
        self.assertTrue(self.cmd("blocker", "list")[0].startswith("B-01 [OPEN] W-01: need"))
        self.assertEqual(json.loads(self.cmd("work", "list", "--json")[0])[0]["id"], "W-01")

    def test_listing_does_not_mutate_or_execute(self):
        self.setup_task(command='python3 -c "open(\'danger\',\'w\').write(\'x\')"')
        before = (locate(self.repo) / "task.json").read_bytes()
        self.cmd("check", "list"); self.cmd("work", "list")
        self.assertEqual(before, (locate(self.repo) / "task.json").read_bytes()); self.assertFalse((self.repo / "danger").exists())

    def test_status_only_renders_the_requested_sections(self):
        self.setup_task()
        out, _ = self.cmd("status", "--only", "work")
        self.assertIn("## Work", out); self.assertNotIn("## Checks", out); self.assertNotIn("## Still owed", out)
        out, _ = self.cmd("status", "--only", "owed", "--only", "next"); self.assertIn("## Still owed", out); self.assertIn("## Next actions", out); self.assertNotIn("## Work", out)


class RecoverCase(GitFixture):
    """S8: recover-run inspects the runner and prints why it accepted the proof."""
    def stranded(self, pid, host=None):
        d = locate(self.repo); t = load_task(d)
        t["running"] = {"token": "x", "check": "A-01", "checks": ["A-01"], "started": "then", "source": {}, "candidates": [],
                        "pid": pid, "host": host or __import__("socket").gethostname(), "interrupted": True}
        (d / "task.json").write_text(json.dumps(t)); return d

    def test_live_runner_refuses_recovery(self):
        self.setup_task(); self.stranded(os.getpid())
        _, err = self.cmd("recover-run", "--proof", "nothing external", code=2)
        self.assertIn("still alive", err); self.assertIsNotNone(load_task(locate(self.repo))["running"])

    def test_dead_runner_is_recovered_with_the_reason(self):
        self.setup_task(); self.stranded(2 ** 22 + 7)
        out, _ = self.cmd("recover-run", "--proof", "no container remained; docker ps empty")
        found = json.loads(out)
        self.assertFalse(found["runner_alive"]); self.assertEqual(found["run_lock"], "free"); self.assertTrue(found["interrupted_flag"])
        self.assertIn("no longer exists", found["accepted_because"])
        t = load_task(locate(self.repo)); self.assertIsNone(t["running"]); self.assertEqual(t["recovery"][-1]["found"]["pid"], 2 ** 22 + 7)

    def test_other_host_runner_is_recovered_with_the_caveat(self):
        self.setup_task(); self.stranded(1, host="elsewhere.example")
        found = json.loads(self.cmd("recover-run", "--proof", "checked the other machine")[0])
        self.assertIsNone(found["runner_alive"]); self.assertIn("elsewhere.example", found["accepted_because"])

    def test_nothing_to_recover_is_an_error(self):
        self.setup_task(); _, err = self.cmd("recover-run", "--proof", "x", code=2); self.assertIn("nothing to recover", err)


class ReviewHashCase(GitFixture):
    """S9: the final review fingerprints once."""
    def test_review_fingerprints_once(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        real = __import__("dmdlib.model", fromlist=["task_fingerprint"]).task_fingerprint
        with patch("dmdlib.cli.task_fingerprint", wraps=real) as fp:
            self.cmd("review", "--kind", "self", "--reviewer", "r", "--note", "n", "--evidence", str(self.review))
        self.assertEqual(fp.call_count, 1)
        self.assertEqual(json.loads(self.cmd("gate")[0])["status"], "COMPLETE")

    def test_manual_pass_fingerprints_once(self):
        self.setup_task()
        self.cmd("check", "add", "--req", "R-01", "--work", "W-01", "--method", "manual", "--expect", "seen", "--attested-because", "a person looks")
        real = __import__("dmdlib.model", fromlist=["task_fingerprint"]).task_fingerprint
        with patch("dmdlib.cli.task_fingerprint", wraps=real) as fp:
            self.cmd("check", "set", "--id", "A-02", "--status", "PASS", "--note", "seen it", "--evidence", str(self.review))
        self.assertEqual(fp.call_count, 1)




class ReasonCase(GitFixture):
    """0.5.1: a pre-0.5.0 worktree receipt survives the upgrade, every gate reason names
    its cause, the gate groups them, the Stop hook repeats the headline, and a run reports
    progress on stderr without disturbing the stdout rows."""
    def legacy(self, cid):
        from dmdlib.model import CHECK_FIELDS; from dmdlib.storage import digest
        d = locate(self.repo); t = load_task(d)
        c = next(x for x in t["checks"] if x["id"] == cid)
        c.pop("candidate", None); c.pop("exclusive", None)
        c["receipt"].pop("candidate"); c["receipt"].pop("head")
        c["receipt"]["source"] = task_fingerprint(t)[str(self.repo.resolve())]  # 0.4.x bound every receipt to the task root
        c["receipt"]["definition"] = digest({k: c.get(k) for k in CHECK_FIELDS})
        (d / "task.json").write_text(json.dumps(t))
        # Make the worktree differ from the root so their fingerprints cannot coincide.
        (Path(c["cwd"]) / "notes.txt").write_text("worktree-only\n")

    def test_legacy_worktree_receipt_is_still_accepted_after_the_upgrade(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other); self.cmd("run", cid, *QUIET); self.legacy(cid)
        g = json.loads(self.cmd("status", "--json")[0])["gate"]
        self.assertFalse(any(r.startswith(cid + ":") for r in g["reasons"]), g["reasons"])
        self.assertIn(cid, g["summary"]["checks"]["legacy"])
        report, _ = self.cmd("report")
        self.assertIn("receipt predates 0.5.0 candidate binding", report)
        self.assertIn(f"rerun to bind it to {other}", report)

    def test_legacy_receipt_goes_stale_with_the_root_and_says_why(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other); self.cmd("run", cid, *QUIET); self.legacy(cid)
        (self.repo / "subject.py").write_text("VALUE = 43\n")
        g = json.loads(self.cmd("status", "--json")[0])["gate"]
        line = next(r for r in g["reasons"] if r.startswith(cid + ":"))
        self.assertIn("predates 0.5.0", line); self.assertIn(str(other), line)
        self.assertIn(cid, g["summary"]["checks"]["stale"])

    def test_rerun_rebinds_a_legacy_receipt_to_its_worktree(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other); self.cmd("run", cid, *QUIET); self.legacy(cid)
        self.cmd("approve", cid, "--note", "re-inspected after the upgrade"); self.cmd("run", cid, *QUIET)
        c = next(x for x in load_task(locate(self.repo))["checks"] if x["id"] == cid)
        self.assertEqual(c["receipt"]["candidate"], str(other))
        g = json.loads(self.cmd("status", "--json")[0])["gate"]
        self.assertIn(cid, g["summary"]["checks"]["accepted"]); self.assertNotIn(cid, g["summary"]["checks"]["legacy"])

    def test_every_reason_names_its_cause_and_the_summary_groups_them(self):
        other = self.linked_worktree(); self.setup_task()
        cid = self.add_check(other)
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertIn("A-01: not run", g["reasons"]); self.assertIn(f"{cid}: not run", g["reasons"])
        self.assertEqual(g["summary"]["checks"]["not_run"], ["A-01", cid])
        self.assertEqual(g["summary"]["rerun"], f"dmd run A-01 {cid}")
        self.assertIn("2 check(s) not run", g["summary"]["headline"])
        self.cmd("run", "A-01", cid, *QUIET)
        (other / "subject.py").write_text("VALUE = 43\n")
        g = json.loads(self.cmd("gate", code=1)[0])
        line = next(r for r in g["reasons"] if r.startswith(cid + ":"))
        self.assertTrue(line.startswith(f"{cid}: stale: {other} changed since the receipt (tested @ "), line)
        self.assertEqual(g["summary"]["checks"]["stale"], [cid]); self.assertEqual(g["summary"]["checks"]["accepted"], ["A-01"])
        self.assertEqual(g["summary"]["rerun"], f"dmd run {cid}"); self.assertEqual(g["summary"]["review"], "owed")
        self.assertIn("1 check(s) stale", g["summary"]["headline"]); self.assertIn("final review owed", g["summary"]["headline"])
        self.cmd("check", "edit", "--id", cid, "--expect", "verifies VALUE again")  # an edit resets the receipt
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertIn(f"{cid}: not run", g["reasons"]); self.assertEqual(g["summary"]["rerun"], f"dmd run {cid}")

    def test_failed_check_reason_and_verified_work_reason(self):
        self.setup_task(command='python3 -c "print(123)"')
        self.cmd("run", "A-01", *QUIET, code=1)
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertIn("A-01: FAIL: exit or match failed; fix and rerun", g["reasons"])
        self.assertIn("W-01: status todo, not verified; checks owed: A-01", g["reasons"])
        self.assertEqual(g["summary"]["checks"]["failed"], ["A-01"])
        _, err = self.cmd("work", "set", "--id", "W-01", "--status", "verified", code=2)
        self.assertIn("A-01: FAIL", err)

    def test_verified_work_with_stale_evidence_names_the_checks(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        (self.repo / "subject.py").write_text("VALUE = 43\n")
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertIn("W-01: verified, but evidence is not current for A-01", g["reasons"])
        self.assertEqual([x["action"] for x in g["next"] if x["id"] == "W-01"], ["rerun stale evidence: A-01"])
        self.assertEqual(g["summary"]["work_unverified"], ["W-01"])

    def test_stop_hook_repeats_the_headline_and_rerun_command(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        (self.repo / "subject.py").write_text("VALUE = 43\n")
        out, _ = self.cmd("hook", "stop", stdin=self.payload())
        message = json.loads(out)["systemMessage"]
        self.assertIn("1 check(s) stale", message); self.assertIn("Rerun: dmd run A-01", message); self.assertIn("A-01: stale:", message)
        self.cmd("config", "--mode", "enforce")
        out, _ = self.cmd("hook", "stop", stdin=self.payload())
        self.assertIn("Rerun: dmd run A-01", json.loads(out)["reason"])

    def test_run_reports_progress_on_stderr_and_keeps_stdout_rows(self):
        self.setup_task()
        out, err = self.cmd("run", "A-01", *QUIET)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["check"] for r in rows], ["A-01"]); self.assertEqual(rows[0]["result"], "PASS")
        events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
        self.assertEqual([(e["check"], e["event"]) for e in events], [("A-01", "start"), ("A-01", "finish")])
        self.assertEqual(events[0]["candidate"], str(self.repo.resolve())); self.assertEqual(events[1]["exit"], 0)

    def test_session_start_tells_a_resuming_session_what_to_read_and_what_is_owed(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        (self.repo / "subject.py").write_text("VALUE = 43\n")
        out, _ = self.cmd("hook", "session-start", stdin=self.payload())
        text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("references/recovery.md", text); self.assertIn("dmd reconcile", text)
        self.assertIn("Read the full SKILL.md only to initialise a new task", text)
        self.assertIn("1 check(s) stale", text); self.assertIn("Rerun: dmd run A-01", text)

    def test_brief_gate_replaces_the_source_map_with_a_digest(self):
        other = self.linked_worktree(); self.setup_task(); self.add_check(other)
        full = json.loads(self.cmd("gate", code=1)[0]); brief = json.loads(self.cmd("gate", "--brief", code=1)[0])
        self.assertEqual(len(full["source"]), 2); self.assertNotIn("source", brief)
        self.assertEqual(brief["candidates"], 2); self.assertEqual(brief["source_digest"], source_digest(full["source"]))
        self.assertEqual(brief["summary"], full["summary"]); self.assertEqual(brief["reasons"], full["reasons"])
        brief = json.loads(self.cmd("next", "--brief")[0]); self.assertNotIn("source", brief)

    def test_review_survives_a_rerun_on_an_identical_candidate(self):
        self.setup_task(); self.cmd("run", "A-01", *QUIET); self.cmd("work", "set", "--id", "W-01", "--status", "verified")
        self.cmd("coverage", "assert", "--note", "mapped")
        self.cmd("review", "--kind", "self", "--reviewer", "r", "--note", "n", "--evidence", str(self.review))
        self.cmd("gate")
        self.cmd("run", "A-01", *QUIET)  # same tree, same pass, new timestamp and log
        self.assertEqual(json.loads(self.cmd("gate")[0])["summary"]["review"], "current")
        (self.repo / "notes.txt").write_text("a tree change that keeps the check green\n"); self.cmd("run", "A-01", *QUIET)
        g = json.loads(self.cmd("gate", code=1)[0])
        self.assertEqual(g["summary"]["review"], "owed"); self.assertTrue(any(r.startswith("review:") for r in g["reasons"]))

    def test_every_subcommand_has_help_text(self):
        from dmdlib.cli import HELP, parser
        names = [a.dest for a in parser()._subparsers._group_actions][0]
        sub = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(sorted(sub.choices), sorted(HELP)); self.assertTrue(all(HELP[k].strip() for k in HELP))
        for action in sub._choices_actions:
            self.assertEqual(action.help, HELP[action.dest])


if __name__ == "__main__":
    unittest.main()

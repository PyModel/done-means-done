#!/usr/bin/env python3
"""Execute an isolated real repair workflow, then prove same-size edits invalidate acceptance.

This is a local CLI acceptance demonstration, not a live-agent or host-session benchmark.
Temporary project/state files are removed after the demonstration.
"""
from __future__ import annotations
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='dmd-demo-') as temporary:
        base = Path(temporary).resolve()
        project = base / 'project'; project.mkdir()
        state = base / 'state'
        subject = project / 'subject.py'; subject.write_text('VALUE = 41\n')
        verifier = project / 'verify.py'
        verifier.write_text('from subject import VALUE\nassert VALUE == 42, "EXPECTED_VALUE_42"\nprint("VALUE_ACCEPTANCE_PASS:1")\n')
        artifact = base / 'review.txt'
        artifact.write_text('Deterministic local fixture review. The original outcome maps to R-01, W-01 and A-01. '
                            'A-01 imports the real subject, asserts VALUE equals 42, and prints success only afterward. '
                            'The defect is reproduced at 41, repaired at 42, and checked again after a stale-evidence challenge. '
                            'This is a self-review fixture, not an independent agent review.\n')
        env = {**os.environ, 'DMD_STATE': str(state), 'PYTHONDONTWRITEBYTECODE': '1'}
        def step(*args, expected=0):
            p = subprocess.run([sys.executable, '-B', str(ROOT / 'bin/dmd'), '--cwd', str(project), *args],
                               capture_output=True, text=True, env=env, timeout=30)
            print('$ dmd ' + shlex.join(args))
            print(p.stdout.strip() or p.stderr.strip())
            if p.returncode != expected:
                raise RuntimeError(f'Expected exit {expected}, got {p.returncode}: {p.stderr}')
            return p.stdout
        step('init', '-m', 'Deliver VALUE=42 and remediate the discovered incorrect value', '--authority', 'Local acceptance fixture explicitly authorizes its temporary project')
        step('req', 'add', 'VALUE equals 42', '--anchor', 'Fixture original request')
        step('work', 'add', 'Fix value and verify the actual import', '--req', 'R-01', '--owns', 'subject.py')
        step('check', 'add', '--req', 'R-01', '--work', 'W-01', '--cmd', shlex.join([sys.executable, '-B', 'verify.py']),
             '--expect', 'One real assertion verifies imported VALUE=42', '--match', 'VALUE_ACCEPTANCE_PASS:1',
             '--input', 'verify.py', '--regression', '--red-match', 'EXPECTED_VALUE_42')
        step('finding', 'add', 'VALUE is 41 rather than required 42', '--location', 'subject.py:1', '--status', 'confirmed', '--origin', 'pre-existing')
        step('coverage', 'assert', '--note', 'Original outcome and confirmed defect map to R-01, W-01, A-01 and F-01')
        step('run', 'A-01', expected=2)
        step('preview', 'A-01')
        step('approve', 'A-01', '--note', 'Fixture verifier was authored above and inspected; it performs one assertion with no external effects')
        red = json.loads(step('run', 'A-01', '--red'))
        if red['result'] != 'RED-OK': raise RuntimeError('Intentional baseline not accepted')
        subject.write_text('VALUE = 42\n')
        step('run', 'A-01')
        step('work', 'set', '--id', 'W-01', '--status', 'verified')
        step('finding', 'set', '--id', 'F-01', '--status', 'fixed-verified', '--work', 'W-01', '--check', 'A-01', '--note', 'Actual faulty baseline reproduced; root value repaired and regression verified')
        step('review', '--kind', 'self', '--reviewer', 'deterministic local fixture', '--note', 'Final fixture contract and real assertion inspected', '--evidence', str(artifact))
        if json.loads(step('gate'))['status'] != 'COMPLETE': raise RuntimeError('Valid fixture not completed')
        before = subject.stat(); subject.write_text('VALUE = 43\n'); os.utime(subject, ns=(before.st_atime_ns, before.st_mtime_ns))
        if json.loads(step('gate', expected=1))['status'] == 'COMPLETE': raise RuntimeError('Same-size preserved-mtime change escaped freshness checks')
        step('run', 'A-01', expected=1)
        subject.write_text('VALUE = 42\n'); step('run', 'A-01')
        step('review', '--kind', 'self', '--reviewer', 'deterministic local fixture', '--note', 'Final fixture restored, retested and reviewed after freshness challenge', '--evidence', str(artifact))
        step('gate'); step('report', '--save')

        # A second outcome that only a person can observe: attestation is accepted, labelled,
        # and refused as the sole basis for a requirement until the operator authorizes it.
        observation = base / 'observation.txt'
        observation.write_text('Rendered page inspected at 1280x800; the banner and totals appear as specified.\n')
        step('req', 'add', 'The rendered page looks correct to a person', '--anchor', 'Original request, outcome 2')
        step('work', 'add', 'Lay out and visually check the page', '--req', 'R-02')
        step('check', 'add', '--req', 'R-02', '--work', 'W-02', '--method', 'browser',
             '--expect', 'A person confirms the rendered layout at the declared viewport',
             '--attested-because', 'No command can assert how a rendered page looks to a person')
        step('check', 'set', '--id', 'A-02', '--status', 'PASS',
             '--note', 'Observed at 1280x800; banner and totals as specified', '--evidence', str(observation))
        step('work', 'set', '--id', 'W-02', '--status', 'verified')
        step('coverage', 'assert', '--note', 'Both outcomes mapped; R-02 is observable only by a person')
        blocked = json.loads(step('gate', expected=1))
        if not any('self-attested' in reason for reason in blocked['reasons']):
            raise RuntimeError('A solely attested requirement was not refused')
        if blocked['attestation'] != {'executed': 1, 'self_attested': 1}:
            raise RuntimeError('Acceptance basis was not counted correctly')
        step('req', 'attest-only', '--id', 'R-02',
             '--authority', 'Operator accepts a recorded human observation for this outcome')
        step('coverage', 'assert', '--note', 'Reconciled after recording attested-only authority')
        step('review', '--kind', 'self', '--reviewer', 'deterministic local fixture',
             '--note', 'Both outcomes reconciled; R-02 rests on a recorded human observation', '--evidence', str(artifact))
        if json.loads(step('gate'))['status'] != 'COMPLETE':
            raise RuntimeError('Authorized attested-only requirement did not complete')
        report = step('report', '--save')
        for expected in ('SELF-ATTESTED', 'EXECUTED', 'attested-only by operator authority'):
            if expected not in report:
                raise RuntimeError(f'Report did not disclose {expected}')
    print('DMD_DEMO_PASS:real-red-green-and-stale-rejection;attested-only-refused-then-authorized;temporary-fixtures-removed')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

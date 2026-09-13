#!/usr/bin/env python3
"""Offline static validation of package structure, syntax, links and release identity."""
from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def main():
    required = ['SKILL.md', 'README.md', 'LICENSE', 'SOURCE.json', 'bin/dmd', 'hooks/install.py',
                'references/commands.md', 'references/verification.md', 'references/recovery.md',
                'references/security.md', 'references/adoption.md', 'tests/run.py',
                'evidence/VALIDATION.md', 'evidence/REMEDIATIONS.md', 'examples/local-demo.py']
    for name in required:
        assert (ROOT / name).is_file() and not (ROOT / name).is_symlink(), name
    skill = (ROOT / 'SKILL.md').read_text()
    assert skill.startswith('---\n') and skill.count('\n---\n') >= 1
    front = skill.split('---\n', 2)[1]
    assert re.search(r'^name: done-means-done$', front, re.M)
    assert re.search(r'^description: .{40,1024}$', front, re.M)
    assert 'version: "0.6.0"' in front
    assert len(skill.splitlines()) < 500
    assert '!`' not in skill, 'skill must not execute dynamic commands at load'
    assert 'allowed-tools:' not in front, 'no broad permission preapproval'
    source = json.loads((ROOT / 'SOURCE.json').read_text())
    assert source['name'] == 'done-means-done' and source['version'] == '0.6.0'
    assert len(source['inspected_commit']) == 40
    python_files = list(ROOT.rglob('*.py')) + [ROOT / 'bin/dmd']
    for path in python_files:
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 10))
    docs = list(ROOT.rglob('*.md')); links = 0
    for path in docs:
        text = path.read_text(); fenced = False
        for line in text.splitlines():
            if line.startswith('```'): fenced = not fenced
        assert not fenced, f'unclosed fence: {path}'
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in target or target.startswith(('#','mailto:')): continue
            target = target.split('#', 1)[0]
            resolved = (path.parent / target).resolve()
            assert resolved.is_relative_to(ROOT.resolve()) and resolved.exists(), (path, target)
            links += 1
    readme = (ROOT / 'README.md').read_text()
    assert not re.search(r'#\s*\d+ tests', readme), 'README must not hard-code the test count'
    assert 'Done Means Done 0.6.0' in (ROOT / 'evidence/VALIDATION.md').read_text().splitlines()[2], 'VALIDATION.md release header is stale'
    for path in ROOT.rglob('*'):
        assert not path.is_symlink(), f'nonportable symlink: {path}'
        assert path.name != '__pycache__', f'bytecode cache in package: {path}'
    print(f'DMD_PACKAGE_PASS:python={len(python_files)};markdown={len(docs)};local-links={links};skill-lines={len(skill.splitlines())}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

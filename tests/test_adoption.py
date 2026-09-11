"""Real installer and legacy migration regression tests in isolated temporary paths."""
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dmdlib.cli import main, locate, load_task

ROOT = Path(__file__).resolve().parents[1]

class AdoptionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve(); self.repo = self.home / 'project'; self.repo.mkdir()
        self.state = self.home / 'private state'; self.settings = self.home / 'Claude settings' / 'settings.json'
        self.env = patch.dict(os.environ, {'DMD_STATE': str(self.state), 'PYTHONDONTWRITEBYTECODE': '1'}); self.env.start(); self.addCleanup(self.env.stop)
    def cli(self, *args, code=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = main(['--cwd', str(self.repo), *args])
        self.assertEqual(result, code, (args, result, out.getvalue(), err.getvalue()))
        return out.getvalue()
    def install(self, *args, code=0, settings=None):
        p = subprocess.run([sys.executable, '-B', str(ROOT / 'hooks/install.py'), '--settings', str(settings or self.settings), '--state-dir', str(self.state), *args], capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, code, (p.stdout, p.stderr))
        return p
    def old(self, **changes):
        t = {'schema': 1, 'task_id': 'old-task', 'status': 'COMPLETE', 'original_request': 'Deliver all required behavior',
             'requirements': [{'id':'R-01','text':'Correct behavior','status':'active'}],
             'work_items':[{'id':'W-01','text':'Implement behavior','req':'R-01','status':'verified','deps':[]}],
             'checks':[{'id':'A-01','req':'R-01','method':'command','command':'python3 verify.py','expect':'Behavior works','status':'PASS'}],
             'findings':[{'id':'F-01','text':'Pre-existing defect','location':'code.py:1','confidence':'confirmed','scope':'out-of-scope','disposition':'reported-out-of-scope'}],
             'blockers':[], 'uncertain':[], 'amendments':[]}
        t.update(changes); p = self.home / 'legacy.json'; p.write_text(json.dumps(t)); return p
    def test_preview_creates_no_state_or_settings(self):
        p = self.install(); self.assertTrue(json.loads(p.stdout)['preview']); self.assertFalse(self.state.exists()); self.assertFalse(self.settings.parent.exists())
    def test_first_install_creates_valid_settings(self):
        self.install('--apply'); data = json.loads(self.settings.read_text()); self.assertEqual(len(data['hooks']), 5)
        self.assertNotIn('matcher', data['hooks']['TaskCompleted'][0])
    def test_repeat_install_is_idempotent(self):
        self.install('--apply'); before = self.settings.read_bytes(); self.install('--apply'); self.assertEqual(before, self.settings.read_bytes())
        self.assertEqual(list(self.settings.parent.glob('*.dmd-backup-*')), [])
    def test_install_preserves_existing_settings_and_hooks(self):
        self.settings.parent.mkdir(); self.settings.write_text(json.dumps({'model':'chosen-model','hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo unrelated'}]}]}}))
        self.install('--apply'); d = json.loads(self.settings.read_text()); self.assertEqual(d['model'],'chosen-model'); self.assertEqual(d['hooks']['Stop'][0]['hooks'][0]['command'],'echo unrelated')
        self.assertEqual(len(list(self.settings.parent.glob('*.dmd-backup-*'))), 1)
    def test_uninstall_removes_only_exact_managed_commands(self):
        self.install('--apply'); d = json.loads(self.settings.read_text())
        keep = d['hooks']['Stop'][0]['hooks'][0]['command'] + ' --unrelated-wrapper'
        d['hooks']['Stop'][0]['hooks'].append({'type':'command','command':keep}); d['custom']='preserved'; self.settings.write_text(json.dumps(d))
        self.install('--remove','--apply'); d = json.loads(self.settings.read_text()); self.assertEqual(d['custom'],'preserved')
        self.assertEqual(d['hooks']['Stop'][0]['hooks'][0]['command'],keep); self.assertTrue((self.state/'installations').exists())
    def test_paths_with_spaces_are_shell_quoted(self):
        p = self.install(); data = json.loads(p.stdout)['result']; cmd = data['hooks']['Stop'][0]['hooks'][0]['command']; parts = shlex.split(cmd)
        self.assertEqual(parts[1], 'DMD_STATE=' + str(self.state)); self.assertEqual(parts[-2:], ['hook','stop'])
    def test_invalid_settings_shape_not_modified(self):
        self.settings.parent.mkdir(); self.settings.write_text('{"hooks": []}'); before = self.settings.read_bytes()
        self.install('--apply', code=2); self.assertEqual(before,self.settings.read_bytes())
    def test_settings_symlink_refused(self):
        target = self.home/'real.json'; target.write_text('{}'); self.settings.parent.mkdir(); self.settings.symlink_to(target)
        self.install('--apply',code=2); self.assertEqual(target.read_text(),'{}')
    def test_settings_symlink_parent_resolves_to_the_physical_directory(self):
        # A symlinked settings home is the ordinary dotfiles layout, not an attack. It is
        # canonicalized and written through to its physical path; the file itself may still
        # not be a symlink, and write_settings still requires this user to own the directory.
        real = self.home/'real'; real.mkdir(); (real/'settings.json').write_text('{}'); self.settings.parent.symlink_to(real, target_is_directory=True)
        self.install(code=0); self.assertFalse(self.state.exists())
        self.install('--apply'); d = json.loads((real/'settings.json').read_text()); self.assertEqual(len(d['hooks']), 5)
        self.assertFalse((real/'settings.json').is_symlink())
    def test_state_dir_under_a_symlinked_ancestor_installs(self):
        real = self.home/'real-state'; real.mkdir(); link = self.home/'linked-state'; link.symlink_to(real, target_is_directory=True)
        p = subprocess.run([sys.executable,'-B',str(ROOT/'hooks/install.py'),'--settings',str(self.settings),
                            '--state-dir',str(link/'state'),'--apply'], capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr)); self.assertTrue((real/'state'/'installations').is_dir())
    def test_uninstall_removes_orphaned_registrations_without_a_manifest(self):
        self.install('--apply'); shutil.rmtree(self.state/'installations')
        self.install('--remove','--apply'); self.assertNotIn('hooks', json.loads(self.settings.read_text()))
    def test_uninstall_keeps_a_foreign_hook_that_merely_mentions_dmd(self):
        self.install('--apply'); d = json.loads(self.settings.read_text())
        foreign = 'echo ' + str(ROOT/'bin/dmd') + ' is installed'
        d['hooks']['Stop'].append({'hooks':[{'type':'command','command':foreign}]}); self.settings.write_text(json.dumps(d))
        self.install('--remove','--apply'); d = json.loads(self.settings.read_text())
        self.assertEqual(d['hooks']['Stop'][0]['hooks'][0]['command'], foreign)
    def test_link_bin_previews_then_installs_a_path_shim_and_removes_it(self):
        bin_dir = self.home / 'bin'
        p = self.install('--link-bin', str(bin_dir)); self.assertEqual(json.loads(p.stdout)['bin_link'], str(bin_dir / 'dmd')); self.assertFalse(bin_dir.exists())
        self.install('--link-bin', str(bin_dir), '--apply'); link = bin_dir / 'dmd'
        self.assertTrue(link.is_symlink()); self.assertEqual(os.readlink(link), str(ROOT / 'bin/dmd'))
        r = subprocess.run([str(link), '--version'], capture_output=True, text=True, timeout=15); self.assertEqual(r.stdout.strip(), __import__('dmdlib').__version__)
        manifest = next((self.state / 'installations').glob('*.json')); self.assertEqual(json.loads(manifest.read_text())['bin_link'], str(link))
        self.install('--remove', '--apply'); self.assertFalse(link.exists()); self.assertFalse(link.is_symlink())
    def test_link_bin_alone_leaves_settings_untouched(self):
        bin_dir = self.home / 'bin'; self.install('--link-bin', str(bin_dir), '--no-hooks', '--apply')
        self.assertTrue((bin_dir / 'dmd').is_symlink()); self.assertFalse(self.settings.exists())
        self.install('--no-hooks', code=2)
    def test_link_bin_refuses_a_foreign_file_or_link(self):
        bin_dir = self.home / 'bin'; bin_dir.mkdir(); (bin_dir / 'dmd').write_text('#!/bin/sh\n')
        p = self.install('--link-bin', str(bin_dir), '--apply', code=2); self.assertIn('not a symlink', p.stderr)
        (bin_dir / 'dmd').unlink(); os.symlink(self.home / 'elsewhere', bin_dir / 'dmd')
        p = self.install('--link-bin', str(bin_dir), '--apply', code=2); self.assertIn('not a dmd runtime', p.stderr)
        self.assertEqual(os.readlink(bin_dir / 'dmd'), str(self.home / 'elsewhere'))
    def test_migration_invalid_graph_leaves_active_task_unchanged(self):
        self.cli('init','-m','Existing obligation','--authority','operator invocation'); directory=locate(self.repo); before=(directory/'task.json').read_bytes()
        source=self.old(work_items=[{'id':'W-01','text':'broken graph','req':'R-01','status':'verified','deps':['W-99']}]); oldbytes=source.read_bytes()
        self.cli('migrate','--from-task',str(source),'--authority','explicit import','--new',code=2)
        self.assertEqual(locate(self.repo),directory); self.assertEqual((directory/'task.json').read_bytes(),before); self.assertEqual(source.read_bytes(),oldbytes)
    def test_migration_discards_green_and_reopens_confirmed_findings(self):
        source=self.old(); oldbytes=source.read_bytes(); self.cli('migrate','--from-task',str(source),'--authority','explicit import')
        t=load_task(locate(self.repo)); self.assertEqual(t['state'],'ACTIVE'); self.assertEqual(t['checks'][0]['status'],'NOT_RUN'); self.assertTrue(t['checks'][0]['needs_review'])
        self.assertEqual(t['findings'][0]['status'],'confirmed'); self.assertIsNone(t['coverage']); self.assertEqual(source.read_bytes(),oldbytes); self.cli('gate',code=1)
    def test_migration_preserves_cancelled_state(self):
        source=self.old(status='CANCELLED'); self.cli('migrate','--from-task',str(source),'--authority','explicit import'); self.assertEqual(load_task(locate(self.repo))['state'],'CANCELLED')
    def test_migration_refuses_unsupported_schema_without_task(self):
        source=self.old(schema=99); self.cli('migrate','--from-task',str(source),'--authority','explicit import',code=2); self.assertIsNone(locate(self.repo))
    def test_migration_recovers_unmapped_obligations(self):
        source=self.old(requirements=[], work_items=[{'id':'W-01','text':'lost owner','req':None,'deps':[]}]); self.cli('migrate','--from-task',str(source),'--authority','explicit import')
        t=load_task(locate(self.repo)); self.assertGreater(len(t['requirements']),0); self.assertTrue(t['work']); self.cli('gate',code=1)

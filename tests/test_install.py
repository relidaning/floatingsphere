"""Service reconciliation uses a temporary home and fake systemctl only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServiceInstallationTests(unittest.TestCase):
    def test_relocated_unit_replaces_legacy_symlink_and_repeat_skips_mutations(self):
        with tempfile.TemporaryDirectory(prefix='sphere setup ') as temporary:
            home = Path(temporary)
            repo = home / 'apps with spaces/sphere%instance'
            repo.mkdir(parents=True)
            for name in ('install.sh', 'sphere-web.service'): shutil.copy2(ROOT / name, repo / name)
            bindir = home / 'bin'
            bindir.mkdir()
            for name, content in {
                'python3': '#!/bin/sh\n[ "$1" != -c ] || exit 0\nexec /usr/bin/python3 "$@"\n',
                'kitty': '#!/bin/sh\nexit 0\n', 'hyprctl': '#!/bin/sh\nexit 0\n',
                'systemctl': '''#!/usr/bin/python3
import os,sys
from pathlib import Path
h=Path(os.environ['HOME'])
if 'show' in sys.argv:print('no')
elif 'is-enabled' in sys.argv:sys.exit(0 if (h/'enabled').exists() else 1)
elif 'is-active' in sys.argv:sys.exit(0 if (h/'active').exists() else 1)
else:
    with (h/'mutations').open('a') as stream:stream.write(' '.join(sys.argv[1:])+'\\n')
    if 'enable' in sys.argv:(h/'enabled').touch()
    if 'start' in sys.argv or 'restart' in sys.argv:(h/'active').touch()
''',
            }.items():
                p=bindir/name;p.write_text(content);p.chmod(0o755)
            unit=home/'.config/systemd/user/sphere-web.service'
            unit.parent.mkdir(parents=True)
            unit.symlink_to(repo/'sphere-web.service')
            template=(repo/'sphere-web.service').read_text()
            env={'HOME':str(home),'PATH':str(bindir)+':/usr/bin:/bin'}
            result=subprocess.run(['bash',str(repo/'install.sh')],env=env,cwd='/tmp',capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertFalse(unit.is_symlink())
            self.assertIn(str(repo).replace('%','%%')+'/sphere_web.py',unit.read_text())
            self.assertEqual(template,(repo/'sphere-web.service').read_text())
            before=(unit.stat().st_ino,unit.stat().st_mtime_ns,(home/'mutations').read_text())
            result=subprocess.run(['bash',str(repo/'install.sh')],env=env,cwd='/tmp',capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(before,(unit.stat().st_ino,unit.stat().st_mtime_ns,(home/'mutations').read_text()))

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher = load('launcher', 'start-silent.py')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.launcher.LOG_DIR = Path(self.tmp.name)

    def test_existing_server_is_reused_without_browser_for_workroom(self):
        with patch.object(self.launcher, 'ready', return_value=True), \
             patch.object(self.launcher.sys, 'argv', ['start-silent.py', '--no-open']), \
             patch.object(self.launcher.subprocess, 'Popen') as spawn, \
             patch.object(self.launcher.subprocess, 'run') as run:
            self.launcher.main()
        spawn.assert_not_called()
        run.assert_not_called()

    def test_cold_start_is_detached_without_debugger(self):
        with patch.object(self.launcher, 'ready', side_effect=[False, True]), \
             patch.object(self.launcher.sys, 'argv', ['start-silent.py', '--no-open']), \
             patch.object(self.launcher.subprocess, 'Popen') as spawn:
            self.launcher.main()
        args, kwargs = spawn.call_args
        self.assertIn('--no-debugger', args[0])
        self.assertIn('--no-reload', args[0])
        self.assertEqual(kwargs['env']['FLASK_DEBUG'], '0')
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], self.launcher.subprocess.DEVNULL)

    def test_failed_server_reports_log_path(self):
        child = Mock()
        child.poll.return_value = 1
        with patch.object(self.launcher, 'ready', return_value=False), \
             patch.object(self.launcher.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(RuntimeError, 'hwptopdf.log'):
                self.launcher.main()

    def test_desktop_opens_browser_after_server_is_ready(self):
        with patch.object(self.launcher, 'ready', return_value=True), \
             patch.object(self.launcher.sys, 'argv', ['start-silent.py']), \
             patch.object(self.launcher.subprocess, 'run') as run:
            self.launcher.main()
        run.assert_called_once_with(['/usr/bin/open', self.launcher.URL], check=True)


class InstallerTests(unittest.TestCase):
    def test_install_backs_up_code_and_preserves_private_data(self):
        installer = load('installer', 'install-macos.py')
        with tempfile.TemporaryDirectory(prefix="한글 path's ") as tmp:
            root = Path(tmp)
            source, runtime = root / 'source', root / 'Applications/runtime'
            source.mkdir()
            runtime.mkdir(parents=True)
            for name in ('app.py', 'index.html', 'start-silent.py'):
                (source / name).write_text('new code')
            (source / 'pdf2jpg').mkdir()
            (source / 'pdf2jpg/index.html').write_text('images')
            (source / 'gemini_token.json').write_text('source credential')
            (runtime / 'gemini_token.json').write_text('existing credential')
            (runtime / 'app.py').write_text('old code')
            python = runtime / 'venv/bin/python3'
            python.parent.mkdir(parents=True)
            python.touch()
            desktop = root / 'Desktop/HWP-PDF 변환기.app'
            desktop.mkdir(parents=True)
            (desktop / 'previous').touch()
            installer.SOURCE, installer.RUNTIME, installer.DESKTOP_APP = source, runtime, desktop

            def fake_run(args, **kwargs):
                if args[0] == '/usr/bin/osacompile':
                    generated = Path(args[2])
                    generated.mkdir()
                    script = Path(args[3]).read_text()
                    self.assertIn('do shell script', script)
                    self.assertNotIn('tell application "Terminal"', script)
                return Mock(returncode=0)

            with patch.object(installer.sys, 'platform', 'darwin'), \
                 patch.object(installer.subprocess, 'run', side_effect=fake_run):
                installer.install()
            self.assertEqual((runtime / 'app.py').read_text(), 'new code')
            self.assertEqual((runtime / 'gemini_token.json').read_text(), 'existing credential')
            backup = next(runtime.glob('install-backup-*'))
            self.assertEqual((backup / 'app.py').read_text(), 'old code')
            self.assertTrue((backup / desktop.name / 'previous').exists())


if __name__ == '__main__':
    unittest.main()

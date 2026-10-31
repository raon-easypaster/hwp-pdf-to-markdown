#!/usr/bin/env python3
"""Start the converter without Terminal, reusing an existing server."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:5050/'
LOG_DIR = Path.home() / '.raon-workspace'


def ready():
    try:
        with urllib.request.urlopen(URL, timeout=1) as response:
            return b'HWP/PDF to Markdown' in response.read()
    except Exception:
        return False


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / 'hwptopdf.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not ready():
            env = dict(os.environ, FLASK_DEBUG='0', BROWSER='none',
                       PYTHONDONTWRITEBYTECODE='1')
            with (LOG_DIR / 'hwptopdf.log').open('ab') as log:
                child = subprocess.Popen(
                    [str(ROOT / 'venv/bin/python3'), '-m', 'flask',
                     '--app', 'app', 'run', '--host', '127.0.0.1',
                     '--port', '5050', '--no-debugger', '--no-reload'],
                    cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, start_new_session=True,
                )
            for _ in range(100):
                if ready():
                    break
                if child.poll() is not None:
                    raise RuntimeError('변환기를 시작하지 못했습니다. 로그: ' + str(LOG_DIR / 'hwptopdf.log'))
                time.sleep(0.3)
            else:
                raise RuntimeError('변환기 시작 시간이 초과되었습니다. 로그: ' + str(LOG_DIR / 'hwptopdf.log'))
    if '--no-open' not in sys.argv:
        subprocess.run(['/usr/bin/open', URL], check=True)


if __name__ == '__main__':
    main()

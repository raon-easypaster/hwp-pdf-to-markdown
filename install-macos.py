#!/usr/bin/env python3
"""Install the converter and a Terminal-free desktop app on macOS."""
from datetime import datetime
from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
RUNTIME = Path.home() / 'Applications' / 'hwp-pdf-to-markdown'
DESKTOP_APP = Path.home() / 'Desktop' / 'HWP-PDF 변환기.app'


def install():
    if sys.platform != 'darwin':
        raise RuntimeError('이 설치기는 macOS 전용입니다. Windows에서는 run.bat를 사용하세요.')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    backup = RUNTIME / ('install-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir()
    # Copy application code only, preserving credentials and conversions.
    for name in ('app.py', 'index.html', 'pdf2jpg', 'start-silent.py'):
        source, target = SOURCE / name, RUNTIME / name
        if source.resolve() == target.resolve():
            continue
        if target.exists():
            if target.is_dir():
                shutil.copytree(target, backup / name)
            else:
                shutil.copy2(target, backup / name)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    python = RUNTIME / 'venv/bin/python3'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(RUNTIME / 'venv')], check=True)
    check = subprocess.run([str(python), '-c', 'import flask, olefile, pypdf, hwp5, six, tqdm'])
    if check.returncode:
        subprocess.run([str(python), '-m', 'pip', 'install',
                        'flask', 'pyhwp', 'olefile', 'six', 'pypdf', 'tqdm'], check=True)
    command = ' '.join(shlex.quote(str(p)) for p in (python, RUNTIME / 'start-silent.py'))
    script = ('try\n    do shell script ' + json.dumps(command, ensure_ascii=False) +
              '\non error errorMessage\n    display alert "HWP/PDF 변환기를 열지 못했습니다" '
              'message errorMessage as critical\nend try\n')
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'launcher.applescript'
        source.write_text(script, encoding='utf-8')
        app = Path(tmp) / DESKTOP_APP.name
        subprocess.run(['/usr/bin/osacompile', '-o', str(app), str(source)], check=True)
        if DESKTOP_APP.exists():
            shutil.move(str(DESKTOP_APP), str(backup / DESKTOP_APP.name))
        shutil.copytree(app, DESKTOP_APP)
    print('설치 완료: ' + str(DESKTOP_APP))
    print('앞으로는 바탕화면의 HWP-PDF 변환기 앱을 더블클릭하세요. 터미널은 열리지 않습니다.')
    print('실행 기록: ~/.raon-workspace/hwptopdf.log')
    subprocess.run(['/usr/bin/open', str(DESKTOP_APP)], check=True)


if __name__ == '__main__':
    install()

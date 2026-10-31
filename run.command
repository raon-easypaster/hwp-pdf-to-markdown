#!/bin/bash
# One-time macOS setup. Daily launches use the installed desktop .app.
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3을 먼저 설치하세요: https://www.python.org/downloads/'
  exit 1
fi
echo '최초 설치 중입니다. 설치 후에는 바탕화면의 HWP-PDF 변환기 앱을 사용하세요.'
python3 install-macos.py

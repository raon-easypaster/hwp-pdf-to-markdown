#!/bin/bash
# Compatibility launcher for existing macOS app wrappers.
set -e
RUNTIME="$HOME/Applications/hwp-pdf-to-markdown"
if [ -x "$RUNTIME/venv/bin/python3" ] && [ -f "$RUNTIME/start-silent.py" ]; then
  exec "$RUNTIME/venv/bin/python3" "$RUNTIME/start-silent.py" "$@"
fi
echo '먼저 run.command를 한 번 실행하여 설치해 주세요.' >&2
exit 1

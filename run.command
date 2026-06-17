#!/bin/bash
# Move to the directory where this script is located
cd "$(dirname "$0")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 HWP to Obsidian 변환기 실행기를 시작합니다..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Check Python installation
if ! command -v python3 &> /dev/null; then
    echo "❌ 파이썬(Python 3)이 설치되어 있지 않습니다."
    echo "https://www.python.org/downloads/ 에서 파이썬을 설치한 후 다시 실행해주세요."
    read -p "엔터를 누르면 종료됩니다..."
    exit 1
fi

# 2. Setup Virtual Environment if not present
if [ ! -d "venv" ]; then
    echo "📦 가상 환경(venv)을 초기 설정하는 중입니다... (최초 1회 실행)"
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "❌ 가상 환경 생성 실패"
        read -p "엔터를 누르면 종료됩니다..."
        exit 1
    fi
fi

# 3. Install/Update Dependencies
echo "📥 필요한 패키지(Flask, pyhwp, olefile, six, pypdf 등)를 설치하고 있습니다..."
./venv/bin/pip install --upgrade pip &> /dev/null
./venv/bin/pip install pyhwp olefile tqdm flask six pypdf
if [ $? -ne 0 ]; then
    echo "❌ 패키지 설치 실패"
    read -p "엔터를 누르면 종료됩니다..."
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 서버 준비 완료!"
echo "   http://localhost:5001"
echo "   (서버를 종료하려면 이 터미널 창을 닫거나 Ctrl+C를 누르세요)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 4. Start Flask server
./venv/bin/python3 app.py &
SERVER_PID=$!

# Wait 2 seconds for server to start, then open browser
sleep 2
open http://localhost:5001

# Wait for server process
wait $SERVER_PID

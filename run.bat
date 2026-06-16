@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo 🚀 HWP to Obsidian 변환기 실행기를 시작합니다...
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

:: 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ❌ 파이썬(Python 3)이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 파이썬을 설치한 후 다시 실행해주세요.
    echo (설치 시 'Add Python to PATH' 옵션을 반드시 체크해주세요.)
    pause
    exit /b 1
)

:: 2. Setup Virtual Environment if not present
if not exist venv (
    echo 📦 가상 환경(venv)을 초기 설정하는 중입니다... (최초 1회 실행)
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ❌ 가상 환경 생성 실패
        pause
        exit /b 1
    )
)

:: 3. Install/Update Dependencies
echo 📥 필요한 패키지(Flask, pyhwp, olefile, six 등)를 설치하고 있습니다...
venv\Scripts\python -m pip install --upgrade pip >nul 2>nul
venv\Scripts\pip install pyhwp olefile tqdm flask six
if %errorlevel% neq 0 (
    echo ❌ 패키지 설치 실패
    pause
    exit /b 1
)

echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo 🎉 서버 준비 완료!
echo   http://localhost:5001
echo   (서버를 종료하려면 이 창을 닫거나 Ctrl+C를 누르세요)
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

:: 4. Start Flask server and open browser
start "" "http://localhost:5001"
venv\Scripts\python app.py

pause

#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
DIR="/Users/galeb76/Documents/tools/hwp2obsidian"
cd "$DIR"

# Clean old processes safely
if lsof -Pi :5001 -sTCP:LISTEN -t >/dev/null ; then
    kill -9 $(lsof -t -i :5001) 2>/dev/null
    sleep 1
fi

source venv/bin/activate
python app.py > /dev/null 2>&1 &
sleep 1.5
open http://localhost:5001

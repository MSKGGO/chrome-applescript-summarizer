#!/bin/bash
# 더블클릭으로 GUI 앱 실행
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
exec /usr/bin/python3 "$DIR/app.py"

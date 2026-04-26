#!/bin/bash
# 단일 URL 테스트 wrapper
# USAGE: bash test_url.sh <URL>

if [ -z "$1" ]; then
    echo "USAGE: bash test_url.sh <URL>"
    exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== Fetching + Summarizing: $1 ==="
echo
time python3 "$DIR/summarize.py" "$1"

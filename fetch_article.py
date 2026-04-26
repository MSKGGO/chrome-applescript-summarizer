"""
fetch_article.py
================
URL → 평소 macOS Google Chrome(AppleScript)으로 본문 추출 → JSON stdout.

핵심 원리:
  - 사용자 평소 Chrome 사용 → 자동화 흔적 0 + 로그인 세션 그대로
  - 새 탭은 백그라운드로 열림 (Chrome 앞으로 튀어나오지 않음, 활성 탭도 안 빼앗김)
  - 본문 1500자 이상 채워질 때까지 1.5초 간격 polling
  - 추출 후 그 탭 자동 close

사전 조건:
  Chrome > View > Developer > "Allow JavaScript from Apple Events" 체크 (1회)
  첫 실행 시 macOS가 Apple Events 권한 팝업 → 허용

USAGE:
  python3 fetch_article.py <URL>
  python3 fetch_article.py --headed <URL>   # 브라우저 보이게 (디버그용)

OUTPUT (stdout JSON):
  성공: {"title": "...", "url": "...", "body": "..."}
  실패: {"error": "timeout_or_paywalled", ...}  + exit code 1
"""
import sys
import json
import time
import subprocess

url = next((a for a in sys.argv[1:] if a.startswith("http")), None)
if not url:
    print("USAGE: python3 fetch_article.py <URL>", file=sys.stderr)
    sys.exit(2)

# Step 1: 평소 Chrome에서 새 탭 열기 (완전 백그라운드)
OPEN_SCRIPT = f'''
tell application "Google Chrome"
    if not running then run
    if (count of windows) is 0 then
        make new window
    end if
    try
        set originalActive to active tab index of window 1
    on error
        set originalActive to 1
    end try
    make new tab at end of tabs of window 1 with properties {{URL:"{url}"}}
    try
        set active tab index of window 1 to originalActive
    end try
end tell
'''
subprocess.run(["osascript", "-e", OPEN_SCRIPT], check=True)

# Step 2: 본문이 충분히 채워질 때까지 polling
GET_SCRIPT = '''
tell application "Google Chrome"
    return execute (last tab of window 1) javascript "JSON.stringify({t:document.title,u:location.href,b:(document.querySelector('article')||document.querySelector('main')||document.body).innerText})"
end tell
'''

# Step 3 준비: 추출 끝나면 URL 매칭으로 그 탭만 close
URL_MATCH = url.split("?")[0][:80].replace('"', '')
CLOSE_SCRIPT = f'''
tell application "Google Chrome"
    repeat with w in windows
        set tabList to tabs of w
        repeat with i from 1 to count of tabList
            try
                if URL of (item i of tabList) starts with "{URL_MATCH}" then
                    close (item i of tabList)
                    return
                end if
            end try
        end repeat
    end repeat
end tell
'''

deadline = time.time() + 60
result = None
last_proc = None
time.sleep(1)  # 첫 polling은 1초 후
while time.time() < deadline:
    last_proc = subprocess.run(
        ["osascript", "-e", GET_SCRIPT],
        capture_output=True,
        text=True,
    )
    if last_proc.returncode != 0:
        time.sleep(1.5)
        continue
    out = last_proc.stdout.strip()
    if not out:
        time.sleep(1.5)
        continue
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        time.sleep(1.5)
        continue
    body = result.get("b", "") or ""
    if len(body) > 1500 and "Subscribe to continue" not in body[:500]:
        break
    time.sleep(1.5)

# 결과 처리 + 탭 정리
try:
    subprocess.run(["osascript", "-e", CLOSE_SCRIPT], capture_output=True, timeout=10)
except Exception:
    pass

if result and len(result.get("b", "")) > 1000:
    print(json.dumps(
        {"title": result["t"], "url": result["u"], "body": result["b"]},
        ensure_ascii=False,
    ))
    sys.exit(0)

print(json.dumps({
    "error": "timeout_or_paywalled",
    "title": result.get("t") if result else None,
    "url": result.get("u") if result else None,
    "body_len": len(result.get("b", "")) if result else 0,
    "stderr": last_proc.stderr if last_proc else None,
}, ensure_ascii=False))
sys.exit(1)

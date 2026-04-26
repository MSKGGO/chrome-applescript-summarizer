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
# 셀렉터 다중화 — 글로벌 표준 + 한국 그누보드/제로보드/네이버/티스토리 등 흔한 본문 컨테이너
# 모든 후보 중 innerText 길이가 가장 긴 것 선택 → 사이드바의 짧은 <article> 잡혀도 진짜 본문이 더 길어서 이김
GET_SCRIPT = r'''
tell application "Google Chrome"
    return execute (last tab of window 1) javascript "(function(){var sels=['article','main','[itemprop=\"articleBody\"]','[role=\"main\"]','#articleBody','#article-view-content-div','#newsContent','#news_body_area','.article_body','.article-body','.article_view','.view_content','.entry-content','.post-content'];var best='';for(var i=0;i<sels.length;i++){try{var el=document.querySelector(sels[i]);if(el){var t=el.innerText||'';if(t.length>best.length)best=t;}}catch(e){}}if(best.length<200)best=document.body.innerText||'';return JSON.stringify({t:document.title,u:location.href,b:best});})()"
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

# Cloudflare/봇체크 + 한국 사이트 로그인 요구 감지 키워드 (소문자 비교)
CHALLENGE_HINTS = [
    # 영어 (Cloudflare / 봇 차단)
    "just a moment", "checking your browser", "verify you are human",
    "are you a robot", "ddos protection by", "cloudflare",
    "challenge-platform", "needs to review the security",
    "performance & security by", "press & hold",
    "please confirm you are not a bot",
    # 한국어 (로그인 요구 / 본인 인증) — 사용자가 평소 계정으로 직접 통과할 시간 확보
    "본인 인증", "본인인증",
    "로그인이 필요", "로그인 후 이용", "로그인 해주세요",
    "회원 가입", "회원가입 후",
    "구독자만", "유료 회원",
    "인증이 필요합니다",
]


def is_challenge_page(body: str, title: str) -> bool:
    """본문이 짧고 챌린지 키워드 포함 시 True."""
    if len(body) > 3000:
        return False
    blob = (title + " " + body[:1500]).lower()
    return any(h in blob for h in CHALLENGE_HINTS)


# 챌린지 감지 시: 새 탭을 활성화 + Chrome을 앞으로 (사용자가 즉시 보고 통과)
ACTIVATE_LAST_TAB = '''
tell application "Google Chrome"
    activate
    try
        set active tab index of window 1 to (count of tabs of window 1)
    end try
end tell
'''

deadline = time.time() + 60
result = None
last_proc = None
challenge_alerted = False
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
    title = result.get("t", "") or ""

    # 본문이 충분(800자 이상)하고 챌린지/페이월 아니면 polling 종료
    # 한국 단신(800-1500자)도 통과 가능. 최종 검증은 아래 1000자 + 페이월 키워드로 한 번 더
    if len(body) > 800 and "Subscribe to continue" not in body[:500] \
            and "Sign in to continue" not in body[:500] \
            and not is_challenge_page(body, title):
        break

    # 챌린지 감지: 한 번만 탭 활성화 + 시간 연장 (5분까지)
    if not challenge_alerted and is_challenge_page(body, title):
        challenge_alerted = True
        try:
            subprocess.run(["osascript", "-e", ACTIVATE_LAST_TAB], capture_output=True, timeout=5)
        except Exception:
            pass
        deadline = time.time() + 300
        print(f"[CHALLENGE DETECTED] 탭 활성화. 5분까지 대기. title={title!r}", file=sys.stderr)

    time.sleep(1.5)

# 결과 처리 + 탭 정리
try:
    subprocess.run(["osascript", "-e", CLOSE_SCRIPT], capture_output=True, timeout=10)
except Exception:
    pass

if result and len(result.get("b", "")) > 1000:
    final_body = result.get("b", "") or ""
    # 페이월 첫 단락 방어 — 1000자 넘었어도 명백한 페이월 키워드 있으면 거부
    if "Subscribe to continue" not in final_body[:500] \
            and "Sign in to continue" not in final_body[:500]:
        print(json.dumps(
            {"title": result["t"], "url": result["u"], "body": final_body},
            ensure_ascii=False,
        ))
        sys.exit(0)

err_type = "timeout_or_paywalled"
if challenge_alerted:
    err_type = "challenge_not_passed"

print(json.dumps({
    "error": err_type,
    "hint": "Cloudflare/봇 챌린지가 통과되지 않았습니다. 그 Chrome 탭에서 직접 클릭/체크 후 다시 시도하세요." if challenge_alerted else None,
    "title": result.get("t") if result else None,
    "url": result.get("u") if result else None,
    "body_len": len(result.get("b", "")) if result else 0,
    "stderr": last_proc.stderr if last_proc else None,
}, ensure_ascii=False))
sys.exit(1)

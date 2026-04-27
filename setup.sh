#!/bin/bash
# ==============================================================
#  Chrome AppleScript Summarizer — macOS 자동 셋업 (v0.1)
# ==============================================================
#  실행: bash setup.sh
#
#  자동 처리:
#    1. macOS / Chrome / Python3 확인
#    2. Homebrew 미설치 시 설치 안내
#    3. Node.js 미설치 시 자동 설치 (brew install node)
#    4. OAuth CLI 중 하나 선택해서 자동 설치 (npm install)
#    5. Chrome 옵션 안내 (수동 1회: "Allow JavaScript from Apple Events")
#    6. 첫 실행 권한 팝업 안내
# ==============================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Chrome AppleScript Summarizer — 자동 셋업               ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}\n"

# ── 1. OS 확인 (macOS 전용) ──
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}[X] 이 도구는 macOS 전용입니다 (현재: $(uname))${NC}"
    echo -e "${YELLOW}Windows/Linux는 지원하지 않습니다 — README의 'Windows 미지원' 섹션 참조.${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] macOS $(sw_vers -productVersion 2>/dev/null)${NC}"

# ── 2. Google Chrome ──
if [ ! -d "/Applications/Google Chrome.app" ]; then
    echo -e "${RED}[X] Google Chrome 미설치${NC}"
    echo "    설치: https://www.google.com/chrome/"
    exit 1
fi
echo -e "${GREEN}[✓] Google Chrome 설치됨${NC}"

# ── 3. Python 3 ──
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}[X] Python 3 없음${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] $(python3 --version)${NC}"

# ── 4. Homebrew (선택, Node.js 자동 설치용) ──
if ! command -v brew &>/dev/null; then
    echo -e "${YELLOW}[!] Homebrew 미설치 — Node.js/CLI 자동 설치를 원하면 먼저 설치 권장${NC}"
    echo "    설치 명령: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    HAS_BREW=0
else
    echo -e "${GREEN}[✓] Homebrew $(brew --version | head -1)${NC}"
    HAS_BREW=1
fi

# ── 5. Node.js (CLI 설치용) ──
if ! command -v node &>/dev/null; then
    echo -e "${YELLOW}[!] Node.js 미설치 (OAuth CLI 설치에 필요)${NC}"
    if [ "$HAS_BREW" = "1" ]; then
        read -p "지금 자동 설치하시겠습니까? (brew install node) [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            brew install node
            echo -e "${GREEN}[✓] Node.js $(node --version)${NC}"
        else
            echo -e "${YELLOW}    스킵 — 나중에 'brew install node' 실행 후 OAuth CLI 설치 가능${NC}"
        fi
    else
        echo "    Homebrew 설치 후 'brew install node' 실행"
    fi
else
    echo -e "${GREEN}[✓] Node.js $(node --version)${NC}"
fi

# ── 6. OAuth CLI 자동 설치 (선택) ──
echo
echo -e "${BLUE}── OAuth CLI 설치 (인증 방식 선택) ──${NC}"
echo "OAuth CLI 한 개만 설치/로그인하면 됩니다. 어느 거 사용하시겠습니까?"
echo "  ${GREEN}1)${NC} Claude Code      — Anthropic 계정 OAuth"
echo "  ${GREEN}2)${NC} Codex (ChatGPT)  — ChatGPT Plus/Pro 구독자 OAuth"
echo "  ${GREEN}3)${NC} Gemini CLI       — Google 계정 OAuth (무료 티어 후함)"
echo "  ${GREEN}4)${NC} 스킵 (API 키 사용 또는 나중에 GUI에서 설치)"
echo
read -p "선택 [1-4]: " -n 1 CLI_CHOICE
echo

if [ "$CLI_CHOICE" = "1" ]; then
    if command -v node &>/dev/null; then
        echo -e "${BLUE}Claude Code 설치 중...${NC}"
        npm install -g @anthropic-ai/claude-code 2>&1 | tail -3
        echo -e "${GREEN}[✓] 설치 완료. 터미널에 'claude' 실행해 OAuth 로그인 (1회)${NC}"
    else
        echo -e "${RED}    Node.js 필요 — 설치 후 'npm install -g @anthropic-ai/claude-code'${NC}"
    fi
elif [ "$CLI_CHOICE" = "2" ]; then
    if command -v node &>/dev/null; then
        echo -e "${BLUE}Codex CLI 설치 중...${NC}"
        npm install -g @openai/codex 2>&1 | tail -3
        echo -e "${GREEN}[✓] 설치 완료. 터미널에 'codex' 실행 → 'Sign in with ChatGPT'${NC}"
    else
        echo -e "${RED}    Node.js 필요${NC}"
    fi
elif [ "$CLI_CHOICE" = "3" ]; then
    if command -v node &>/dev/null; then
        echo -e "${BLUE}Gemini CLI 설치 중...${NC}"
        npm install -g @google/gemini-cli 2>&1 | tail -3
        echo -e "${GREEN}[✓] 설치 완료. 터미널에 'gemini' 실행 → 'Login with Google'${NC}"
    else
        echo -e "${RED}    Node.js 필요${NC}"
    fi
else
    echo -e "${YELLOW}    스킵 — 나중에 GUI [⚙️ 설정 변경] 패널의 [📦 설치] 버튼 사용 가능${NC}"
fi

# ── 7. Chrome 옵션 안내 (수동 필수) ──
echo
echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║  ⚠️  Chrome 1회 설정 필요 (수동, 자동화 불가)            ║${NC}"
echo -e "${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
echo "Chrome 메뉴 → ${YELLOW}보기(View)${NC} → ${YELLOW}개발자 정보(Developer)${NC}"
echo "         → ${YELLOW}\"Allow JavaScript from Apple Events\"${NC} 체크"
echo
read -p "위 설정 완료했나요? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}    설정 후 다시 'bash setup.sh' 실행 권장${NC}"
fi

# ── 8. Summarizer.app 자동 빌드 (더블클릭 실행 — 터미널 안 뜸) ──
DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$DIR/Summarizer.app"
echo
echo -e "${BLUE}── Summarizer.app 빌드 (더블클릭 실행 가능) ──${NC}"

# 기존 .app 있으면 제거 후 새로 빌드
rm -rf "$APP_PATH"

# AppleScript (임시 파일 경유 — osacompile은 /dev/stdin 못 읽음):
#   1) 8765 포트가 점유 중이면 → 이미 떠있는 인스턴스. 브라우저만 새로 열기
#   2) 포트 비어있으면 → 백그라운드로 app_web.py 실행 + 브라우저 열기
TMP_SCPT="$(mktemp -t summarizer.XXXXXX).applescript"
cat > "$TMP_SCPT" <<APPLESCRIPT
on run
    set workDir to "$DIR"
    set logPath to "/tmp/summarizer.log"
    set isRunning to false
    try
        do shell script "lsof -nP -iTCP:8765 -sTCP:LISTEN > /dev/null 2>&1"
        set isRunning to true
    end try
    if not isRunning then
        do shell script "cd " & quoted form of workDir & " && /usr/bin/python3 app_web.py > " & logPath & " 2>&1 &"
        delay 1.5
    end if
    do shell script "open http://localhost:8765/"
end run
APPLESCRIPT
osacompile -o "$APP_PATH" "$TMP_SCPT"
rm -f "$TMP_SCPT"

if [ -d "$APP_PATH" ]; then
    echo -e "${GREEN}[✓] Summarizer.app 빌드 완료 — $APP_PATH${NC}"
    echo "    Finder에서 더블클릭하면 GUI 자동 실행"
else
    echo -e "${YELLOW}[!] .app 빌드 실패 (osacompile). 터미널로 'python3 app_web.py' 직접 실행 가능${NC}"
fi

# ── 9. 완료 ──
echo
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✓ 셋업 완료                                              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo
echo -e "${GREEN}실행 방법 (택 1):${NC}"
echo "  A. ${GREEN}Finder에서 Summarizer.app 더블클릭${NC} (가장 편함)"
echo "  B. ${GREEN}python3 $DIR/app_web.py${NC} (터미널)"
echo
echo "Tip: Summarizer.app을 ${BLUE}/Applications${NC}으로 옮기거나 Dock에 드래그하면 더 편리."
echo
echo "첫 실행 시 macOS 권한 팝업 (\"Summarizer가 Chrome 제어 허용?\") → ${GREEN}허용${NC} 클릭"
echo "이후엔 모든 게 자동 — URL 붙여넣기만 하면 한국어 요약."
echo
echo "문서: README.md / 트러블슈팅 / 라이선스 모두 포함"

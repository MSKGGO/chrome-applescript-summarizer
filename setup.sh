#!/bin/bash
# ==============================================================
#  Paywall News Summarizer — macOS 1회 셋업 체크 스크립트
# ==============================================================
#  실행: bash setup.sh
#  - 의존성 확인 (Chrome, Python, claude CLI)
#  - Chrome 설정 안내 (Allow JavaScript from Apple Events)
#  - 첫 권한 팝업 안내
# ==============================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Paywall News Summarizer 셋업 체크 ===${NC}\n"

# ── 1. macOS 확인 ──
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}[X] macOS 전용입니다 (현재: $(uname))${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] macOS$(sw_vers -productVersion 2>/dev/null)${NC}"

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

# ── 4. claude CLI ──
if ! command -v claude &>/dev/null; then
    echo -e "${RED}[X] claude CLI 미설치${NC}"
    echo "    설치 가이드: https://docs.claude.com/en/docs/claude-code/setup"
    exit 1
fi
CLAUDE_VER=$(claude --version 2>&1 | head -1)
echo -e "${GREEN}[✓] $CLAUDE_VER${NC}"

# ── 5. claude 로그인 상태 ──
echo -e "\n${YELLOW}claude CLI 로그인 상태 확인 중...${NC}"
LOGIN_TEST=$(echo "ok" | claude -p --output-format text "한 단어로만 답: 안녕" 2>&1 | head -3)
if echo "$LOGIN_TEST" | grep -qi "not logged in\|please run /login\|authentication"; then
    echo -e "${RED}[X] claude CLI 로그인 안 됨${NC}"
    echo "    터미널에서 'claude' 한 번 실행해 로그인 후 재시도"
    exit 1
fi
echo -e "${GREEN}[✓] claude CLI OAuth 정상${NC}"

# ── 6. Chrome AppleScript JS 옵션 안내 ──
echo -e "\n${YELLOW}=== 다음 1회 설정 필요 (수동) ===${NC}"
echo "Chrome 메뉴 > 보기(View) > 개발자 정보(Developer)"
echo " → ${YELLOW}\"Allow JavaScript from Apple Events\"${NC} 체크"
echo
read -p "위 설정 완료했나요? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}설정 후 다시 실행해주세요.${NC}"
    exit 1
fi

# ── 7. 작업 디렉토리 + counts 파일 위치 ──
COUNTS_DIR="$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")"
echo -e "\n${GREEN}[✓] 패키지 위치: $COUNTS_DIR${NC}"

# ── 8. 첫 실행 안내 ──
echo -e "\n${GREEN}=== 셋업 완료 ===${NC}"
echo
echo "테스트 실행:"
echo "  ${YELLOW}python3 $COUNTS_DIR/summarize.py https://www.cnbc.com/2026/04/26/example.html${NC}"
echo
echo "첫 실행 시 macOS가 ${YELLOW}\"Terminal이 Google Chrome 제어 허용?\"${NC} 팝업을 띄움"
echo "→ ${GREEN}허용${NC} 클릭 (이후엔 자동)"
echo
echo "텔레그램 봇 통합: telegram_bot_integration.py 참고"

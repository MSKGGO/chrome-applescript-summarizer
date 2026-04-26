# Chrome AppleScript Summarizer v0.2

> ⚠️ **macOS 전용 도구입니다.** Windows / Linux 미지원 — AppleScript는 macOS Apple Events API라 OS-level 등가물이 다른 OS에 없습니다. Windows에서는 작동하지 않으며, 별도 포팅 계획도 현재 없습니다.

URL을 던지면 **평소 쓰던 macOS Chrome**으로 본문을 가져와 **한국어 요약 포맷**으로 돌려주는 자동화 패키지.

**지원 사이트:** Bloomberg / WSJ / FT / Reuters / CNBC / 한국 언론 / 블로그 / 보도자료 등 거의 모든 웹 기사. 평소 Chrome에 로그인된 paywall 사이트도 그대로 통과.

---

## 시스템 요구사항 (필수)

| 항목 | 요구 | 자동 처리? |
|---|---|---|
| **OS** | macOS 10.15+ | — |
| **Google Chrome** | 설치 + 평소 사용 (paywall 사이트 로그인된 상태) | — |
| **Python 3** | 3.9+ (macOS 기본 포함) | ✅ |
| **Homebrew** | (선택) Node.js/CLI 자동 설치용 | 안내 |
| **Node.js** | (선택) OAuth CLI 사용 시 필요 | ✅ setup.sh에서 자동 설치 옵션 |
| **OAuth CLI 또는 API 키** | 6가지 중 하나 | ✅ setup.sh + GUI 모두 자동 설치 버튼 |

> ❌ **Windows / Linux 사용자**: 지원 불가. README 하단 "왜 Windows 미지원인가" 참조.

---

## 5분 설치 (3단계)

```bash
# 1. 클론
git clone https://github.com/MSKGGO/chrome-applescript-summarizer.git
cd chrome-applescript-summarizer

# 2. 자동 셋업 (인터랙티브)
bash setup.sh
#   → macOS/Chrome/Python 확인
#   → (선택) Node.js 자동 설치 (brew install node)
#   → (선택) OAuth CLI 1개 자동 설치 (Claude/Codex/Gemini 중)
#   → Chrome "Allow JavaScript from Apple Events" 안내 (수동 1회)

# 3. GUI 실행 (브라우저 자동 열림)
python3 app_web.py
```

→ 첫 실행 시 macOS가 **"Terminal이 Google Chrome 제어 허용?"** 팝업 → **허용** 클릭. 이후 자동.

---

## 자동화된 것 vs 수동 필요한 것

| 단계 | 자동 / 수동 |
|---|---|
| OS / Chrome / Python 환경 체크 | ✅ setup.sh 자동 |
| Node.js 설치 | ✅ Homebrew 있으면 setup.sh가 자동 (사용자 동의 후) |
| OAuth CLI 설치 (npm install) | ✅ setup.sh 또는 GUI [📦 설치] 버튼 |
| OAuth 로그인 (브라우저 인증) | ⚠️ 사용자가 터미널에서 한 번 (예: `claude` 또는 `gemini`) |
| Chrome "Allow JavaScript from Apple Events" 옵션 | ❌ Chrome 메뉴에서 수동 1회 (자동화 불가) |
| macOS Apple Events 권한 팝업 | ❌ 첫 실행 시 사용자가 "허용" 클릭 (자동화 불가) |
| 평소 Chrome에 paywall 사이트 로그인 | ❌ 사용자가 평소처럼 |
| URL → 본문 → 요약 | ✅ 완전 자동 |

---

## 사용법 — 3가지

### 🖥️ A. 웹 GUI (가장 편함, 추천)
```bash
python3 app_web.py
# 브라우저 자동 열림 → http://localhost:8765/
```

기능:
- **textarea에 URL 여러 개** 한 번에 → ⌘+⏎ 또는 [큐에 추가]
- **순차 처리** (Chrome/LLM 충돌 회피)
- 작업 카드로 **실시간 진행 표시** (대기 → 처리 중 → 완료/실패)
- **[⚙️ 설정 변경]** 패널에서:
  - CLI 라이브 상태 (✅⚠️❌)
  - **[📦 설치]** 버튼 — 미설치 CLI를 1클릭 설치
  - **[🔑 로그인 안내]** 버튼 — OAuth 로그인 정확한 명령 팝업
  - Provider 6가지 중 선택 + 모델 선택 + (필요시) API 키 입력

### ⌨️ B. CLI 단독
```bash
python3 summarize.py https://www.bloomberg.com/news/articles/...
# → 한국어 요약 stdout 출력
```

### 🤖 C. 텔레그램 봇 통합
`telegram_bot/bot.py` (본인 봇이 있다면) 에 `telegram_bot_integration.py` 안의 코드 복붙. 자세한 가이드는 그 파일 상단 주석.

---

## 인증 방식 6가지

### 🔐 OAuth (CLI 한 번 로그인하면 끝, API 키 입력 불필요)

| Provider | 설치 명령 | 로그인 | 비용 |
|---|---|---|---|
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` 또는 https://docs.claude.com/en/docs/claude-code/setup | 터미널에 `claude` → Anthropic OAuth | 본인 Claude Code 구독 |
| **OpenAI Codex (ChatGPT)** | `npm install -g @openai/codex` | 터미널에 `codex` → ChatGPT 계정 OAuth | ChatGPT Plus/Pro 구독에 포함 |
| **Google Gemini CLI** | `npm install -g @google/gemini-cli` | 터미널에 `gemini` → Google 계정 OAuth | **무료 티어 후함** (Gemini 2.5 Pro 1M tokens/day) |

→ setup.sh 또는 GUI [📦 설치] 버튼으로 자동 설치 가능. **첫 실행 시 OAuth 자동 감지** — 위 중 하나만 로그인되어 있으면 즉시 사용.

### 🔑 API 키 (폴백, OAuth 못 쓰는 환경용)

| Provider | 키 발급 |
|---|---|
| Anthropic | https://console.anthropic.com/settings/keys |
| OpenAI | https://platform.openai.com/api-keys |
| Google AI Studio | https://aistudio.google.com/apikey (무료 티어) |

설정은 `~/.config/chrome-applescript-summarizer/config.json` (chmod 600)에 본인 기기에만 저장. 외부 전송 X.

---

## Cloudflare / 봇 챌린지 자동 대응

manilatimes 같은 사이트에서 가끔 뜨는 Cloudflare "Verify you are human" / "Just a moment" 챌린지:

1. polling 중 챌린지 키워드 감지 (Cloudflare, PRESS&HOLD 등)
2. 자동으로 그 Chrome 탭을 활성화 + Chrome을 foreground로
3. 사용자가 화면에서 직접 통과 (체크박스 클릭 또는 잠시 대기)
4. timeout 60s → 300s 자동 연장
5. 통과되면 polling이 본문 잡음 → 정상 추출

**한 번 통과하면** Cloudflare가 신뢰 쿠키 발행 → 다음번엔 자동 통과.

---

## 처리 시간

| 단계 | 시간 |
|---|---|
| 본문 추출 (Chrome 새 탭 + JS) | 5~15초 |
| LLM 요약 (Haiku/Flash 기준) | 10~25초 |
| **합계 (정상)** | **15~40초** |
| Cloudflare 챌린지 시 | + 사용자 통과 시간 |

---

## 일일 사용량 가드 (자가 제어)

텔레그램 봇 통합 시 자동:
- 도메인별 **50건/일 권장** (`bloomberg.com` 50, `wsj.com` 50…)
- 초과해도 **차단 X — 알림만** (사용량 자제 권장)
- 매일 자정 자동 리셋
- 저장: `~/Crawler/.daily_counts.json`

GUI 단독 사용 시는 카운터 없음 (사용자 책임).

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `timeout_or_paywalled` (body 0자) | (1) Chrome이 안 켜짐 → 켜고 재시도. (2) 해당 사이트에 평소 로그인 X → Chrome에서 수동 로그인 후 재시도 |
| `Not logged in · Please run /login` | OAuth CLI 로그인 만료 → 터미널에서 해당 CLI 다시 실행 |
| `env: node: No such file or directory` | launchd 환경에 PATH 누락 — 코드에 `/opt/homebrew/bin` 강제 포함되어 있음 (수정됨) |
| Chrome 창이 갑자기 앞으로 옴 | Cloudflare 챌린지 감지 → 정상 동작. 화면에서 통과해주세요 |
| `challenge_not_passed` | 챌린지가 5분 안에 통과 안 됨. 다시 시도 |
| 본문 한국어인데 영어로 요약됨 | prompt가 한국어 우선 명시되어 있음. 모델을 sonnet/pro로 바꿔보기 |

---

## 윤리적 사용 가이드

본 도구는 **본인이 정상적으로 접근 가능한 기사를 빠르게 요약하는 자가 제어 도구**입니다.

**해야 할 것:**
- ✅ 본인 구독 권한 안에서만 사용
- ✅ 일일 50건/도메인 권장 사용량 준수
- ✅ 평소 본인이 읽을 만한 페이스 유지

**하지 말아야 할 것:**
- ❌ 사이트 ToS의 "automated access" 금지 조항을 무시
- ❌ 대량 일괄 크롤링 (한 번에 50건 이상)
- ❌ "탐지 회피" 목적의 위장 코드 추가 (User-Agent 회전, 사람 흉내 등) — 본 패키지는 의도적으로 제외
- ❌ 본인 권한 없는 paywall 우회

---

## 파일 구성

```
chrome-applescript-summarizer/
├── README.md                       # 이 파일
├── LICENSE                         # MIT
├── .gitignore                      # config.json 등 제외
├── app_web.py                      # 🖥️ 웹 GUI (큐, 설정 패널, CLI 자동 설치)
├── app.py                          # GUI 백엔드 (LLM 호출, OAuth 감지)
├── Summarizer.command              # 더블클릭 실행
├── fetch_article.py                # AppleScript 본문 추출 + Cloudflare 감지
├── summarize.py                    # CLI: 본문 + claude -p 요약
├── prompt_template.md              # 한국어 요약 프롬프트 (커스터마이즈 가능)
├── setup.sh                        # macOS 자동 셋업 (인터랙티브)
├── test_url.sh                     # 단일 URL 테스트
└── telegram_bot_integration.py     # python-telegram-bot 통합용 코드
```

---

## 왜 Windows / Linux 미지원인가

| 핵심 의존성 | macOS | Windows | Linux |
|---|---|---|---|
| Apple Events (OS 레벨 Chrome 조작) | ✅ AppleScript / osascript | ❌ 등가물 없음 | ❌ 등가물 없음 |
| 자동화 흔적 0 (사용자 평소 Chrome) | ✅ | ⚠️ CDP attach 가능하지만 매번 재실행 마찰 | 동일 |
| 사용자 평소 로그인 세션 활용 | ✅ | ⚠️ CDP attach 시만 | 동일 |

**Windows에서 작동 가능한 우회들:**
- CDP attach (`chrome.exe --remote-debugging-port=9222` 재실행) — 매번 마찰
- PyAutoGUI 화면 자동화 — fragile, 좌표 기반
- 자체 Chrome Extension — 개발 부담 큼 + 사이트 deny list 위험

→ macOS의 AppleScript처럼 깔끔한 OS-level 통합이 Windows엔 없어서, **별도 포팅 시 UX가 크게 떨어집니다.** 그래서 v0.x에서는 macOS 전용으로 유지합니다.

Windows 사용자께는:
1. **paywall 약한 사이트** (Reuters, CNBC, MarketWatch) → 일반 Playwright로 충분
2. **Bloomberg/WSJ/FT 빡센 paywall** → 본문 복붙이 가장 빠름

---

## 변경 이력

- **v0.2** (2026-04-26)
  - README 명확화 (macOS 전용 강조, 자동/수동 구분)
  - setup.sh 자동화 강화 (Node.js 자동 설치, OAuth CLI 선택 자동 설치)
- **v0.1** (2026-04-26)
  - 초기 공개 릴리스
  - 6 provider OAuth/API 키 지원
  - 큐 기반 다중 URL 처리
  - Cloudflare 챌린지 자동 감지
  - 텔레그램 봇 통합 코드

---

## 라이선스 / 면책

- MIT License (LICENSE 파일 참조)
- 사용 결과 발생하는 ToS 위반/계정 정지 등 모든 책임은 사용자 본인
- LLM API 사용량/비용은 사용자 본인 계정에 청구

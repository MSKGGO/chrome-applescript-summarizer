# Chrome AppleScript Summarizer

URL을 던지면 **평소 쓰던 macOS Chrome**으로 본문을 가져와 **한국어 요약 포맷**으로 돌려주는 자동화 패키지.

**지원 사이트:** Bloomberg / WSJ / FT / Reuters / CNBC / 한국 언론 / 블로그 / 보도자료 등 거의 모든 웹 기사. 평소 Chrome에 로그인된 paywall 사이트도 그대로 통과.

## 사용 방법 3가지 (목적에 맞게 선택)

| 방식 | 누구에게 | 의존성 |
|---|---|---|
| **🖥️ GUI 앱** (`app.py`) | 본인 API 키만 있으면 누구나 | Python 3.9+ (Tkinter 표준), 본인 Anthropic 또는 OpenAI API 키 |
| **⌨️ CLI** (`summarize.py`) | Claude Code 사용자 | `claude` CLI 설치 + 로그인 |
| **🤖 텔레그램 봇 통합** (`telegram_bot_integration.py`) | 본인 텔레그램 봇 운영자 | python-telegram-bot v20+ |

---

## 핵심 원리 — 왜 이게 통하나

| 방식 | Bloomberg 통과 | 이유 |
|---|---|---|
| `WebFetch` / `requests` | ❌ | IP/UA만으로 Akamai 403 |
| Playwright Chromium | ❌ | `navigator.webdriver=true` 등 자동화 흔적 → PRESS&HOLD 거부 |
| Playwright + stealth | ⚠️ | PRESS&HOLD는 통과해도 Google OAuth가 자동화 컨테이너 거부 |
| Claude for Chrome 확장 | ❌ | Anthropic이 bloomberg.com 도메인을 시스템 deny |
| **이 패키지 (AppleScript + 평소 Chrome)** | ✅ | OS Apple Event로 사용자 평소 Chrome 조작 → 자동화 흔적 0, 평소 로그인 세션 그대로 |

**전제:** 본인이 정식 구독자이거나 무료 접근 가능한 기사만 사용. 자동화로 권한 우회하지 않음.

---

## 1회 셋업 (5분)

### A. 필수 도구
- **macOS**
- **Google Chrome** 설치 + 평소 로그인 상태 (paywall 사이트는 본인 구독으로 로그인되어 있어야)
- **Claude Code CLI** 설치 + 로그인 (https://docs.claude.com/en/docs/claude-code/setup)
  - `claude --version` 으로 확인
- **Python 3.9+** (macOS 기본 `/usr/bin/python3` OK)

### B. Chrome 설정 (1회)
1. Chrome 메뉴 → **보기(View)** → **개발자 정보(Developer)** → **"Allow JavaScript from Apple Events"** 체크

### C. 패키지 설치
```bash
# 1. 이 share 디렉토리를 원하는 위치에 복사
cp -r share ~/Crawler

# 2. 자동 셋업 스크립트 실행 (권한 안내 + 의존성 체크)
bash ~/Crawler/setup.sh
```

### D. 첫 실행 — 권한 허용
```bash
bash ~/Crawler/test_url.sh https://www.cnbc.com/2026/04/26/something.html
```
첫 실행 시 macOS가 **"Terminal이 Google Chrome 제어 허용?"** 팝업을 띄움 → **허용** 클릭.
이후엔 자동.

---

## 사용법

### 🖥️ GUI 앱 (가장 간단)
```bash
python3 app.py
# 또는 더블클릭: Summarizer.command
```

**인증 방식 — OAuth 우선, 첫 실행 시 자동 감지:**

### 🔐 OAuth (CLI 한 번 로그인하면 끝, API 키 입력 불필요)

| Provider | 설치 명령 | 로그인 | 비용 |
|---|---|---|---|
| **Claude Code** | https://docs.claude.com/en/docs/claude-code/setup | 터미널에 `claude` → 브라우저 OAuth | 본인 Claude Code 구독에 청구 |
| **OpenAI Codex (ChatGPT)** | `npm install -g @openai/codex` | 터미널에 `codex` → ChatGPT 계정 OAuth | 본인 ChatGPT Plus/Pro 구독에 포함 |
| **Google Gemini CLI** | `npm install -g @google/gemini-cli` | 터미널에 `gemini` → Google 계정 OAuth | 무료 티어 후함 (Gemini 2.5 Pro 1M tokens/day) |

**첫 실행 시** app.py가 위 3개 CLI를 우선순위대로 자동 감지 → 로그인된 게 있으면 즉시 사용.

### 🔑 API 키 (폴백, OAuth CLI 못 쓰는 환경용)

| Provider | 키 발급 |
|---|---|
| Anthropic | https://console.anthropic.com/settings/keys |
| OpenAI | https://platform.openai.com/api-keys |
| Google Gemini | https://aistudio.google.com/apikey (무료) |

비용은 본인 API 계정에 청구.

설정은 `~/.config/chrome-applescript-summarizer/config.json` (chmod 600)에 본인 기기에만 저장. 외부 전송 X.

설정은 `~/.config/chrome-applescript-summarizer/config.json` (chmod 600)에 본인 기기에만 저장. 외부 전송 X.

### ⌨️ CLI 단독
```bash
# URL → 한국어 요약 stdout 출력
python3 summarize.py https://www.bloomberg.com/news/articles/...
```
이 방식은 `claude` CLI가 설치돼 있고 로그인된 상태여야 함 (사용자 Claude Code 구독으로 처리).

### 🤖 텔레그램 봇 통합
`telegram_bot_integration.py` 안의 코드를 본인 봇 `bot.py`에 복붙. 자세한 가이드는 그 파일 상단 주석.

핵심 기능:
- `/sum <URL>` 명령어 또는 그냥 URL 메시지 → 자동 요약
- 여러 URL 한 번에 던지면 큐에 쌓아 순차 처리
- 도메인별 일일 사용량 표시 (자가 제어)
- `/qstatus` `/qclear` `/qrestart` 관리 명령어

---

## 동작 원리 (기술 스택)

```
사용자 → URL
   ↓
fetch_article.py
   - osascript로 평소 Chrome에 새 탭(백그라운드) 열기
   - 1.5초 간격 polling으로 article 본문 1500자 이상 채워질 때까지 대기
   - JS로 document.querySelector('article')||main||body innerText 추출
   - 추출 끝나면 그 탭 자동 close
   ↓
summarize.py
   - claude -p --model haiku 로 한국어 요약 (prompt_template.md 형식 적용)
   ↓
JSON 또는 마크다운 stdout 출력
```

**처리 시간:** 보통 15~30초 (본문 추출 5~10초 + claude haiku 요약 10~20초)

---

## 일일 사용량 가드 (자가 제어)

`telegram_bot_integration.py` 통합 시 자동 적용:
- 도메인별 **50건/일** 권장 (`bloomberg.com` 50, `wsj.com` 50…)
- 초과해도 차단 X — 경고 메시지만 표시 (사용량 자제 알림)
- 매일 자정 자동 리셋
- 저장: `~/Crawler/.daily_counts.json`

CLI 단독 사용 시는 카운터 없음 (사용자 책임으로 자제).

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `timeout_or_paywalled` (body 0자) | (1) Chrome이 안 켜짐 → 켜고 재시도. (2) 해당 사이트에 평소 로그인 X → Chrome에서 로그인 후 재시도. (3) `<article>` 태그 없는 페이지 → 일부 사이트 미지원 |
| `Not logged in · Please run /login` | claude CLI 인증 만료 → 터미널에서 `claude` 한 번 띄워 로그인 |
| `env: node: No such file or directory` | 봇 환경(launchd) PATH에 node 없음 → `summarize.py`의 `SUBPROC_ENV`에 `/opt/homebrew/bin` 포함되어 있는지 확인 |
| Chrome 창이 앞으로 튀어나옴 | `fetch_article.py`에서 `activate` 호출 제거됨 확인. 새 탭이 활성화되지 않게 `originalActive` 복원 코드 있어야 함 |
| 본문 한국어인데 영어로 요약됨 | `prompt_template.md`의 "한국어 우선" 부분 강조 또는 모델 변경 (`--model sonnet`) |
| Bloomberg PRESS & HOLD 같은 봇 챌린지 뜸 | 평소 Chrome으로 1회 통과 → 그 후 쿠키 유지로 자동 |

---

## 윤리적 사용 가이드

이 패키지는 **본인이 정상적으로 접근 가능한 기사를 빠르게 요약하는 자가 제어 도구**입니다.

**해야 할 것:**
- ✅ 본인 구독 권한 안에서만 사용
- ✅ 일일 50건/도메인 권장 사용량 준수
- ✅ 평소 본인이 읽을 만한 페이스 유지

**하지 말아야 할 것:**
- ❌ 사이트 ToS의 "automated access" 금지 조항을 인지한 채 무시하지 말 것 (각 사이트 약관 확인)
- ❌ 대량 일괄 크롤링 (한 번에 50건 이상 던지기)
- ❌ "탐지 회피" 목적의 위장 코드 추가 (User-Agent 회전, 랜덤 딜레이로 사람 흉내 등)
- ❌ 본인 권한 없는 paywall 우회 시도

**Anthropic Claude API 사용 정책** (claude CLI 호출 시): https://www.anthropic.com/legal/aup

각 사이트 ToS에 따라 본인 책임. 이 도구는 사용자 환경에서 본인 권한으로 동작할 뿐, 권한 없는 접근을 우회하지 않습니다.

---

## 파일 구성

```
share/
├── README.md                       # 이 파일
├── LICENSE                         # MIT
├── .gitignore                      # config.json, .daily_counts.json 등 제외
├── app.py                          # 🖥️ GUI 앱 (Anthropic/OpenAI API 직접 호출, Tkinter)
├── Summarizer.command              # 더블클릭 실행 wrapper
├── fetch_article.py                # AppleScript 본문 추출 (stdlib only)
├── summarize.py                    # CLI: 본문 + claude -p 요약
├── prompt_template.md              # 한국어 요약 프롬프트 (커스터마이즈 가능)
├── setup.sh                        # macOS 1회 셋업 체크
├── test_url.sh                     # 단일 URL 테스트
└── telegram_bot_integration.py     # python-telegram-bot 통합용 코드
```

---

## 라이선스 / 면책

- 이 패키지 자체는 자유 사용 (no warranty).
- 사용 결과 발생하는 ToS 위반/계정 정지 등 모든 책임은 사용자 본인.
- Anthropic Claude API 사용량/비용은 사용자 본인의 Claude Code 구독에 청구.

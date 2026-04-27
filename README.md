# Chrome AppleScript Summarizer v0.4

> 📜 **MIT 라이선스 — 누구나 자유롭게 사용/수정/배포/포크 가능.** 본인의 Claude/OpenAI/Gemini 계정과 본인의 macOS Chrome 세션을 쓰는 클라이언트 측 도구입니다. 코드 수정·개선 PR 환영.
>
> ⚠️ **macOS 전용 도구입니다.** Windows / Linux 미지원 — AppleScript는 macOS Apple Events API라 OS-level 등가물이 다른 OS에 없습니다. Windows에서는 작동하지 않으며, 별도 포팅 계획도 현재 없습니다.

URL을 던지면 **평소 쓰던 macOS Chrome**으로 본문을 가져와 **한국어 요약 포맷**으로 돌려주는 자동화 패키지.

**지원 사이트:** Bloomberg / WSJ / FT / Reuters / CNBC / 한국 언론 / 블로그 / 보도자료 등 거의 모든 웹 기사. 평소 Chrome에 로그인된 paywall 사이트도 그대로 통과.

**v0.4 주요 기능:**
- 🖱️ **더블클릭 실행** — `Summarizer.app` 자동 빌드 (setup.sh가 osacompile로 생성). 터미널 안 뜸, Dock에 드래그 가능
- 🔑 **OAuth 1클릭 로그인** — GUI [🔑 OAuth 로그인] 버튼 클릭만으로 Terminal.app 자동 열고 CLI 자동 실행 (사용자는 OAuth 진행만)
- 🖥️ 웹 GUI (큐 처리, 다크모드, 모바일 반응형, **모든 버튼 클릭 피드백**)
- 🔐 OAuth 6가지 / API 키 폴백 자동 감지 — **GUI ↔ 텔레그램 봇 cfg 공유** (한 번 설정하면 양쪽 반영)
- 🤖 **텔레그램 봇 multi-provider 지원** + 응답에 사용된 모델명 footer 자동 표시
- 📝 **프롬프트 실시간 편집기** — 1.5초 debounce 자동 저장 + 외부 편집 5초 폴링 감지. GUI ↔ 봇 같은 파일 공유
- 💾 **자동 저장 (날짜별 누적 로그)** — `~/Documents/Summaries/YYYY-MM-DD.md` 한 파일에 그날 요약 모두 시간순 append
- 🛡️ **안전장치** — 도메인별 일일 사용량 / 배치 크기 / 도메인 throttling (차단이 아니라 경고만)
- ✕ **작업별 취소 버튼** — 큐의 각 카드에서 진행 중/대기 중인 작업을 즉시 종료
- 🇰🇷 **한국 뉴스/블로그 본문 추출 강화** — 그누보드/제로보드/네이버/티스토리 셀렉터 다중화 + 한국어 챌린지 키워드

## CHANGELOG

### v0.4 (2026-04-27) — 설치/실행 UX 개선
- 🖱️ **`Summarizer.app` 자동 빌드** — setup.sh가 osacompile로 macOS Application 번들 생성. Finder에서 더블클릭하면 GUI 자동 실행 (터미널 안 뜸). Dock에 드래그 가능, /Applications으로 이동 가능
  - 8765 포트 점유 체크 → 이미 떠있으면 브라우저만 새로 열기 (중복 실행 방지)
- 🔑 **OAuth 1클릭 로그인** — GUI 설정 패널의 [🔑 OAuth 로그인] 버튼 클릭만으로 Terminal.app이 자동으로 열리고 해당 CLI(claude/codex/gemini) 명령 자동 입력. 사용자는 OAuth 단계만 진행하면 됨
  - 이전: 사용자가 직접 터미널 열고 CLI 명령어 타이핑
  - 신규: GUI에서 1클릭 → 자동화

### v0.3 (2026-04-27)
- 🤖 **텔레그램 봇 multi-provider 지원** — claude/codex/gemini OAuth + 3가지 API 키. GUI cfg(`~/.config/.../config.json`)를 공유하여 GUI에서 한 번 설정하면 봇도 즉시 반영
- 🏷️ **응답에 모델 footer 자동 표시** — `_🤖 요약: gemini-2.5-pro (Gemini CLI (OAuth))_` 한 줄 추가. 어떤 모델로 요약됐는지 항상 가시화
- 🇰🇷 **한국 뉴스/블로그 본문 추출 fix** — 14개 셀렉터 다중 시도 + 가장 긴 innerText 채택. 그누보드(`#articleBody`), 제로보드(`.article_view`), 네이버 블로그(`[role="main"]`), 티스토리(`.entry-content`) 등 모두 커버
- 📏 **본문 길이 임계값 2단계** — polling break 800자 / 최종 success 1000자 + 페이월 키워드 재검사. 한국 단신(800-1500자) 통과 + 페이월 첫 단락 오인 방지
- 🚪 **한국어 챌린지 키워드 추가** — "본인 인증", "로그인이 필요", "회원 가입", "구독자만" 등 9개. 로그인 요구 화면 자동 감지 → 5분 대기로 사용자가 직접 통과
- 🎨 **GUI 모든 버튼 클릭 피드백** — `:active` scale(0.96) + brightness + box-shadow ring 애니메이션. 키보드 포커스 outline + 모바일 탭 하이라이트 제거
- 🏷️ **버튼명 정확화** — "완료/실패 정리" → "완료/실패/취소 정리" (백엔드 동작과 일치)

### v0.2 (2026-04-27)
- ✕ **작업별 취소 버튼 추가** — 각 작업 카드 우상단의 [✕] 버튼으로 pending/running 상태 작업 즉시 취소. subprocess terminate(1초)→kill 패턴
- 📝 **프롬프트 실시간 편집기** — settings 패널에서 분리, 상단 [📝 프롬프트] 버튼으로 1클릭 접근. debounce 자동 저장, 외부 편집 자동 감지
- 💾 **자동 저장 모드 변경** — 기본을 날짜별 누적 로그로. 토글을 화면 상단 Quick Bar에 노출
- 🛡️ **안전장치 GUI 통합** — 도메인별 사용량 진행 바, 한도 초과 모달, 같은 도메인 자동 throttle
- 📜 **LICENSE 정리** — 표준 MIT만 남기고 부가 면책은 README로. GitHub이 자동으로 "MIT License" 인식
- 🐛 **자정/타임존 버그 수정** — "오늘 로그" 버튼이 자정 후 stale 표시되던 문제 + UTC vs KST 불일치 수정 (60초 자동 갱신 + 로컬 타임존 사용)

### v0.1 (초기 공개)
- 웹 GUI / OAuth 6가지 / Chrome AppleScript 본문 추출 / 다크모드 / Cloudflare 챌린지 자동 대응 / 텔레그램 봇 통합

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
- 화면 상단 **💾 자동 저장 토글** — 켜면 모든 완료 요약이 `YYYY-MM-DD.md` 한 파일에 누적 append
- **[📝 프롬프트]** 버튼 — 요약 형식을 textarea로 실시간 편집, 1.5초 후 자동 저장. vim 등 외부 편집도 5초 이내 자동 감지
- **[🛡️ 안전 가이드]** 버튼 — 윤리적 사용 가이드 + 오늘 도메인별 사용량 진행 바 + 한도 조정
- **[⚙️ 설정 변경]** 패널에서:
  - CLI 라이브 상태 (✅⚠️❌)
  - **[📦 설치]** 버튼 — 미설치 CLI를 1클릭 설치
  - **[🔑 로그인 안내]** 버튼 — OAuth 로그인 정확한 명령 팝업
  - Provider 6가지 중 선택 + 모델 선택 + (필요시) API 키 입력
  - 저장 모드 (날짜별 누적 / 기사 1건당 1파일)

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
├── LICENSE                         # MIT — 자유 사용/수정/배포 OK
├── .gitignore                      # config.json / prompt.md / daily_usage.json 등 사용자별 파일 제외
├── app_web.py                      # 🖥️ 웹 GUI (큐, 안전장치, 프롬프트 편집기, 자동 저장)
├── app.py                          # GUI 백엔드 (LLM 호출, OAuth 감지, 누적 로그 저장)
├── Summarizer.command              # 더블클릭 실행
├── fetch_article.py                # AppleScript 본문 추출 + Cloudflare 감지
├── summarize.py                    # CLI: 본문 + claude -p 요약 (텔레그램 봇이 호출)
├── prompt_template.md              # 한국어 요약 프롬프트 가이드 문서 (참고용)
├── setup.sh                        # macOS 자동 셋업 (인터랙티브)
├── test_url.sh                     # 단일 URL 테스트
└── telegram_bot_integration.py     # python-telegram-bot 통합용 코드
```

**사용자별 파일 (git에 안 올라감, 자동 생성):**
```
~/.config/chrome-applescript-summarizer/
├── config.json                     # provider/model/api_key/save_mode/safety 설정
├── prompt.md                       # 사용자가 편집한 요약 프롬프트 (GUI ↔ 텔레그램 봇 공유)
├── prompt.md.bak-*                 # reset 시 자동 백업
└── daily_usage.json                # 도메인별 일일 사용량 추적 (7일치)
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

## 라이선스 / 자유 사용 / 면책

### 📜 MIT License — 누구나 자유롭게 사용·수정·배포 가능

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software...
```

- ✅ **개인/상업적 사용** 모두 자유
- ✅ **포크 / 수정 / 재배포** 자유 (저작권 표시만 유지)
- ✅ **본인 프로젝트에 통합** 자유
- ✅ **Pull Request 환영** — 개선/버그 수정/포팅 PR은 언제나 환영합니다
- ✅ **이슈 자유롭게 등록** — 질문/제안/버그 보고 환영

### ⚠️ 면책

- 사용 결과 발생하는 ToS 위반 / IP 차단 / 계정 정지 등 **모든 책임은 사용자 본인**
- LLM API 사용량/비용은 사용자 본인 계정에 청구 (도구는 코드 한 줄도 외부 서버에 전송 안 함)
- 본 도구는 **anti-bot bypass / fingerprint spoofing / "사람 흉내" 코드를 의도적으로 제외**합니다 — 사용자 평소 Chrome 세션을 그대로 쓰는 클라이언트 측 도구일 뿐입니다
- Cloudflare 등 봇 챌린지는 **사용자가 직접 통과**하는 방식 (자동 우회 X)

### 🤝 기여 가이드

- PR / 이슈 / 포크 모두 환영
- 새 기능 추가 시 README의 해당 섹션에도 반영해주세요
- 자동화 윤리 가이드(상단 "윤리적 사용 가이드")의 정신은 유지해주세요 — anti-bot 우회 코드는 PR 받지 않습니다

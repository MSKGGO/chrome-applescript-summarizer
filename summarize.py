"""
summarize.py
============
URL → 본문 추출(fetch_article.py) → claude -p 한국어 요약 → stdout

요약 모델: claude sonnet (분석 깊이 — 시장/지정학적 함의 해석에 적합)
요약 포맷: prompt_template.md 또는 내장 default

사전 조건:
  - claude CLI 설치 및 로그인 (https://docs.claude.com/en/docs/claude-code/setup)
  - fetch_article.py 같은 디렉토리에 위치
  - macOS Apple Events 권한 (fetch_article.py 설명 참조)

USAGE:
  python3 summarize.py <URL>
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FETCH_SCRIPT = HERE / "fetch_article.py"
# 사용자 편집 가능한 프롬프트 (GUI / 텔레그램 봇 공유)
USER_PROMPT_FILE = Path.home() / ".config" / "chrome-applescript-summarizer" / "prompt.md"

# launchd 환경에선 PATH가 빈약 → 절대경로 자동 탐색
CLAUDE_BIN = shutil.which("claude") or "/opt/homebrew/bin/claude"
PYTHON_BIN = shutil.which("python3") or "/usr/bin/python3"

# claude CLI 내부에서 `env node`를 호출하므로 PATH에 node 위치 보장 필요
SUBPROC_ENV = os.environ.copy()
SUBPROC_ENV["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + SUBPROC_ENV.get("PATH", "")

# ── 프롬프트 템플릿 (외부 파일 우선, 없으면 내장 default) ──
DEFAULT_PROMPT = """다음 영문(또는 외국어) 뉴스 기사 본문을 한국어로 요약해주세요.
출력은 반드시 아래 형식만, 별도 인사/서두/주석 없이 시작합니다.

기사제목 (한국어 의역)

* 핵심 포인트1 제목: 1~3문장 분석. 시장/경제/지정학적 함의를 해석.
* 핵심 포인트2 제목: 1~3문장 분석.
* 핵심 포인트3 제목: 1~3문장 분석.
* (사안이 크면 4~5번째 포인트까지 추가)

(언론사명, YYYY-MM-DD)
{url}

규칙:
- 단순 사실 나열이 아니라 시장/경제/지정학적 함의를 해석
- 핵심 포인트 보통 3개, 사안 크면 4~5개
- 굵게(**...**) 표시는 사용하지 않음 — 모든 텍스트 일반 서식
- 한국어 우선, 고유명사는 원어 병기 가능
- 발행일은 본문/URL에서 추출, 미상이면 (언론사명)만

원문 URL: {url}
원문 제목: {title}
원문 본문:
---
{body}
---
"""


def _load_prompt_template() -> str:
    """사용자가 편집한 ~/.config/chrome-applescript-summarizer/prompt.md 우선.
    없으면 내장 DEFAULT_PROMPT. GUI(app.py)와 같은 파일을 공유 — 한 번 편집하면 양쪽 모두 반영."""
    if USER_PROMPT_FILE.exists():
        try:
            text = USER_PROMPT_FILE.read_text(encoding="utf-8")
            if "{url}" in text and "{title}" in text and "{body}" in text:
                return text
        except Exception:
            pass
    return DEFAULT_PROMPT


def fetch_and_summarize(url: str) -> str:
    # 1) 본문 추출
    proc = subprocess.run(
        [PYTHON_BIN, str(FETCH_SCRIPT), url],
        capture_output=True,
        text=True,
        timeout=150,
        env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"본문 추출 실패: {(proc.stderr or proc.stdout)[:500]}")

    # JSON만 추출 (진단 메시지가 섞일 수 있음)
    last_json_line = ""
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last_json_line = line
    if not last_json_line:
        raise RuntimeError(f"본문 JSON 파싱 실패: {proc.stdout[:300]}")
    data = json.loads(last_json_line)
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    final_url = (data.get("url") or url).strip()

    if len(body) < 500:
        raise RuntimeError(
            f"본문이 너무 짧습니다 (paywall/로딩 실패 가능): {len(body)}자"
        )

    # 본문 너무 길면 자르기 (요약엔 10K로 충분)
    MAX_BODY = 10000
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n[...중략...]"

    # 2) claude -p 요약 (sonnet 모델 — 분석 깊이)
    prompt = _load_prompt_template().format(url=final_url, title=title, body=body)
    claude_proc = subprocess.run(
        [
            CLAUDE_BIN, "-p",
            "--model", "sonnet",
            "--output-format", "text",
            "--append-system-prompt",
            "당신은 외국 뉴스 본문을 한국어로 요약하는 도구입니다. 사용자가 제시한 형식만 정확히 따르세요. 메모리/스킬/도구 호출 없이 즉시 답하세요.",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=SUBPROC_ENV,
    )
    if claude_proc.returncode != 0:
        raise RuntimeError(
            f"claude 호출 실패: {(claude_proc.stderr or claude_proc.stdout)[:500]}"
        )

    out = claude_proc.stdout.strip()
    if not out:
        raise RuntimeError("claude 응답이 비어있음")
    return out


if __name__ == "__main__":
    url = next((a for a in sys.argv[1:] if a.startswith("http")), None)
    if not url:
        print("USAGE: python3 summarize.py <URL>", file=sys.stderr)
        sys.exit(2)
    try:
        print(fetch_and_summarize(url))
    except Exception as e:
        print(f"❌ 요약 실패: {e}", file=sys.stderr)
        sys.exit(1)

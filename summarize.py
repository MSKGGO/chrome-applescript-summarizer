"""
summarize.py
============
URL → 본문 추출(fetch_article.py) → multi-provider LLM 한국어 요약 → stdout

요약 provider/model: GUI 설정 파일 공유 (~/.config/chrome-applescript-summarizer/config.json)
- gemini_cli (gemini-2.5-flash/pro), claude_cli (haiku/sonnet/opus), codex_cli (ChatGPT)
- API 키 폴백: anthropic, openai, gemini
- cfg 없으면 default: claude_cli + sonnet

요약 포맷: ~/.config/chrome-applescript-summarizer/prompt.md (GUI ↔ 봇 공유)

응답 마지막에 footer로 사용된 모델명 표시.

사전 조건:
  - 해당 OAuth CLI 로그인되어 있거나, API 키가 cfg에 저장되어 있어야 함
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
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
FETCH_SCRIPT = HERE / "fetch_article.py"
# 사용자 편집 가능한 프롬프트 + cfg (GUI ↔ 텔레그램 봇 공유)
CONFIG_DIR = Path.home() / ".config" / "chrome-applescript-summarizer"
USER_PROMPT_FILE = CONFIG_DIR / "prompt.md"
CONFIG_FILE = CONFIG_DIR / "config.json"

PYTHON_BIN = shutil.which("python3") or "/usr/bin/python3"

# launchd 환경에선 PATH가 빈약 → /opt/homebrew/bin 강제 포함
SUBPROC_ENV = os.environ.copy()
SUBPROC_ENV["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + SUBPROC_ENV.get("PATH", "")

SYSTEM_HINT = "당신은 외국 뉴스 본문을 한국어로 요약하는 도구입니다. 사용자가 제시한 형식만 정확히 따르세요. 메모리/스킬/도구 호출 없이 즉시 답하세요."

# Provider 메타 — app.py PROVIDERS와 일치 (default model + 표시 라벨)
PROVIDER_DEFAULTS = {
    "claude_cli":  {"label": "Claude Code (OAuth)",     "default_model": "sonnet"},
    "codex_cli":   {"label": "ChatGPT/Codex (OAuth)",   "default_model": ""},
    "gemini_cli":  {"label": "Gemini CLI (OAuth)",      "default_model": "gemini-2.5-flash"},
    "anthropic":   {"label": "Anthropic (API)",         "default_model": "claude-haiku-4-5"},
    "openai":      {"label": "OpenAI (API)",            "default_model": "gpt-4o-mini"},
    "gemini":      {"label": "Google AI Studio (API)",  "default_model": "gemini-2.5-flash"},
}

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
- 출력의 가장 마지막 줄은 반드시 "(언론사명, 날짜)" 다음 줄에 원문 URL 한 줄 (아래 "원문 URL:" 값 그대로). 절대 생략하지 말 것.

원문 URL: {url}
원문 제목: {title}
원문 본문:
---
{body}
---
"""


def _load_prompt_template() -> str:
    """사용자 prompt.md 우선. 필수 placeholder 없으면 DEFAULT."""
    if USER_PROMPT_FILE.exists():
        try:
            text = USER_PROMPT_FILE.read_text(encoding="utf-8")
            if "{url}" in text and "{title}" in text and "{body}" in text:
                return text
        except Exception:
            pass
    return DEFAULT_PROMPT


def _load_config() -> dict:
    """GUI cfg 공유. 없으면 default = claude_cli + sonnet."""
    if not CONFIG_FILE.exists():
        return {"provider": "claude_cli", "model": "sonnet"}
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        if not cfg.get("provider"):
            cfg["provider"] = "claude_cli"
        if not cfg.get("model"):
            cfg["model"] = PROVIDER_DEFAULTS.get(cfg["provider"], {}).get("default_model", "")
        return cfg
    except Exception:
        return {"provider": "claude_cli", "model": "sonnet"}


# ── 본문 추출 ──
def _fetch_body(url: str) -> dict:
    proc = subprocess.run(
        [PYTHON_BIN, str(FETCH_SCRIPT), url],
        capture_output=True, text=True, timeout=150, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"본문 추출 실패: {(proc.stderr or proc.stdout)[:500]}")
    last_json_line = ""
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last_json_line = line
    if not last_json_line:
        raise RuntimeError(f"본문 JSON 파싱 실패: {proc.stdout[:300]}")
    return json.loads(last_json_line)


# ── LLM 호출들 ──
def _call_claude_cli(prompt: str, model: str) -> str:
    bin_path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    proc = subprocess.run(
        [bin_path, "-p",
         "--model", model or "sonnet",
         "--output-format", "text",
         "--append-system-prompt", SYSTEM_HINT,
         prompt],
        capture_output=True, text=True, timeout=120, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    return proc.stdout.strip()


def _call_codex_cli(prompt: str, model: str = "") -> str:
    bin_path = shutil.which("codex") or "/opt/homebrew/bin/codex"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    proc = subprocess.run(
        [bin_path, "exec", full_prompt],
        capture_output=True, text=True, timeout=180, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"codex CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    return proc.stdout.strip()


def _call_gemini_cli(prompt: str, model: str = "gemini-2.5-flash") -> str:
    bin_path = shutil.which("gemini") or "/opt/homebrew/bin/gemini"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    args = [bin_path, "-m", model, "-p", full_prompt] if model else [bin_path, "-p", full_prompt]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180, env=SUBPROC_ENV)
    if proc.returncode != 0:
        raise RuntimeError(f"gemini CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    return proc.stdout.strip()


def _call_anthropic_api(api_key: str, prompt: str, model: str) -> str:
    payload = {"model": model, "max_tokens": 4000,
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["content"][0]["text"]


def _call_openai_api(api_key: str, prompt: str, model: str) -> str:
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def _call_gemini_api(api_key: str, prompt: str, model: str) -> str:
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ── Dispatch ──
def _dispatch(prompt: str, cfg: dict) -> str:
    provider = cfg.get("provider", "claude_cli")
    model = cfg.get("model", "")
    api_key = cfg.get("api_key", "")
    if provider == "claude_cli":   return _call_claude_cli(prompt, model)
    if provider == "codex_cli":    return _call_codex_cli(prompt, model)
    if provider == "gemini_cli":   return _call_gemini_cli(prompt, model)
    if provider == "anthropic":    return _call_anthropic_api(api_key, prompt, model)
    if provider == "openai":       return _call_openai_api(api_key, prompt, model)
    if provider == "gemini":       return _call_gemini_api(api_key, prompt, model)
    raise ValueError(f"unknown provider: {provider}")


def get_summary_model_label() -> str:
    """텔레그램 봇이 사전 알림 메시지에 표시할 모델 라벨.
    예: "gemini-2.5-pro (Gemini CLI)" — 봇이 cfg 안 읽고 이 함수 import해서 사용 가능."""
    cfg = _load_config()
    provider = cfg.get("provider", "?")
    model = cfg.get("model") or PROVIDER_DEFAULTS.get(provider, {}).get("default_model", "default")
    label_full = PROVIDER_DEFAULTS.get(provider, {}).get("label", provider)
    # "Claude Code (OAuth)" → "Claude Code"로 단순화
    label_short = label_full.split(" (")[0]
    return f"{model} ({label_short})"


def fetch_and_summarize(url: str) -> str:
    cfg = _load_config()
    data = _fetch_body(url)
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    final_url = (data.get("url") or url).strip()

    if len(body) < 500:
        raise RuntimeError(f"본문이 너무 짧습니다 (paywall/로딩 실패 가능): {len(body)}자")
    if len(body) > 10000:
        body = body[:10000] + "\n[...중략...]"

    prompt = _load_prompt_template().format(url=final_url, title=title, body=body)
    out = _dispatch(prompt, cfg).strip()
    if not out:
        raise RuntimeError(f"{cfg.get('provider')} 응답이 비어있음")
    # footer 제거 — 모델 정보는 봇이 사전 알림 메시지에 표시 (사용자 요청)
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

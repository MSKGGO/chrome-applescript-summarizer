"""
app.py
======
Chrome AppleScript Summarizer — 단독 GUI 앱

URL 입력 → 평소 Chrome으로 본문 추출 → LLM 한국어 요약.

인증 방식 (OAuth 우선, API 키는 폴백):

  🔐 OAuth (CLI 한 번 로그인하면 끝, API 키 입력 불필요)
    1. Claude Code (`claude` CLI)
    2. ChatGPT / OpenAI Codex (`codex` CLI) — ChatGPT Plus/Pro 구독 OAuth
    3. Google Gemini CLI (`gemini` CLI) — Google 계정 OAuth, 무료 티어 후함

  🔑 API 키 (폴백, CLI 못 쓸 때)
    4. Anthropic API key
    5. OpenAI API key
    6. Google AI Studio API key

USAGE:
  python3 app.py
  # 또는 Summarizer.command 더블클릭
"""
import os
import re
import sys
import json
import shutil
import threading
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse as _urlparse

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

HERE = Path(__file__).parent
FETCH_SCRIPT = HERE / "fetch_article.py"
CONFIG_DIR = Path.home() / ".config" / "chrome-applescript-summarizer"
CONFIG_FILE = CONFIG_DIR / "config.json"
USAGE_FILE = CONFIG_DIR / "daily_usage.json"
# 사용자 편집 가능한 프롬프트 (GUI / 텔레그램 봇 공유). 없으면 DEFAULT_PROMPT 사용.
PROMPT_FILE = CONFIG_DIR / "prompt.md"

# ── 안전장치 기본값 (사용자가 설정에서 조정 가능, 차단이 아니라 "경고만") ──
SAFETY_DEFAULTS = {
    "soft_limit_per_domain_per_day": 50,   # 도메인별 하루 권장 한도
    "soft_limit_per_batch": 20,            # 한 번에 큐에 추가하는 URL 수 권장 한도
    "min_interval_same_domain_sec": 8,     # 같은 도메인 연속 호출 간 최소 권장 간격
}

SUBPROC_ENV = os.environ.copy()
SUBPROC_ENV["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + SUBPROC_ENV.get("PATH", "")

DEFAULT_PROMPT_TEMPLATE = """다음 영문(또는 외국어) 뉴스 기사 본문을 한국어로 요약해주세요.
출력은 반드시 아래 형식만, 별도 인사/서두/주석 없이 시작합니다.

기사제목 (한국어 의역)

* (5~20자 짧은 분석 헤더): 1~3문장 분석. 시장/경제/지정학적 함의를 해석.
* (5~20자 짧은 분석 헤더): 1~3문장 분석.
* (5~20자 짧은 분석 헤더): 1~3문장 분석.
* (사안이 크면 4~5번째 포인트까지 추가)

(언론사명, YYYY-MM-DD)
{url}

규칙:
- 단순 사실 나열이 아니라 시장/경제/지정학적 함의를 해석
- 핵심 포인트 보통 3개, 사안 크면 4~5개
- "(5~20자 짧은 분석 헤더)" 자리에는 실제 내용에 맞는 짧은 한국어 헤더를 직접 작성 (예: "코어 CPI 둔화", "연준 금리 인하 기대"). "핵심 포인트1 제목" / "분석 헤더" / "(5~20자 짧은 분석 헤더)" 같은 placeholder 표현을 그대로 출력하면 안 됨.
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

# 하위 호환 alias
PROMPT_TEMPLATE = DEFAULT_PROMPT_TEMPLATE


def load_prompt_template() -> str:
    """사용자가 편집한 prompt.md가 있으면 그걸, 없으면 DEFAULT.
    필수 placeholder: {url}, {title}, {body}.
    GUI와 텔레그램 봇(summarize.py)이 같은 파일 공유."""
    if PROMPT_FILE.exists():
        try:
            text = PROMPT_FILE.read_text(encoding="utf-8")
            # 필수 placeholder 검사 (없으면 사용자가 망친 거 → fallback)
            if "{url}" in text and "{title}" in text and "{body}" in text:
                return text
        except Exception:
            pass
    return DEFAULT_PROMPT_TEMPLATE


def save_prompt_template(text: str) -> str:
    """프롬프트 템플릿을 사용자별 위치에 저장. 필수 placeholder 검증."""
    missing = [p for p in ("{url}", "{title}", "{body}") if p not in text]
    if missing:
        raise ValueError(f"필수 placeholder 누락: {', '.join(missing)}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_FILE.write_text(text, encoding="utf-8")
    return str(PROMPT_FILE)


def reset_prompt_template() -> str:
    """사용자 prompt.md를 DEFAULT 내용으로 다시 쓰기."""
    return save_prompt_template(DEFAULT_PROMPT_TEMPLATE)


# OAuth 옵션을 위로, API 키 폴백을 아래로
PROVIDERS = {
    "claude_cli": {
        "label": "🔐 Claude Code (OAuth)",
        "default_model": "haiku",
        "models": ["haiku", "sonnet", "opus"],
        "needs_key": False,
        "cli_bin": "claude",
        "install_hint": "https://docs.claude.com/en/docs/claude-code/setup",
    },
    "codex_cli": {
        "label": "🔐 ChatGPT / OpenAI Codex (OAuth)",
        "default_model": "",  # codex가 자동 결정
        "models": [""],  # 모델 선택 불필요 (codex 기본 사용)
        "needs_key": False,
        "cli_bin": "codex",
        "install_hint": "npm install -g @openai/codex  # 후 'codex' 실행 → ChatGPT 계정 로그인",
    },
    "gemini_cli": {
        "label": "🔐 Google Gemini CLI (OAuth, 무료 티어 후함)",
        "default_model": "gemini-2.5-flash",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "needs_key": False,
        "cli_bin": "gemini",
        "install_hint": "npm install -g @google/gemini-cli  # 후 'gemini' 실행 → Google 계정 로그인",
    },
    "anthropic": {
        "label": "🔑 Anthropic Claude (API key)",
        "default_model": "claude-haiku-4-5",
        "models": ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5"],
        "needs_key": True,
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "🔑 OpenAI (API key)",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
        "needs_key": True,
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "🔑 Google Gemini (API key, 무료 티어)",
        "default_model": "gemini-2.5-flash",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        "needs_key": True,
        "key_url": "https://aistudio.google.com/apikey",
    },
}


# ════════════════════════════════════════════════════════════
#  Config
# ════════════════════════════════════════════════════════════

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  CLI 인증 상태 체크 (claude / codex / gemini 공통)
# ════════════════════════════════════════════════════════════

def check_cli(cli_name: str, test_args: list, timeout: int = 30) -> tuple:
    """반환: (installed, logged_in, version, hint)"""
    bin_path = shutil.which(cli_name) or f"/opt/homebrew/bin/{cli_name}"
    if not Path(bin_path).exists():
        return False, False, "", f"{cli_name} CLI 미설치"
    try:
        ver_proc = subprocess.run(
            [bin_path, "--version"], capture_output=True, text=True, timeout=10, env=SUBPROC_ENV,
        )
        version = ver_proc.stdout.strip().splitlines()[0] if ver_proc.stdout else "unknown"
    except Exception as e:
        return True, False, "", f"버전 확인 실패: {e}"
    try:
        test = subprocess.run(
            [bin_path] + test_args, capture_output=True, text=True, timeout=timeout, env=SUBPROC_ENV,
        )
        out = (test.stdout + test.stderr).lower()
        if any(k in out for k in ("not logged in", "please run /login", "sign in", "authentication required", "not authenticated")):
            return True, False, version, f"{cli_name} CLI 미로그인 — 터미널에서 '{cli_name}' 실행 후 OAuth"
        if test.returncode != 0 and not test.stdout.strip():
            return True, False, version, f"호출 실패: {(test.stderr or test.stdout)[:200]}"
        return True, True, version, "정상"
    except Exception as e:
        return True, False, version, f"호출 오류: {e}"


def check_claude_cli():
    return check_cli("claude", ["-p", "--output-format", "text", "ok"])


def check_codex_cli():
    return check_cli("codex", ["exec", "answer one word: ok"], timeout=45)


def check_gemini_cli():
    return check_cli("gemini", ["-p", "answer one word: ok"])


# ════════════════════════════════════════════════════════════
#  Cancellable subprocess 헬퍼
#  ────────────────────────────────────────────────────────────
#  proc_holder: dict | None
#    {"proc": subprocess.Popen} 형태로 호출자가 핸들 회수 → 외부 cancel 가능
#  ════════════════════════════════════════════════════════════

class _ProcResult:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_capture(args, timeout: int, env=None, proc_holder=None) -> _ProcResult:
    """subprocess.run의 cancellable 버전.
    proc_holder가 주어지면 Popen 객체를 holder["proc"]에 등록 — 외부에서 terminate 가능."""
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env or SUBPROC_ENV,
    )
    if proc_holder is not None:
        proc_holder["proc"] = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except Exception:
            stdout, stderr = "", ""
        raise
    finally:
        # 정상 종료 후엔 holder의 proc은 의미 없으므로 None으로 (cancel 시도 시 no-op 유도)
        if proc_holder is not None and proc_holder.get("proc") is proc:
            proc_holder["proc"] = None
    return _ProcResult(proc.returncode, stdout, stderr)


# ════════════════════════════════════════════════════════════
#  본문 추출
# ════════════════════════════════════════════════════════════

def fetch_body(url: str, proc_holder=None) -> dict:
    proc = _run_capture(
        ["python3", str(FETCH_SCRIPT), url],
        timeout=150, env=SUBPROC_ENV, proc_holder=proc_holder,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"본문 추출 실패: {(proc.stderr or proc.stdout)[:300]}")
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise RuntimeError(f"본문 JSON 없음: {proc.stdout[:200]}")


# ════════════════════════════════════════════════════════════
#  LLM 호출 — OAuth (CLI 기반)
# ════════════════════════════════════════════════════════════

SYSTEM_HINT = "당신은 외국 뉴스 본문을 한국어로 요약하는 도구입니다. 사용자가 제시한 형식만 정확히 따르세요. 메모리/스킬/도구 호출 없이 즉시 답하세요."


def call_claude_cli(prompt: str, model: str = "haiku", proc_holder=None) -> str:
    bin_path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    proc = _run_capture(
        [
            bin_path, "-p",
            "--model", model or "haiku",
            "--output-format", "text",
            "--append-system-prompt", SYSTEM_HINT,
            prompt,
        ],
        timeout=120, env=SUBPROC_ENV, proc_holder=proc_holder,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("claude CLI 응답 비어있음")
    return out


def call_codex_cli(prompt: str, model: str = "", proc_holder=None) -> str:
    """OpenAI Codex CLI: codex exec "prompt" """
    bin_path = shutil.which("codex") or "/opt/homebrew/bin/codex"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    proc = _run_capture(
        [bin_path, "exec", full_prompt],
        timeout=180, env=SUBPROC_ENV, proc_holder=proc_holder,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"codex CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("codex CLI 응답 비어있음")
    return out


def call_gemini_cli(prompt: str, model: str = "gemini-2.5-flash", proc_holder=None) -> str:
    """Google Gemini CLI: gemini -p "prompt" -m <model>"""
    bin_path = shutil.which("gemini") or "/opt/homebrew/bin/gemini"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    args = [bin_path, "-p", full_prompt]
    if model:
        args = [bin_path, "-m", model, "-p", full_prompt]
    proc = _run_capture(
        args, timeout=180, env=SUBPROC_ENV, proc_holder=proc_holder,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gemini CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("gemini CLI 응답 비어있음")
    return out


# ════════════════════════════════════════════════════════════
#  LLM 호출 — API 키 (HTTP 직접)
# ════════════════════════════════════════════════════════════

def call_anthropic(api_key: str, prompt: str, model: str) -> str:
    payload = {
        "model": model, "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API {e.code}: {e.read().decode(errors='ignore')[:300]}")
    return data["content"][0]["text"]


def call_openai(api_key: str, prompt: str, model: str) -> str:
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI API {e.code}: {e.read().decode(errors='ignore')[:300]}")
    return data["choices"][0]["message"]["content"]


def call_gemini(api_key: str, prompt: str, model: str) -> str:
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini API {e.code}: {e.read().decode(errors='ignore')[:300]}")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini 응답 파싱 실패: {str(data)[:300]}")


# ════════════════════════════════════════════════════════════
#  Dispatch
# ════════════════════════════════════════════════════════════

def summarize_url(url: str, cfg: dict, proc_holder=None) -> str:
    """proc_holder: dict | None. 주어지면 하위 subprocess 핸들이 holder["proc"]에
    실시간으로 등록됨 — 외부에서 proc.terminate()로 cancel 가능."""
    data = fetch_body(url, proc_holder=proc_holder)
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    final_url = (data.get("url") or url).strip()
    if len(body) < 500:
        raise RuntimeError(f"본문이 너무 짧음 (paywall/로딩 실패): {len(body)}자")
    if len(body) > 10000:
        body = body[:10000] + "\n[...중략...]"

    prompt = load_prompt_template().format(url=final_url, title=title, body=body)
    provider = cfg.get("provider", "claude_cli")
    model = cfg.get("model", PROVIDERS.get(provider, {}).get("default_model", ""))

    if provider == "claude_cli":
        return call_claude_cli(prompt, model, proc_holder=proc_holder)
    elif provider == "codex_cli":
        return call_codex_cli(prompt, model, proc_holder=proc_holder)
    elif provider == "gemini_cli":
        return call_gemini_cli(prompt, model, proc_holder=proc_holder)
    elif provider == "anthropic":
        return call_anthropic(cfg.get("api_key", ""), prompt, model)
    elif provider == "openai":
        return call_openai(cfg.get("api_key", ""), prompt, model)
    elif provider == "gemini":
        return call_gemini(cfg.get("api_key", ""), prompt, model)
    raise ValueError(f"unknown provider: {provider}")


DEFAULT_SAVE_DIR = "~/Documents/Summaries"


def save_summary_to_file(url: str, title: str, result: str, cfg: dict, save_dir: str = None) -> str:
    """요약 결과를 마크다운 파일로 저장.
    기본: 날짜별 누적 로그 (예: 2026-04-26.md) — 하루 분량 한 파일에 append.
    cfg["save_mode"] == "per_article" 이면 기존처럼 1건당 1파일.
    반환: 저장된 절대 경로."""
    base = Path(save_dir or cfg.get("save_dir") or DEFAULT_SAVE_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    try:
        domain = _urlparse(url).netloc.replace("www.", "")
    except Exception:
        domain = "unknown"

    provider = cfg.get("provider", "?")
    model = cfg.get("model", "?")
    prov_label = PROVIDERS.get(provider, {}).get("label", provider)

    save_mode = cfg.get("save_mode", "daily_log")  # daily_log | per_article
    now = datetime.now()

    if save_mode == "per_article":
        safe_domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
        ts = now.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{safe_domain}.md"
        filepath = base / filename
        body = (
            f"# {title or url}\n\n"
            f"> **Source:** [{url}]({url})  \n"
            f"> **Saved:** {now.strftime('%Y-%m-%d %H:%M:%S')}  \n"
            f"> **Provider:** {prov_label} / `{model or 'default'}`\n\n"
            f"---\n\n"
            f"{result}\n"
        )
        filepath.write_text(body, encoding="utf-8")
        return str(filepath)

    # ── 기본 모드: 날짜별 누적 로그 ──
    date_str = now.strftime("%Y-%m-%d")
    filepath = base / f"{date_str}.md"

    is_new = not filepath.exists()
    header_block = ""
    if is_new:
        header_block = (
            f"# 📰 {date_str} 뉴스 요약 로그\n\n"
            f"> 자동 누적 로그 — 이 파일에 오늘 요약된 모든 기사가 시간순으로 쌓입니다.\n\n"
            f"---\n\n"
        )

    entry = (
        f"## {now.strftime('%H:%M:%S')} · {domain}\n\n"
        f"> **Source:** [{url}]({url})  \n"
        f"> **Title:** {title or '(제목 추출 실패)'}  \n"
        f"> **Provider:** {prov_label} / `{model or 'default'}`\n\n"
        f"{result}\n\n"
        f"---\n\n"
    )

    with filepath.open("a", encoding="utf-8") as f:
        if header_block:
            f.write(header_block)
        f.write(entry)
    return str(filepath)


# ════════════════════════════════════════════════════════════
#  안전장치: 일일 사용량 추적 (도메인별)
# ════════════════════════════════════════════════════════════

def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _domain_of(url: str) -> str:
    try:
        d = _urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "unknown"


def load_usage() -> dict:
    """파일 구조: {"YYYY-MM-DD": {"domain": [ts1, ts2, ...]}}. 7일치만 유지."""
    if not USAGE_FILE.exists():
        return {}
    try:
        data = json.loads(USAGE_FILE.read_text())
    except Exception:
        return {}
    # GC: 7일 넘은 날짜 제거
    cutoff = datetime.now().timestamp() - 7 * 86400
    cleaned = {}
    for date_str, domains in data.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
            if d >= cutoff:
                cleaned[date_str] = domains
        except Exception:
            continue
    return cleaned


def save_usage(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(USAGE_FILE, 0o600)
    except Exception:
        pass


def record_usage(url: str):
    """URL 호출 직전(또는 직후)에 호출. 타임스탬프 누적."""
    data = load_usage()
    today = _today_key()
    domain = _domain_of(url)
    data.setdefault(today, {}).setdefault(domain, []).append(int(datetime.now().timestamp()))
    save_usage(data)


def get_today_stats() -> dict:
    """반환: {"domain": count, ...} 오늘 기준."""
    data = load_usage()
    today = data.get(_today_key(), {})
    return {d: len(ts) for d, ts in today.items()}


def get_domain_recent_ts(url: str) -> int:
    """해당 도메인의 가장 최근 호출 타임스탬프 (없으면 0)."""
    data = load_usage()
    today = data.get(_today_key(), {})
    domain = _domain_of(url)
    ts_list = today.get(domain, [])
    return max(ts_list) if ts_list else 0


def check_safety_warnings(urls: list, cfg: dict = None) -> list:
    """큐 추가 직전 호출. 차단하지 않고 경고 문자열 리스트만 반환.
    UI에서 사용자에게 보여주고 "그래도 진행" 선택지를 줌."""
    cfg = cfg or {}
    safety = cfg.get("safety", {})
    per_domain = safety.get("soft_limit_per_domain_per_day", SAFETY_DEFAULTS["soft_limit_per_domain_per_day"])
    per_batch = safety.get("soft_limit_per_batch", SAFETY_DEFAULTS["soft_limit_per_batch"])

    warnings = []
    if len(urls) > per_batch:
        warnings.append(
            f"⚠️ 한 번에 {len(urls)}개 URL을 큐에 추가하려고 합니다 (권장 {per_batch}개 이하). "
            f"대량 일괄 크롤링은 사이트 ToS 위반 소지가 있고 IP 차단을 부를 수 있어요."
        )

    today_stats = get_today_stats()
    incoming_per_domain = {}
    for u in urls:
        d = _domain_of(u)
        incoming_per_domain[d] = incoming_per_domain.get(d, 0) + 1

    for domain, incoming in incoming_per_domain.items():
        already = today_stats.get(domain, 0)
        total_after = already + incoming
        if total_after > per_domain:
            warnings.append(
                f"⚠️ {domain}: 오늘 이미 {already}건, 추가하면 {total_after}건 (권장 한도 {per_domain}/일 초과). "
                f"평소 본인이 읽을 만한 페이스를 유지하세요."
            )
    return warnings


def auto_detect_oauth_provider() -> str:
    """우선순위대로 OAuth CLI 검사 → 첫 번째 로그인된 것 반환. 없으면 빈 문자열."""
    for prov, checker in [
        ("claude_cli", check_claude_cli),
        ("codex_cli", check_codex_cli),
        ("gemini_cli", check_gemini_cli),
    ]:
        try:
            installed, logged_in, _, _ = checker()
            if installed and logged_in:
                return prov
        except Exception:
            continue
    return ""


# ════════════════════════════════════════════════════════════
#  설정 다이얼로그
# ════════════════════════════════════════════════════════════

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("인증 설정")
        self.geometry("620x560")
        self.cfg = dict(cfg)
        self.result = None

        # OAuth CLI 상태 한 번에 체크 (다이얼로그 첫 로딩 시)
        ttk.Label(self, text="OAuth CLI 상태", font=("Helvetica", 12, "bold")).pack(
            anchor="w", padx=15, pady=(15, 5))

        self.cli_status_frame = ttk.Frame(self)
        self.cli_status_frame.pack(fill="x", padx=15, pady=5)
        self._render_cli_statuses()

        # Provider 선택
        ttk.Label(self, text="Provider 선택", font=("Helvetica", 12, "bold")).pack(
            anchor="w", padx=15, pady=(15, 5))
        self.provider_var = tk.StringVar(value=cfg.get("provider", "claude_cli"))
        provider_frame = ttk.Frame(self)
        provider_frame.pack(fill="x", padx=15, pady=5)
        self.provider_combo = ttk.Combobox(
            provider_frame, textvariable=self.provider_var,
            values=[f"{k}  ({v['label']})" for k, v in PROVIDERS.items()],
            state="readonly", width=55,
        )
        self.provider_combo.pack(side="left", fill="x", expand=True)
        for k, v in PROVIDERS.items():
            if cfg.get("provider", "claude_cli") == k:
                self.provider_combo.set(f"{k}  ({v['label']})")
                break
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        # 모델
        ttk.Label(self, text="모델").pack(anchor="w", padx=15, pady=(10, 0))
        self.model_var = tk.StringVar(value=cfg.get("model", ""))
        self.model_combo = ttk.Combobox(self, textvariable=self.model_var, state="readonly", width=55)
        self.model_combo.pack(fill="x", padx=15, pady=5)

        # API 키 (필요 시)
        self.api_label = ttk.Label(self, text="API 키 (해당 시)")
        self.api_label.pack(anchor="w", padx=15, pady=(10, 0))
        self.api_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        api_frame = ttk.Frame(self)
        api_frame.pack(fill="x", padx=15, pady=5)
        self.api_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, show="*", width=55)
        self.api_entry.pack(side="left", fill="x", expand=True)
        self.show_key_var = tk.BooleanVar()
        self.show_key_check = ttk.Checkbutton(
            api_frame, text="보이기", variable=self.show_key_var,
            command=self._toggle_key_visibility,
        )
        self.show_key_check.pack(side="left", padx=5)

        self.key_link_label = ttk.Label(self, text="", foreground="blue", cursor="hand2")
        self.key_link_label.pack(anchor="w", padx=15)
        self.key_link_label.bind("<Button-1>", self._open_key_url)

        # 안내
        info = ttk.Label(self, text=(
            "• OAuth(🔐) 옵션 사용 시 API 키 입력 불필요. CLI 한 번 로그인하면 끝.\n"
            "• API 키(🔑) 옵션은 CLI 못 쓰는 환경용. 본인 계정 청구.\n"
            "• 설정 저장: ~/.config/chrome-applescript-summarizer/config.json (chmod 600)"
        ), foreground="gray", justify="left", wraplength=580)
        info.pack(anchor="w", padx=15, pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="저장", command=self._save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="취소", command=self.destroy).pack(side="left", padx=5)

        self._refresh_models()
        self._update_key_field_state()
        self.transient(parent)
        self.grab_set()

    def _render_cli_statuses(self):
        """3개 OAuth CLI 상태 표시 (한 줄씩)."""
        for w in self.cli_status_frame.winfo_children():
            w.destroy()
        checkers = [
            ("Claude Code", "claude", check_claude_cli, PROVIDERS["claude_cli"]["install_hint"]),
            ("Codex (ChatGPT)", "codex", check_codex_cli, PROVIDERS["codex_cli"]["install_hint"]),
            ("Gemini", "gemini", check_gemini_cli, PROVIDERS["gemini_cli"]["install_hint"]),
        ]
        for label, _, checker, hint in checkers:
            installed, logged_in, version, msg = checker()
            if installed and logged_in:
                text = f"✅ {label}: 로그인됨 ({version})"
                color = "green"
            elif installed:
                text = f"⚠️ {label}: 미로그인 — 터미널에서 해당 CLI 실행 후 로그인"
                color = "orange"
            else:
                text = f"❌ {label}: 미설치 — {hint}"
                color = "gray"
            tk.Label(self.cli_status_frame, text=text, fg=color, justify="left", anchor="w",
                     wraplength=580).pack(fill="x", anchor="w")

    def _on_provider_change(self, evt=None):
        self._refresh_models()
        self._update_key_field_state()

    def _refresh_models(self):
        provider = self.provider_var.get().split()[0]
        cfg = PROVIDERS.get(provider, PROVIDERS["claude_cli"])
        models = cfg["models"]
        self.model_combo["values"] = models
        if not self.model_var.get() or self.model_var.get() not in models:
            self.model_combo.set(cfg["default_model"])

    def _update_key_field_state(self):
        provider = self.provider_var.get().split()[0]
        needs_key = PROVIDERS.get(provider, {}).get("needs_key", False)
        if needs_key:
            self.api_entry.config(state="normal")
            self.show_key_check.config(state="normal")
            url = PROVIDERS[provider].get("key_url", "")
            self.key_link_label.config(text=f"API 키 발급: {url}" if url else "")
        else:
            self.api_entry.config(state="disabled")
            self.show_key_check.config(state="disabled")
            self.key_link_label.config(text="(OAuth 사용 — API 키 입력 불필요)")

    def _toggle_key_visibility(self):
        self.api_entry.config(show="" if self.show_key_var.get() else "*")

    def _open_key_url(self, evt=None):
        provider = self.provider_var.get().split()[0]
        url = PROVIDERS.get(provider, {}).get("key_url", "")
        if url:
            subprocess.run(["open", url])

    def _save(self):
        provider = self.provider_var.get().split()[0]
        model = self.model_var.get()
        result = {"provider": provider, "model": model}
        if PROVIDERS[provider].get("needs_key"):
            api_key = self.api_key_var.get().strip()
            if not api_key:
                messagebox.showerror("오류", "API 키를 입력하세요", parent=self)
                return
            result["api_key"] = api_key
        else:
            result["api_key"] = ""
        self.result = result
        self.destroy()


# ════════════════════════════════════════════════════════════
#  메인 윈도우
# ════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chrome AppleScript Summarizer")
        self.geometry("780x680")
        self.cfg = load_config()
        self._build_ui()
        self.update_idletasks()  # GUI 즉시 그리기
        if not self.cfg:
            # 첫 실행: OAuth 자동 감지를 background thread에서 (GUI 응답성 유지)
            self.status_label.config(text="🔍 OAuth CLI 감지 중... (최대 1~2분)", foreground="blue")
            threading.Thread(target=self._auto_detect_async, daemon=True).start()

    def _auto_detect_async(self):
        try:
            auto = auto_detect_oauth_provider()
        except Exception as e:
            auto = ""
        def _apply():
            if auto:
                model = PROVIDERS[auto]["default_model"]
                self.cfg = {"provider": auto, "model": model, "api_key": ""}
                save_config(self.cfg)
                self._refresh_provider_label()
                prov_label = PROVIDERS[auto]["label"]
                self.status_label.config(text=f"✓ 자동 인증: {prov_label}", foreground="green")
            else:
                self.status_label.config(text="OAuth CLI 미감지 — 설정창에서 인증 방식 선택",
                                         foreground="orange")
                self.open_settings()
        self.after(0, _apply)

    def _build_ui(self):
        menubar = tk.Menu(self)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="인증 설정", command=self.open_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="설정 파일 위치", command=self._show_config_path)
        menubar.add_cascade(label="설정", menu=settings_menu)
        self.config(menu=menubar)

        ttk.Label(self, text="뉴스 URL을 입력하고 ⏎ 또는 [요약] 클릭",
                  font=("Helvetica", 12)).pack(pady=(15, 5))

        url_frame = ttk.Frame(self)
        url_frame.pack(fill="x", padx=15, pady=5)
        self.url_entry = ttk.Entry(url_frame, font=("Helvetica", 13))
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.run_summary())
        self.button = ttk.Button(url_frame, text="요약", command=self.run_summary, width=10)
        self.button.pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(self, text="대기 중", foreground="gray")
        self.status_label.pack(pady=5)

        result_frame = ttk.LabelFrame(self, text="요약 결과")
        result_frame.pack(fill="both", expand=True, padx=15, pady=10)
        self.result_text = scrolledtext.ScrolledText(
            result_frame, wrap=tk.WORD, font=("Helvetica", 12), padx=10, pady=10
        )
        self.result_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=15, pady=(0, 15))
        ttk.Button(bottom, text="복사", command=self._copy_result).pack(side="left")
        ttk.Button(bottom, text="지우기", command=self._clear_result).pack(side="left", padx=5)
        self.provider_label = ttk.Label(bottom, text="", foreground="gray")
        self.provider_label.pack(side="left", padx=10)
        self._refresh_provider_label()

    def _refresh_provider_label(self):
        prov = self.cfg.get("provider", "미설정")
        model = self.cfg.get("model", "-")
        prov_label = PROVIDERS.get(prov, {}).get("label", prov)
        self.provider_label.config(text=f"  | {prov_label} / {model or 'default'}")

    def open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result:
            self.cfg.update(dlg.result)
            save_config(self.cfg)
            self._refresh_provider_label()
            messagebox.showinfo("저장됨", "설정이 저장되었습니다", parent=self)

    def _show_config_path(self):
        messagebox.showinfo("설정 파일", str(CONFIG_FILE), parent=self)

    def _copy_result(self):
        text = self.result_text.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_label.config(text="📋 클립보드에 복사됨", foreground="green")

    def _clear_result(self):
        self.result_text.delete("1.0", tk.END)
        self.status_label.config(text="대기 중", foreground="gray")

    def run_summary(self):
        url = self.url_entry.get().strip()
        if not url.startswith("http"):
            messagebox.showerror("오류", "URL은 http(s)로 시작해야 합니다", parent=self)
            return
        if not self.cfg.get("provider"):
            messagebox.showerror("오류", "먼저 [설정 > 인증 설정]에서 인증 방식 선택", parent=self)
            self.open_settings()
            return

        self.button.config(state="disabled")
        self.status_label.config(text="🔍 본문 추출 + 요약 중... (보통 15~30초)", foreground="blue")
        self.result_text.delete("1.0", tk.END)

        def worker():
            try:
                result = summarize_url(url, self.cfg)
                self.after(0, lambda: self._display_result(result))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: self._display_error(err_msg))

        threading.Thread(target=worker, daemon=True).start()

    def _display_result(self, result: str):
        self.result_text.insert("1.0", result)
        self.status_label.config(text="✓ 완료", foreground="green")
        self.button.config(state="normal")

    def _display_error(self, msg: str):
        self.status_label.config(text="❌ 실패", foreground="red")
        self.result_text.insert("1.0", f"오류:\n\n{msg}")
        self.button.config(state="normal")


if __name__ == "__main__":
    App().mainloop()

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

SUBPROC_ENV = os.environ.copy()
SUBPROC_ENV["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + SUBPROC_ENV.get("PATH", "")

PROMPT_TEMPLATE = """다음 영문(또는 외국어) 뉴스 기사 본문을 한국어로 요약해주세요.
출력은 반드시 아래 형식만, 별도 인사/서두/주석 없이 시작합니다.

**기사제목 (한국어 의역)**

* **핵심 포인트1 제목:** 1~3문장 분석. 시장/경제/지정학적 함의를 해석. 수치는 굵게.
* **핵심 포인트2 제목:** 1~3문장 분석.
* **핵심 포인트3 제목:** 1~3문장 분석.
* (사안이 크면 4~5번째 포인트까지 추가)

(언론사명, YYYY-MM-DD)
[원문]({url})

규칙:
- 단순 사실 나열이 아니라 시장/경제/지정학적 함의를 해석
- 핵심 포인트 보통 3개, 사안 크면 4~5개
- 수치(%, $, 배수)는 본문에서도 굵게 (**...**)
- 한국어 우선, 고유명사는 원어 병기 가능
- 발행일은 본문/URL에서 추출, 미상이면 (언론사명)만

원문 URL: {url}
원문 제목: {title}
원문 본문:
---
{body}
---
"""

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
#  본문 추출
# ════════════════════════════════════════════════════════════

def fetch_body(url: str) -> dict:
    proc = subprocess.run(
        ["python3", str(FETCH_SCRIPT), url],
        capture_output=True, text=True, timeout=150, env=SUBPROC_ENV,
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


def call_claude_cli(prompt: str, model: str = "haiku") -> str:
    bin_path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    proc = subprocess.run(
        [
            bin_path, "-p",
            "--model", model or "haiku",
            "--output-format", "text",
            "--append-system-prompt", SYSTEM_HINT,
            prompt,
        ],
        capture_output=True, text=True, timeout=120, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("claude CLI 응답 비어있음")
    return out


def call_codex_cli(prompt: str, model: str = "") -> str:
    """OpenAI Codex CLI: codex exec "prompt" """
    bin_path = shutil.which("codex") or "/opt/homebrew/bin/codex"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    proc = subprocess.run(
        [bin_path, "exec", full_prompt],
        capture_output=True, text=True, timeout=180, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"codex CLI 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("codex CLI 응답 비어있음")
    return out


def call_gemini_cli(prompt: str, model: str = "gemini-2.5-flash") -> str:
    """Google Gemini CLI: gemini -p "prompt" -m <model>"""
    bin_path = shutil.which("gemini") or "/opt/homebrew/bin/gemini"
    full_prompt = f"{SYSTEM_HINT}\n\n{prompt}"
    args = [bin_path, "-p", full_prompt]
    if model:
        args = [bin_path, "-m", model, "-p", full_prompt]
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=180, env=SUBPROC_ENV,
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

def summarize_url(url: str, cfg: dict) -> str:
    data = fetch_body(url)
    body = (data.get("body") or "").strip()
    title = (data.get("title") or "").strip()
    final_url = (data.get("url") or url).strip()
    if len(body) < 500:
        raise RuntimeError(f"본문이 너무 짧음 (paywall/로딩 실패): {len(body)}자")
    if len(body) > 10000:
        body = body[:10000] + "\n[...중략...]"

    prompt = PROMPT_TEMPLATE.format(url=final_url, title=title, body=body)
    provider = cfg.get("provider", "claude_cli")
    model = cfg.get("model", PROVIDERS.get(provider, {}).get("default_model", ""))

    if provider == "claude_cli":
        return call_claude_cli(prompt, model)
    elif provider == "codex_cli":
        return call_codex_cli(prompt, model)
    elif provider == "gemini_cli":
        return call_gemini_cli(prompt, model)
    elif provider == "anthropic":
        return call_anthropic(cfg.get("api_key", ""), prompt, model)
    elif provider == "openai":
        return call_openai(cfg.get("api_key", ""), prompt, model)
    elif provider == "gemini":
        return call_gemini(cfg.get("api_key", ""), prompt, model)
    raise ValueError(f"unknown provider: {provider}")


DEFAULT_SAVE_DIR = "~/Documents/Summaries"


def save_summary_to_file(url: str, title: str, result: str, cfg: dict, save_dir: str = None) -> str:
    """요약 결과를 마크다운 파일로 저장. 반환: 저장된 절대 경로."""
    base = Path(save_dir or cfg.get("save_dir") or DEFAULT_SAVE_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    try:
        domain = _urlparse(url).netloc.replace("www.", "")
    except Exception:
        domain = "unknown"
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{safe_domain}.md"
    filepath = base / filename

    provider = cfg.get("provider", "?")
    model = cfg.get("model", "?")
    prov_label = PROVIDERS.get(provider, {}).get("label", provider)

    body = (
        f"# {title or url}\n\n"
        f"> **Source:** [{url}]({url})  \n"
        f"> **Saved:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"> **Provider:** {prov_label} / `{model or 'default'}`\n\n"
        f"---\n\n"
        f"{result}\n"
    )
    filepath.write_text(body, encoding="utf-8")
    return str(filepath)


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

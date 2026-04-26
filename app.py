"""
app.py
======
Chrome AppleScript Summarizer — 단독 GUI 앱

URL 입력만 하면 본문 추출 + 한국어 요약 결과를 GUI 창에 표시.

인증 방식 (우선순위):
  1. **Claude Code OAuth (권장, 기본)** — 사용자가 `claude` CLI를 설치하고
     한 번 `claude` 실행해 브라우저로 로그인하면 끝. 별도 API 키 입력 X.
     Anthropic 계정 OAuth로 처리.
  2. **(폴백) API 키 직접 입력** — claude CLI 못 쓰는 환경용.
     Anthropic 또는 OpenAI API 키.

의존성:
  - Python 3.9+ (Tkinter 표준 라이브러리만 사용, 추가 pip install 없음)
  - macOS + Google Chrome (fetch_article.py 동일 조건)
  - 인증: 위 둘 중 하나

USAGE:
  python3 app.py
  # 또는 Summarizer.command 더블클릭
"""
import os
import sys
import json
import shutil
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

HERE = Path(__file__).parent
FETCH_SCRIPT = HERE / "fetch_article.py"
CONFIG_DIR = Path.home() / ".config" / "chrome-applescript-summarizer"
CONFIG_FILE = CONFIG_DIR / "config.json"

# launchd/GUI 환경에선 PATH가 빈약 → claude/node 위치 보장
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

PROVIDERS = {
    "claude_cli": {
        "label": "Claude Code (OAuth, 권장)",
        "default_model": "haiku",
        "models": ["haiku", "sonnet", "opus"],
        "needs_key": False,
        "info": "Claude Code CLI로 로그인된 OAuth 사용. API 키 입력 불필요.",
    },
    "anthropic": {
        "label": "Anthropic Claude (API key)",
        "default_model": "claude-haiku-4-5",
        "models": ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5"],
        "needs_key": True,
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "OpenAI (API key)",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
        "needs_key": True,
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Google Gemini (API key, 무료 티어 후함)",
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
#  claude CLI 인증 상태 체크
# ════════════════════════════════════════════════════════════

def check_claude_cli() -> tuple:
    """반환: (installed: bool, logged_in: bool, version: str, hint: str)"""
    claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
    if not Path(claude_bin).exists():
        return False, False, "", "claude CLI 미설치"

    try:
        ver_proc = subprocess.run(
            [claude_bin, "--version"],
            capture_output=True, text=True, timeout=10, env=SUBPROC_ENV,
        )
        version = ver_proc.stdout.strip().splitlines()[0] if ver_proc.stdout else "unknown"
    except Exception as e:
        return True, False, "", f"버전 확인 실패: {e}"

    # 로그인 상태 — 가장 가벼운 호출로 확인
    try:
        test_proc = subprocess.run(
            [claude_bin, "-p", "--output-format", "text", "한 단어로 답: ok"],
            capture_output=True, text=True, timeout=30, env=SUBPROC_ENV,
        )
        out = (test_proc.stdout + test_proc.stderr).lower()
        if "not logged in" in out or "please run /login" in out or "authentication" in out:
            return True, False, version, "claude CLI 미로그인 — 터미널에서 'claude' 한 번 실행 후 로그인"
        if test_proc.returncode != 0:
            return True, False, version, f"호출 실패: {(test_proc.stderr or test_proc.stdout)[:200]}"
        return True, True, version, "정상"
    except Exception as e:
        return True, False, version, f"호출 오류: {e}"


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
#  LLM 호출
# ════════════════════════════════════════════════════════════

def call_claude_cli(prompt: str, model: str = "haiku") -> str:
    """claude CLI 비대화 모드 (OAuth 사용)."""
    claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
    proc = subprocess.run(
        [
            claude_bin, "-p",
            "--model", model,
            "--output-format", "text",
            "--append-system-prompt",
            "당신은 외국 뉴스 본문을 한국어로 요약하는 도구입니다. 사용자가 제시한 형식만 정확히 따르세요. 메모리/스킬/도구 호출 없이 즉시 답하세요.",
            prompt,
        ],
        capture_output=True, text=True, timeout=120, env=SUBPROC_ENV,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 호출 실패: {(proc.stderr or proc.stdout)[:500]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("claude CLI 응답 비어있음")
    return out


def call_anthropic(api_key: str, prompt: str, model: str) -> str:
    payload = {
        "model": model,
        "max_tokens": 4000,
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
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
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
        url,
        data=json.dumps(payload).encode(),
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
    model = cfg.get("model", PROVIDERS[provider]["default_model"])

    if provider == "claude_cli":
        return call_claude_cli(prompt, model)
    elif provider == "anthropic":
        api_key = cfg.get("api_key", "")
        if not api_key:
            raise RuntimeError("Anthropic API 키가 설정 안 됨")
        return call_anthropic(api_key, prompt, model)
    elif provider == "openai":
        api_key = cfg.get("api_key", "")
        if not api_key:
            raise RuntimeError("OpenAI API 키가 설정 안 됨")
        return call_openai(api_key, prompt, model)
    elif provider == "gemini":
        api_key = cfg.get("api_key", "")
        if not api_key:
            raise RuntimeError("Gemini API 키가 설정 안 됨")
        return call_gemini(api_key, prompt, model)
    raise ValueError(f"unknown provider: {provider}")


# ════════════════════════════════════════════════════════════
#  설정 다이얼로그
# ════════════════════════════════════════════════════════════

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("인증 설정")
        self.geometry("560x440")
        self.cfg = dict(cfg)
        self.result = None

        # claude CLI 상태 자동 체크 표시
        installed, logged_in, version, hint = check_claude_cli()
        cli_status_text = ""
        if installed and logged_in:
            cli_status_text = f"✅ Claude Code 정상 ({version}) — 별도 입력 불필요"
            cli_color = "green"
        elif installed:
            cli_status_text = f"⚠️ Claude Code 설치됨 ({version}), 미로그인\n→ 터미널에서 'claude' 실행 후 브라우저 OAuth 로그인"
            cli_color = "orange"
        else:
            cli_status_text = "❌ Claude Code 미설치\n→ https://docs.claude.com/en/docs/claude-code/setup\n또는 아래에서 API 키 입력"
            cli_color = "red"

        ttk.Label(self, text="LLM Provider", font=("Helvetica", 13, "bold")).grid(
            row=0, column=0, sticky="w", padx=15, pady=(15, 5))
        self.provider_var = tk.StringVar(value=cfg.get("provider", "claude_cli"))
        provider_keys = list(PROVIDERS.keys())
        self.provider_combo = ttk.Combobox(
            self, textvariable=self.provider_var,
            values=[f"{k}  ({v['label']})" for k, v in PROVIDERS.items()],
            state="readonly", width=45,
        )
        self.provider_combo.grid(row=0, column=1, padx=15, pady=(15, 5), sticky="we")
        for k, v in PROVIDERS.items():
            if cfg.get("provider", "claude_cli") == k:
                self.provider_combo.set(f"{k}  ({v['label']})")
                break
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        # claude CLI 상태 라벨
        self.cli_status_label = tk.Label(
            self, text=cli_status_text, fg=cli_color,
            justify="left", anchor="w", wraplength=520,
        )
        self.cli_status_label.grid(row=1, column=0, columnspan=2, sticky="we", padx=15, pady=10)

        # 모델 선택
        ttk.Label(self, text="모델").grid(row=2, column=0, sticky="w", padx=15, pady=5)
        self.model_var = tk.StringVar(value=cfg.get("model", ""))
        self.model_combo = ttk.Combobox(self, textvariable=self.model_var, state="readonly", width=45)
        self.model_combo.grid(row=2, column=1, padx=15, pady=5, sticky="we")

        # API 키 입력 (claude_cli 선택 시 비활성)
        self.api_label = ttk.Label(self, text="API 키")
        self.api_label.grid(row=3, column=0, sticky="w", padx=15, pady=5)
        self.api_key_var = tk.StringVar(value=cfg.get("api_key", ""))
        self.api_entry = ttk.Entry(self, textvariable=self.api_key_var, show="*", width=45)
        self.api_entry.grid(row=3, column=1, padx=15, pady=5, sticky="we")
        self.show_key_var = tk.BooleanVar()
        self.show_key_check = ttk.Checkbutton(
            self, text="키 보이기", variable=self.show_key_var,
            command=self._toggle_key_visibility,
        )
        self.show_key_check.grid(row=4, column=1, sticky="w", padx=15)

        self.key_link_label = ttk.Label(self, text="", foreground="blue", cursor="hand2")
        self.key_link_label.grid(row=5, column=1, sticky="w", padx=15, pady=(5, 10))
        self.key_link_label.bind("<Button-1>", self._open_key_url)

        # 안내
        info = ttk.Label(self, text=(
            "• Claude Code OAuth 사용 시: 본인 Claude Code 구독에 청구\n"
            "• API 키 사용 시: 본인 API 콘솔에 청구\n"
            "• 모든 설정은 ~/.config/chrome-applescript-summarizer/config.json에 저장 (chmod 600)"
        ), foreground="gray", justify="left", wraplength=520)
        info.grid(row=6, column=0, columnspan=2, sticky="w", padx=15, pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="저장", command=self._save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="취소", command=self.destroy).pack(side="left", padx=5)

        self.columnconfigure(1, weight=1)
        self._refresh_models()
        self._update_key_field_state()
        self.transient(parent)
        self.grab_set()

    def _on_provider_change(self, evt=None):
        self._refresh_models()
        self._update_key_field_state()

    def _refresh_models(self):
        provider = self.provider_var.get().split()[0]
        cfg = PROVIDERS.get(provider, PROVIDERS["claude_cli"])
        self.model_combo["values"] = cfg["models"]
        if not self.model_var.get() or self.model_var.get() not in cfg["models"]:
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
            self.key_link_label.config(text="(claude CLI 인증 사용 — 입력 불필요)")

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
            result["api_key"] = ""  # claude_cli인 경우 키 비움
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
        # 첫 실행 시: claude CLI 자동 사용 시도, 안 되면 설정창
        if not self.cfg:
            installed, logged_in, ver, hint = check_claude_cli()
            if installed and logged_in:
                # 자동으로 claude_cli provider로 셋업
                self.cfg = {
                    "provider": "claude_cli",
                    "model": "haiku",
                    "api_key": "",
                }
                save_config(self.cfg)
                self._refresh_provider_label()
            else:
                self.after(200, self.open_settings)

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
        self.provider_label.config(text=f"  | {prov_label} / {model}")

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

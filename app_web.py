"""
app_web.py
==========
Chrome AppleScript Summarizer — 웹 브라우저 기반 GUI (큐 지원)

- Tkinter 의존성 0 (Python 표준 http.server만)
- 여러 URL 한 번에 입력 → 큐에 쌓아 순차 처리 (Chrome/LLM 충돌 회피)
- 각 작업이 카드로 표시 (pending → running → done/failed)
- 자동 polling으로 진행 상황 실시간 업데이트

USAGE:
  python3 app_web.py
  # 브라우저 자동 열림 (http://localhost:8765/)
"""
import os
import re
import sys
import json
import time
import uuid
import queue
import shutil
import threading
import subprocess
import webbrowser
import http.server
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from app import (
    summarize_url, load_config, save_config,
    auto_detect_oauth_provider, PROVIDERS,
    check_claude_cli, check_codex_cli, check_gemini_cli,
    save_summary_to_file, DEFAULT_SAVE_DIR,
    record_usage, get_today_stats, get_domain_recent_ts,
    check_safety_warnings, SAFETY_DEFAULTS, _domain_of,
    load_prompt_template, save_prompt_template, reset_prompt_template,
    DEFAULT_PROMPT_TEMPLATE, PROMPT_FILE,
)

PORT = 8765

# OAuth CLI별 설치/로그인 명령
CLI_INSTALL = {
    "Claude Code": {
        "npm_pkg": "@anthropic-ai/claude-code",
        "login_cmd": "claude",
        "login_hint": "터미널에서 'claude' 실행 → Anthropic 계정 OAuth 로그인",
    },
    "Codex (ChatGPT)": {
        "npm_pkg": "@openai/codex",
        "login_cmd": "codex",
        "login_hint": "터미널에서 'codex' 실행 → 'Sign in with ChatGPT' 선택 → 브라우저 OAuth (ChatGPT Plus/Pro 구독 필요)",
    },
    "Gemini CLI": {
        "npm_pkg": "@google/gemini-cli",
        "login_cmd": "gemini",
        "login_hint": "터미널에서 'gemini' 실행 → 'Login with Google' 선택 → 브라우저 OAuth (무료 티어 후함)",
    },
}

# ════════════════════════════════════════════════════════════
#  큐 + 워커 (단일 워커로 순차 처리)
# ════════════════════════════════════════════════════════════

_job_queue = queue.Queue()
_jobs = {}  # job_id → {id, url, status, result, error, created_at, started_at, finished_at}
_jobs_lock = threading.Lock()
_worker_thread = None
_URL_RE = re.compile(r"https?://\S+")


def _extract_title(result: str) -> str:
    """요약 결과 첫 줄(보통 **제목**)에서 제목만 발췌."""
    if not result:
        return ""
    first = result.strip().splitlines()[0].strip()
    # **제목** 또는 # 제목 형태에서 제목만
    m = re.match(r"^\*+\s*(.+?)\s*\*+$", first)
    if m:
        return m.group(1)
    m = re.match(r"^#+\s*(.+)$", first)
    if m:
        return m.group(1)
    return first[:80]


def _worker_loop():
    while True:
        job_id = _job_queue.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                _job_queue.task_done()
                continue
            # pending 상태에서 이미 cancel 요청됨 → 처리 안 함
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["error"] = "사용자가 취소함 (대기 중)"
                job["finished_at"] = time.time()
                _job_queue.task_done()
                continue
            job["status"] = "running"
            job["started_at"] = time.time()
            proc_holder = job.setdefault("proc_holder", {"proc": None})
        try:
            cfg = load_config()
            # 안전장치: 같은 도메인 연속 호출 시 최소 간격 보장 (사람 페이스 흉내)
            min_interval = cfg.get("safety", {}).get(
                "min_interval_same_domain_sec",
                SAFETY_DEFAULTS["min_interval_same_domain_sec"],
            )
            if min_interval > 0:
                last_ts = get_domain_recent_ts(job["url"])
                wait_for = (last_ts + min_interval) - time.time()
                # 대기 도중에도 cancel 체크 (1초 단위)
                end_wait = time.time() + min(wait_for, 30)
                while time.time() < end_wait:
                    if job.get("cancel_requested"):
                        break
                    time.sleep(min(0.5, end_wait - time.time()))
            # cancel 체크 — 대기 중 취소됐으면 여기서 끊음
            if job.get("cancel_requested"):
                raise RuntimeError("사용자가 취소함 (대기 중)")
            # 사용량 기록 (실행 직전 — 실패해도 시도는 카운트)
            try:
                record_usage(job["url"])
            except Exception:
                pass
            result = summarize_url(job["url"], cfg, proc_holder=proc_holder)
            with _jobs_lock:
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["error"] = "사용자가 취소함 (완료 직전)"
                else:
                    job["result"] = result
                    job["status"] = "done"
            # 자동 저장 (cancel 안 됐고 결과 있을 때만)
            if cfg.get("auto_save") and job.get("status") == "done":
                try:
                    title = _extract_title(result)
                    saved_path = save_summary_to_file(
                        job["url"], title, result, cfg,
                        cfg.get("save_dir", DEFAULT_SAVE_DIR),
                    )
                    with _jobs_lock:
                        job["saved_path"] = saved_path
                except Exception as e:
                    with _jobs_lock:
                        job["save_error"] = str(e)
        except Exception as e:
            with _jobs_lock:
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["error"] = "사용자가 취소함 (실행 중)"
                else:
                    job["error"] = str(e)
                    job["status"] = "failed"
        finally:
            with _jobs_lock:
                job["finished_at"] = time.time()
                # proc_holder 비우기 (gc 도움)
                if "proc_holder" in job:
                    job["proc_holder"]["proc"] = None
            _job_queue.task_done()


def _ensure_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def _enqueue_urls(urls: list) -> list:
    _ensure_worker()
    new_ids = []
    with _jobs_lock:
        for url in urls:
            job_id = uuid.uuid4().hex[:8]
            _jobs[job_id] = {
                "id": job_id, "url": url, "status": "pending",
                "result": None, "error": None,
                "created_at": time.time(),
                "started_at": None, "finished_at": None,
                "cancel_requested": False, "cancelled_at": None,
                "proc_holder": {"proc": None},
            }
            _job_queue.put(job_id)
            new_ids.append(job_id)
    return new_ids


def _job_public(job: dict) -> dict:
    """JSON 직렬화 가능한 형태만 골라서 반환 (proc_holder는 Popen 객체라 직렬화 불가)."""
    out = {k: v for k, v in job.items() if k != "proc_holder"}
    return out


def _list_jobs(limit: int = 50) -> list:
    with _jobs_lock:
        all_jobs = [_job_public(j) for j in _jobs.values()]
    all_jobs.sort(key=lambda j: -j["created_at"])
    return all_jobs[:limit]


def _clear_finished():
    """완료/실패/취소된 작업만 정리. 진행/대기 중은 유지."""
    with _jobs_lock:
        to_remove = [jid for jid, j in _jobs.items() if j["status"] in ("done", "failed", "cancelled")]
        for jid in to_remove:
            del _jobs[jid]
    return len(to_remove)


def _cancel_job(job_id: str) -> dict:
    """job cancel 요청. pending이면 그냥 플래그 세팅, running이면 proc.terminate()."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        if job["status"] in ("done", "failed", "cancelled"):
            return {"error": f"이미 종료됨: {job['status']}"}
        job["cancel_requested"] = True
        job["cancelled_at"] = time.time()
        proc = job.get("proc_holder", {}).get("proc")
        prev_status = job["status"]
    # 락 밖에서 proc 정리 (terminate가 시간 걸릴 수 있어서)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
    return {"ok": True, "prev_status": prev_status, "killed_proc": proc is not None}


# ════════════════════════════════════════════════════════════
#  HTML
# ════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Chrome AppleScript Summarizer</title>
  <style>
    /* ── 색상 토큰 (라이트 / 다크) ── */
    :root {
      --bg: #f8f9fa;
      --text: #1a1a1a;
      --text-muted: #666;
      --text-dim: #888;
      --card-bg: #ffffff;
      --card-bg-alt: #fafafa;
      --border: #e0e0e0;
      --border-soft: #eeeeee;
      --code-bg: #f0f0f0;
      --input-bg: #ffffff;
      --input-border: #cccccc;
      --primary: #4a90e2;
      --primary-hover: #357abd;
      --secondary-bg: #e0e0e0;
      --secondary-bg-hover: #d0d0d0;
      --secondary-text: #333333;
      --warn-bg: #fff3cd;
      --warn-text: #856404;
      --warn-border: #ffc107;
      --pending: #999999;
      --running-bg: #e3f2fd;
      --running-fg: #1565c0;
      --done-fg: #2e7d32;
      --failed-bg: #ffebee;
      --failed-fg: #c62828;
      --shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    [data-theme="dark"] {
      --bg: #161718;
      --text: #e6e6e6;
      --text-muted: #a0a0a0;
      --text-dim: #777777;
      --card-bg: #232425;
      --card-bg-alt: #1c1d1e;
      --border: #393a3c;
      --border-soft: #303133;
      --code-bg: #2c2d2f;
      --input-bg: #1c1d1e;
      --input-border: #444547;
      --primary: #5aa0f2;
      --primary-hover: #7ab4f5;
      --secondary-bg: #34353a;
      --secondary-bg-hover: #41434a;
      --secondary-text: #e6e6e6;
      --warn-bg: #3a2f00;
      --warn-text: #ffd966;
      --warn-border: #ffc107;
      --pending: #666666;
      --running-bg: #14304a;
      --running-fg: #7eb8f0;
      --done-fg: #6ec077;
      --failed-bg: #401a1a;
      --failed-fg: #ef5350;
      --shadow: 0 1px 2px rgba(0,0,0,0.4);
    }

    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
      max-width: 960px; margin: 25px auto; padding: 20px;
      background: var(--bg); color: var(--text);
      transition: background 0.18s, color 0.18s;
    }
    .top-row {
      display: flex; align-items: flex-start; gap: 10px;
    }
    .top-row .grow { flex: 1; }
    .theme-toggle {
      background: var(--card-bg); color: var(--text);
      border: 1px solid var(--border); border-radius: 6px;
      padding: 6px 10px; cursor: pointer; font-size: 13px;
    }
    .theme-toggle:hover { background: var(--secondary-bg); }
    h1 { color: var(--text); margin: 0 0 8px; font-size: 22px; }
    .subtitle { color: var(--text-muted); margin: 0 0 20px; font-size: 14px; }
    .info-bar {
      display: flex; align-items: center; gap: 10px;
      font-size: 12px; color: var(--text-muted); padding: 8px 12px;
      background: var(--card-bg);
      border-radius: 6px; border: 1px solid var(--border-soft); margin-bottom: 15px;
      box-shadow: var(--shadow);
    }
    .info-bar code { background: var(--code-bg); padding: 2px 6px; border-radius: 3px; color: var(--text); }
    .info-bar .grow { flex: 1; }
    .settings-toggle {
      background: var(--secondary-bg); color: var(--secondary-text); border: 0;
      padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
    }
    .settings-toggle:hover { background: var(--secondary-bg-hover); }
    .settings-panel {
      display: none; background: var(--card-bg); border: 1px solid var(--border);
      border-radius: 8px; padding: 16px; margin-bottom: 15px;
      box-shadow: var(--shadow);
    }
    .settings-panel.open { display: block; }
    .settings-panel h3 { margin: 0 0 12px; font-size: 14px; color: var(--text); }
    .settings-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
    .settings-row label { width: 90px; font-size: 13px; color: var(--text-muted); }
    .settings-row select, .settings-row input {
      flex: 1; padding: 8px; font-size: 13px;
      border: 1px solid var(--input-border); background: var(--input-bg); color: var(--text);
      border-radius: 4px; font-family: inherit;
    }
    .settings-actions { display: flex; gap: 8px; margin-top: 12px; }
    .cli-status-list { font-size: 12px; color: var(--text-muted); margin: 8px 0; }
    .cli-status-list .cli-row {
      display: flex; align-items: center; gap: 8px; padding: 4px 0;
    }
    .cli-status-list .cli-row .label { flex: 1; }
    .cli-status-list button {
      font-size: 11px; padding: 3px 10px; border-radius: 4px;
      background: var(--primary); color: white; border: 0; cursor: pointer;
    }
    .cli-status-list button.secondary {
      background: var(--secondary-bg); color: var(--secondary-text);
    }
    .cli-status-list button:disabled { background: var(--pending); cursor: not-allowed; }
    .node-warning {
      padding: 8px 12px; background: var(--warn-bg); color: var(--warn-text);
      border-left: 3px solid var(--warn-border); margin: 8px 0; font-size: 12px;
      border-radius: 4px;
    }
    textarea {
      width: 100%; padding: 12px; font-size: 14px;
      border: 1px solid var(--input-border); background: var(--input-bg); color: var(--text);
      border-radius: 6px; font-family: ui-monospace, "SF Mono", Menlo, monospace;
      resize: vertical; min-height: 110px;
    }
    textarea:focus { outline: none; border-color: var(--primary); }
    .form-row {
      display: flex; gap: 8px; align-items: center; margin-top: 10px;
    }
    button {
      padding: 11px 22px; font-size: 14px; cursor: pointer; border: 0;
      border-radius: 6px; background: var(--primary); color: white; font-weight: 600;
    }
    button:hover:not(:disabled) { background: var(--primary-hover); }
    button:disabled { background: var(--pending); cursor: not-allowed; }
    button.secondary { background: var(--secondary-bg); color: var(--secondary-text); }
    button.secondary:hover { background: var(--secondary-bg-hover); }
    .help { font-size: 12px; color: var(--text-dim); margin-left: auto; }
    .stats { margin: 15px 0; font-size: 13px; color: var(--text-muted); }
    .stats span { margin-right: 12px; }
    .stats .pending { color: var(--pending); }
    .stats .running { color: var(--running-fg); font-weight: 600; }
    .stats .done { color: var(--done-fg); }
    .stats .failed { color: var(--failed-fg); }
    .stats .cancelled { color: var(--text-dim); }

    .jobs { display: flex; flex-direction: column; gap: 10px; }
    .job {
      background: var(--card-bg); border-radius: 8px; padding: 14px 16px;
      border-left: 4px solid var(--border); box-shadow: var(--shadow);
    }
    .job.pending { border-left-color: var(--pending); }
    .job.running { border-left-color: var(--running-fg); background: var(--running-bg); }
    .job.done { border-left-color: var(--done-fg); }
    .job.failed { border-left-color: var(--failed-fg); background: var(--failed-bg); }
    .job.cancelled { border-left-color: var(--text-dim); opacity: 0.7; }

    .job-head {
      display: flex; align-items: center; gap: 10px; margin-bottom: 6px;
    }
    .job-status {
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      padding: 2px 8px; border-radius: 10px;
      background: var(--code-bg); color: var(--text-muted);
    }
    .job-status.running { background: var(--running-fg); color: white; }
    .job-status.done { background: var(--done-fg); color: white; }
    .job-status.failed { background: var(--failed-fg); color: white; }
    .job-status.cancelled { background: var(--text-dim); color: white; }
    .cancel-btn {
      background: transparent; color: var(--text-muted);
      border: 1px solid var(--border); border-radius: 50%;
      width: 22px; height: 22px; padding: 0;
      font-size: 12px; line-height: 1; cursor: pointer;
      font-weight: 700; flex-shrink: 0;
    }
    .cancel-btn:hover { background: var(--failed-fg); color: white; border-color: var(--failed-fg); }
    .job-url {
      font-size: 12px; color: var(--text-muted); word-break: break-all;
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
      flex: 1;
    }
    .job-time { font-size: 11px; color: var(--text-dim); }
    .job-result {
      white-space: pre-wrap; font-size: 14px; line-height: 1.7;
      margin-top: 10px; padding: 10px 12px;
      background: var(--card-bg-alt); color: var(--text);
      border-radius: 6px;
    }
    .job-error {
      font-size: 13px; color: var(--failed-fg); margin-top: 8px;
      padding: 8px 12px; background: var(--card-bg); border-radius: 6px;
    }
    .copy-btn {
      font-size: 11px; padding: 4px 8px;
      background: var(--secondary-bg); color: var(--secondary-text);
      border-radius: 4px; cursor: pointer; border: 0; margin-top: 6px;
    }
    .copy-btn:hover { background: var(--secondary-bg-hover); }
    .empty {
      text-align: center; padding: 40px; color: var(--text-dim); font-size: 14px;
    }

    /* ── 안전 가이드 패널 ── */
    .safety-banner {
      display: flex; align-items: center; gap: 10px;
      padding: 10px 14px; margin-bottom: 12px;
      background: var(--warn-bg); color: var(--warn-text);
      border-left: 4px solid var(--warn-border); border-radius: 6px;
      font-size: 13px;
    }
    .safety-banner .grow { flex: 1; }
    .safety-banner button {
      font-size: 11px; padding: 4px 10px; border-radius: 4px;
      background: rgba(0,0,0,0.08); color: var(--warn-text); border: 0;
      font-weight: 600;
    }
    .safety-panel {
      display: none; background: var(--card-bg); border: 1px solid var(--border);
      border-radius: 8px; padding: 16px; margin-bottom: 15px;
      box-shadow: var(--shadow);
    }
    .safety-panel.open { display: block; }
    .safety-panel h3 { margin: 0 0 10px; font-size: 14px; color: var(--text); }
    .safety-panel ul { margin: 6px 0 12px 18px; padding: 0; font-size: 13px; color: var(--text-muted); }
    .safety-panel li { margin: 4px 0; }
    .safety-panel li b { color: var(--text); }
    .safety-panel .ok { color: var(--done-fg); }
    .safety-panel .no { color: var(--failed-fg); }
    .usage-table {
      width: 100%; font-size: 12px; margin: 8px 0;
      border-collapse: collapse;
    }
    .usage-table th, .usage-table td {
      text-align: left; padding: 6px 10px;
      border-bottom: 1px solid var(--border-soft);
    }
    .usage-table th { color: var(--text-muted); font-weight: 600; }
    .usage-table .bar {
      display: inline-block; height: 8px; border-radius: 3px;
      background: var(--done-fg); vertical-align: middle; margin-right: 6px;
    }
    .usage-table .bar.warn { background: #ffc107; }
    .usage-table .bar.danger { background: var(--failed-fg); }

    /* ── 상단 Quick Bar (자동 저장 토글) ── */
    .quick-bar {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 14px; margin-bottom: 12px;
      background: var(--card-bg); border: 1px solid var(--border);
      border-radius: 8px; box-shadow: var(--shadow);
      font-size: 13px; flex-wrap: wrap;
    }
    .quick-bar .switch {
      position: relative; display: inline-block; width: 42px; height: 22px;
    }
    .quick-bar .switch input { opacity: 0; width: 0; height: 0; }
    .quick-bar .slider {
      position: absolute; cursor: pointer; inset: 0;
      background-color: var(--secondary-bg); transition: .2s;
      border-radius: 22px;
    }
    .quick-bar .slider:before {
      position: absolute; content: ""; height: 16px; width: 16px;
      left: 3px; bottom: 3px; background-color: white;
      transition: .2s; border-radius: 50%;
    }
    .quick-bar input:checked + .slider { background-color: var(--done-fg); }
    .quick-bar input:checked + .slider:before { transform: translateX(20px); }
    .quick-bar .toggle-label { font-weight: 600; color: var(--text); cursor: pointer; user-select: none; }
    .quick-bar .toggle-hint { color: var(--text-muted); font-size: 11px; }
    .quick-bar .toggle-hint code { background: var(--code-bg); padding: 1px 5px; border-radius: 3px; }
    .quick-bar .qb-actions { margin-left: auto; display: flex; gap: 6px; }
    .quick-bar button.mini {
      font-size: 11px; padding: 5px 10px;
      background: var(--secondary-bg); color: var(--secondary-text);
      border: 0; border-radius: 4px; cursor: pointer; font-weight: 500;
    }
    .quick-bar button.mini:hover { background: var(--secondary-bg-hover); }

    /* ── 프롬프트 편집기 (상단 독립 패널) ── */
    .prompt-panel {
      display: none; background: var(--card-bg); border: 1px solid var(--border);
      border-radius: 8px; padding: 16px; margin-bottom: 15px;
      box-shadow: var(--shadow);
    }
    .prompt-panel.open { display: block; }
    .prompt-panel-head {
      display: flex; align-items: center; gap: 12px; margin-bottom: 8px;
    }
    .prompt-panel-head h3 { margin: 0; font-size: 14px; color: var(--text); flex: 1; }
    .save-status {
      font-size: 11px; padding: 3px 10px; border-radius: 10px;
      font-weight: 600; min-width: 60px; text-align: center;
      transition: all 0.18s;
    }
    .save-status.saved { background: var(--done-fg); color: white; }
    .save-status.dirty { background: #ffc107; color: #5a4500; }
    .save-status.saving { background: var(--running-fg); color: white; }
    .save-status.error { background: var(--failed-fg); color: white; }
    .save-status.idle { background: var(--code-bg); color: var(--text-muted); }

    .prompt-editor textarea {
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
      font-size: 12px; min-height: 320px; line-height: 1.55;
      width: 100%; padding: 12px;
      border: 1px solid var(--input-border); background: var(--input-bg); color: var(--text);
      border-radius: 6px; resize: vertical;
    }
    .prompt-editor textarea:focus { outline: none; border-color: var(--primary); }
    .prompt-meta {
      font-size: 11px; color: var(--text-dim); margin: 4px 0 8px;
      padding: 6px 10px; background: var(--card-bg-alt); border-radius: 4px;
      line-height: 1.5;
    }
    .prompt-meta code { background: var(--code-bg); padding: 1px 5px; border-radius: 3px; color: var(--text); }
    .prompt-meta b { color: var(--text); }
    .placeholder-warn {
      font-size: 12px; color: var(--failed-fg); margin: 4px 0;
      padding: 6px 10px; background: var(--failed-bg); border-radius: 4px;
      display: none;
    }
    .ext-edit-warn {
      font-size: 12px; color: var(--warn-text); margin: 6px 0;
      padding: 8px 12px; background: var(--warn-bg);
      border-left: 3px solid var(--warn-border); border-radius: 4px;
      display: none;
    }
    .ext-edit-warn button {
      font-size: 11px; padding: 3px 10px; margin-left: 8px;
      background: var(--warn-border); color: #5a4500; border: 0;
      border-radius: 3px; cursor: pointer; font-weight: 600;
    }

    /* ── 안전 경고 모달 ── */
    .modal-backdrop {
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5);
      z-index: 100; align-items: center; justify-content: center;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      background: var(--card-bg); color: var(--text);
      border-radius: 10px; padding: 20px 22px; max-width: 520px; width: 92%;
      box-shadow: 0 10px 40px rgba(0,0,0,0.3);
    }
    .modal h3 { margin: 0 0 12px; font-size: 16px; color: var(--warn-text); }
    .modal .warn-list {
      background: var(--warn-bg); color: var(--warn-text);
      padding: 10px 14px; border-radius: 6px; font-size: 13px;
      border-left: 3px solid var(--warn-border);
      max-height: 240px; overflow-y: auto;
    }
    .modal .warn-list div { margin: 6px 0; line-height: 1.5; }
    .modal-actions {
      display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;
    }
  </style>
</head>
<body>
  <div class="top-row">
    <div class="grow">
      <h1>📰 Chrome AppleScript Summarizer</h1>
      <p class="subtitle">URL을 한 줄에 하나씩(또는 공백으로 구분해) 입력 → 큐에 쌓아 순차 처리</p>
    </div>
    <button class="theme-toggle" onclick="toggleTheme()" id="theme-toggle-btn" title="다크모드 토글">🌙</button>
  </div>

  <div class="info-bar">
    <span class="grow" id="provider-info">로딩 중...</span>
    <button class="settings-toggle" onclick="togglePrompt()">📝 프롬프트</button>
    <button class="settings-toggle" onclick="toggleSafety()">🛡️ 안전 가이드</button>
    <button class="settings-toggle" onclick="toggleSettings()">⚙️ 설정 변경</button>
  </div>

  <!-- 📝 프롬프트 편집 패널 (독립, 자동 저장) -->
  <div class="prompt-panel" id="prompt-panel">
    <div class="prompt-panel-head">
      <h3>📝 요약 프롬프트 (실시간 편집)</h3>
      <span class="save-status idle" id="save-status">준비</span>
    </div>
    <div class="prompt-meta">
      파일: <code id="prompt-file-path">~/.config/chrome-applescript-summarizer/prompt.md</code>
      · <span id="prompt-mtime">미저장</span><br>
      필수 placeholder: <code>{url}</code> <code>{title}</code> <code>{body}</code> — 요약 시 자동 치환됨.
      <b>이 파일은 텔레그램 봇(summarize.py)도 같이 읽음</b> — 한 번 편집하면 양쪽 모두 반영.
      <span style="color:var(--done-fg)">✓ 자동 저장</span> (타이핑 멈추고 1.5초 후)
    </div>
    <div class="ext-edit-warn" id="ext-edit-warn">
      <span>⚠️ 외부 에디터에서 파일이 수정된 것 같습니다. 화면 내용과 디스크가 다를 수 있어요.</span>
      <button onclick="reloadPromptTemplate(true)">디스크에서 다시 로드</button>
    </div>
    <div class="prompt-editor">
      <textarea id="prompt-textarea" placeholder="⏳ 로딩 중..." spellcheck="false"></textarea>
      <div id="placeholder-warn" class="placeholder-warn">⚠️ 누락된 placeholder가 있습니다.</div>
      <div style="display:flex; gap:6px; margin-top:8px; align-items:center">
        <button onclick="savePromptTemplate(true)" style="padding:6px 14px; font-size:12px">💾 즉시 저장</button>
        <button class="secondary" onclick="resetPromptTemplate()" style="padding:6px 14px; font-size:12px">🔄 기본값으로 복원</button>
        <button class="secondary" onclick="reloadPromptTemplate(true)" style="padding:6px 14px; font-size:12px">↻ 디스크에서 다시 로드</button>
        <span style="margin-left:auto; font-size:11px; color:var(--text-dim)">
          글자 수: <span id="prompt-char-count">0</span>
        </span>
      </div>
    </div>
  </div>

  <!-- 상단 Quick Bar: 자동 저장 즉시 토글 -->
  <div class="quick-bar">
    <label class="switch">
      <input type="checkbox" id="quick-auto-save" onchange="quickToggleAutoSave()">
      <span class="slider"></span>
    </label>
    <label class="toggle-label" for="quick-auto-save">💾 요약 자동 저장</label>
    <span class="toggle-hint">
      날짜별 누적 로그 — <code id="quick-save-target">~/Documents/Summaries/YYYY-MM-DD.md</code>
    </span>
    <div class="qb-actions">
      <button class="mini" onclick="openSaveFolderQuick()">📂 폴더</button>
      <button class="mini" onclick="openTodayLog()">📄 오늘 로그</button>
    </div>
  </div>

  <div class="safety-banner" id="safety-banner" style="display:none">
    <span class="grow" id="safety-banner-text"></span>
    <button onclick="toggleSafety()">자세히</button>
  </div>

  <div class="safety-panel" id="safety-panel">
    <h3>🛡️ 윤리적 사용 가이드</h3>
    <p style="font-size:13px; color:var(--text-muted); margin:0 0 8px">
      본 도구는 <b>본인이 정상적으로 접근 가능한 기사를 빠르게 요약하는 자가 제어 도구</b>입니다.
      안전장치는 <b>차단이 아니라 경고</b>로 작동 — 사용자가 페이스를 인지하도록 돕는 게 목적입니다.
    </p>
    <ul>
      <li><span class="ok">✓ 해야 할 것:</span> 본인 구독 권한 안에서만 사용 / 일일 도메인당 권장량 준수 / 평소 본인이 읽을 만한 페이스 유지</li>
      <li><span class="no">✗ 하지 말 것:</span> 사이트 ToS의 "automated access" 금지 무시 / 대량 일괄 크롤링 / 탐지 회피 위장 / 본인 권한 없는 paywall 우회</li>
    </ul>

    <h3 style="margin-top:14px">📊 오늘 사용량 (도메인별)</h3>
    <div id="usage-content"><span style="font-size:12px; color:var(--text-dim)">로딩 중...</span></div>

    <h3 style="margin-top:14px">⚙️ 안전장치 한도 (사용자가 조정 가능)</h3>
    <div class="settings-row">
      <label>도메인/일 권장</label>
      <input type="number" id="safety-per-domain" min="1" max="500">
    </div>
    <div class="settings-row">
      <label>배치 권장</label>
      <input type="number" id="safety-per-batch" min="1" max="200">
    </div>
    <div class="settings-row">
      <label>도메인 최소 간격(초)</label>
      <input type="number" id="safety-min-interval" min="0" max="120">
    </div>
    <div style="font-size:11px; color:var(--text-dim); margin:6px 0 10px">
      한도 초과 시 <b>경고 모달이 떠서 확인 후 진행</b> — 차단 X. 같은 도메인 연속 호출 시 자동 대기.
    </div>
    <div class="settings-actions">
      <button onclick="saveSafetyLimits()">한도 저장</button>
      <button class="secondary" onclick="toggleSafety()">닫기</button>
    </div>
  </div>

  <!-- 큐 추가 시 한도 초과 경고 모달 -->
  <div class="modal-backdrop" id="warn-modal">
    <div class="modal">
      <h3>⚠️ 사용량 권장 한도 초과</h3>
      <div class="warn-list" id="warn-list"></div>
      <p style="font-size:12px; color:var(--text-muted); margin:12px 0 0">
        그래도 진행할 수 있지만, 사이트 ToS / IP 차단 위험은 사용자 본인 책임입니다.
      </p>
      <div class="modal-actions">
        <button class="secondary" onclick="closeWarnModal()">취소</button>
        <button onclick="confirmEnqueue()">그래도 진행</button>
      </div>
    </div>
  </div>

  <div class="settings-panel" id="settings-panel">
    <h3>인증 / 모델 설정</h3>
    <div class="cli-status-list" id="cli-status-list">CLI 상태 확인 중...</div>
    <div class="settings-row">
      <label>Provider</label>
      <select id="provider-select" onchange="onProviderChange()"></select>
    </div>
    <div class="settings-row">
      <label>모델</label>
      <select id="model-select"></select>
    </div>
    <div class="settings-row" id="api-key-row">
      <label>API 키</label>
      <input type="password" id="api-key-input" placeholder="(OAuth 사용 시 비워두세요)">
    </div>

    <h3 style="margin-top:18px">📁 요약 파일 저장</h3>
    <div class="settings-row">
      <label>저장 모드</label>
      <select id="save-mode-select">
        <option value="daily_log">날짜별 누적 로그 (권장 — 하루 한 파일)</option>
        <option value="per_article">기사 1건당 1파일 (구버전 방식)</option>
      </select>
    </div>
    <div class="settings-row">
      <label>저장 폴더</label>
      <input type="text" id="save-dir-input" placeholder="~/Documents/Summaries">
      <button class="secondary" onclick="openSaveFolder()" style="padding:8px 14px; font-size:12px">📂 열기</button>
    </div>
    <div style="font-size:11px; color:var(--text-dim); margin:4px 0 8px">
      💡 <b>날짜별 누적 로그</b>: 같은 날 요약된 모든 기사가 <code>YYYY-MM-DD.md</code> 한 파일에 시간순으로 append.
      자동 저장 ON/OFF는 화면 상단 토글에서.
    </div>

    <div style="font-size:11px; color:var(--text-dim); margin:8px 0">
      💡 요약 프롬프트 형식 편집은 상단 [📝 프롬프트] 버튼에서.
    </div>

    <div class="settings-actions">
      <button onclick="saveSettings()">저장</button>
      <button class="secondary" onclick="toggleSettings()">취소</button>
    </div>
  </div>

  <textarea id="urls" placeholder="https://www.bloomberg.com/news/articles/...
https://www.wsj.com/articles/...
https://www.cnbc.com/2026/04/26/..."></textarea>
  <div class="form-row">
    <button id="enqueue-btn" onclick="enqueueAll()">큐에 추가</button>
    <button class="secondary" onclick="clearFinished()">완료/실패 정리</button>
    <span class="help">⌘+⏎ 로도 추가 가능</span>
  </div>

  <div class="stats" id="stats"></div>
  <div class="jobs" id="jobs"></div>

  <script>
    // ── 다크모드 ──
    function applyTheme(theme) {
      document.documentElement.setAttribute('data-theme', theme);
      const btn = document.getElementById('theme-toggle-btn');
      if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    }
    function toggleTheme() {
      const cur = document.documentElement.getAttribute('data-theme') || 'light';
      const next = cur === 'dark' ? 'light' : 'dark';
      localStorage.setItem('theme', next);
      applyTheme(next);
    }
    // 페이지 로드 시 적용 — 저장된 값 > OS 선호 > light
    (function initTheme() {
      const saved = localStorage.getItem('theme');
      if (saved === 'dark' || saved === 'light') {
        applyTheme(saved);
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        applyTheme('dark');
      } else {
        applyTheme('light');
      }
    })();

    const ta = document.getElementById('urls');
    const btn = document.getElementById('enqueue-btn');
    const statsEl = document.getElementById('stats');
    const jobsEl = document.getElementById('jobs');
    const providerInfo = document.getElementById('provider-info');

    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault(); enqueueAll();
      }
    });

    let PROVIDERS_META = {};  // {key: {label, models, default_model, needs_key, key_url}}
    let CURRENT_CFG = {};

    async function loadProviderInfo() {
      try {
        const cfgRes = await fetch('/config');
        CURRENT_CFG = await cfgRes.json();
        const provRes = await fetch('/providers');
        PROVIDERS_META = await provRes.json();

        if (CURRENT_CFG.provider) {
          updateInfoBar();
        } else {
          providerInfo.innerHTML = '⚠️ 인증 미설정 — OAuth CLI 자동 감지 중...';
          const detRes = await fetch('/auto-detect', {method: 'POST'});
          const det = await detRes.json();
          if (det.provider) {
            CURRENT_CFG = {provider: det.provider, model: PROVIDERS_META[det.provider].default_model, api_key: ''};
            updateInfoBar();
          } else {
            providerInfo.innerHTML = '❌ OAuth CLI 미감지 — ⚙️ 설정 변경에서 다른 방식 선택';
          }
        }
      } catch (e) {
        providerInfo.textContent = '설정 로딩 실패: ' + e;
      }
    }

    function updateInfoBar() {
      const p = CURRENT_CFG.provider;
      const meta = PROVIDERS_META[p] || {};
      const label = meta.label || p;
      providerInfo.innerHTML = `현재 인증: <code>${label}</code> / model: <code>${CURRENT_CFG.model || 'default'}</code>`;
      updateQuickBar();
    }

    // ── 상단 Quick Bar (자동 저장) ──
    function updateQuickBar() {
      const checkbox = document.getElementById('quick-auto-save');
      checkbox.checked = !!CURRENT_CFG.auto_save;
      const dir = CURRENT_CFG.save_dir || '~/Documents/Summaries';
      const mode = CURRENT_CFG.save_mode || 'daily_log';
      const today = new Date().toISOString().slice(0, 10);
      const target = mode === 'daily_log'
        ? `${dir}/${today}.md`
        : `${dir}/{시각}_{도메인}.md`;
      document.getElementById('quick-save-target').textContent = target;
    }

    async function quickToggleAutoSave() {
      const enabled = document.getElementById('quick-auto-save').checked;
      const merged = Object.assign({}, CURRENT_CFG, {auto_save: enabled});
      // 필수 필드 보장 (provider 등)
      if (!merged.provider) merged.provider = Object.keys(PROVIDERS_META)[0] || 'claude_cli';
      const res = await fetch('/config', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(merged),
      });
      const data = await res.json();
      if (data.error) {
        alert('저장 실패: ' + data.error);
        document.getElementById('quick-auto-save').checked = !enabled;  // 롤백
        return;
      }
      CURRENT_CFG.auto_save = enabled;
      updateQuickBar();
    }

    async function openSaveFolderQuick() {
      const folder = CURRENT_CFG.save_dir || '~/Documents/Summaries';
      await fetch('/open-folder', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({folder}),
      });
    }

    async function openTodayLog() {
      const dir = CURRENT_CFG.save_dir || '~/Documents/Summaries';
      const today = new Date().toISOString().slice(0, 10);
      const res = await fetch('/open-file', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: `${dir}/${today}.md`}),
      });
      const data = await res.json();
      if (data.error) alert('파일 열기 실패: ' + data.error);
    }

    async function toggleSettings() {
      const panel = document.getElementById('settings-panel');
      panel.classList.toggle('open');
      if (panel.classList.contains('open')) {
        await populateSettingsUI();
      }
    }

    async function populateSettingsUI() {
      const statusEl = document.getElementById('cli-status-list');
      statusEl.innerHTML = '⏳ CLI 상태 확인 중...';
      try {
        const r = await fetch('/cli-status');
        const data = await r.json();
        let html = '';
        if (!data.node_available) {
          html += `<div class="node-warning">⚠️ <b>Node.js 미설치</b> — 터미널에서 <code>brew install node</code> 실행 후 새로고침. (CLI 설치는 npm 필요)</div>`;
        }
        html += data.statuses.map(s => {
          const icon = s.installed ? (s.logged_in ? '✅' : '⚠️') : '❌';
          const color = s.installed && s.logged_in ? '#2e7d32' : (s.installed ? '#e67e00' : '#999');
          let actionBtn = '';
          if (!s.installed && data.node_available) {
            actionBtn = `<button onclick="installCli('${s.name}')" id="install-btn-${s.name.replace(/\s/g, '-')}">📦 설치</button>`;
          } else if (s.installed && !s.logged_in) {
            actionBtn = `<button class="secondary" onclick="showLoginHint('${s.name}')">🔑 로그인 안내</button>`;
          }
          return `<div class="cli-row" style="color:${color}">
            <span>${icon}</span>
            <span class="label"><b>${s.name}</b> ${s.installed ? '(' + (s.version||'설치됨') + ')' : '미설치'} — ${s.hint}</span>
            ${actionBtn}
          </div>`;
        }).join('');
        statusEl.innerHTML = html;
      } catch (e) { statusEl.textContent = '상태 확인 실패: ' + e; }

      // Provider select
      const provSel = document.getElementById('provider-select');
      provSel.innerHTML = Object.entries(PROVIDERS_META).map(([k, v]) =>
        `<option value="${k}">${v.label}</option>`
      ).join('');
      provSel.value = CURRENT_CFG.provider || Object.keys(PROVIDERS_META)[0];
      onProviderChange();
      // 현재 model/api_key 채우기
      document.getElementById('model-select').value = CURRENT_CFG.model || PROVIDERS_META[provSel.value].default_model;
      document.getElementById('api-key-input').value = CURRENT_CFG.api_key || '';
      // 저장 옵션
      document.getElementById('save-mode-select').value = CURRENT_CFG.save_mode || 'daily_log';
      document.getElementById('save-dir-input').value = CURRENT_CFG.save_dir || '~/Documents/Summaries';
    }

    // ── 📝 프롬프트 편집기 (실시간 자동 저장) ──
    let _LAST_SAVED_TEXT = '';   // 마지막으로 저장된 본문
    let _LAST_KNOWN_MTIME = 0;   // 마지막으로 본 디스크 mtime
    let _SAVE_TIMER = null;
    let _IS_TYPING = false;

    function setSaveStatus(state, text) {
      const el = document.getElementById('save-status');
      el.className = 'save-status ' + state;
      el.textContent = text;
    }

    function updateCharCount() {
      const ta = document.getElementById('prompt-textarea');
      document.getElementById('prompt-char-count').textContent = ta.value.length;
    }

    function fmtMtime(ts) {
      if (!ts) return '미저장';
      const d = new Date(ts * 1000);
      const today = new Date();
      const same = d.toDateString() === today.toDateString();
      const time = d.toLocaleTimeString('ko-KR', {hour12: false});
      return same ? `오늘 ${time} 저장` : `${d.toLocaleDateString('ko-KR')} ${time} 저장`;
    }

    async function togglePrompt() {
      const panel = document.getElementById('prompt-panel');
      panel.classList.toggle('open');
      if (panel.classList.contains('open')) {
        await reloadPromptTemplate(false);
      }
    }

    async function reloadPromptTemplate(showAlert = false) {
      const ta = document.getElementById('prompt-textarea');
      try {
        const r = await fetch('/prompt-template');
        const data = await r.json();
        if (data.error) throw new Error(data.error);

        ta.value = data.text || '';
        _LAST_SAVED_TEXT = ta.value;
        _LAST_KNOWN_MTIME = data.mtime || 0;
        document.getElementById('prompt-file-path').textContent = data.path;
        document.getElementById('prompt-mtime').textContent = fmtMtime(data.mtime);
        document.getElementById('ext-edit-warn').style.display = 'none';

        // 이벤트 핸들러는 한 번만 (idempotent)
        if (!ta._wired) {
          ta._wired = true;
          ta.addEventListener('input', onPromptInput);
        }
        checkPlaceholders();
        updateCharCount();
        setSaveStatus('saved', '✓ 저장됨');
        if (showAlert) console.log('프롬프트 다시 로드됨');
      } catch (e) {
        ta.value = '로딩 실패: ' + e;
        setSaveStatus('error', '오류');
      }
    }

    function onPromptInput() {
      _IS_TYPING = true;
      const text = document.getElementById('prompt-textarea').value;
      checkPlaceholders();
      updateCharCount();

      if (text === _LAST_SAVED_TEXT) {
        setSaveStatus('saved', '✓ 저장됨');
        return;
      }
      setSaveStatus('dirty', '● 수정 중');

      // debounce: 1.5초간 입력 멈추면 자동 저장
      if (_SAVE_TIMER) clearTimeout(_SAVE_TIMER);
      _SAVE_TIMER = setTimeout(() => {
        _IS_TYPING = false;
        savePromptTemplate(false);
      }, 1500);
    }

    function checkPlaceholders() {
      const text = document.getElementById('prompt-textarea').value;
      const missing = ['{url}', '{title}', '{body}'].filter(p => !text.includes(p));
      const warn = document.getElementById('placeholder-warn');
      if (missing.length > 0) {
        warn.style.display = 'block';
        warn.textContent = '⚠️ 누락된 placeholder: ' + missing.join(', ') + ' — 저장 안 됨 (LLM 호출 깨짐 방지)';
        return false;
      } else {
        warn.style.display = 'none';
        return true;
      }
    }

    async function savePromptTemplate(notifyOnSuccess = false) {
      const text = document.getElementById('prompt-textarea').value;
      if (!checkPlaceholders()) {
        setSaveStatus('error', '저장 안 됨');
        return;
      }
      setSaveStatus('saving', '저장 중...');
      try {
        const r = await fetch('/prompt-template', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text}),
        });
        const data = await r.json();
        if (data.error) {
          setSaveStatus('error', '오류');
          if (notifyOnSuccess) alert('저장 실패: ' + data.error);
          return;
        }
        _LAST_SAVED_TEXT = text;
        _LAST_KNOWN_MTIME = data.mtime || 0;
        document.getElementById('prompt-mtime').textContent = fmtMtime(data.mtime);
        setSaveStatus('saved', '✓ 저장됨');
        if (notifyOnSuccess) {
          // 잠시 강조 표시
          setSaveStatus('saved', '✓ 즉시 저장 완료');
          setTimeout(() => setSaveStatus('saved', '✓ 저장됨'), 1500);
        }
      } catch (e) {
        setSaveStatus('error', '오류');
        if (notifyOnSuccess) alert('저장 호출 실패: ' + e);
      }
    }

    async function resetPromptTemplate() {
      if (!confirm('현재 편집 중인 내용이 사라지고 기본 프롬프트로 복원됩니다. 진행할까요?')) return;
      try {
        const r = await fetch('/prompt-template/reset', {method: 'POST'});
        const data = await r.json();
        if (data.error) {
          alert('초기화 실패: ' + data.error);
          return;
        }
        await reloadPromptTemplate(false);
        setSaveStatus('saved', '✓ 기본값 복원');
        setTimeout(() => setSaveStatus('saved', '✓ 저장됨'), 2000);
      } catch (e) { alert('호출 실패: ' + e); }
    }

    // 외부 에디터 수정 감지 — 패널 열려있을 때 5초마다 mtime 폴링
    async function checkExternalEdit() {
      const panel = document.getElementById('prompt-panel');
      if (!panel.classList.contains('open')) return;
      if (_IS_TYPING) return;  // 사용자 타이핑 중이면 건드리지 않음
      try {
        const r = await fetch('/prompt-template');
        const data = await r.json();
        if (data.mtime && _LAST_KNOWN_MTIME && data.mtime > _LAST_KNOWN_MTIME + 0.5) {
          // 외부에서 더 최근에 저장됨
          const ta = document.getElementById('prompt-textarea');
          if (ta.value === _LAST_SAVED_TEXT) {
            // 사용자가 편집 안 한 상태 → 자동으로 새 내용 가져옴
            ta.value = data.text;
            _LAST_SAVED_TEXT = data.text;
            _LAST_KNOWN_MTIME = data.mtime;
            document.getElementById('prompt-mtime').textContent = fmtMtime(data.mtime);
            checkPlaceholders();
            updateCharCount();
            setSaveStatus('saved', '↻ 외부 갱신됨');
            setTimeout(() => setSaveStatus('saved', '✓ 저장됨'), 2500);
          } else {
            // 사용자가 편집 중인 채로 외부 변경 → 충돌 경고만 (덮어쓰지 않음)
            document.getElementById('ext-edit-warn').style.display = 'block';
          }
        }
      } catch (e) { /* skip */ }
    }

    async function openSaveFolder() {
      const folder = document.getElementById('save-dir-input').value.trim() || '~/Documents/Summaries';
      await fetch('/open-folder', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({folder}),
      });
    }

    function onProviderChange() {
      const provSel = document.getElementById('provider-select');
      const modelSel = document.getElementById('model-select');
      const apiRow = document.getElementById('api-key-row');
      const apiInput = document.getElementById('api-key-input');
      const meta = PROVIDERS_META[provSel.value];
      if (!meta) return;
      modelSel.innerHTML = meta.models.map(m =>
        `<option value="${m}">${m || '(default)'}</option>`
      ).join('');
      modelSel.value = meta.default_model;
      if (meta.needs_key) {
        apiRow.style.display = 'flex';
        apiInput.disabled = false;
        apiInput.placeholder = meta.key_url ? `발급: ${meta.key_url}` : 'API 키 입력';
      } else {
        apiRow.style.display = 'flex';
        apiInput.disabled = true;
        apiInput.placeholder = '(OAuth 사용 — API 키 불필요)';
        apiInput.value = '';
      }
    }

    async function installCli(name) {
      const btnId = `install-btn-${name.replace(/\s/g, '-')}`;
      const btn = document.getElementById(btnId);
      if (btn) { btn.disabled = true; btn.textContent = '⏳ 설치 중...'; }
      try {
        const res = await fetch('/install-cli', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name}),
        });
        const data = await res.json();
        if (data.error) {
          alert('설치 실패:\n' + data.error.substring(0, 500));
        } else {
          alert('✅ ' + name + ' 설치 완료!\n\n다음 단계 — OAuth 로그인:\n' + data.login_hint);
        }
        await populateSettingsUI();  // 상태 새로고침
      } catch (e) {
        alert('설치 호출 실패: ' + e);
        if (btn) { btn.disabled = false; btn.textContent = '📦 설치'; }
      }
    }

    function showLoginHint(name) {
      const hints = {
        'Claude Code': "터미널에서:\n  claude\n\n→ 첫 실행 시 브라우저 자동 열림 → Anthropic 계정 OAuth 로그인",
        'Codex (ChatGPT)': "터미널에서:\n  codex\n\n→ 'Sign in with ChatGPT' 선택 → 브라우저 OAuth\n(ChatGPT Plus/Pro 구독자만 OAuth 가능)",
        'Gemini CLI': "터미널에서:\n  gemini\n\n→ 'Login with Google' 선택 → 브라우저에서 Google 계정 권한 허용\n(무료 티어: Gemini 2.5 Pro 1M tokens/day)",
      };
      alert('🔑 ' + name + ' 로그인 안내:\n\n' + (hints[name] || '터미널에서 해당 CLI 실행 후 화면 안내 따라 OAuth 로그인'));
    }

    async function saveSettings() {
      const provider = document.getElementById('provider-select').value;
      const model = document.getElementById('model-select').value;
      const api_key = document.getElementById('api-key-input').value.trim();
      const save_mode = document.getElementById('save-mode-select').value;
      const save_dir = document.getElementById('save-dir-input').value.trim() || '~/Documents/Summaries';
      const meta = PROVIDERS_META[provider];
      if (meta.needs_key && !api_key) {
        alert('이 Provider는 API 키 입력이 필요합니다.');
        return;
      }
      // auto_save / safety는 그대로 유지 (상단 토글 / 안전 패널이 관리)
      const merged = Object.assign({}, CURRENT_CFG, {
        provider, model, api_key: meta.needs_key ? api_key : '',
        save_mode, save_dir,
      });
      const res = await fetch('/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(merged),
      });
      const data = await res.json();
      if (data.error) {
        alert('저장 실패: ' + data.error);
        return;
      }
      CURRENT_CFG = merged;
      updateInfoBar();
      toggleSettings();
    }

    async function saveJob(jobId) {
      const btn = document.querySelector(`#job-${jobId} .save-btn`);
      if (btn) { btn.disabled = true; btn.textContent = '⏳ 저장 중...'; }
      try {
        const res = await fetch('/save-job', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({job_id: jobId}),
        });
        const data = await res.json();
        if (data.error) {
          alert('저장 실패: ' + data.error);
          if (btn) { btn.disabled = false; btn.textContent = '💾 파일 저장'; }
        } else {
          if (btn) { btn.textContent = '✓ 저장됨'; }
          refresh();  // saved_path 표시 업데이트
        }
      } catch (e) {
        alert('저장 호출 실패: ' + e);
        if (btn) { btn.disabled = false; btn.textContent = '💾 파일 저장'; }
      }
    }

    let PENDING_URLS = [];  // 경고 모달에서 확인 대기 중인 URL들

    async function enqueueAll(force = false) {
      const text = ta.value;
      const urls = force ? PENDING_URLS : (text.match(/https?:\/\/\S+/g) || []).map(u => u.replace(/[.,);]+$/, ''));
      if (urls.length === 0) {
        alert('URL이 없습니다 (http:// 또는 https://로 시작해야 함)');
        return;
      }
      btn.disabled = true;
      try {
        const res = await fetch('/enqueue', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({urls, force}),
        });
        const data = await res.json();
        if (data.needs_confirm) {
          // 안전 경고 — 모달 열기
          PENDING_URLS = urls;
          showWarnModal(data.warnings || []);
          return;
        }
        ta.value = '';
        PENDING_URLS = [];
        refresh();
        loadSafetyStats();  // 사용량 갱신
      } catch (e) {
        alert('큐 추가 실패: ' + e);
      } finally {
        btn.disabled = false;
      }
    }

    function showWarnModal(warnings) {
      const list = document.getElementById('warn-list');
      list.innerHTML = warnings.map(w => `<div>${escapeHtml(w)}</div>`).join('');
      document.getElementById('warn-modal').classList.add('open');
    }
    function closeWarnModal() {
      document.getElementById('warn-modal').classList.remove('open');
      PENDING_URLS = [];
    }
    async function confirmEnqueue() {
      document.getElementById('warn-modal').classList.remove('open');
      await enqueueAll(true);
    }

    // ── 안전 패널 ──
    async function toggleSafety() {
      const panel = document.getElementById('safety-panel');
      panel.classList.toggle('open');
      if (panel.classList.contains('open')) {
        await loadSafetyStats();
      }
    }

    async function loadSafetyStats() {
      try {
        const res = await fetch('/safety-stats');
        const data = await res.json();
        const limits = data.limits || {};
        const today = data.today || {};

        // 한도 input 채우기
        document.getElementById('safety-per-domain').value = limits.per_domain_per_day;
        document.getElementById('safety-per-batch').value = limits.per_batch;
        document.getElementById('safety-min-interval').value = limits.min_interval_same_domain_sec;

        // 사용량 테이블
        const domains = Object.keys(today).sort((a,b) => today[b] - today[a]);
        const cap = limits.per_domain_per_day || 50;
        const target = document.getElementById('usage-content');
        if (domains.length === 0) {
          target.innerHTML = '<span style="font-size:12px; color:var(--text-dim)">오늘 사용 내역이 없습니다.</span>';
        } else {
          let html = '<table class="usage-table"><thead><tr><th>도메인</th><th style="text-align:right">오늘 / 권장</th><th style="width:40%">사용률</th></tr></thead><tbody>';
          let totalToday = 0;
          let warnCount = 0;
          domains.forEach(d => {
            const n = today[d];
            totalToday += n;
            const pct = Math.min(100, Math.round(n / cap * 100));
            const cls = pct >= 100 ? 'danger' : (pct >= 80 ? 'warn' : '');
            if (pct >= 80) warnCount++;
            html += `<tr><td><code>${escapeHtml(d)}</code></td><td style="text-align:right">${n} / ${cap}</td><td><span class="bar ${cls}" style="width:${pct}%"></span> ${pct}%</td></tr>`;
          });
          html += '</tbody></table>';
          target.innerHTML = html;

          // 배너 업데이트
          updateSafetyBanner(warnCount, totalToday);
        }
      } catch (e) { /* skip */ }
    }

    function updateSafetyBanner(warnCount, totalToday) {
      const banner = document.getElementById('safety-banner');
      const txt = document.getElementById('safety-banner-text');
      if (warnCount > 0) {
        banner.style.display = 'flex';
        txt.textContent = `🛡️ ${warnCount}개 도메인이 권장 한도의 80%를 넘었습니다 (오늘 총 ${totalToday}건). 페이스 조절 권장.`;
      } else if (totalToday >= 30) {
        banner.style.display = 'flex';
        txt.textContent = `🛡️ 오늘 ${totalToday}건 처리됨. 본인 평소 페이스를 유지하세요.`;
      } else {
        banner.style.display = 'none';
      }
    }

    async function saveSafetyLimits() {
      const safety = {
        soft_limit_per_domain_per_day: parseInt(document.getElementById('safety-per-domain').value) || 50,
        soft_limit_per_batch: parseInt(document.getElementById('safety-per-batch').value) || 20,
        min_interval_same_domain_sec: parseInt(document.getElementById('safety-min-interval').value) || 0,
      };
      // 현재 cfg에 safety만 덮어 저장
      const merged = Object.assign({}, CURRENT_CFG, {safety});
      const res = await fetch('/config', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(merged),
      });
      const data = await res.json();
      if (data.error) {
        alert('한도 저장 실패: ' + data.error);
        return;
      }
      CURRENT_CFG.safety = safety;
      alert('✓ 안전장치 한도 저장됨');
      loadSafetyStats();
    }

    async function clearFinished() {
      const res = await fetch('/clear-finished', {method: 'POST'});
      const data = await res.json();
      refresh();
    }

    async function cancelJob(jobId) {
      if (!confirm('이 작업을 취소할까요?\n실행 중이면 진행 중인 subprocess가 즉시 종료됩니다.')) return;
      try {
        const r = await fetch('/cancel-job', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({job_id: jobId}),
        });
        const data = await r.json();
        if (data.error) {
          alert('취소 실패: ' + data.error);
        }
        refresh();
      } catch (e) {
        alert('취소 호출 실패: ' + e);
      }
    }

    function fmtTime(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString('ko-KR', {hour12: false});
    }

    function fmtElapsed(start, end) {
      if (!start) return '';
      const e = end || (Date.now() / 1000);
      const sec = Math.round(e - start);
      return sec < 60 ? `${sec}s` : `${Math.floor(sec/60)}m ${sec%60}s`;
    }

    function renderJobs(jobs) {
      const counts = {pending: 0, running: 0, done: 0, failed: 0, cancelled: 0};
      jobs.forEach(j => { if (counts[j.status] !== undefined) counts[j.status]++; });
      statsEl.innerHTML = `
        <span class="pending">대기 ${counts.pending}</span>
        <span class="running">처리 중 ${counts.running}</span>
        <span class="done">완료 ${counts.done}</span>
        <span class="failed">실패 ${counts.failed}</span>
        <span class="cancelled">취소 ${counts.cancelled}</span>
      `;

      if (jobs.length === 0) {
        jobsEl.innerHTML = '<div class="empty">아직 작업이 없습니다. 위에 URL을 입력하세요.</div>';
        return;
      }

      jobsEl.innerHTML = jobs.map(j => {
        const elapsed = fmtElapsed(j.started_at, j.finished_at);
        const created = fmtTime(j.created_at);
        const savedBadge = j.saved_path
          ? `<div style="font-size:11px; color:var(--done-fg); margin-top:6px">💾 저장됨: <code>${escapeHtml(j.saved_path)}</code></div>`
          : '';
        const actions = j.result ? `
          <div style="display:flex; gap:6px; margin-top:8px">
            <button class="copy-btn" onclick="copyJob('${j.id}')">📋 복사</button>
            ${!j.saved_path ? `<button class="copy-btn save-btn" onclick="saveJob('${j.id}')">💾 파일 저장</button>` : ''}
          </div>` : '';
        const cancellable = (j.status === 'pending' || j.status === 'running');
        const cancelBtn = cancellable
          ? `<button class="cancel-btn" onclick="cancelJob('${j.id}')" title="이 작업 취소">✕</button>`
          : '';
        return `
          <div class="job ${j.status}" id="job-${j.id}">
            <div class="job-head">
              <span class="job-status ${j.status}">${j.status}</span>
              <span class="job-url">${escapeHtml(j.url)}</span>
              <span class="job-time">${created}${elapsed ? ' · ' + elapsed : ''}</span>
              ${cancelBtn}
            </div>
            ${j.result ? `<div class="job-result">${escapeHtml(j.result)}</div>` : ''}
            ${savedBadge}
            ${actions}
            ${j.error ? `<div class="job-error">❌ ${escapeHtml(j.error)}</div>` : ''}
            ${j.save_error ? `<div class="job-error">💾 자동저장 실패: ${escapeHtml(j.save_error)}</div>` : ''}
          </div>
        `;
      }).join('');
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
      }[c]));
    }

    async function copyJob(jobId) {
      const el = document.querySelector(`#job-${jobId} .job-result`);
      if (el) {
        await navigator.clipboard.writeText(el.textContent);
      }
    }

    async function refresh() {
      try {
        const res = await fetch('/jobs');
        const data = await res.json();
        renderJobs(data.jobs);
      } catch (e) { /* skip */ }
    }

    loadProviderInfo();
    refresh();
    loadSafetyStats();
    // 진행 상황 자동 polling (1.5초)
    setInterval(refresh, 1500);
    // 사용량 배너 갱신 (15초)
    setInterval(loadSafetyStats, 15000);
    // 외부 에디터 수정 감지 (5초, 패널 열려있고 타이핑 중 아닐 때만)
    setInterval(checkExternalEdit, 5000);
    // 페이지 떠나기 전 미저장 변경 있으면 강제 flush
    window.addEventListener('beforeunload', (e) => {
      if (_LAST_SAVED_TEXT !== document.getElementById('prompt-textarea').value) {
        savePromptTemplate(false);
      }
    });
  </script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════
#  HTTP Handler
# ════════════════════════════════════════════════════════════

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
        elif path == "/config":
            self._send_json(load_config())
        elif path == "/jobs":
            self._send_json({"jobs": _list_jobs()})
        elif path == "/providers":
            # PROVIDERS dict를 frontend 친화적으로 직렬화
            out = {}
            for k, v in PROVIDERS.items():
                out[k] = {
                    "label": v["label"],
                    "models": v["models"],
                    "default_model": v["default_model"],
                    "needs_key": v.get("needs_key", False),
                    "key_url": v.get("key_url", ""),
                }
            self._send_json(out)
        elif path == "/prompt-template":
            try:
                text = load_prompt_template()
                mtime = PROMPT_FILE.stat().st_mtime if PROMPT_FILE.exists() else 0
                exists = PROMPT_FILE.exists()
                self._send_json({
                    "text": text,
                    "path": str(PROMPT_FILE),
                    "is_default": text == DEFAULT_PROMPT_TEMPLATE,
                    "mtime": mtime,
                    "exists": exists,
                })
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/safety-stats":
            cfg = load_config()
            safety = cfg.get("safety", {})
            self._send_json({
                "today": get_today_stats(),
                "limits": {
                    "per_domain_per_day": safety.get(
                        "soft_limit_per_domain_per_day",
                        SAFETY_DEFAULTS["soft_limit_per_domain_per_day"]),
                    "per_batch": safety.get(
                        "soft_limit_per_batch",
                        SAFETY_DEFAULTS["soft_limit_per_batch"]),
                    "min_interval_same_domain_sec": safety.get(
                        "min_interval_same_domain_sec",
                        SAFETY_DEFAULTS["min_interval_same_domain_sec"]),
                },
            })
        elif path == "/cli-status":
            statuses = []
            for name, checker in [("Claude Code", check_claude_cli), ("Codex (ChatGPT)", check_codex_cli), ("Gemini CLI", check_gemini_cli)]:
                try:
                    installed, logged_in, version, hint = checker()
                except Exception as e:
                    installed, logged_in, version, hint = False, False, "", str(e)
                statuses.append({"name": name, "installed": installed, "logged_in": logged_in, "version": version, "hint": hint})
            node_available = shutil.which("node") is not None and shutil.which("npm") is not None
            self._send_json({"statuses": statuses, "node_available": node_available})
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/enqueue":
            try:
                body = self._read_json()
                urls = body.get("urls", [])
                force = bool(body.get("force", False))  # 경고 무시 강제 진행
                # 정규화 + 중복 제거 (동일 큐 내)
                seen = set()
                clean = []
                for u in urls:
                    u = (u or "").strip().rstrip(".,);")
                    if u.startswith("http") and u not in seen:
                        seen.add(u)
                        clean.append(u)
                # 안전장치 검사 (차단 X — 경고만 + 사용자 확인 후 force=true로 재호출)
                cfg = load_config()
                warnings = check_safety_warnings(clean, cfg)
                if warnings and not force:
                    self._send_json({"warnings": warnings, "count": len(clean), "needs_confirm": True})
                    return
                ids = _enqueue_urls(clean)
                self._send_json({"job_ids": ids, "count": len(ids)})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/auto-detect":
            try:
                auto = auto_detect_oauth_provider()
                if auto:
                    cfg = {
                        "provider": auto,
                        "model": PROVIDERS[auto]["default_model"],
                        "api_key": "",
                    }
                    save_config(cfg)
                    self._send_json({"provider": auto, "label": PROVIDERS[auto]["label"]})
                else:
                    self._send_json({"provider": None, "error": "no OAuth CLI logged in"})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/clear-finished":
            n = _clear_finished()
            self._send_json({"cleared": n})
        elif path == "/cancel-job":
            try:
                body = self._read_json()
                jid = body.get("job_id", "")
                self._send_json(_cancel_job(jid))
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/save-job":
            try:
                body = self._read_json()
                jid = body.get("job_id")
                with _jobs_lock:
                    job = _jobs.get(jid)
                if not job or not job.get("result"):
                    self._send_json({"error": "작업이 없거나 결과 없음"})
                    return
                cfg = load_config()
                title = _extract_title(job["result"])
                save_dir = body.get("save_dir") or cfg.get("save_dir", DEFAULT_SAVE_DIR)
                path = save_summary_to_file(job["url"], title, job["result"], cfg, save_dir)
                with _jobs_lock:
                    job["saved_path"] = path
                self._send_json({"saved_path": path})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/prompt-template":
            try:
                body = self._read_json()
                text = body.get("text", "")
                if not text.strip():
                    self._send_json({"error": "프롬프트가 비어있음"})
                    return
                p = save_prompt_template(text)
                mtime = PROMPT_FILE.stat().st_mtime
                self._send_json({"ok": True, "path": p, "mtime": mtime})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/prompt-template/reset":
            try:
                p = reset_prompt_template()
                mtime = PROMPT_FILE.stat().st_mtime
                self._send_json({"ok": True, "path": p, "mtime": mtime})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/open-file":
            try:
                body = self._read_json()
                target = body.get("path", "")
                p = Path(target).expanduser()
                if not p.exists():
                    self._send_json({"error": f"파일이 아직 없음: {p} (요약 1건 이상 자동 저장 후 생성됨)"})
                    return
                subprocess.run(["open", str(p)])
                self._send_json({"opened": str(p)})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/open-folder":
            # 저장 폴더를 Finder로 열기
            try:
                body = self._read_json()
                folder = body.get("folder") or load_config().get("save_dir", DEFAULT_SAVE_DIR)
                p = Path(folder).expanduser()
                p.mkdir(parents=True, exist_ok=True)
                subprocess.run(["open", str(p)])
                self._send_json({"opened": str(p)})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/install-cli":
            try:
                body = self._read_json()
                name = body.get("name", "")
                info = CLI_INSTALL.get(name)
                if not info:
                    self._send_json({"error": f"unknown CLI: {name}"})
                    return
                npm = shutil.which("npm")
                if not npm:
                    self._send_json({"error": "npm 미설치 — 터미널에서 'brew install node' 먼저 실행"})
                    return
                proc = subprocess.run(
                    [npm, "install", "-g", info["npm_pkg"]],
                    capture_output=True, text=True, timeout=300,
                    env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")},
                )
                if proc.returncode != 0:
                    self._send_json({"error": (proc.stderr or proc.stdout)[:800]})
                else:
                    self._send_json({"ok": True, "login_hint": info["login_hint"]})
            except subprocess.TimeoutExpired:
                self._send_json({"error": "설치 시간 초과 (5분). 네트워크 확인."})
            except Exception as e:
                self._send_json({"error": str(e)})
        elif path == "/config":
            try:
                body = self._read_json()
                old = load_config()
                provider = body.get("provider", old.get("provider", ""))
                model = body.get("model", old.get("model", ""))
                api_key = body.get("api_key", old.get("api_key", ""))
                auto_save = bool(body.get("auto_save", old.get("auto_save", False)))
                save_dir = (body.get("save_dir") or old.get("save_dir") or DEFAULT_SAVE_DIR).strip() or DEFAULT_SAVE_DIR
                save_mode = body.get("save_mode") or old.get("save_mode") or "daily_log"
                if save_mode not in ("daily_log", "per_article"):
                    save_mode = "daily_log"
                if provider not in PROVIDERS:
                    raise ValueError(f"unknown provider: {provider}")
                if PROVIDERS[provider].get("needs_key") and not api_key:
                    raise ValueError("API 키 필요")
                # 안전장치 설정 (요청에 없으면 기존값 유지)
                old_safety = old.get("safety", {})
                safety_in = body.get("safety") or old_safety
                safety = {
                    "soft_limit_per_domain_per_day": int(
                        safety_in.get("soft_limit_per_domain_per_day",
                                      SAFETY_DEFAULTS["soft_limit_per_domain_per_day"])
                    ),
                    "soft_limit_per_batch": int(
                        safety_in.get("soft_limit_per_batch",
                                      SAFETY_DEFAULTS["soft_limit_per_batch"])
                    ),
                    "min_interval_same_domain_sec": int(
                        safety_in.get("min_interval_same_domain_sec",
                                      SAFETY_DEFAULTS["min_interval_same_domain_sec"])
                    ),
                }
                cfg = {
                    "provider": provider, "model": model, "api_key": api_key,
                    "auto_save": auto_save, "save_dir": save_dir,
                    "save_mode": save_mode,
                    "safety": safety,
                }
                save_config(cfg)
                self._send_json({"ok": True, "config": cfg})
            except Exception as e:
                self._send_json({"error": str(e)})
        else:
            self._send(404, "text/plain", b"not found")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(200, "application/json; charset=utf-8", body)

    def log_message(self, fmt, *args):
        pass


# ThreadingMixIn으로 동시 요청 처리 (큐 polling + summarize 동시 처리)
class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global PORT
    for attempt in range(10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            PORT += 1
    else:
        print("포트 8765-8775 모두 사용 중. 다른 포트로 시도하세요.", file=sys.stderr)
        sys.exit(1)

    url = f"http://localhost:{PORT}/"
    print(f"🌐 서버 시작: {url}  (Ctrl+C 로 종료)")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
        httpd.shutdown()


if __name__ == "__main__":
    main()

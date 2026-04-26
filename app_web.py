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


def _worker_loop():
    while True:
        job_id = _job_queue.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                _job_queue.task_done()
                continue
            job["status"] = "running"
            job["started_at"] = time.time()
        try:
            cfg = load_config()
            result = summarize_url(job["url"], cfg)
            with _jobs_lock:
                job["result"] = result
                job["status"] = "done"
        except Exception as e:
            with _jobs_lock:
                job["error"] = str(e)
                job["status"] = "failed"
        finally:
            with _jobs_lock:
                job["finished_at"] = time.time()
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
            }
            _job_queue.put(job_id)
            new_ids.append(job_id)
    return new_ids


def _list_jobs(limit: int = 50) -> list:
    with _jobs_lock:
        all_jobs = list(_jobs.values())
    all_jobs.sort(key=lambda j: -j["created_at"])
    return all_jobs[:limit]


def _clear_finished():
    """완료/실패한 작업만 정리. 진행/대기 중은 유지."""
    with _jobs_lock:
        to_remove = [jid for jid, j in _jobs.items() if j["status"] in ("done", "failed")]
        for jid in to_remove:
            del _jobs[jid]
    return len(to_remove)


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

    .jobs { display: flex; flex-direction: column; gap: 10px; }
    .job {
      background: var(--card-bg); border-radius: 8px; padding: 14px 16px;
      border-left: 4px solid var(--border); box-shadow: var(--shadow);
    }
    .job.pending { border-left-color: var(--pending); }
    .job.running { border-left-color: var(--running-fg); background: var(--running-bg); }
    .job.done { border-left-color: var(--done-fg); }
    .job.failed { border-left-color: var(--failed-fg); background: var(--failed-bg); }

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
    <button class="settings-toggle" onclick="toggleSettings()">⚙️ 설정 변경</button>
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
      const meta = PROVIDERS_META[provider];
      if (meta.needs_key && !api_key) {
        alert('이 Provider는 API 키 입력이 필요합니다.');
        return;
      }
      const res = await fetch('/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({provider, model, api_key: meta.needs_key ? api_key : ''}),
      });
      const data = await res.json();
      if (data.error) {
        alert('저장 실패: ' + data.error);
        return;
      }
      CURRENT_CFG = {provider, model, api_key: meta.needs_key ? api_key : ''};
      updateInfoBar();
      toggleSettings();
    }

    async function enqueueAll() {
      const text = ta.value;
      const urls = (text.match(/https?:\/\/\S+/g) || []).map(u => u.replace(/[.,);]+$/, ''));
      if (urls.length === 0) {
        alert('URL이 없습니다 (http:// 또는 https://로 시작해야 함)');
        return;
      }
      btn.disabled = true;
      try {
        const res = await fetch('/enqueue', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({urls}),
        });
        const data = await res.json();
        ta.value = '';
        refresh();
      } catch (e) {
        alert('큐 추가 실패: ' + e);
      } finally {
        btn.disabled = false;
      }
    }

    async function clearFinished() {
      const res = await fetch('/clear-finished', {method: 'POST'});
      const data = await res.json();
      refresh();
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
      const counts = {pending: 0, running: 0, done: 0, failed: 0};
      jobs.forEach(j => counts[j.status]++);
      statsEl.innerHTML = `
        <span class="pending">대기 ${counts.pending}</span>
        <span class="running">처리 중 ${counts.running}</span>
        <span class="done">완료 ${counts.done}</span>
        <span class="failed">실패 ${counts.failed}</span>
      `;

      if (jobs.length === 0) {
        jobsEl.innerHTML = '<div class="empty">아직 작업이 없습니다. 위에 URL을 입력하세요.</div>';
        return;
      }

      jobsEl.innerHTML = jobs.map(j => {
        const elapsed = fmtElapsed(j.started_at, j.finished_at);
        const created = fmtTime(j.created_at);
        return `
          <div class="job ${j.status}" id="job-${j.id}">
            <div class="job-head">
              <span class="job-status ${j.status}">${j.status}</span>
              <span class="job-url">${escapeHtml(j.url)}</span>
              <span class="job-time">${created}${elapsed ? ' · ' + elapsed : ''}</span>
            </div>
            ${j.result ? `<div class="job-result">${escapeHtml(j.result)}</div>
              <button class="copy-btn" onclick="copyJob('${j.id}')">📋 복사</button>` : ''}
            ${j.error ? `<div class="job-error">❌ ${escapeHtml(j.error)}</div>` : ''}
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
    // 진행 상황 자동 polling (1.5초)
    setInterval(refresh, 1500);
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
                # 정규화 + 중복 제거 (동일 큐 내)
                seen = set()
                clean = []
                for u in urls:
                    u = (u or "").strip().rstrip(".,);")
                    if u.startswith("http") and u not in seen:
                        seen.add(u)
                        clean.append(u)
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
                provider = body.get("provider", "")
                model = body.get("model", "")
                api_key = body.get("api_key", "")
                if provider not in PROVIDERS:
                    raise ValueError(f"unknown provider: {provider}")
                if PROVIDERS[provider].get("needs_key") and not api_key:
                    raise ValueError("API 키 필요")
                cfg = {"provider": provider, "model": model, "api_key": api_key}
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

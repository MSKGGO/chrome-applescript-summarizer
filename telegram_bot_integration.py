"""
telegram_bot_integration.py
===========================
python-telegram-bot (v20+) 통합용 코드 모음.

이 파일을 그대로 import 하지 말고, 본인 bot.py에 필요한 부분을 복붙하세요.
(파일 경로/import 충돌 방지)

== 통합 단계 ==

1. summarize.py 절대경로를 SUMMARIZE_SCRIPT에 설정
2. 아래 4개 함수 + 전역 변수들을 본인 bot.py에 복사:
   - _summary_queue, _summary_worker_task
   - _root_domain, _today_key, _load/_save_daily_counts, _record_quota_use
   - _telegram_safe_markdown, _send_with_fallback
   - _summary_worker, _ensure_summary_worker, _enqueue_summary, _do_summarize
3. 핸들러 등록 (main 함수에 추가):
   ```python
   app.add_handler(CommandHandler("sum",        cmd_summary))
   app.add_handler(CommandHandler("summary",    cmd_summary))
   app.add_handler(CommandHandler("qstatus",    cmd_qstatus))
   app.add_handler(CommandHandler("qclear",     cmd_qclear))
   app.add_handler(CommandHandler("qrestart",   cmd_qrestart))
   app.add_handler(CommandHandler("qreset",     cmd_qreset))
   app.add_handler(CommandHandler("botrestart", cmd_botrestart))
   ```
4. 일반 텍스트 메시지 핸들러에 URL 자동 감지 분기 추가:
   ```python
   urls = _URL_RE.findall(text[:5000])
   if urls:
       for u in urls:
           await _enqueue_summary(update, u.rstrip(".,);"))
       return
   ```
5. _is_my_dm 함수가 본인 bot.py에 있어야 함 (DM 권한 체크).
   없으면 단순화 가능: `return update.effective_chat.id == YOUR_CHAT_ID`
6. _split_text 함수도 (텔레그램 4096자 분할). 없으면 아래 단순 버전 추가:
   ```python
   def _split_text(text, max_len=4000):
       return [text[i:i+max_len] for i in range(0, len(text), max_len)]
   ```

== 사용법 (사용자 입장) ==

- /sum <URL> [URL ...] : 명시적 요약 명령
- 그냥 URL 메시지 던지기 : 자동 감지 + 요약
- 여러 URL 한 번에 : 큐에 쌓아 순차 처리
- /qstatus : 큐 상태 + 도메인별 오늘 사용량
- /qclear : 대기열 비우기
- /qrestart : 워커만 재시작
- /qreset <domain> : 특정 도메인 카운터 리셋
- /botrestart : 봇 프로세스 자체 재시작 (launchd 자동 부활 가정)
"""

import os
import re
import asyncio
import logging
import json as _json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse as _urlparse
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  설정
# ════════════════════════════════════════════════════════════

# summarize.py 절대 경로 (본인 환경에 맞게 수정)
SUMMARIZE_SCRIPT = Path.home() / "Crawler" / "summarize.py"

# 일일 도메인별 권장 한도 (초과해도 차단 X, 알림만)
DAILY_CAP_PER_DOMAIN = 50

# 카운터 영속화 위치
_COUNTS_FILE = Path.home() / "Crawler" / ".daily_counts.json"

# ════════════════════════════════════════════════════════════
#  큐 + 워커 (전역)
# ════════════════════════════════════════════════════════════

_summary_queue: "asyncio.Queue" = asyncio.Queue()
_summary_worker_task = None
_URL_RE = re.compile(r"https?://\S+")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


# ════════════════════════════════════════════════════════════
#  도메인별 일일 카운터
# ════════════════════════════════════════════════════════════

def _root_domain(url: str) -> str:
    """URL → 'bloomberg.com'. 단순 휴리스틱: 마지막 두 부분."""
    try:
        netloc = _urlparse(url).netloc.lower()
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        parts = netloc.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else netloc
    except Exception:
        return "unknown"


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_daily_counts() -> dict:
    if not _COUNTS_FILE.exists():
        return {}
    try:
        return _json.loads(_COUNTS_FILE.read_text())
    except Exception:
        return {}


def _save_daily_counts(counts: dict):
    try:
        _COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        keep = set(sorted(counts.keys())[-10:])
        clean = {k: v for k, v in counts.items() if k in keep}
        _COUNTS_FILE.write_text(_json.dumps(clean, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"daily counts 저장 실패: {e}")


def _record_quota_use(url: str):
    """카운터 +1, (current, cap, domain, exceeded) 반환. 차단 안 함."""
    domain = _root_domain(url)
    today = _today_key()
    counts = _load_daily_counts()
    today_counts = counts.setdefault(today, {})
    current = today_counts.get(domain, 0) + 1
    today_counts[domain] = current
    _save_daily_counts(counts)
    return current, DAILY_CAP_PER_DOMAIN, domain, current > DAILY_CAP_PER_DOMAIN


# ════════════════════════════════════════════════════════════
#  텔레그램 메시지 안전 전송
# ════════════════════════════════════════════════════════════

def _telegram_safe_markdown(text: str) -> str:
    """claude의 표준 markdown(`**bold**`) → 텔레그램 V1(`*bold*`) 호환."""
    return _BOLD_RE.sub(r"*\1*", text)


def _split_text(text: str, max_len: int = 4000) -> list:
    return [text[i:i+max_len] for i in range(0, len(text), max_len)]


async def _send_with_fallback(message_obj, text: str):
    """markdown 파싱 실패하면 plain text로 폴백."""
    try:
        await message_obj.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"markdown 파싱 실패 → plain text: {e}")
        await message_obj.reply_text(text)


# ════════════════════════════════════════════════════════════
#  워커 (큐에서 한 건씩 순차 처리)
# ════════════════════════════════════════════════════════════

async def _do_summarize(message_obj, url: str, notice):
    """실제 본문 추출 + claude 요약 + 답장."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(SUMMARIZE_SCRIPT), url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            await notice.edit_text("❌ 요약 시간 초과 (3분)")
            return

        if proc.returncode != 0:
            err = (stderr.decode(errors="ignore") or stdout.decode(errors="ignore"))[:1500]
            await notice.edit_text(f"❌ 실패\n\n```\n{err}\n```", parse_mode=ParseMode.MARKDOWN)
            return

        result = stdout.decode(errors="ignore").strip()
        if not result:
            await notice.edit_text("❌ 결과 비어있음")
            return

        try:
            await notice.delete()
        except Exception:
            pass
        result = _telegram_safe_markdown(result)
        for part in _split_text(result, 4000):
            await _send_with_fallback(message_obj, part)
        logger.info(f"뉴스 요약 완료: {url}")
    except Exception as e:
        logger.exception("summarize 오류")
        try:
            await notice.edit_text(f"❌ 오류: {e}")
        except Exception:
            await message_obj.reply_text(f"❌ 오류: {e}")


async def _summary_worker():
    """큐에서 (message, url, notice) 꺼내 순차 처리."""
    logger.info("요약 워커 시작")
    while True:
        try:
            message_obj, url, notice = await _summary_queue.get()
        except Exception:
            await asyncio.sleep(1)
            continue
        try:
            try:
                await notice.edit_text("🔍 본문 추출 + 요약 중... (보통 15~30초)")
            except Exception:
                pass
            await _do_summarize(message_obj, url, notice)
        except Exception as e:
            logger.exception(f"요약 워커 처리 오류: {e}")
            try:
                await notice.edit_text(f"❌ 오류: {e}")
            except Exception:
                pass
        finally:
            _summary_queue.task_done()


def _ensure_summary_worker():
    """워커 코루틴이 죽었으면 재시작."""
    global _summary_worker_task
    if _summary_worker_task is None or _summary_worker_task.done():
        _summary_worker_task = asyncio.create_task(_summary_worker())


# ════════════════════════════════════════════════════════════
#  enqueue (URL 핸들러에서 호출)
# ════════════════════════════════════════════════════════════

async def _enqueue_summary(update: Update, url: str):
    """요약 작업을 큐에 추가. 일일 사용량은 알림만, 차단은 안 함."""
    count, cap, domain, exceeded = _record_quota_use(url)
    quota_tag = f"({domain} {count}/{cap})"
    warning_suffix = ""
    if exceeded:
        warning_suffix = f"\n⚠️ *일일 권장 한도 초과 ({count}/{cap})* — 사용량 자제 권장"

    _ensure_summary_worker()
    pos = _summary_queue.qsize() + 1
    if pos == 1:
        notice = await update.message.reply_text(
            f"🔍 본문 추출 + 요약 중... (보통 15~30초) {quota_tag}\n"
            f"`{url[:80]}`{warning_suffix}",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        notice = await update.message.reply_text(
            f"📥 대기열 {pos}번 (앞에 {pos-1}개) {quota_tag}\n"
            f"`{url[:80]}`{warning_suffix}",
            parse_mode=ParseMode.MARKDOWN,
        )
    await _summary_queue.put((update.message, url, notice))


# ════════════════════════════════════════════════════════════
#  명령어 핸들러
#  ※ _is_my_dm은 본인 봇에 정의되어 있다고 가정.
#    없으면: lambda u: u.effective_chat.id == YOUR_CHAT_ID
# ════════════════════════════════════════════════════════════

# 본인 봇에 _is_my_dm 정의되어 있어야 함. 없으면 아래로 대체:
def _is_my_dm(update: Update) -> bool:
    # YOUR_CHAT_ID 본인 텔레그램 chat id로 변경
    YOUR_CHAT_ID = 0  # ← 본인 ID
    return update.effective_chat and update.effective_chat.id == YOUR_CHAT_ID


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/sum <URL> [URL...]"""
    if not _is_my_dm(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("사용법: /sum <URL> [URL ...]")
        return
    valid_urls = [u for u in args if u.startswith("http")]
    if not valid_urls:
        await update.message.reply_text("❌ URL이 없습니다 (http로 시작)")
        return
    for u in valid_urls:
        await _enqueue_summary(update, u.rstrip(".,);"))


async def cmd_qstatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/qstatus — 큐 상태 + 도메인별 사용량."""
    if not _is_my_dm(update):
        return
    qsize = _summary_queue.qsize()
    worker_alive = (_summary_worker_task is not None and not _summary_worker_task.done())
    lines = [
        "📊 *요약 큐 상태*",
        f"• 대기 중: *{qsize}개*",
        f"• 워커: {'🟢 살아있음' if worker_alive else '🔴 죽음'}",
    ]
    today = _today_key()
    counts = _load_daily_counts().get(today, {})
    if counts:
        lines.append(f"\n📅 *오늘 ({today}) 사용량* (cap {DAILY_CAP_PER_DOMAIN}/도메인)")
        for dom, c in sorted(counts.items(), key=lambda x: -x[1]):
            bar = "🟢" if c < DAILY_CAP_PER_DOMAIN * 0.6 else ("🟡" if c < DAILY_CAP_PER_DOMAIN * 0.9 else "🔴")
            lines.append(f"  {bar} `{dom}`: *{c}/{DAILY_CAP_PER_DOMAIN}*")
    else:
        lines.append(f"\n📅 오늘 사용량 0건")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_qclear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/qclear — 대기열 모두 취소."""
    if not _is_my_dm(update):
        return
    cleared = 0
    while not _summary_queue.empty():
        try:
            msg_obj, url, notice = _summary_queue.get_nowait()
            try:
                await notice.edit_text(f"⛔ /qclear 로 취소됨\n`{url[:80]}`",
                                       parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
            cleared += 1
            _summary_queue.task_done()
        except asyncio.QueueEmpty:
            break
    _ensure_summary_worker()
    await update.message.reply_text(
        f"🧹 대기열 *{cleared}개* 취소됨.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_qrestart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/qrestart — 워커만 재시작."""
    global _summary_worker_task
    if not _is_my_dm(update):
        return
    if _summary_worker_task and not _summary_worker_task.done():
        _summary_worker_task.cancel()
        try:
            await _summary_worker_task
        except (asyncio.CancelledError, Exception):
            pass
    _summary_worker_task = None
    _ensure_summary_worker()
    await update.message.reply_text(
        f"🔄 워커 재시작. 큐에 *{_summary_queue.qsize()}개* 대기.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_qreset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/qreset <domain> — 특정 도메인 카운터 리셋."""
    if not _is_my_dm(update):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("사용법: /qreset <domain> 또는 /qreset all")
        return
    target = args[0].lower()
    today = _today_key()
    counts = _load_daily_counts()
    today_counts = counts.get(today, {})
    if target == "all":
        n = len(today_counts)
        counts[today] = {}
        _save_daily_counts(counts)
        await update.message.reply_text(f"🧹 오늘 전체 카운터 리셋 ({n}개)")
    elif target in today_counts:
        prev = today_counts.pop(target)
        _save_daily_counts(counts)
        await update.message.reply_text(f"🧹 `{target}` 리셋 (이전: {prev})", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ `{target}` 오늘 사용 기록 없음", parse_mode=ParseMode.MARKDOWN)


async def cmd_botrestart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/botrestart — 봇 프로세스 자체 SIGTERM (launchd 자동 부활 가정)."""
    if not _is_my_dm(update):
        return
    await update.message.reply_text("🔁 봇 재시작 중...")
    await asyncio.sleep(1)
    import signal
    os.kill(os.getpid(), signal.SIGTERM)

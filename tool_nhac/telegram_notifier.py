import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

_sent_keys: set[str] = set()


def _env(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


def is_configured() -> bool:
    return bool(_env("TELEGRAM_BOT_TOKEN") and (_env("TELEGRAM_CHAT_ID") or _env("TELEGRAM_CHAT_IDS")))


def _chat_ids() -> list[str]:
    single = _env("TELEGRAM_CHAT_ID")
    multi = _env("TELEGRAM_CHAT_IDS")
    raw = []
    if single:
        raw.append(single)
    if multi:
        raw.extend([x.strip() for x in multi.split(",") if x.strip()])
    seen = set()
    ordered = []
    for item in raw:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_message(entry: dict) -> str:
    source = _escape(entry.get("source", "unknown"))
    audio_id = _escape(str(entry.get("audio_id", "")))
    audio_name = _escape(entry.get("audio_name", "Unknown"))
    creator = _escape(entry.get("creator_username", "") or "-")
    usage = int(entry.get("usage_count", 0) or 0)
    ai_score = entry.get("ai_score", 0) or 0
    speech = entry.get("speech_ratio", 0) or 0
    reason = _escape(entry.get("reason", "") or "-")
    audio_page_url = entry.get("audio_page_url", "") or ""
    video_url = entry.get("video_url", "") or ""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "<b>ACCEPTED REALTIME</b>",
        f"Source: <b>{source}</b>",
        f"Audio: {audio_name}",
        f"Creator: @{creator}" if creator != "-" else "Creator: -",
        f"Usage: {usage:,}",
        f"AI: {float(ai_score):.2f} | Speech: {int(round(float(speech)))}%",
        f"Reason: {reason}",
        f"Audio ID: <code>{audio_id}</code>",
        f"Time: {now_str}",
    ]
    if audio_page_url:
        lines.append(f"Audio URL: {audio_page_url}")
    if video_url:
        lines.append(f"Video URL: {video_url}")
    return "\n".join(lines)


async def notify_result(entry: dict) -> bool:
    if not is_configured():
        return False

    if (entry.get("status") or "").lower() != "accepted":
        return False

    audio_id = str(entry.get("audio_id", "") or "").strip()
    if not audio_id:
        return False

    dedupe_key = f"accepted:{audio_id}"
    if dedupe_key in _sent_keys:
        return False

    token = _env("TELEGRAM_BOT_TOKEN")
    chat_ids = _chat_ids()
    if not token or not chat_ids:
        return False

    message = _build_message(entry)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_base = {
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        ok = False
        for chat_id in chat_ids:
            payload = {**payload_base, "chat_id": chat_id}
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API returned not ok for chat_id={chat_id}")
            ok = True

    if ok:
        _sent_keys.add(dedupe_key)
    return ok

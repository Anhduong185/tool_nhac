"""
background_recheck.py — V2.1 Background Re-check Job
======================================================
Chạy độc lập mỗi 6 tiếng. Không cần browser, không cần GPU.
Nhiệm vụ:
  - Lấy 50 audio gần nhất từ DB
  - Gọi API nhẹ để cập nhật usage mới nhất
  - Lưu snapshot vào audio_usage_history để tính velocity

Cách chạy thủ công:
    python background_recheck.py

Cách chạy tự động (ví dụ dùng Task Scheduler hoặc loop):
    python background_recheck.py --interval 360   # Chạy mỗi 360 phút
"""

import asyncio
import argparse
import httpx
import re
from datetime import datetime, timezone
from loguru import logger

# Tránh import vòng: thêm thư mục gốc vào sys.path
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import (
    init_db,
    get_recent_audio_ids,
    snapshot_usage,
    get_usage_velocity,
)
from config import DB_PATH

# ── Cấu hình ──────────────────────────────────────────────────────────────────
RECHECK_LIMIT      = 50    # Số audio check mỗi lần chạy
DELAY_BETWEEN_REQS = 1.5   # Giây nghỉ giữa mỗi request (anti-rate-limit)
VELOCITY_BOOST_THRESHOLD = 50.0  # lượt/giờ → đánh dấu boost priority


# ── Lấy usage từ TikTok API (không cần browser) ───────────────────────────────
async def fetch_usage_lightweight(audio_id: str, client: httpx.AsyncClient) -> int:
    """
    Gọi API TikTok public để lấy usage count mà không cần mở browser.
    Dùng endpoint không auth — có thể bị rate-limit nếu gọi quá nhanh.
    Fallback: trả về 0 để không làm crash job.
    """
    try:
        # Endpoint 1: music detail
        url = f"https://www.tiktok.com/api/music/detail/?musicId={audio_id}&aid=1988"
        r = await client.get(url, timeout=8.0)
        if r.status_code == 200:
            try:
                data = r.json()
                music = data.get("musicInfo", {}).get("music", {})
                count = (
                    music.get("userCount") or
                    music.get("useCount")  or
                    music.get("videoCount") or 0
                )
                if count:
                    return int(count)
            except Exception:
                pass

        # Endpoint 2: fallback — gọi trực tiếp trang nhạc, parse HTML
        url2 = f"https://www.tiktok.com/music/x-{audio_id}"
        r2 = await client.get(url2, timeout=10.0, follow_redirects=True)
        if r2.status_code == 200:
            text = r2.text
            # Tìm userCount trong JSON nhúng trong HTML
            m = re.search(r'"userCount"\s*:\s*(\d+)', text)
            if m:
                return int(m.group(1))
            m = re.search(r'"useCount"\s*:\s*(\d+)', text)
            if m:
                return int(m.group(1))

    except Exception as e:
        logger.debug(f"  fetch_usage [{audio_id}]: {e}")

    return 0


# ── Main job ──────────────────────────────────────────────────────────────────
async def run_recheck():
    """Chạy một vòng re-check: lấy 50 audio gần nhất, update usage snapshot."""
    logger.info("🔄 [Re-check Job] Bắt đầu cập nhật usage snapshot ...")
    await init_db()

    recent = await get_recent_audio_ids(limit=RECHECK_LIMIT)
    if not recent:
        logger.info("  Không có audio nào trong DB để re-check.")
        return

    logger.info(f"  Sẽ re-check {len(recent)} audio ...")

    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        },
        follow_redirects=True,
    ) as client:

        boosted = []
        for audio_id, old_usage in recent:
            current_usage = await fetch_usage_lightweight(audio_id, client)

            if current_usage > 0:
                await snapshot_usage(audio_id, current_usage)
                velocity = await get_usage_velocity(audio_id)

                change = current_usage - (old_usage or 0)
                log_msg = (
                    f"  [{audio_id}] usage: {old_usage} → {current_usage} "
                    f"(+{change}) | vel={velocity:.1f} lượt/h"
                )

                if velocity >= VELOCITY_BOOST_THRESHOLD:
                    logger.warning(f"🚀 BOOST! {log_msg}")
                    boosted.append((audio_id, velocity))
                else:
                    logger.debug(log_msg)
            else:
                logger.debug(f"  [{audio_id}] Không lấy được usage mới (skip)")

            await asyncio.sleep(DELAY_BETWEEN_REQS)

    # Tổng kết
    logger.success(
        f"✅ [Re-check Job] Hoàn thành. "
        f"Đã update {len(recent)} audio | "
        f"{len(boosted)} audio đang BOOST velocity."
    )
    if boosted:
        logger.info("  🔥 Top audio đang tăng nhanh:")
        for aid, vel in sorted(boosted, key=lambda x: -x[1])[:5]:
            logger.info(f"    - {aid}: {vel:.1f} lượt/h")


# ── Loop mode (chạy liên tục) ─────────────────────────────────────────────────
async def run_loop(interval_minutes: int):
    """Chạy re-check theo interval (phút)."""
    logger.info(f"⏰ Re-check Job chạy mỗi {interval_minutes} phút. Nhấn Ctrl+C để dừng.")
    while True:
        start = datetime.now(timezone.utc)
        try:
            await run_recheck()
        except Exception as e:
            logger.error(f"Re-check Job lỗi: {e}")

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        wait    = max(0, interval_minutes * 60 - elapsed)
        logger.info(f"  💤 Nghỉ {wait/60:.1f} phút đến lần check tiếp theo ...")
        await asyncio.sleep(wait)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TikTok Audio Background Re-check Job V2.1")
    parser.add_argument(
        "--interval", type=int, default=0,
        help="Chạy lặp mỗi N phút. Mặc định=0 (chỉ chạy 1 lần rồi thoát)."
    )
    parser.add_argument(
        "--limit", type=int, default=RECHECK_LIMIT,
        help=f"Số audio check mỗi lần (default: {RECHECK_LIMIT})."
    )
    args = parser.parse_args()

    logger.add("data/logs/recheck_job.log", rotation="10 MB", retention="7 days")

    if args.interval > 0:
        asyncio.run(run_loop(args.interval))
    else:
        asyncio.run(run_recheck())

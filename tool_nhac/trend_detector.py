"""
trend_detector.py — V2.1 Trend Detection Engine
================================================
Phát hiện xu hướng âm thanh sử dụng kết hợp:
  - Date Density: mật độ video gần đây / tổng video
  - Trend Depth: số video tuyệt đối trong 7 ngày qua
  - Early Trend: bắt audio mới nổi trước khi phổ biến (kèm filter clone)
  - Usage Velocity: tốc độ tăng lượt dùng qua thời gian (cần DB snapshot)

Không phụ thuộc vào DB phức tạp — phần Date Density hoàn toàn stateless.
"""

import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from loguru import logger
import httpx


# ── Cấu hình ngưỡng ─────────────────────────────────────────────────────────
HOT_TREND_VELOCITY   = 0.6   # >= 60% video đăng trong 7 ngày gần → HOT
HOT_TREND_DEPTH      = 8     # Tối thiểu 8 video trong 7 ngày
RISING_VELOCITY      = 0.4
RISING_DEPTH         = 5

EARLY_TREND_MAX_USAGE        = 300   # Usage thấp nhưng đang bùng
EARLY_TREND_MIN_VIDEOS_3DAYS = 8     # >= 8 video trong 3 ngày gần nhất
EARLY_TREND_MIN_CREATORS     = 5     # >= 5 creator khác nhau (đã qua filter clone)

# Filter chống clone farm
CLONE_MIN_FOLLOWERS  = 500   # Creator phải có ít nhất 500 followers
CLONE_MIN_AGE_DAYS   = 30    # Tài khoản phải >= 30 ngày tuổi


# ── Data Classes ─────────────────────────────────────────────────────────────
from dataclasses import dataclass, field

@dataclass
class TrendResult:
    tag: str               # "HOT_TREND" | "RISING" | "EARLY_TREND" | "NORMAL"
    trend_score: float     # 0.0 → 1.0
    trend_velocity: float  # % video gần đây
    trend_depth: int       # Số video tuyệt đối trong 7 ngày
    recent_creators: list  # Danh sách creator dùng audio gần đây
    is_early_trend: bool = False
    velocity_per_hour: float = 0.0  # Từ DB snapshot (nếu có)


@dataclass
class CreatorInfo:
    username: str
    follower_count: int = 0
    account_age_days: int = 999
    is_valid: bool = True   # Qua filter clone hay không


# ── Hàm parse ngày đăng TikTok ───────────────────────────────────────────────
def _parse_tiktok_date(raw: str) -> Optional[datetime]:
    """
    TikTok hiển thị ngày dạng: "2024-4-20", "Apr 20", "1d ago", "3h ago", v.v.
    Trả về datetime UTC hoặc None nếu không parse được.
    """
    now = datetime.now(timezone.utc)
    raw = raw.strip()

    # "Xd ago" / "Xh ago" / "Xw ago"
    m = re.match(r'(\d+)\s*(h|d|w)\s*ago', raw, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {'h': timedelta(hours=n), 'd': timedelta(days=n), 'w': timedelta(weeks=n)}
        return now - delta.get(unit, timedelta(days=n))

    # "YYYY-M-D" hoặc "YYYY-MM-DD"
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, tzinfo=timezone.utc)

    # Timestamp unix
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)

    return None


# ── Filter chống Clone Farm ───────────────────────────────────────────────────
async def check_creator_is_real(username: str, client: httpx.AsyncClient) -> CreatorInfo:
    """
    Kiểm tra sơ bộ creator có phải clone account không.
    Dùng TikTok public API (không cần auth) để lấy follower count và ngày tạo.
    Fallback về `is_valid=True` nếu không lấy được (tránh false negative).
    """
    info = CreatorInfo(username=username)
    try:
        # TikTok public user info endpoint (không cần cookie)
        url = f"https://www.tiktok.com/api/user/detail/?uniqueId={username}&aid=1988"
        r = await client.get(url, timeout=8.0)
        if r.status_code != 200:
            return info  # Không lấy được → giả định hợp lệ

        data = r.json()
        user = data.get("userInfo", {}).get("user", {})
        stats = data.get("userInfo", {}).get("stats", {})

        info.follower_count = int(stats.get("followerCount", 0))
        create_ts = int(user.get("createTime", 0))
        if create_ts > 0:
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(create_ts, tz=timezone.utc)).days
            info.account_age_days = age

        # Áp rule filter
        if info.follower_count < CLONE_MIN_FOLLOWERS:
            info.is_valid = False
            logger.debug(f"  Clone filter: @{username} chỉ {info.follower_count} followers")
        elif info.account_age_days < CLONE_MIN_AGE_DAYS:
            info.is_valid = False
            logger.debug(f"  Clone filter: @{username} mới {info.account_age_days} ngày")

    except Exception:
        pass  # Lỗi mạng → giả định hợp lệ

    return info


# ── Hàm chính: Phân tích trend từ danh sách video ────────────────────────────
async def analyze_trend(
    audio_id: str,
    usage_count: int,
    video_dates: list,           # list[str | int] — ngày đăng của từng video dùng audio
    creator_ids: list = None,    # list[str] — username của creator dùng audio
    usage_prev: int = 0,         # Usage lúc check trước (từ DB snapshot)
    hours_since_prev: float = 0, # Số giờ từ lần check trước
    check_early_clone: bool = True,
) -> TrendResult:
    """
    Phân tích mức độ trend của một audio dựa trên mật độ video gần đây.
    
    Args:
        audio_id: ID audio TikTok
        usage_count: Số lượt dùng hiện tại
        video_dates: Danh sách ngày đăng video đang dùng audio
        creator_ids: Danh sách creator dùng audio
        usage_prev: Usage lần check trước (để tính velocity từ DB)
        hours_since_prev: Khoảng thời gian giữa 2 lần check
        check_early_clone: Có kiểm tra clone filter không (chậm hơn một chút)
    """
    now = datetime.now(timezone.utc)
    creator_ids = creator_ids or []

    # ── Parse và phân loại video theo độ tuổi ──────────────────────────────
    parsed_dates = []
    for d in video_dates:
        dt = _parse_tiktok_date(str(d)) if not isinstance(d, datetime) else d
        if dt:
            parsed_dates.append(dt)

    total_checked = len(parsed_dates)
    if total_checked == 0:
        return TrendResult("NORMAL", 0.0, 0.0, 0, creator_ids)

    within_7d  = [d for d in parsed_dates if (now - d).days <= 7]
    within_3d  = [d for d in parsed_dates if (now - d).days <= 3]

    trend_depth    = len(within_7d)
    trend_velocity = round(trend_depth / total_checked, 3)

    # ── Normalize trend_depth về 0–1 (max 15 video = 1.0) ──────────────────
    trend_depth_norm = min(trend_depth / 15, 1.0)
    trend_score      = round(trend_velocity * 0.6 + trend_depth_norm * 0.4, 3)

    # ── Usage Velocity từ DB snapshot ──────────────────────────────────────
    velocity_per_hour = 0.0
    if usage_prev > 0 and hours_since_prev > 0:
        velocity_per_hour = round((usage_count - usage_prev) / hours_since_prev, 2)

    # ── Phân loại tag ──────────────────────────────────────────────────────
    tag = "NORMAL"
    if trend_velocity >= HOT_TREND_VELOCITY and trend_depth >= HOT_TREND_DEPTH:
        tag = "HOT_TREND"
    elif trend_velocity >= RISING_VELOCITY and trend_depth >= RISING_DEPTH:
        tag = "RISING"

    # ── Early Trend Detection ───────────────────────────────────────────────
    is_early = False
    if usage_count < EARLY_TREND_MAX_USAGE and len(within_3d) >= EARLY_TREND_MIN_VIDEOS_3DAYS:
        # Kiểm tra có đủ creator khác nhau không (và không phải clone)
        valid_creator_count = len(set(creator_ids))  # Mặc định không check clone (nhanh)
        
        if check_early_clone and creator_ids and valid_creator_count >= EARLY_TREND_MIN_CREATORS:
            # Bật clone filter → check thực sự (chậm hơn ~1-2s)
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True
            ) as client:
                check_tasks = [check_creator_is_real(u, client) for u in list(set(creator_ids))[:10]]
                results = await asyncio.gather(*check_tasks, return_exceptions=True)
                valid_creator_count = sum(
                    1 for r in results
                    if isinstance(r, CreatorInfo) and r.is_valid
                )

        if valid_creator_count >= EARLY_TREND_MIN_CREATORS:
            is_early = True
            tag = "EARLY_TREND"  # Override tag — ưu tiên cao nhất

    logger.info(
        f"🔍 Trend [{audio_id}] usage={usage_count} | "
        f"tag={tag} | velocity={trend_velocity:.2f} | "
        f"depth={trend_depth} | score={trend_score:.2f}"
        + (f" | vel/h={velocity_per_hour}" if velocity_per_hour else "")
    )

    return TrendResult(
        tag=tag,
        trend_score=trend_score,
        trend_velocity=trend_velocity,
        trend_depth=trend_depth,
        recent_creators=creator_ids,
        is_early_trend=is_early,
        velocity_per_hour=velocity_per_hour,
    )


# ── Priority Queue Helper ─────────────────────────────────────────────────────
PRIORITY_MAP = {
    "EARLY_TREND": 0,
    "HOT_TREND":   1,
    "RISING":      2,
    "NORMAL":      3,
}

def get_priority(trend_result: TrendResult, is_vip_creator: bool = False) -> int:
    """
    Trả về số priority để đưa vào queue.
    Số nhỏ hơn = ưu tiên cao hơn.
    
    Priority order:
      0 = EARLY TREND (bắt sớm nhất)
      1 = HOT TREND   (đang nổ mạnh)
      2 = CREATOR VIP (nguồn đáng tin)
      3 = RISING      (tiềm năng)
      4 = NORMAL      (xử lý sau cùng)
    """
    base = PRIORITY_MAP.get(trend_result.tag, 4)
    # Creator VIP chen giữa HOT_TREND và RISING
    if is_vip_creator and base >= 2:
        return min(base, 2)
    return base

"""
market_expander.py — FYP Bubble Breaker
=========================================
Vấn đề: TikTok FYP nhốt acc trong bong bóng creator cùng quốc gia.
Sau khi lướt một hồi → chỉ thấy creator Việt Nam đã follow → cạn data.

Giải pháp (dạy lại TikTok algorithm):
  1. Đọc keyword từ tool_nhac/keywords.txt (đa ngôn ngữ: EN/ID/ES/PT...)
  2. Tìm kiếm keyword đó trên TikTok
  3. Xem 3–5 video quốc tế (watch time 40–60%) → signal mạnh cho algo
  4. Quay lại FYP → algo tự mở rộng đề xuất ra thị trường mới

Kết quả: sau 2–3 vòng như vậy → FYP sẽ mix international content.
"""

import asyncio
import random
from pathlib import Path
from loguru import logger

# Dùng paths từ config — đã được resolve đúng theo vị trí thực tế
from config import SHARED_KEYWORDS, SHARED_CREATORS

# Từ khóa fallback nếu không đọc được file
FALLBACK_KEYWORDS = [
    "storytime", "voiceover", "originalvoice",
    "cerita lucu", "suara asli", "historia real",
    "reaction video", "pov story", "my voice",
    "gagingreaction", "comedyvoice", "monologue",
]

# Market keywords để thoát bong bóng VN — tìm kiếm cụm từ này
MARKET_KEYWORDS = {
    "en": ["storytime", "voiceover", "my voice original", "pov story"],
    "id": ["suara asli", "cerita lucu", "curhat", "ngomongin"],
    "es": ["historia real", "mi voz", "storytime español"],
    "pt": ["historia verdadeira", "minha voz", "storytime br"],
    "th": ["เสียงต้นฉบับ", "เล่าเรื่อง"],
    "in": ["original voice", "my story", "voice over hindi"],
}

# Số video cần xem để signal TikTok
WATCH_COUNT_PER_SESSION = 4  # Xem 4 video quốc tế mỗi lần expand
WATCH_RATIO_MIN = 0.40       # Xem tối thiểu 40% thời lượng
WATCH_RATIO_MAX = 0.70       # Xem tối đa 70%
DEFAULT_WATCH_DURATION = 8.0 # Giây nếu không đọc được duration


def load_international_keywords() -> list:
    """
    Đọc keywords.txt từ tool_nhac, lọc lấy từ khóa KHÔNG phải tiếng Việt.
    Các từ khóa đa ngôn ngữ (EN/ID/ES) là nguồn signal tốt nhất.
    """
    keywords = []
    try:
        if SHARED_KEYWORDS.exists():
            for line in SHARED_KEYWORDS.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                viet_chars = set('àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ')
                if any(c in viet_chars for c in line.lower()):
                    continue
                keywords.append(line)
            logger.info(f"MarketExpander: nạp {len(keywords)} từ khóa quốc tế từ keywords.txt")
        else:
            logger.warning(f"MarketExpander: không tìm thấy {SHARED_KEYWORDS}")
    except Exception as e:
        logger.warning(f"MarketExpander: lỗi đọc keywords.txt: {e}")

    for lang_kws in MARKET_KEYWORDS.values():
        keywords.extend(lang_kws)

    random.shuffle(keywords)
    return list(dict.fromkeys(keywords))


def load_international_creators() -> list:
    """
    Đọc creators_list.txt từ tool_nhac.
    Dùng để visit profile → watch video → signal algo.
    """
    creators = []
    try:
        if SHARED_CREATORS.exists():
            for line in SHARED_CREATORS.read_text(encoding='utf-8').splitlines():
                line = line.strip().lstrip('@')
                if line and not line.startswith('#'):
                    creators.append(line)
            logger.info(f"MarketExpander: nạp {len(creators)} creator từ creators_list.txt")
        else:
            logger.warning(f"MarketExpander: chưa có creators_list.txt tại {SHARED_CREATORS}")
    except Exception as e:
        logger.warning(f"MarketExpander: lỗi đọc creators_list.txt: {e}")
    return creators


class MarketExpander:
    """
    Bộ mở rộng thị trường cho FYP tool.
    Gọi expand() sau mỗi N video để phá bong bóng quốc gia.
    Gọi expand_with_harvest() để vừa signal algo vừa lấy audio.
    """

    def __init__(self, page):
        self.page = page
        self._keywords = load_international_keywords()
        self._creators = load_international_creators()
        self._expand_count = 0

    def _pick_keyword(self) -> str:
        if self._keywords:
            idx = self._expand_count % len(self._keywords)
            return self._keywords[idx]
        return random.choice(FALLBACK_KEYWORDS)

    def _pick_creator(self) -> str | None:
        if self._creators:
            idx = self._expand_count % len(self._creators)
            return self._creators[idx]
        return None

    async def _watch_current_video(self, max_seconds: float = DEFAULT_WATCH_DURATION):
        """Giả lập xem video hiện tại với watch time ngẫu nhiên."""
        watch_time = random.uniform(
            max_seconds * WATCH_RATIO_MIN,
            max_seconds * WATCH_RATIO_MAX,
        )
        watch_time = max(3.0, min(watch_time, 20.0))
        logger.debug(f"  [Watch] Xem {watch_time:.1f}s để signal algo...")
        await asyncio.sleep(watch_time)

    async def expand_via_search(self) -> int:
        """
        Chiến thuật 1: Tìm kiếm keyword quốc tế → xem video.
        Đây là signal mạnh nhất để phá bubble.
        Returns: số video đã xem
        """
        keyword = self._pick_keyword()
        logger.info(f"🌍 [Expand] Search '{keyword}' để mở rộng thị trường...")

        watched = 0
        try:
            # Mở trang search
            search_url = f"https://www.tiktok.com/search?q={keyword.replace(' ', '%20')}&type=video"
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            # Tìm các video trong kết quả search
            video_links = await self.page.query_selector_all('a[href*="/video/"]')
            valid_links = []
            for vl in video_links[:20]:
                href = await vl.get_attribute("href") or ""
                if "/video/" in href and len(href.split("/video/")[-1].split("?")[0]) > 15:
                    full = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                    if full not in valid_links:
                        valid_links.append(full)

            logger.info(f"  [Expand] Tìm thấy {len(valid_links)} video → xem {WATCH_COUNT_PER_SESSION}")

            # Xem từng video (không lấy audio — chỉ để signal)
            for url in valid_links[:WATCH_COUNT_PER_SESSION]:
                try:
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(1.5)
                    await self._watch_current_video()
                    watched += 1
                    logger.debug(f"  [Watch {watched}/{WATCH_COUNT_PER_SESSION}] ✓")
                except Exception as e:
                    logger.debug(f"  [Watch] Lỗi: {e}")

        except Exception as e:
            logger.warning(f"[Expand] Search failed: {e}")

        return watched

    async def expand_via_creator(self, checked_audio: set = None, process_fn=None) -> dict:
        """
        Chiến thuật 2: Visit trang creator quốc tế → xem video + (tùy chọn) thu hoạch audio.
        
        Args:
            checked_audio: Set audio_id đã biết — nếu truyền vào thì sẽ harvest audio
            process_fn:    async fn(video_url, checked_audio) — hàm xử lý video từ main.py
            
        Returns:
            dict: watched (số video xem), harvested (số audio lấy được)
        """
        creator = self._pick_creator()
        if not creator:
            logger.warning("[Expand] Không có creator nào trong list")
            return {"watched": 0, "harvested": 0}

        mode = "harvest" if (checked_audio is not None and process_fn) else "signal"
        logger.info(f"👤 [Expand] Visit @{creator} (mode={mode})")

        watched = 0
        harvested = 0

        try:
            profile_url = f"https://www.tiktok.com/@{creator}"
            await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            # Lấy danh sách video từ profile
            video_links = await self.page.query_selector_all('a[href*="/video/"]')
            valid_links = []
            for vl in video_links[:30]:
                href = await vl.get_attribute("href") or ""
                if "/video/" in href and len(href.split("/video/")[-1].split("?")[0]) > 15:
                    full = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                    if full not in valid_links:
                        valid_links.append(full)

            logger.info(f"  [Creator @{creator}] {len(valid_links)} videos")

            for url in valid_links[:5]:  # Tối đa 5 video/creator
                try:
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(1.5)

                    # HARVEST MODE: lấy audio_id từ trang video
                    if mode == "harvest" and process_fn:
                        audio_link_el = await self.page.query_selector('a[href*="/music/"]')
                        if audio_link_el:
                            href = await audio_link_el.get_attribute("href") or ""
                            audio_id = href.split("/")[-1].split("-")[-1].split("?")[0]
                            if audio_id and audio_id not in checked_audio:
                                logger.info(f"  🎵 Harvest audio {audio_id} từ @{creator}")
                                try:
                                    await asyncio.wait_for(
                                        process_fn(url, checked_audio),
                                        timeout=60
                                    )
                                    harvested += 1
                                except Exception as he:
                                    logger.debug(f"  Harvest lỗi: {he}")

                    # Luôn xem để signal algo
                    await self._watch_current_video(15.0)
                    watched += 1

                except Exception as e:
                    logger.debug(f"  [CreatorWatch] Lỗi: {e}")

        except Exception as e:
            logger.warning(f"[Expand] Creator visit failed: {e}")

        return {"watched": watched, "harvested": harvested}

    async def expand(self, use_creator: bool = False,
                     checked_audio: set = None, process_fn=None) -> dict:
        """
        Entry point chính.
        
        Args:
            use_creator:   True → visit creator, False → search keyword
            checked_audio: Nếu truyền vào → creator mode sẽ harvest audio luôn
            process_fn:    Hàm xử lý video từ main.py
        """
        self._expand_count += 1
        strategy = "creator" if use_creator else "search"
        logger.info(f"🌍 [MarketExpander #{self._expand_count}] Chiến thuật: {strategy}")

        if use_creator:
            result = await self.expand_via_creator(
                checked_audio=checked_audio,
                process_fn=process_fn
            )
            watched   = result["watched"]
            harvested = result.get("harvested", 0)
        else:
            watched   = await self.expand_via_search()
            harvested = 0

        out = {
            "expand_count": self._expand_count,
            "strategy":     strategy,
            "watched":      watched,
            "harvested":    harvested,
        }
        logger.info(f"✅ [Expand done] xem={watched} thu hoạch={harvested} audio")
        return out

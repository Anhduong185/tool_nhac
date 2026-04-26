import re
import time
from datetime import datetime
from loguru import logger

# =====================================================================
# SOUND EFFECT / BACKGROUND MUSIC KEYWORDS
# =====================================================================
SOUND_EFFECT_KEYWORDS = [
    "laugh", "laughter", "laughing", "haha", "hehe", "hihi",
    "tiếng cười", "cười", "sound effect", "sfx", "soundfx",
    "hiệu ứng", "âm hiệu", "applause", "clapping", "vỗ tay",
    "notification", "ringtone", "chuông", "whoosh", "swoosh",
]

BACKGROUND_MUSIC_KEYWORDS = [
    "nhạc nền", "background music", "bgm", "instrumental",
    "no copyright music", "free music", "royalty free",
    "lofi", "lo-fi", "chill music", "study music", "ambient",
    "piano", "guitar", "violin",
]

# =====================================================================
# SMART LSD THRESHOLD - Động theo năm
# =====================================================================
ONE_WEEK_SEC = 7 * 24 * 3600

def get_dynamic_min_usage(create_time: int) -> int:
    """
    Ngưỡng LSD tối thiểu theo năm đăng video + thời gian gần đây.
    - Trong 1 tuần    → 300 LSD
    - 2025-2026       → 500 LSD
    - 2024            → 500 LSD
    - 2023            → 1000 LSD
    - < 2023          → 999999 (không lấy, trừ re-use)
    """
    if create_time <= 0:
        return 500
    now = time.time()
    if create_time >= now - ONE_WEEK_SEC:
        return 300
    year = datetime.fromtimestamp(create_time).year
    if year >= 2025: return 500
    if year == 2024: return 500
    if year == 2023: return 1000
    return 999999

def has_sound_effect(audio_name: str) -> bool:
    name_lower = audio_name.strip().lower()
    return any(kw in name_lower for kw in SOUND_EFFECT_KEYWORDS)

def has_background_music_name(audio_name: str) -> bool:
    name_lower = audio_name.strip().lower()
    return any(kw in name_lower for kw in BACKGROUND_MUSIC_KEYWORDS)

# =====================================================================
# ORIGINAL SOUND DETECTION (từ tool_nhac)
# =====================================================================
REJECT_KEYWORDS = [
    "remix", "official", "soundtrack", "instrumental",
    "cover", "karaoke", "lofi", "beat", "prod by",
    "ft.", "feat.", " ost", "bgm", "music by", "composed by",
    "piano solo", "music box", "sped up", "slowed", "reverb",
    "chill vibes", "aesthetic", "spedup", "reverbed", "slowdown",
]

ACCEPT_KEYWORDS = [
    "voice", "story", "talk", "react", "pov", "narration",
    "monologue", "speaking", "kể chuyện", "chia sẻ", "tâm sự",
    "cerita", "suara", "história", "voz", "hablar", "contar",
    "reaction", "rant", "confession", "vlog",
]

ORIGINAL_PATTERNS = [
    "âm thanh gốc", "original sound", "originalton",
    "suara asli", "오리지널 사운드", "sonido original",
    "som original", "son original", "เสียงต้นฉบับ",
    "original audio", "my voice", "my sound",
]

MUSIC_CDN_DOMAINS = [
    "sf16-ies-music-sg.tiktokcdn.com",
    "sf9-ies-music-sg.tiktokcdn.com",
    "sf16-music-sign.tiktokcdn.com",
    "sf3-ttcdn-tos.pstatp.com",
    "p16-sign-sg.tiktokcdn.com",
]

def is_original_sound(audio_name: str) -> tuple:
    name_lower = audio_name.strip().lower()
    for kw in REJECT_KEYWORDS:
        if kw in name_lower:
            return False, f"Rejected by keyword: {kw}"
    for p in ORIGINAL_PATTERNS:
        if p in name_lower:
            return True, "Passed (Original pattern)"
    for kw in ACCEPT_KEYWORDS:
        if kw in name_lower:
            return True, f"Passed (Speech keyword: {kw})"
    if re.search(r"\(\d{7,}\)$", name_lower):
        return False, "Rejected (Library ID pattern)"
    # Short name fallback: reject if looks like music
    MUSIC_WORDS = ["song", "music", "track", "album", "nhạc", "bài", "piano", "guitar", "beat", "melody"]
    if len(name_lower) < 60 and not any(bw in name_lower for bw in MUSIC_WORDS):
        return True, "Passed (Short name)"
    return False, "Not an original sound pattern"

def is_library_music_url(audio_url: str) -> bool:
    if not audio_url: return False
    for domain in MUSIC_CDN_DOMAINS:
        if domain in audio_url: return True
    return False


class FilterEngine:
    @staticmethod
    def is_original_sound(audio_name: str):
        return is_original_sound(audio_name)

    @staticmethod
    def is_library_music_url(audio_url: str):
        return is_library_music_url(audio_url)

    @staticmethod
    def is_valid(audio_data: dict):
        """
        Pipeline lọc hoàn chỉnh - không phụ thuộc view/like.

        audio_data keys:
          audio_name, audio_url, duration, usage_count,
          is_copyrighted, speech_ratio, create_time, year,
          tiktok_has_lyrics, tiktok_is_commerce, tiktok_category,
          tiktok_author_name
        """
        audio_name = audio_data.get('audio_name', '')
        duration   = audio_data.get('duration', 0)
        usage      = audio_data.get('usage_count', 0)

        # 1. Thời lượng ≤ 59s
        if duration > 59:
            return False, f"Duration {duration}s > 59s"
        if duration <= 0:
            return False, "Duration zero/negative"

        # 2. Fix 2 & 5: Lọc SFX / nhạc nền qua tên
        if has_sound_effect(audio_name):
            return False, "Sound effect / tiếng cười detected"
        if has_background_music_name(audio_name):
            return False, "Background music name detected"

        # 3. Fix 4: Metadata ẩn TikTok (nếu có)
        if audio_data.get('tiktok_has_lyrics'):
            return False, "Has lyrics → bài hát"
        if audio_data.get('tiktok_is_commerce'):
            return False, "Commercial music"
        cat = audio_data.get('tiktok_category', -1)
        if cat in (1, 3):  # 1=music, 3=sfx
            return False, f"TikTok category {cat} (music/sfx)"
        sfx_authors = ["tiktok sound", "tiktok effect", "sound effect", "sfx"]
        author_name = audio_data.get('tiktok_author_name', '').lower()
        if any(p in author_name for p in sfx_authors):
            return False, f"SFX author: {author_name}"

        # 4. URL CDN nhạc thư viện
        if is_library_music_url(audio_data.get('audio_url', '')):
            return False, "Library music URL (TikTok CDN)"

        # 5. Phải là original sound
        is_orig, reason = is_original_sound(audio_name)
        if not is_orig:
            return False, reason

        # 6. Bản quyền Shazam
        if audio_data.get('is_copyrighted'):
            return False, "Bản quyền (Shazam)"

        # 7. Tỉ lệ giọng nói Whisper (nếu đã check)
        speech_ratio = audio_data.get('speech_ratio', -1)
        if speech_ratio != -1 and speech_ratio < 0.70:
            return False, f"Speech thấp ({speech_ratio:.0%} < 70%)"

        # 8. Fix 1: LSD động theo năm (KHÔNG dùng view/like)
        create_time = audio_data.get('create_time', 0)
        # Nếu không có create_time, fallback về year
        if create_time <= 0:
            year = audio_data.get('year', 2024)
            create_time = int(datetime(year, 6, 1).timestamp())

        # Kiểm tra trước 2023 (không lấy trừ re-use)
        if create_time > 0 and create_time < 1672531200:  # 2023-01-01
            if usage < 5000:  # Re-use exception
                return False, "Video trước 2023, LSD không đủ để re-use"

        min_required = get_dynamic_min_usage(create_time)
        # usage=0 means can't get count → reject to be safe
        if usage == 0:
            return False, "LSD = 0 (không lấy được số lượt dùng)"
        if usage < min_required:
            year = datetime.fromtimestamp(create_time).year if create_time > 0 else "?"
            return False, f"LSD {usage:,} < {min_required:,} (yêu cầu năm {year})"

        return True, "Passed"

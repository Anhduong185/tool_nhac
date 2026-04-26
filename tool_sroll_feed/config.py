import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ───────────────────────────────────────────────────────────────
FYP_DIR      = Path(__file__).resolve().parent
ROOT_DIR     = FYP_DIR.parent

# ── Liên kết với tool_nhac ────────────────────────────────────────────────
TOOL_NHAC_DIR     = ROOT_DIR / "tool_nhac"
TOOL_NHAC_DB      = TOOL_NHAC_DIR / "data" / "database" / "audio_automation.db"
SHARED_KEYWORDS   = TOOL_NHAC_DIR / "keywords.txt"        # Dùng cho MarketExpander
SHARED_CREATORS   = TOOL_NHAC_DIR / "creators_list.txt"   # Dùng cho Creator Mode

# URL của server.py — gửi kết quả qua HTTP thay vì stdout
SERVER_URL        = os.getenv("DASHBOARD_URL", "http://localhost:8000")
FYP_RESULT_URL    = f"{SERVER_URL}/fyp/result"

# Audio Filtering Thresholds
RULES = {
    "max_duration": 59,
    "min_usage_total": 500,
    "years": {
        2023: {"min_usage": 1000},
        2024: {"min_usage": 500},
        2025: {"min_usage": 300, "recent_days": 7},
        2026: {"min_usage": 300, "recent_days": 7},
    },
    "source_ai": {
        "min_usage": 1000,
        "recent_usage": 500,
        "recent_days": 7
    },
    "source_game": {
        "2023_recent_reuse": True,
        "2026_min_usage": 500
    },
    "reject_keywords": [
        "remix", "official", "soundtrack", "instrumental",
        "cover", "karaoke", "lofi", "beat", "prod by",
        "ft.", "feat.", " ost", "bgm", "music by", "composed by",
        "piano solo", "music box", "sped up", "slowed", "reverb",
        "chill vibes", "aesthetic", "spedup", "reverbed", "slowdown"
    ],
    "accept_keywords": [
        "voice", "story", "talk", "react", "pov", "narration",
        "monologue", "speaking", "kể chuyện", "chia sẻ", "tâm sự",
        "cerita", "suara", "história", "voz", "hablar", "contar",
        "reaction", "rant", "confession", "vlog"
    ],
    "original_patterns": [
        "âm thanh gốc", "original sound", "originalton",
        "suara asli", "오리지널 사운드", "sonido original",
        "som original", "son original", "เสียงต้นฉบับ",
        "original audio", "my voice", "my sound",
    ],
    "music_cdn_domains": [
        "sf16-ies-music-sg.tiktokcdn.com",
        "sf9-ies-music-sg.tiktokcdn.com",
        "sf16-music-sign.tiktokcdn.com",
        "sf3-ttcdn-tos.pstatp.com",
        "p16-sign-sg.tiktokcdn.com",
    ],
}

# Browser Settings
BROWSER_CONFIG = {
    "headless": False,
    "use_existing_browser": False,
    "remote_debugging_port": 9222,
    "user_data_dir": str(FYP_DIR / "tiktok_session"),
    "viewport": {"width": 1280, "height": 720},
}

# Local paths
DB_PATH        = str(FYP_DIR / "tiktok_audio.db")
TEMP_AUDIO_DIR = str(FYP_DIR / "temp_audio")

os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

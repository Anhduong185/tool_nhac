import os
from dotenv import load_dotenv

load_dotenv()

# Audio Filtering Thresholds
RULES = {
    "max_duration": 59,
    "min_usage_total": 300,
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
    "excluded_keywords": ["movie", "tv show", "netflix", "brand", "official", "show"],
}

# Browser Settings
BROWSER_CONFIG = {
    "headless": False,  # Đổi thành True nếu muốn ẩn hoàn toàn
    "use_existing_browser": False, # Chuyển về False để bạn đăng nhập trực tiếp 1 lần rồi lưu lại
    "remote_debugging_port": 9222,
    "user_data_dir": "tiktok_session",
    "viewport": {"width": 1280, "height": 720},
}

# Paths
DB_PATH = "tiktok_audio.db"
TEMP_AUDIO_DIR = "temp_audio"

if not os.path.exists(TEMP_AUDIO_DIR):
    os.makedirs(TEMP_AUDIO_DIR)

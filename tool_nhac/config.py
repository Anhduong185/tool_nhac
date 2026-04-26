import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent

# Ưu tiên sử dụng ffmpeg.exe và ffprobe.exe có sẵn trong thư mục tool_nhac
local_ffmpeg = ROOT_DIR / "ffmpeg.exe"
if local_ffmpeg.exists():
    os.environ["PATH"] = str(ROOT_DIR) + os.pathsep + os.environ["PATH"]
else:
    # Dự phòng dùng imageio_ffmpeg
    try:
        import imageio_ffmpeg
        import shutil
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_dir = os.path.dirname(ffmpeg_exe)
        
        ffmpeg_alias = os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not os.path.exists(ffmpeg_alias):
            shutil.copy(ffmpeg_exe, ffmpeg_alias)
            
        os.environ["PATH"] += os.pathsep + ffmpeg_dir
    except ImportError:
        pass

# =======================================
# Paths
# =======================================

DATA_DIR = ROOT_DIR / "data"
AUDIOS_DIR = DATA_DIR / "audios"
DATABASE_DIR = DATA_DIR / "database"
LOGS_DIR = DATA_DIR / "logs"
OUTPUT_DIR = DATA_DIR / "output"

# Create directories if they don't exist
for d in [DATA_DIR, AUDIOS_DIR, DATABASE_DIR, LOGS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATABASE_DIR / "audio_automation.db"
KEYWORDS_FILE = ROOT_DIR / "keywords.txt"
RESULTS_FILE = OUTPUT_DIR / "results.csv"
COOKIES_FILE = DATA_DIR / "cookies.json"

# =======================================
# Crawler Settings
# =======================================
SCROLL_COUNT = 15          # Tối ưu: 15 lượt cuộn là đủ lấy video tốp đầu nhanh hơn
TARGET_AUDIOS = 60         # Top 60 candidates/keyword
PAGE_LOAD_TIMEOUT = 60000   # ms (Tăng lên 60s cho mạng yếu/máy chậm)
MIN_USAGE_COUNT = 450       # Hạ tiêu chuẩn quét! Chỉ cần 50 người dùng lại là đã chứng minh được audio này tiềm năng và không dính bản quyền
MIN_VIDEO_VIEWS = 1000      # Hạ xuống để vớt các audio tiềm năng từ video nhỏ
MAX_DURATION = 59          # Khống chế thời lượng (giây) max
CHOOSE_TYPE = "speech"     # "speech" hoặc "music" (Anh đang muốn lấy giọng nói)
OPEN_VIDEO_LINK = False    # Tắt auto-open tab - link được log ra console
DOWNLOAD_AUDIO = True      # Vẫn cần tải tạm về để AI check Shazam và Speech, xong sẽ xoá
API_TIMEOUT = 10000         # ms
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", None)
GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL", "")

# =======================================
# Filter Rules
# =======================================
MAX_DURATION = 59  # giây
MIN_USAGE = 500    # số lượng usage / play tối thiểu

BLACKLIST_KEYWORDS = [
    # Các từ chỉ nhạc thương mại / bài hát có bản quyền
    "official",
    "remix",
    "movie",
    "soundtrack",
    "promo",
    "music",
    "song",
    "cover",
    "dj",
    "beat",
    "instrumental",
    "ft.",
    "feat.",
    "prod.",
    "prod by",
    "ver.",
    "version",
    # Từ chỉ loại nhạc (không phải giọng nói creator)
    "lofi",
    "lo-fi",
    "edm",
    "piano",
    "guitar",
    "violin",
    "acoustic",
    "jazz",
    "hip hop",
    "hip-hop",
    "trap",
    "drill",
    "rnb",
    "r&b",
    "pop",
    "rock",
    "indie",
    "ambient",
    "classical",
    "kpop",
    "k-pop",
    "vpop",
    "v-pop",
    "nhạc trẻ",
    "nhạc vàng",
    "nhạc đỏ",
    "ost",
    "theme song",
    "opening",
    "ending",
]

# =======================================
# API / Library Config
# =======================================
SHAZAM_DELAY = float(os.getenv("SHAZAM_DELAY", "2.0"))
WHISPER_MODEL_SIZE = "tiny"  # Chuyển sang Tiny để bứt tốc độ trên 8GB RAM

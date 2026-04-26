from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class AudioMetadata:
    audio_id: str
    audio_name: str
    duration: int
    usage_count: int
    audio_url: str
    audio_page_url: str
    video_url: str
    keyword: str
    status: str = "pending"
    reason: Optional[str] = None
    file_path: Optional[str] = None
    date_added: str = field(default_factory=lambda: datetime.now().isoformat())
    is_speech: Optional[bool] = None
    ai_score: Optional[float] = None
    speech_ratio: float = 0.0   # Tỷ lệ giọng nói thực tế (Whisper)
    video_views: int = 0        # Lượt xem video gốc
    video_likes: int = 0        # Lượt like video gốc
    create_time: int = 0        # Unix timestamp ngày đăng video
    author_username: str = ""   # Username của tác giả video gốc
    source_type: str = "keyword" # Nguồn gốc: keyword, creator_scan, manual_check, v.v.

    # === METADATA ẨN TỪ TIKTOK API (Idea 2 - Deep Filter) ===
    tiktok_is_original: bool = True     # music.original → False = nhạc thư viện chắc chắn
    tiktok_author_name: str = ""        # music.authorName → "TikTok" / "TikTok Sounds" = SFX thư viện
    tiktok_category: int = -1           # music.categoryType → thử đọc (1=music, 2=voice, 3=sfx)
    tiktok_is_ai: bool = False          # music.tta hoặc music.isAiGenerated = AI voice
    tiktok_is_commerce: bool = False    # music.isCommerce → True = nhạc thương mại
    tiktok_has_lyrics: bool = False     # music.hasLyrics → True = bài hát có lời → nhạc
    tiktok_music_by_author: bool = True # music.musicByAuthor → False = nhạc không phải tác giả tự làm

import asyncio
import aiosqlite
import httpx
import csv
import re
from io import StringIO
from models import AudioMetadata
from config import DB_PATH, GOOGLE_SHEET_CSV_URL
from loguru import logger
from pathlib import Path

_sheet_authors = set()

async def load_authors_from_sources():
    """Tải danh sách tác giả từ Google Sheet và file Excel nội bộ"""
    global _sheet_authors
    _sheet_authors.clear()
    
    import pandas as pd
    from pathlib import Path
    
    count_sheet = 0
    count_excel = 0
    
    # 1. Tải từ file Excel nội bộ
    excel_path = Path("e:/tool_nhac/tool_nhac/Link kênh tiktok check trùng .xlsx")
    if excel_path.exists():
        try:
            df = pd.read_excel(excel_path)
            # Chuyển toàn bộ dataframe thành string để dễ regex
            text = df.to_string()
            matches = re.findall(r'@([a-zA-Z0-9_.]+)', text)
            for m in matches:
                _sheet_authors.add(m.lower())
                count_excel += 1
                
            # Duyệt các cột chứa text để lấy username ko có @ (tương đối)
            for col in df.columns:
                for cell in df[col].dropna().astype(str):
                    cell_clean = cell.strip()
                    if cell_clean and ' ' not in cell_clean and '/' not in cell_clean and len(cell_clean) > 2:
                        _sheet_authors.add(cell_clean.lower())
                        
            logger.info(f"Đã tải tác giả từ Excel cục bộ (tổng cộng {_sheet_authors.__len__()} users).")
        except Exception as e:
            logger.error(f"Lỗi tải file Excel: {e}")

    # 2. Tải từ Google Sheet
    if GOOGLE_SHEET_CSV_URL:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(GOOGLE_SHEET_CSV_URL)
                response.raise_for_status()
                text = response.text
                
                reader = csv.reader(StringIO(text))
                for row in reader:
                    if not row: continue
                    cell = row[0].strip()
                    if not cell: continue
                    
                    if '@' in cell:
                        m = re.search(r'@([a-zA-Z0-9_.]+)', cell)
                        if m: 
                            _sheet_authors.add(m.group(1).lower())
                            count_sheet += 1
                    else:
                        if ' ' not in cell and '/' not in cell:
                            _sheet_authors.add(cell.lower())
                            count_sheet += 1
                            
                logger.info(f"Đã tải {count_sheet} tác giả từ Google Sheet.")
        except Exception as e:
            logger.error(f"Lỗi tải Google Sheet: {e}")

# =====================================================================
# BƯỚC 1: SHARED DATABASE BRIDGE
# Đọc audio đã processed từ tool_sroll_feed để tránh xử lý trùng
# =====================================================================
_fyp_synced_ids: set = set()

async def sync_from_fyp_db() -> int:
    """
    Đọc tất cả audio_id đã processed từ tool_sroll_feed/tiktok_audio.db.
    Thêm vào cache để check_duplicate() biết và bỏ qua chúng.
    Returns: số lượng ID đã sync
    """
    from pathlib import Path
    fyp_db = Path("e:/tool_nhac/tool_sroll_feed/tiktok_audio.db")
    if not fyp_db.exists():
        logger.debug("FYP DB không tồn tại, bỏ qua sync.")
        return 0
    
    synced = 0
    try:
        async with aiosqlite.connect(str(fyp_db)) as db:
            async with db.execute("SELECT audio_id FROM audiorecord") as cursor:
                async for (audio_id,) in cursor:
                    _fyp_synced_ids.add(str(audio_id))
                    synced += 1
        logger.info(f"✅ [Bridge] Sync {synced} audio IDs từ tool_sroll_feed → Tránh xử lý trùng.")
    except Exception as e:
        logger.error(f"Lỗi sync FYP DB: {e}")
    return synced

async def init_db():
    try:
        async with aiosqlite.connect(DB_PATH, timeout=30) as db:
            # WAL mode: cho phép đọc song song trong khi đang ghi → loại bỏ "database is locked"
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute('''
                CREATE TABLE IF NOT EXISTS audio_history (
                    audio_id TEXT PRIMARY KEY,
                    audio_name TEXT,
                    duration INTEGER,
                    usage_count INTEGER,
                    audio_url TEXT,
                    audio_page_url TEXT,
                    video_url TEXT,
                    keyword TEXT,
                    status TEXT,
                    reason TEXT,
                    file_path TEXT,
                    date_added TEXT,
                    is_speech BOOLEAN,
                    ai_score REAL,
                    speech_ratio REAL DEFAULT 0.0,
                    video_views INTEGER DEFAULT 0,
                    video_likes INTEGER DEFAULT 0,
                    create_time INTEGER DEFAULT 0,
                    source_type TEXT DEFAULT 'keyword',
                    author_username TEXT DEFAULT '',
                    trend_tag TEXT DEFAULT 'NORMAL',
                    trend_score REAL DEFAULT 0.0,
                    trend_velocity REAL DEFAULT 0.0,
                    music_ratio REAL DEFAULT 0.0
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS target_users (
                    username TEXT PRIMARY KEY,
                    is_crawled BOOLEAN DEFAULT 0,
                    date_added TEXT
                )
            ''')
            # V2.1: Bảng snapshot usage để tính velocity theo thời gian
            await db.execute('''
                CREATE TABLE IF NOT EXISTS audio_usage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audio_id TEXT NOT NULL,
                    usage_count INTEGER NOT NULL,
                    checked_at TEXT NOT NULL
                )
            ''')
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_history_audio "
                "ON audio_usage_history(audio_id, checked_at DESC)"
            )
            # Migration: thêm các cột mới nếu chưa có
            for col, col_type in [
                ("speech_ratio",   "REAL DEFAULT 0.0"),
                ("video_views",    "INTEGER DEFAULT 0"),
                ("video_likes",    "INTEGER DEFAULT 0"),
                ("create_time",    "INTEGER DEFAULT 0"),
                ("source_type",    "TEXT DEFAULT 'keyword'"),
                ("author_username","TEXT DEFAULT ''"),
                ("trend_tag",      "TEXT DEFAULT 'NORMAL'"),
                ("trend_score",    "REAL DEFAULT 0.0"),
                ("trend_velocity", "REAL DEFAULT 0.0"),
                ("music_ratio",    "REAL DEFAULT 0.0"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE audio_history ADD COLUMN {col} {col_type}")
                except Exception:
                    pass
            await db.commit()
            logger.info("Database initialized (WAL mode, V2.1 schema).")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

async def load_authors_from_sources():
    """Đọc file Excel và nạp danh sách tác giả vào creators_list.txt và DB để check trùng."""
    import pandas as pd
    from datetime import datetime, timezone
    from pathlib import Path
    
    excel_file = Path("Link kênh tiktok check trùng .xlsx")
    txt_file = Path("creators_list.txt")
    
    loaded_count = 0
    try:
        if excel_file.exists():
            df = pd.read_excel(excel_file)
            # Giả sử cột chứa link kênh hoặc username là cột đầu tiên hoặc cột có tên 'Link'/'Username'
            col_name = df.columns[0]
            for col in df.columns:
                if 'link' in col.lower() or 'user' in col.lower() or 'kênh' in col.lower():
                    col_name = col
                    break
                    
            usernames = []
            for val in df[col_name].dropna():
                val = str(val).strip()
                if not val: continue
                # Parse username from URL (https://www.tiktok.com/@username)
                if '@' in val:
                    username = val.split('@')[-1].split('?')[0].split('/')[0]
                else:
                    username = val.split('/')[-1].split('?')[0]
                
                if username:
                    usernames.append(username.lower())
            
            if usernames:
                async with aiosqlite.connect(DB_PATH) as db:
                    for u in set(usernames):
                        await db.execute('''
                            INSERT OR IGNORE INTO target_users (username, is_crawled, date_added)
                            VALUES (?, 1, ?)
                        ''', (u, datetime.now(timezone.utc).isoformat()))
                    await db.commit()
                loaded_count += len(set(usernames))
                logger.info(f"Đã nạp {len(set(usernames))} tác giả từ Excel vào DB check trùng.")
                
    except Exception as e:
        logger.error(f"Lỗi đọc file Excel tác giả: {e}")

async def check_duplicate(audio_id: str) -> bool:
    """Kiểm tra audio đã có trong DB nội bộ hoặc đã xử lý bởi tool_sroll_feed chưa."""
    if str(audio_id) in _fyp_synced_ids:
        return True
    try:
        async with aiosqlite.connect(DB_PATH, timeout=10) as db:
            async with db.execute('SELECT 1 FROM audio_history WHERE audio_id = ?', (audio_id,)) as cursor:
                return await cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"DB error check_duplicate: {e}")
        return False

# Global write lock — đảm bảo chỉ 1 coroutine ghi DB tại 1 thời điểm (lazy init)
_db_write_lock = None
def _get_db_lock():
    global _db_write_lock
    if _db_write_lock is None:
        _db_write_lock = asyncio.Lock()
    return _db_write_lock

async def insert_audio(audio: AudioMetadata):
    """Thêm bản ghi audio vào DB với retry 3 lần nếu bị lock."""
    for attempt in range(3):
        try:
            async with _get_db_lock():
                async with aiosqlite.connect(DB_PATH, timeout=30) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute('''
                        INSERT OR REPLACE INTO audio_history 
                        (audio_id, audio_name, duration, usage_count, audio_url, audio_page_url,
                         video_url, keyword, status, reason, file_path, date_added,
                         is_speech, ai_score, speech_ratio, video_views, video_likes, create_time, source_type, author_username)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        audio.audio_id, audio.audio_name, audio.duration, audio.usage_count,
                        audio.audio_url, audio.audio_page_url, audio.video_url, audio.keyword,
                        audio.status, audio.reason, audio.file_path, audio.date_added,
                        audio.is_speech, audio.ai_score,
                        getattr(audio, 'speech_ratio', 0.0),
                        getattr(audio, 'video_views', 0),
                        getattr(audio, 'video_likes', 0),
                        getattr(audio, 'create_time', 0),
                        getattr(audio, 'source_type', 'keyword'),
                        getattr(audio, 'author_username', ''),
                    ))
                    await db.commit()
                    return  # Thành công
        except Exception as e:
            if attempt < 2:
                logger.warning(f"Insert retry {attempt+1}/3 cho {audio.audio_id}: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                logger.error(f"Failed to insert audio {audio.audio_id} sau 3 lần: {e}")

                logger.error(f"Failed to insert audio {audio.audio_id} sau 3 lần: {e}")

async def get_pending_ai_audios(limit: int = 5) -> list:
    """Lấy danh sách audio đang chờ AI check (status = 'pending_ai')"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT * FROM audio_history WHERE status = 'pending_ai' ORDER BY date_added ASC LIMIT ?",
                (limit,)
            ) as cursor:
                columns = [description[0] for description in cursor.description]
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    data = dict(zip(columns, row))
                    # Chuyển dict thành object AudioMetadata
                    audio = AudioMetadata(
                        audio_id=data['audio_id'],
                        audio_name=data['audio_name'],
                        duration=data['duration'],
                        usage_count=data['usage_count'],
                        audio_url=data['audio_url'],
                        audio_page_url=data['audio_page_url'],
                        video_url=data['video_url'],
                        keyword=data['keyword'],
                        status=data['status'],
                        reason=data['reason'],
                        file_path=data['file_path'],
                        date_added=data['date_added'],
                        is_speech=bool(data['is_speech']) if data['is_speech'] is not None else None,
                        ai_score=data['ai_score'],
                        speech_ratio=data['speech_ratio'] or 0.0,
                        video_views=data['video_views'] or 0,
                        video_likes=data['video_likes'] or 0,
                        create_time=data['create_time'] or 0,
                        author_username=data['author_username'] or "",
                        source_type=data['source_type'] or "keyword"
                    )
                    results.append(audio)
                return results
    except Exception as e:
        logger.error(f"get_pending_ai_audios error: {e}")
        return []

async def update_audio_ai_result(audio_id: str, ai_score: float, speech_ratio: float, status: str = "accepted"):
    """Cập nhật kết quả chấm điểm AI vào DB"""
    try:
        async with _get_db_lock():
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute('''
                    UPDATE audio_history 
                    SET ai_score = ?, speech_ratio = ?, status = ?
                    WHERE audio_id = ?
                ''', (ai_score, speech_ratio, status, audio_id))
                await db.commit()
    except Exception as e:
        logger.error(f"update_audio_ai_result error: {e}")

async def get_all_results():
    """Lấy danh sách tất cả các audio từ DB để export CSV"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT * FROM audio_history') as cursor:
                columns = [description[0] for description in cursor.description]
                results = []
                async for row in cursor:
                    results.append(dict(zip(columns, row)))
                return results
    except Exception as e:
        logger.error(f"Failed to get results: {e}")
        return []

async def get_accepted_results(limit: int = None):
    """Chỉ lấy các audio đã được chấp nhận (status = 'accepted') để export CSV sạch"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            query = "SELECT * FROM audio_history WHERE status = 'accepted' ORDER BY usage_count DESC"
            if limit is not None:
                query += f" LIMIT {limit}"
            async with db.execute(query) as cursor:
                columns = [description[0] for description in cursor.description]
                results = []
                async for row in cursor:
                    results.append(dict(zip(columns, row)))
                return results
    except Exception as e:
        logger.error(f"Failed to get accepted results: {e}")
        return []

async def clear_rejected_audios():
    """Xoá tất cả audio rejected/pending khỏi DB.
    Giữ lại accepted để không export trùng.
    Mục đích: keyword cũ sẽ tìm được audio mới thay vì toàn dup."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            result = await db.execute("DELETE FROM audio_history WHERE status != 'accepted'")
            count = result.rowcount
            await db.commit()
            logger.info(f"🧹 Đã xoá {count} audio rejected/pending khỏi DB")
    except Exception as e:
        logger.error(f"Failed to clear rejected audios: {e}")

# ── TARGET USERS (Tracking Creators) ──────────────────────────────────────────

async def add_target_user(username: str):
    """Thêm một username vào danh sách cần truy quét"""
    if not username or username == "user": return
    # Chuẩn hóa username (bỏ dấu @ nếu có)
    username = username.strip().lstrip("@")
    if not username: return
    try:
        from datetime import datetime
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR IGNORE INTO target_users (username, is_crawled, date_added)
                VALUES (?, 0, ?)
            ''', (username, datetime.now().isoformat()))
            await db.commit()
            
        # Thêm vào creators_list.txt (Graph Crawling)
        creators_file = Path(__file__).resolve().parent / "creators_list.txt"
        existing = set()
        if creators_file.exists():
            with open(creators_file, "r", encoding="utf-8") as f:
                existing = {line.strip().lstrip("@").lower() for line in f if line.strip()}
        
        if username.lower() not in existing:
            with open(creators_file, "a", encoding="utf-8") as f:
                f.write(f"@{username}\n")
            logger.info(f"🕷️ [Graph Crawling] Đã thêm @{username} vào creators_list.txt")
            
    except Exception as e:
        logger.error(f"Failed to add target user {username}: {e}")
async def delete_audio(audio_id: str):
    """Xóa vĩnh viễn một audio khỏi lịch sử thành công (accepted)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM audio_history WHERE audio_id = ?", (audio_id,))
        await db.commit()
    logger.info(f"🗑️ Đã xóa audio {audio_id} khỏi database.")

async def get_next_target_user():
    """Lấy một username chưa được quét để AI truy vết
    Ưu tiên creator mới thêm nhất (từ keyword crawler hits) — chất lượng cao hơn."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT username FROM target_users WHERE is_crawled = 0 ORDER BY date_added DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"Failed to get next target user: {e}")
        return None

async def is_author_known(username: str) -> bool:
    """Kiểm tra xem tác giả này đã có video nào được lưu (accepted) trong DB hoặc có trong Sheet chưa"""
    if not username or username == "user": return False
    
    uname_lower = username.lower()
    if uname_lower in _sheet_authors:
        return True
        
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM audio_history WHERE LOWER(author_username) = ? AND status = 'accepted' LIMIT 1", 
                (uname_lower,)
            ) as cursor:
                result = await cursor.fetchone()
                return result is not None
    except Exception as e:
        logger.error(f"Database error in is_author_known: {e}")
        return False

async def mark_user_crawled(username: str):
    """Đánh dấu đã truy quét xong kênh này"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE target_users SET is_crawled = 1 WHERE username = ?", (username,))
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to mark user {username} as crawled: {e}")


# ── V2.1: Usage Snapshot & Velocity ──────────────────────────────────────────

async def snapshot_usage(audio_id: str, usage_count: int):
    """
    Lưu snapshot usage tại thời điểm hiện tại.
    Được gọi mỗi lần check audio hoặc từ Background Re-check Job.
    """
    from datetime import datetime, timezone
    try:
        async with aiosqlite.connect(DB_PATH, timeout=10) as db:
            await db.execute(
                "INSERT INTO audio_usage_history (audio_id, usage_count, checked_at) VALUES (?, ?, ?)",
                (audio_id, usage_count, datetime.now(timezone.utc).isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.debug(f"snapshot_usage error {audio_id}: {e}")


async def get_usage_velocity(audio_id: str) -> float:
    """
    Tính tốc độ tăng usage (lượt/giờ) dựa trên 2 snapshot gần nhất trong DB.
    Trả về 0.0 nếu chưa đủ data.
    """
    from datetime import datetime, timezone
    try:
        async with aiosqlite.connect(DB_PATH, timeout=10) as db:
            async with db.execute(
                "SELECT usage_count, checked_at FROM audio_usage_history "
                "WHERE audio_id = ? ORDER BY checked_at DESC LIMIT 2",
                (audio_id,)
            ) as cursor:
                rows = await cursor.fetchall()

        if len(rows) < 2:
            return 0.0

        usage_now,  time_now  = rows[0]
        usage_prev, time_prev = rows[1]

        dt_now  = datetime.fromisoformat(time_now)
        dt_prev = datetime.fromisoformat(time_prev)
        hours   = (dt_now - dt_prev).total_seconds() / 3600

        if hours <= 0:
            return 0.0

        return round((usage_now - usage_prev) / hours, 2)
    except Exception as e:
        logger.debug(f"get_usage_velocity error {audio_id}: {e}")
        return 0.0


async def get_recent_audio_ids(limit: int = 50) -> list:
    """Lấy danh sách audio_id được thêm gần nhất (để Re-check Job dùng)."""
    try:
        async with aiosqlite.connect(DB_PATH, timeout=10) as db:
            async with db.execute(
                "SELECT audio_id, usage_count FROM audio_history "
                "ORDER BY date_added DESC LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()
    except Exception as e:
        logger.error(f"get_recent_audio_ids error: {e}")
        return []

import argparse
import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
TARGET_DB = BASE_DIR / "data" / "database" / "audio_automation.db"
SOURCE_DB = ROOT_DIR / "tool_sroll_feed" / "tiktok_audio.db"
MIGRATION_NAME = "merge_fyp_sqlite_into_audio_automation"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def ensure_schema(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
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
            music_ratio REAL DEFAULT 0.0,
            original_year INTEGER DEFAULT 0,
            recent_usage INTEGER DEFAULT 0,
            source_db TEXT DEFAULT 'tool_nhac',
            source_status TEXT DEFAULT '',
            scan_session_id TEXT DEFAULT '',
            scan_creator TEXT DEFAULT '',
            phase TEXT DEFAULT ''
        )
        """
    )
    for column, col_type in [
        ("audio_page_url", "TEXT"),
        ("speech_ratio", "REAL DEFAULT 0.0"),
        ("video_views", "INTEGER DEFAULT 0"),
        ("video_likes", "INTEGER DEFAULT 0"),
        ("create_time", "INTEGER DEFAULT 0"),
        ("source_type", "TEXT DEFAULT 'keyword'"),
        ("author_username", "TEXT DEFAULT ''"),
        ("trend_tag", "TEXT DEFAULT 'NORMAL'"),
        ("trend_score", "REAL DEFAULT 0.0"),
        ("trend_velocity", "REAL DEFAULT 0.0"),
        ("music_ratio", "REAL DEFAULT 0.0"),
        ("original_year", "INTEGER DEFAULT 0"),
        ("recent_usage", "INTEGER DEFAULT 0"),
        ("source_db", "TEXT DEFAULT 'tool_nhac'"),
        ("source_status", "TEXT DEFAULT ''"),
        ("scan_session_id", "TEXT DEFAULT ''"),
        ("scan_creator", "TEXT DEFAULT ''"),
        ("phase", "TEXT DEFAULT ''"),
    ]:
        ensure_column(conn, "audio_history", column, col_type)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS viewed_videos (
            video_link TEXT PRIMARY KEY,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shazam_cache (
            audio_id TEXT PRIMARY KEY,
            is_copyrighted BOOLEAN NOT NULL,
            track_title TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS db_migration_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            migration_name TEXT NOT NULL,
            source_path TEXT,
            target_path TEXT,
            source_audio_rows INTEGER DEFAULT 0,
            inserted_audio_rows INTEGER DEFAULT 0,
            skipped_audio_rows INTEGER DEFAULT 0,
            source_viewed_rows INTEGER DEFAULT 0,
            inserted_viewed_rows INTEGER DEFAULT 0,
            source_shazam_rows INTEGER DEFAULT 0,
            inserted_shazam_rows INTEGER DEFAULT 0,
            checksum TEXT,
            dry_run BOOLEAN DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_history_source "
        "ON audio_history(source_type, status)"
    )


def status_to_canonical(status: str) -> str:
    if status == "passed":
        return "pending_ai"
    if status == "rejected":
        return "rejected"
    return status or "pending"


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def checksum_source(conn: sqlite3.Connection) -> str:
    if not table_exists(conn, "audiorecord"):
        return ""
    digest = hashlib.sha256()
    for row in conn.execute(
        "SELECT audio_id, status, usage_count FROM audiorecord ORDER BY audio_id"
    ):
        digest.update(f"{row['audio_id']}|{row['status']}|{row['usage_count']}\n".encode())
    return digest.hexdigest()


def backup(path: Path, stamp: str) -> Path:
    backup_path = path.with_name(f"{path.name}.bak_{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def build_plan(source: sqlite3.Connection, target: sqlite3.Connection) -> dict:
    existing_audio = {row[0] for row in target.execute("SELECT audio_id FROM audio_history")}
    source_audio = list(source.execute("SELECT * FROM audiorecord"))
    source_viewed = list(source.execute("SELECT * FROM viewedvideo")) if table_exists(source, "viewedvideo") else []
    source_shazam = list(source.execute("SELECT * FROM shazamcache")) if table_exists(source, "shazamcache") else []
    existing_viewed = {row[0] for row in target.execute("SELECT video_link FROM viewed_videos")}
    existing_shazam = {row[0] for row in target.execute("SELECT audio_id FROM shazam_cache")}
    return {
        "source_audio_rows": len(source_audio),
        "insert_audio_rows": sum(1 for row in source_audio if row["audio_id"] not in existing_audio),
        "skip_audio_rows": sum(1 for row in source_audio if row["audio_id"] in existing_audio),
        "source_viewed_rows": len(source_viewed),
        "insert_viewed_rows": sum(1 for row in source_viewed if row["video_link"] not in existing_viewed),
        "source_shazam_rows": len(source_shazam),
        "insert_shazam_rows": sum(1 for row in source_shazam if row["audio_id"] not in existing_shazam),
        "checksum": checksum_source(source),
    }


def migrate(source: sqlite3.Connection, target: sqlite3.Connection, summary: dict):
    now = datetime.utcnow().isoformat()
    with target:
        for row in source.execute("SELECT * FROM audiorecord"):
            target.execute(
                """
                INSERT INTO audio_history (
                    audio_id, audio_name, duration, usage_count, audio_url,
                    audio_page_url, video_url, keyword, status, reason,
                    date_added, source_type, original_year, recent_usage,
                    source_db, source_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(audio_id) DO UPDATE SET
                    audio_name = COALESCE(NULLIF(audio_history.audio_name, ''), excluded.audio_name),
                    duration = COALESCE(audio_history.duration, excluded.duration),
                    usage_count = MAX(COALESCE(audio_history.usage_count, 0), COALESCE(excluded.usage_count, 0)),
                    audio_url = COALESCE(NULLIF(audio_history.audio_url, ''), excluded.audio_url),
                    audio_page_url = COALESCE(NULLIF(audio_history.audio_page_url, ''), excluded.audio_page_url),
                    video_url = COALESCE(NULLIF(audio_history.video_url, ''), excluded.video_url),
                    keyword = COALESCE(NULLIF(audio_history.keyword, ''), excluded.keyword),
                    reason = COALESCE(NULLIF(audio_history.reason, ''), excluded.reason),
                    source_type = COALESCE(NULLIF(audio_history.source_type, ''), excluded.source_type),
                    original_year = COALESCE(NULLIF(audio_history.original_year, 0), excluded.original_year),
                    recent_usage = MAX(COALESCE(audio_history.recent_usage, 0), COALESCE(excluded.recent_usage, 0)),
                    source_db = COALESCE(NULLIF(audio_history.source_db, ''), excluded.source_db),
                    source_status = COALESCE(NULLIF(audio_history.source_status, ''), excluded.source_status)
                """,
                (
                    row["audio_id"],
                    row["audio_id"],
                    row["duration"],
                    row["usage_count"],
                    row["audio_link"],
                    row["audio_link"],
                    row["original_video_link"],
                    "fyp",
                    status_to_canonical(row["status"]),
                    row["rejection_reason"],
                    row["created_at"],
                    "fyp",
                    row["year"],
                    row["recent_usage"],
                    "tool_sroll_feed",
                    row["status"],
                ),
            )
        if table_exists(source, "viewedvideo"):
            for row in source.execute("SELECT * FROM viewedvideo"):
                target.execute(
                    "INSERT OR IGNORE INTO viewed_videos (video_link, created_at) VALUES (?, ?)",
                    (row["video_link"], row["created_at"]),
                )
        if table_exists(source, "shazamcache"):
            for row in source.execute("SELECT * FROM shazamcache"):
                target.execute(
                    """
                    INSERT OR IGNORE INTO shazam_cache
                    (audio_id, is_copyrighted, track_title, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["audio_id"], row["is_copyrighted"], row["track_title"], row["created_at"]),
                )
        target.execute(
            """
            INSERT INTO db_migration_audit (
                migration_name, source_path, target_path,
                source_audio_rows, inserted_audio_rows, skipped_audio_rows,
                source_viewed_rows, inserted_viewed_rows,
                source_shazam_rows, inserted_shazam_rows,
                checksum, dry_run, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                MIGRATION_NAME,
                str(SOURCE_DB),
                str(TARGET_DB),
                summary["source_audio_rows"],
                summary["insert_audio_rows"],
                summary["skip_audio_rows"],
                summary["source_viewed_rows"],
                summary["insert_viewed_rows"],
                summary["source_shazam_rows"],
                summary["insert_shazam_rows"],
                summary["checksum"],
                now,
            ),
        )


def main():
    global SOURCE_DB, TARGET_DB

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print migration plan without writing.")
    parser.add_argument("--apply", action="store_true", help="Apply migration and create backups.")
    parser.add_argument("--source", type=Path, default=SOURCE_DB)
    parser.add_argument("--target", type=Path, default=TARGET_DB)
    args = parser.parse_args()

    SOURCE_DB = args.source
    TARGET_DB = args.target

    if args.dry_run == args.apply:
        raise SystemExit("Choose exactly one: --dry-run or --apply")
    if not SOURCE_DB.exists():
        raise SystemExit(f"Source DB not found: {SOURCE_DB}")
    if not TARGET_DB.exists():
        raise SystemExit(f"Target DB not found: {TARGET_DB}")

    with connect(SOURCE_DB) as source, connect(TARGET_DB) as target:
        if not table_exists(source, "audiorecord"):
            raise SystemExit(f"Source DB has no audiorecord table: {SOURCE_DB}")
        ensure_schema(target)
        summary = build_plan(source, target)
        print("Migration plan")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"target_backup: {backup(TARGET_DB, stamp)}")
            print(f"source_backup: {backup(SOURCE_DB, stamp)}")
            migrate(source, target, summary)
            print("migration_applied: true")
            print(f"target_audio_rows_after: {count_rows(target, 'audio_history')}")
            fyp_rows = target.execute(
                "SELECT COUNT(*) FROM audio_history WHERE source_type='fyp'"
            ).fetchone()[0]
            print(f"target_fyp_rows_after: {fyp_rows}")
        else:
            print("migration_applied: false")


if __name__ == "__main__":
    main()

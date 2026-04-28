import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from migrate_fyp_to_canonical_db import (
    SOURCE_DB,
    TARGET_DB,
    backup,
    build_plan,
    connect,
    count_rows,
    ensure_schema,
    migrate,
    table_exists,
)


REQUIRED_AUDIO_COLUMNS = {
    "audio_id",
    "audio_name",
    "duration",
    "usage_count",
    "audio_url",
    "audio_page_url",
    "video_url",
    "keyword",
    "status",
    "reason",
    "file_path",
    "date_added",
    "is_speech",
    "ai_score",
    "speech_ratio",
    "video_views",
    "video_likes",
    "create_time",
    "source_type",
    "author_username",
    "trend_tag",
    "trend_score",
    "trend_velocity",
    "music_ratio",
    "original_year",
    "recent_usage",
    "source_db",
    "source_status",
    "scan_session_id",
    "scan_creator",
    "phase",
}

REQUIRED_TABLES = {
    "audio_history",
    "target_users",
    "audio_usage_history",
    "viewed_videos",
    "shazam_cache",
    "db_migration_audit",
}


def ensure_runtime_tables(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS target_users (
            username TEXT PRIMARY KEY,
            is_crawled BOOLEAN DEFAULT 0,
            date_added TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audio_id TEXT NOT NULL,
            usage_count INTEGER NOT NULL,
            checked_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_history_audio "
        "ON audio_usage_history(audio_id, checked_at DESC)"
    )


def integrity_check(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA integrity_check").fetchone()[0]


def validate_schema(conn: sqlite3.Connection) -> list[str]:
    problems = []
    tables = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing_tables = sorted(REQUIRED_TABLES - tables)
    if missing_tables:
        problems.append(f"missing_tables={','.join(missing_tables)}")

    audio_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(audio_history)")
    } if "audio_history" in tables else set()
    missing_columns = sorted(REQUIRED_AUDIO_COLUMNS - audio_columns)
    if missing_columns:
        problems.append(f"missing_audio_columns={','.join(missing_columns)}")
    return problems


def setup_database(migrate_legacy: bool = True, backup_before_migrate: bool = True) -> dict:
    TARGET_DB.parent.mkdir(parents=True, exist_ok=True)
    if not TARGET_DB.exists():
        TARGET_DB.touch()

    with connect(TARGET_DB) as target:
        ensure_schema(target)
        ensure_runtime_tables(target)
        target.commit()

    migration_summary = None
    backups = []
    if migrate_legacy and SOURCE_DB.exists():
        with connect(SOURCE_DB) as source, connect(TARGET_DB) as target:
            if table_exists(source, "audiorecord"):
                ensure_schema(target)
                ensure_runtime_tables(target)
                migration_summary = build_plan(source, target)
                has_new_rows = (
                    migration_summary["insert_audio_rows"] > 0
                    or migration_summary["insert_viewed_rows"] > 0
                    or migration_summary["insert_shazam_rows"] > 0
                )
                if has_new_rows:
                    if backup_before_migrate:
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        backups.append(str(backup(TARGET_DB, stamp)))
                        backups.append(str(backup(SOURCE_DB, stamp)))
                    migrate(source, target, migration_summary)

    with connect(TARGET_DB) as target:
        ensure_schema(target)
        ensure_runtime_tables(target)
        target.commit()
        schema_problems = validate_schema(target)
        status_counts = target.execute(
            "SELECT COALESCE(status, ''), COUNT(*) FROM audio_history GROUP BY status ORDER BY status"
        ).fetchall()
        source_counts = target.execute(
            "SELECT COALESCE(source_db, ''), COUNT(*) FROM audio_history GROUP BY source_db ORDER BY COUNT(*) DESC"
        ).fetchall()
        fyp_status_counts = target.execute(
            """
            SELECT COALESCE(status, ''), COUNT(*)
            FROM audio_history
            WHERE source_type='fyp'
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        report = {
            "target_db": str(TARGET_DB),
            "source_db": str(SOURCE_DB),
            "integrity": integrity_check(target),
            "schema_ok": not schema_problems,
            "schema_problems": schema_problems,
            "audio_history_total": count_rows(target, "audio_history"),
            "target_users_total": count_rows(target, "target_users"),
            "audio_usage_history_total": count_rows(target, "audio_usage_history"),
            "viewed_videos_total": count_rows(target, "viewed_videos"),
            "shazam_cache_total": count_rows(target, "shazam_cache"),
            "db_migration_audit_total": count_rows(target, "db_migration_audit"),
            "status_counts": [(row[0], row[1]) for row in status_counts],
            "source_db_counts": [(row[0], row[1]) for row in source_counts],
            "fyp_status_counts": [(row[0], row[1]) for row in fyp_status_counts],
            "legacy_migration_plan": migration_summary,
            "backups_created": backups,
        }
    return report


def print_report(report: dict):
    print("Database setup report")
    for key in [
        "target_db",
        "source_db",
        "integrity",
        "schema_ok",
        "audio_history_total",
        "target_users_total",
        "audio_usage_history_total",
        "viewed_videos_total",
        "shazam_cache_total",
        "db_migration_audit_total",
    ]:
        print(f"{key}: {report[key]}")
    print(f"status_counts: {report['status_counts']}")
    print(f"source_db_counts: {report['source_db_counts']}")
    print(f"fyp_status_counts: {report['fyp_status_counts']}")
    if report["legacy_migration_plan"] is not None:
        print(f"legacy_migration_plan: {report['legacy_migration_plan']}")
    if report["backups_created"]:
        print(f"backups_created: {report['backups_created']}")
    if report["schema_problems"]:
        print(f"schema_problems: {report['schema_problems']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-migrate-legacy",
        action="store_true",
        help="Only initialize/verify canonical schema; do not import legacy FYP DB.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create backups before importing any remaining legacy rows.",
    )
    args = parser.parse_args()

    report = setup_database(
        migrate_legacy=not args.no_migrate_legacy,
        backup_before_migrate=not args.no_backup,
    )
    print_report(report)

    if report["integrity"] != "ok" or not report["schema_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

import pandas as pd
from loguru import logger
from database import get_accepted_results
from config import RESULTS_FILE

# Các cột hiển thị trong file output (theo thứ tự ưu tiên)
OUTPUT_COLUMNS = [
    "audio_name",
    "duration",
    "usage_count",
    "speech_ratio",
    "video_views",
    "video_likes",
    "ai_score",
    "audio_page_url",
    "keyword",
    "date_added",
]

async def export_to_csv():
    """Xuất ONLY các audio hợp lệ (status=accepted) sang CSV, sort theo ai_score"""
    logger.info("Exporting accepted results to CSV...")
    results = await get_accepted_results()

    if not results:
        logger.warning("No accepted results to export.")
        return

    try:
        df = pd.DataFrame(results)

        # Chỉ giữ các cột cần thiết
        existing_cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
        df = df[existing_cols]

        # Format đẹp hơn
        if "speech_ratio" in df.columns:
            df["speech_ratio"] = df["speech_ratio"].map(lambda x: f"{x:.0%}" if x else "N/A")
        if "video_views" in df.columns:
            df["video_views"] = df["video_views"].map(lambda x: f"{x:,}" if x else "N/A")
        if "video_likes" in df.columns:
            df["video_likes"] = df["video_likes"].map(lambda x: f"{x:,}" if x else "N/A")

        # Sort theo ai_score cao nhất
        if "ai_score" in df.columns:
            df = df.sort_values("ai_score", ascending=False)

        df.to_csv(RESULTS_FILE, index=False, encoding='utf-8-sig')
        logger.success(f"✅ Exported {len(df)} audio(s) to {RESULTS_FILE}")

        # In preview top 5
        for _, row in df.head(5).iterrows():
            logger.info(f"  [{row.get('ai_score', '?')}đ] {row.get('audio_name')} | {row.get('usage_count')} dùng | {row.get('audio_page_url', '')[:60]}")

    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")

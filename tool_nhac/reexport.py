import asyncio
import sys
import urllib.parse
sys.path.insert(0, '.')
from database import DB_PATH
import aiosqlite

async def fix_and_export():
    # 1. Fix audio_page_url cho các record cũ trong DB
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT audio_id, audio_name FROM audio_history WHERE status='accepted'"
        ) as cursor:
            rows = await cursor.fetchall()

        for audio_id, audio_name in rows:
            page_url = f"https://www.tiktok.com/music/{urllib.parse.quote(audio_name)}-{audio_id}"
            await db.execute(
                "UPDATE audio_history SET audio_page_url = ? WHERE audio_id = ? AND (audio_page_url IS NULL OR audio_page_url = '')",
                (page_url, audio_id)
            )
        await db.commit()
        print(f"Fixed {len(rows)} audio_page_url(s)")

    # 2. Export CSV sạch
    import pandas as pd
    from config import RESULTS_FILE

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT audio_id, audio_name, duration, usage_count, audio_page_url, keyword, is_speech, ai_score, date_added FROM audio_history WHERE status='accepted' ORDER BY usage_count DESC"
        ) as cursor:
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()

    if not rows:
        print("Khong co audio accepted nao!")
        return

    df = {col: [] for col in columns}
    for row in rows:
        for col, val in zip(columns, row):
            df[col].append(val)

    import pandas as pd
    df = pd.DataFrame(df)
    
    # Overwrite file (neu bi locked thi bao loi)
    out_path = str(RESULTS_FILE)
    try:
        df.to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"Xuat thanh cong {len(df)} audio vao {out_path}")
        print()
        print("Danh sach audio hop le:")
        for _, row in df.iterrows():
            print(f"  [{row['usage_count']:,} uses] {row['audio_name']}")
            print(f"    -> {row['audio_page_url']}")
    except PermissionError:
        print("LOI: File dang bi mo o chuong trinh khac (Excel/VSCode).")
        print("Hay dong file results.csv roi thu lai!")

asyncio.run(fix_and_export())

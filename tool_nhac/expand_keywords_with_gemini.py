import asyncio
import os
from loguru import logger
from dotenv import load_dotenv
from ai.multi_ai_manager import MultiAIManager
from config import KEYWORDS_FILE

load_dotenv()

async def smart_expand_keywords():
    ai_manager = MultiAIManager()
    
    # Kiểm tra nếu ít nhất 1 cái có key
    if not ai_manager.gemini.api_key and not ai_manager.groq.api_key:
        logger.error("❌ Chưa cấu hình GEMINI_API_KEY hoặc GROQ_API_KEY trong file .env")
        return

    # 1. Chọn ngẫu nhiên 1 keyword
    base_keyword = ai_manager.get_random_keyword_from_file(KEYWORDS_FILE)
    if not base_keyword:
        logger.error("❌ Không tìm thấy keyword nào trong file keywords.txt")
        return

    logger.info(f"🔍 Keyword gốc được chọn: '{base_keyword}'")
    logger.info("🤖 Đang hỏi AI để tối ưu hóa...")

    # 2. Hỏi AI (Tự động chọn Gemini hoặc Groq)
    new_keywords = await ai_manager.expand_keyword(base_keyword)
    
    if not new_keywords:
        logger.warning("⚠️ AI không trả về keyword nào mới.")
        return

    # 3. Hiển thị và ghi vào file
    logger.success(f"✨ AI đề xuất {len(new_keywords)} keyword mới:")
    for kw in new_keywords:
        print(f"   - {kw}")

    confirm = input("\nBạn có muốn lưu các keyword này vào keywords.txt không? (y/n): ")
    if confirm.lower() == 'y':
        with open(KEYWORDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n\n# --- CÁC TỪ KHÓA ĐƯỢC AI ĐỀ XUẤT (Dựa trên: {base_keyword}) ---\n")
            for kw in new_keywords:
                f.write(f"{kw}\n")
        logger.success("✅ Đã lưu vào keywords.txt")
    else:
        logger.info("Operation cancelled.")

if __name__ == "__main__":
    asyncio.run(smart_expand_keywords())

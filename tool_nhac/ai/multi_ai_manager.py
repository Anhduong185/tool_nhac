import os
from loguru import logger
from typing import List, Optional
from .gemini_expander import GeminiExpander
from .groq_expander import GroqExpander

class MultiAIManager:
    def __init__(self):
        self.mode = os.getenv("AI_MODE", "auto").lower()
        self.gemini = GeminiExpander()
        self.groq = GroqExpander()
        
        # Biến trạng thái để luân phiên (nếu cần)
        self._last_used = "groq" # Ưu tiên Groq vì Quota lớn hơn

    async def expand_keyword(self, base_keyword: str) -> List[str]:
        """Tự động chọn model AI để mở rộng keyword dựa trên cấu hình hoặc tình trạng lỗi."""
        
        # 1. Nếu cấu hình ép buộc dùng 1 loại
        if self.mode == "gemini":
            return await self.gemini.expand_keyword(base_keyword)
        if self.mode == "groq":
            return await self.groq.expand_keyword(base_keyword)
            
        # 2. Chế độ Auto/Luân phiên (Mặc định ưu tiên Groq -> Gemini fallback)
        logger.info(f"Đang sử dụng chế độ AI: {self.mode.upper()}")
        
        # Thử Groq trước vì quota free của Groq rất lớn
        keywords = await self.groq.expand_keyword(base_keyword)
        if keywords:
            return keywords
            
        # Nếu Groq lỗi (hết quota hoặc lỗi mạng), thử Gemini
        logger.warning("Groq không khả dụng, đang thử chuyển sang Gemini...")
        keywords = await self.gemini.expand_keyword(base_keyword)
        return keywords

    def get_random_keyword_from_file(self, file_path: str) -> Optional[str]:
        # Cả hai đều có hàm này giống nhau, lấy cái nào cũng được
        return self.groq.get_random_keyword_from_file(file_path)

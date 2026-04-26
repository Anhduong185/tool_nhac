import os
import random
from loguru import logger
import google.generativeai as genai
from typing import List, Optional
from dotenv import load_dotenv

class GeminiExpander:
    def __init__(self, api_key: Optional[str] = None):
        load_dotenv(override=True)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            # Loại bỏ khoảng trắng hoặc dấu ngoặc kép thừa nếu có
            self.api_key = self.api_key.strip().strip('"').strip("'")
            logger.info(f"Khởi tạo Gemini với Key: {self.api_key[:4]}...{self.api_key[-4:]}")
            
            try:
                genai.configure(api_key=self.api_key)
                # Sử dụng model gemini-pro-latest (ổn định và ít bị giới hạn Quota hơn)
                self.model = genai.GenerativeModel('gemini-flash-latest')
                logger.info("Đã cấu hình Gemini Pro thành công.")
            except Exception as e:
                logger.error(f"Lỗi khi cấu hình Gemini: {e}")
                self.model = None
        else:
            self.model = None
            logger.warning("GEMINI_API_KEY không được tìm thấy trong môi trường.")

    async def expand_keyword(self, base_keyword: str) -> List[str]:
        """Sử dụng Gemini để đề xuất các từ khóa tối ưu hơn dựa trên từ khóa gốc."""
        if not self.model:
            return []

        prompt = f"""
        Bạn là một chuyên gia Data Mining TikTok. Tôi đang tìm kiếm các video viral mang tính chất tâm sự, kể chuyện (storytime), talkshow, hài hước độc thoại (speech-heavy content) trên TikTok.
        Từ khóa mồi của tôi là: "{base_keyword}"
        
        Hãy tạo ra 5-10 từ khóa cực kỳ phổ biến và viral tại các thị trường sau:
        - Châu Phi (Nigeria, Kenya - Tiếng Anh bản địa hoặc tiếng địa phương phổ biến như Pidgin)
        - Nga (Russian)
        - Đức (German)
        - Thái Lan (Thai)
        - Indonesia (Indonesian)

        Yêu cầu:
        1. Từ khóa phải là ngôn ngữ ĐỊA PHƯƠNG của các quốc gia đó (không dịch sang tiếng Việt hay tiếng Anh nếu nước đó dùng ngôn ngữ khác).
        2. Tập trung vào các chủ đề: chuyện tâm linh, chuyện mẹ chồng nàng dâu, kiếm tiền MMO, chuyện văn phòng, lời khuyên cuộc sống, reaction, pov.
        3. Từ khóa phải là các cụm từ mà người bản địa thường dùng để tìm kiếm nội dung (ví dụ: "cara dapat uang dari internet", "เรื่องเล่าผี", "как заработать в интернете").
        4. KHÔNG chọn các từ khóa liên quan đến âm nhạc (song, cover, remix, dj).
        5. Chỉ trả về danh sách các từ khóa, mỗi từ khóa trên một dòng, không thêm số thứ tự hay giải thích.
        """

        try:
            response = await self.model.generate_content_async(prompt)
            
            # Kiểm tra nếu có kết quả trả về
            if not response or not response.candidates:
                logger.warning(f"Gemini không trả về candidate nào cho '{base_keyword}'")
                return []
                
            keywords = [k.strip() for k in response.text.split('\n') if k.strip()]
            
            # Lọc bỏ các ký tự lạ như dấu gạch đầu dòng, số thứ tự
            clean_keywords = []
            for kw in keywords:
                # Xóa dấu -, *, 1., 2. ở đầu dòng
                clean_kw = kw.lstrip('-*•0123456789. ').strip()
                if clean_kw:
                    clean_keywords.append(clean_kw)
            
            logger.info(f"Gemini đề xuất cho '{base_keyword}': {clean_keywords}")
            return clean_keywords
        except Exception as e:
            logger.error(f"Lỗi khi gọi Gemini API: {e}")
            return []

    def get_random_keyword_from_file(self, file_path: str) -> Optional[str]:
        """Lấy ngẫu nhiên một từ khóa từ file (bỏ qua comment)."""
        try:
            if not os.path.exists(file_path):
                return None
            
            with open(file_path, "r", encoding="utf-8") as f:
                keywords = [
                    line.strip() for line in f 
                    if line.strip() and not line.strip().startswith("#")
                ]
            
            if not keywords:
                return None
            
            return random.choice(keywords)
        except Exception as e:
            logger.error(f"Lỗi khi đọc file keywords: {e}")
            return None

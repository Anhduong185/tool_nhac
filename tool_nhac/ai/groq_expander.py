import os
import random
from loguru import logger
from groq import Groq
from typing import List, Optional
from dotenv import load_dotenv

class GroqExpander:
    def __init__(self, api_key: Optional[str] = None):
        load_dotenv(override=True)
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if self.api_key:
            self.api_key = self.api_key.strip().strip('"').strip("'")
            logger.info(f"Khởi tạo Groq với Key: {self.api_key[:4]}...{self.api_key[-4:]}")
            
            try:
                self.client = Groq(api_key=self.api_key)
                # Sử dụng llama-3.3-70b-versatile (Model mới nhất, thay thế cho bản cũ đã ngừng hỗ trợ)
                self.model = "llama-3.3-70b-versatile"
                logger.info(f"Đã cấu hình Groq ({self.model}) thành công.")
            except Exception as e:
                logger.error(f"Lỗi khi cấu hình Groq: {e}")
                self.client = None
        else:
            self.client = None
            logger.warning("GROQ_API_KEY không được tìm thấy trong môi trường.")

    async def expand_keyword(self, base_keyword: str) -> List[str]:
        """Sử dụng Groq để đề xuất các từ khóa tối ưu hơn."""
        if not self.client:
            return []

        prompt = f"""
        Bạn là một chuyên gia tối ưu hóa tìm kiếm trên TikTok. 
        Tôi có một từ khóa gốc là: "{base_keyword}"
        
        Hãy đề xuất 5-10 từ khóa liên quan, tối ưu hơn, có khả năng tìm thấy các video có "Original Sound" (âm thanh gốc) mà người dùng thường dùng để kể chuyện, tâm sự, hoặc hài hước (speech-heavy content).
        
        Yêu cầu:
        1. Các từ khóa nên đa dạng (cùng ngôn ngữ với từ khóa gốc hoặc tiếng Anh/ngôn ngữ phổ biến trong ngách đó).
        2. Tập trung vào các từ khóa mang tính chất "Storytime", "POV", "Talk", "Review", "Advice".
        3. Chỉ trả về danh sách các từ khóa, mỗi từ khóa trên một dòng, không thêm số thứ tự hay giải thích.
        """

        try:
            # Groq dùng sync hoặc async. Ở đây ta dùng sync wrapper cho đơn giản hoặc async nếu cần.
            # Với Groq, việc gọi API rất nhanh nên dùng sync cũng ok trong task này.
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a TikTok SEO expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500,
            )
            
            response_text = completion.choices[0].message.content
            if not response_text:
                return []
                
            keywords = [k.strip() for k in response_text.split('\n') if k.strip()]
            
            # Lọc bỏ các ký tự lạ
            clean_keywords = []
            for kw in keywords:
                clean_kw = kw.lstrip('-*•0123456789. ').strip()
                if clean_kw:
                    clean_keywords.append(clean_kw)
            
            logger.info(f"Groq đề xuất cho '{base_keyword}': {clean_keywords}")
            return clean_keywords
        except Exception as e:
            logger.error(f"Lỗi khi gọi Groq API: {e}")
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

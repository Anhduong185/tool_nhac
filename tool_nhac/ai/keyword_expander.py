from loguru import logger

class KeywordExpander:
    def __init__(self):
        self.model = None

    def _load_model(self):
        if self.model is None:
            logger.info("Loading sentence-transformers model...")
            try:
                from sentence_transformers import SentenceTransformer
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Loading sentence-transformers trên: {device.upper()}")
                self.model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device=device)
            except ImportError:
                logger.error("Vui lòng cài đặt: pip install sentence-transformers")

    def expand_keyword(self, base_keyword: str, candidates: list[str], top_k: int = 3) -> list[str]:
        """Sinh ra các từ khóa tìm kiếm mới tương đồng mặt ngữ nghĩa."""
        try:
            from sentence_transformers import util
            
            self._load_model()
            if not self.model or not candidates:
                return []
                
            base_embedding = self.model.encode(base_keyword, convert_to_tensor=True)
            candidate_embeddings = self.model.encode(candidates, convert_to_tensor=True)
            
            cosine_scores = util.cos_sim(base_embedding, candidate_embeddings)[0]
            
            # Lấy top kết quả dựa trên độ Semantic Similarity
            top_results = []
            for i, score in enumerate(cosine_scores):
                top_results.append((score.item(), candidates[i]))
            
            top_results = sorted(top_results, key=lambda x: x[0], reverse=True)
            
            # Lọc kết quả giống nhau > 40% nhưng không phải trùng lặp 100%
            expanded = [res[1] for res in top_results if res[0] > 0.4 and res[1].lower() != base_keyword.lower()]
            return expanded[:top_k]
            
        except Exception as e:
            logger.error(f"Error expanding keyword: {e}")
            return []

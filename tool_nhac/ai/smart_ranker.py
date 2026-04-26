import math
import time
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import AudioMetadata

MIN_ACCEPTABLE_SCORE = 40

class SmartRanker:
    """Đánh giá và xếp hạng Audio theo formula tổng hợp:
    usage + engagement + speech + recency + duration
    """

    def rank(self, audios: list[AudioMetadata]) -> list[AudioMetadata]:
        now = time.time()
        for audio in audios:
            score = self._compute_score(audio, now)
            audio.ai_score = round(score, 2)

        return sorted(audios, key=lambda x: x.ai_score or 0.0, reverse=True)

    def _compute_score(self, audio: AudioMetadata, now: float) -> float:
        score = 0.0

        # 1. Usage score: log10(500)≈27, log10(5000)≈37, log10(50K)≈47
        if audio.usage_count > 0:
            score += math.log10(audio.usage_count) * 10
        else:
            score += 15  # Unknown usage → điểm trung tính

        # 2. Engagement ratio = likes / views
        engagement_ratio = 0.0
        if audio.video_views > 0 and audio.video_likes > 0:
            engagement_ratio = audio.video_likes / audio.video_views
            if engagement_ratio >= 0.05:
                score += 15   # Engagement cao → viral
            elif engagement_ratio >= 0.02:
                score += 7
            elif engagement_ratio < 0.01:
                score -= 5    # Engagement rất thấp

        # 3. Speech score dùng ratio thực tế từ Whisper
        sr = getattr(audio, 'speech_ratio', 0.0)
        if sr >= 0.90:
            score += 20
        elif sr >= 0.70:
            score += 15
        elif sr >= 0.50:
            score += 5
        else:
            score -= 10

        # 4. Recency score (video mới = có tiềm năng viral)
        if audio.create_time > 0:
            days_old = (now - audio.create_time) / 86400
            if days_old < 7:
                score += 10   # Video < 1 tuần
            elif days_old < 30:
                score += 5    # Video < 1 tháng
            elif days_old > 365:
                score -= 5    # Video > 1 năm

        # 5. Duration bonus
        if 15 <= audio.duration <= 45:
            score += 8
        elif audio.duration < 8:
            score -= 8

        return score

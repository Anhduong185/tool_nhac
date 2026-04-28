"""
speech_classifier.py — V2.1 Whisper Speech Classifier
=======================================================
Nâng cấp so với V1.0:
  - Trả về SpeechResult đầy đủ (ratio + no_speech_prob + segments count)
  - Dùng no_speech_prob đúng cách: lọc từng segment, không chỉ tổng
  - Hỗ trợ phân tích nhiều segment ngắn (từ SmartAudioTrimmer)
  - Tương thích ngược: is_mostly_speech() vẫn giữ nguyên signature
"""

import os
import sys
import threading
from dataclasses import dataclass
from loguru import logger

_whisper_lock = threading.Lock()

if sys.platform == "win32":
    import site
    packages = site.getsitepackages()
    for p in packages:
        nvidia_path = os.path.join(p, "nvidia")
        if os.path.exists(nvidia_path):
            for lib in ["cublas", "cudnn", "cuda_nvrtc"]:
                bin_path = os.path.join(nvidia_path, lib, "bin")
                if os.path.exists(bin_path):
                    try:
                        os.add_dll_directory(bin_path)
                    except Exception:
                        pass
                    os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WHISPER_MODEL_SIZE


# ── Ngưỡng V2.1 ──────────────────────────────────────────────────────────────
NO_SPEECH_PROB_THRESHOLD = 0.70   # Nới lỏng: Chỉ coi là nhạc/ồn khi chắc chắn > 70%
MIN_SEGMENT_DURATION     = 0.5    # Bỏ qua segment < 0.5s (noise flash)


# ── Result container ──────────────────────────────────────────────────────────
@dataclass
class SpeechResult:
    speech_ratio:    float   # Tỉ lệ giọng nói thực sự (0.0–1.0)
    no_speech_prob:  float   # Xác suất trung bình không phải giọng người
    total_duration:  float   # Tổng thời lượng file (giây)
    speech_duration: float   # Tổng thời lượng được xác nhận là giọng nói
    segment_count:   int     # Số segment Whisper chia ra
    speech_segments: int     # Số segment được chấp nhận là giọng nói
    language:        str     # Ngôn ngữ Whisper nhận diện được

    @property
    def is_speech(self) -> bool:
        """Kiểm tra nhanh: đây có phải file giọng nói không."""
        return self.speech_ratio >= 0.85 and self.no_speech_prob <= NO_SPEECH_PROB_THRESHOLD

    def summary(self) -> str:
        return (
            f"speech={self.speech_ratio:.0%} "
            f"no_speech_p={self.no_speech_prob:.2f} "
            f"segs={self.speech_segments}/{self.segment_count} "
            f"lang={self.language}"
        )


class SpeechClassifier:
    def __init__(self):
        self.model      = None
        self.force_cpu  = False

    # ── Load model (Lazy init, giữ trong RAM) ─────────────────────────────────
    def _load_model(self):
        if self.model is not None:
            return

        logger.info(f"Loading faster-whisper model '{WHISPER_MODEL_SIZE}' ...")

        if not self.force_cpu:
            try:
                from faster_whisper import WhisperModel
                device       = "cuda"
                compute_type = "float16"
                logger.info(f"  → GPU: {device.upper()} ({compute_type})")
                self.model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
                return
            except Exception as e:
                logger.warning(f"GPU init failed: {e} → fallback CPU")
                self.force_cpu = True

        from faster_whisper import WhisperModel
        device       = "cpu"
        compute_type = "int8"
        logger.info(f"  → CPU ({compute_type})")
        self.model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)

    def analyze(self, file_path: str) -> SpeechResult:
        """
        Phân tích file audio, trả về SpeechResult đầy đủ.
        
        Cải tiến V2.1 so với V1.0:
          - Lọc segment theo no_speech_prob từng cái (không dùng avg chung)
          - Bỏ qua segment quá ngắn (< 0.5s) — thường là noise flash
          - Tính no_speech_prob tổng hợp chính xác từ các segment bị loại
          - Vẫn dùng vad_filter=True để Whisper tự bỏ im lặng nội bộ
        """
        with _whisper_lock:
            try:
                self._load_model()
                if self.model is None:
                    return SpeechResult(0.0, 1.0, 0.0, 0.0, 0, 0, "unknown")

                segments_iter, info = self.model.transcribe(
                    file_path,
                    beam_size=1,
                    vad_filter=False,  # TẮT VAD nội bộ vì SmartAudioTrimmer đã cắt chuẩn rồi
                )

                total_duration   = info.duration or 0.0
                language         = info.language or "unknown"
                speech_duration  = 0.0
                segment_count    = 0
                speech_segments  = 0
                rejected_probs   = []   # no_speech_prob của segment bị loại

                for seg in segments_iter:
                    seg_len = seg.end - seg.start
                    if seg_len < MIN_SEGMENT_DURATION:
                        continue   # Bỏ qua segment quá ngắn

                    segment_count += 1

                    # V2.1: Lọc theo no_speech_prob từng segment
                    if seg.no_speech_prob <= NO_SPEECH_PROB_THRESHOLD:
                        # Segment được chấp nhận là giọng nói
                        speech_duration += seg_len
                        speech_segments += 1
                    else:
                        # Segment bị nghi là nhạc / im lặng / tiếng ồn
                        rejected_probs.append(seg.no_speech_prob)

                # Tính no_speech_prob tổng hợp: trung bình các segment bị loại
                # Nếu không có segment bị loại → no_speech_prob gần 0 (audio rất sạch)
                if rejected_probs:
                    avg_no_speech = sum(rejected_probs) / len(rejected_probs)
                elif segment_count == 0:
                    avg_no_speech = 1.0   # Không phát hiện gì → coi như không phải giọng
                else:
                    avg_no_speech = 0.05  # Tất cả segment đều pass → rất sạch

                speech_ratio = round(speech_duration / total_duration, 3) if total_duration > 0 else 0.0
                speech_ratio = max(0.0, min(1.0, speech_ratio)) # Giới hạn 0-100%

                result = SpeechResult(
                    speech_ratio    = speech_ratio,
                    no_speech_prob  = round(avg_no_speech, 3),
                    total_duration  = round(total_duration, 2),
                    speech_duration = round(speech_duration, 2),
                    segment_count   = segment_count,
                    speech_segments = speech_segments,
                    language        = language,
                )

                logger.debug(f"  Whisper: {result.summary()}")
                return result

            except Exception as e:
                error_msg = str(e).lower()

                # Auto-fallback GPU → CPU nếu gặp lỗi CUDA
                if any(k in error_msg for k in ["cublas", "cuda", "cudnn", "cudart"]):
                    if not self.force_cpu:
                        logger.warning(f"GPU error → auto-switch CPU: {e}")
                        self.force_cpu = True
                        self.model = None
                        return self.analyze(file_path)

                logger.error(f"SpeechClassifier error [{file_path}]: {e}")
                return SpeechResult(0.0, 1.0, 0.0, 0.0, 0, 0, "error")

    # ── Tương thích ngược với code cũ ────────────────────────────────────────
    def get_speech_ratio(self, file_path: str) -> float:
        """Legacy method — dùng trong audio_processor cũ."""
        return self.analyze(file_path).speech_ratio

    def is_mostly_speech(self, file_path: str, threshold: float = 0.75) -> tuple:
        """
        Legacy method — giữ nguyên signature để không phá code cũ.
        Trả về (is_speech: bool, speech_ratio: float).
        
        V2.1: Ngoài threshold speech_ratio, còn check thêm no_speech_prob.
        """
        result = self.analyze(file_path)
        # Đạt chuẩn nếu đủ cả 2 điều kiện
        is_speech = (
            result.speech_ratio >= threshold
            and result.no_speech_prob <= NO_SPEECH_PROB_THRESHOLD
        )
        return is_speech, result.speech_ratio

    def analyze_batch(self, file_paths: list) -> list:
        """
        Phân tích nhiều file (danh sách segment), trả về list[SpeechResult].
        Dùng trong AudioPipeline.AIAnalyzer để xử lý các đoạn cắt.
        """
        return [self.analyze(p) for p in file_paths]

    def aggregate_segments(self, results: list) -> SpeechResult:
        """
        Gộp nhiều SpeechResult của từng segment thành 1 kết quả tổng hợp.
        Dùng khi phân tích 2–3 đoạn audio khác nhau cùng lúc.
        """
        if not results:
            return SpeechResult(0.0, 1.0, 0.0, 0.0, 0, 0, "empty")

        total_dur    = sum(r.total_duration  for r in results)
        speech_dur   = sum(r.speech_duration for r in results)
        seg_count    = sum(r.segment_count   for r in results)
        speech_segs  = sum(r.speech_segments for r in results)
        avg_no_sp    = sum(r.no_speech_prob  for r in results) / len(results)

        # Lấy ngôn ngữ xuất hiện nhiều nhất
        from collections import Counter
        lang = Counter(r.language for r in results).most_common(1)[0][0]

        speech_ratio = round(speech_dur / total_dur, 3) if total_dur > 0 else 0.0

        return SpeechResult(
            speech_ratio    = speech_ratio,
            no_speech_prob  = round(avg_no_sp, 3),
            total_duration  = round(total_dur, 2),
            speech_duration = round(speech_dur, 2),
            segment_count   = seg_count,
            speech_segments = speech_segs,
            language        = lang,
        )

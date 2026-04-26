"""
yamnet_classifier.py — V2.1 YAMNet Music/Speech Detector
==========================================================
Module phân loại âm thanh sử dụng Google YAMNet.
Chỉ được gọi từ AIAnalyzer khi Whisper trả về vùng xám (0.40–0.65).

Ưu điểm của YAMNet:
  - Nhận diện được nhạc nền lofi, nhạc không bản quyền mà Shazam bỏ sót
  - Model cực nhẹ (~3MB), load nhanh, chạy trên CPU bình thường
  - Output: xác suất cho 521 class âm thanh (Music, Speech, Noise, ...)

Cài đặt:
  pip install tensorflow tensorflow-hub soundfile
"""

import os
import sys
import numpy as np
from dataclasses import dataclass
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── YAMNet Class indices (trong bộ 521 class AudioSet) ───────────────────────
# Nguồn: https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/yamnet_class_map.csv
YAMNET_MUSIC_CLASSES = [
    # 137: Music (tổng quát)
    137,
    # 138–180: Các loại nhạc cụ và thể loại
    138, 139, 140, 141, 142, 143, 144, 145,
    # 15–17: Nhạc pop, rock, hip-hop
    15, 16, 17,
]
YAMNET_SPEECH_CLASSES = [
    0,    # Speech
    1,    # Male speech, man speaking
    2,    # Female speech, woman speaking
    3,    # Child speech, kid speaking
    4,    # Conversation
    5,    # Narration, monologue
    6,    # Babbling
]
YAMNET_SILENCE_CLASSES = [
    494,  # Silence
    495,  # Noise
]


@dataclass
class YAMNetResult:
    music_ratio:   float   # 0.0–1.0 — tỉ lệ frame phân loại là Music
    speech_ratio:  float   # 0.0–1.0 — tỉ lệ frame phân loại là Speech
    top_class:     str     # Tên class chiếm ưu thế
    top_score:     float   # Score của class chiếm ưu thế
    is_music:      bool    # music_ratio > ngưỡng
    is_speech:     bool    # speech_ratio > ngưỡng
    frame_count:   int     # Số frame phân tích

    def summary(self) -> str:
        return (
            f"music={self.music_ratio:.0%} "
            f"speech={self.speech_ratio:.0%} "
            f"top={self.top_class}({self.top_score:.2f})"
        )


class YAMNetClassifier:
    """
    Wrapper cho Google YAMNet model từ TensorFlow Hub.
    Load model một lần, giữ trong memory để tái sử dụng.
    """

    MUSIC_THRESHOLD  = 0.30   # music_ratio > 0.30 → coi là nhạc
    SPEECH_THRESHOLD = 0.40   # speech_ratio > 0.40 → coi là giọng nói

    def __init__(self):
        self._model       = None
        self._class_names = None
        self._available   = None   # None = chưa check; True/False = đã biết

    def is_available(self) -> bool:
        """Kiểm tra xem tensorflow-hub đã cài chưa."""
        if self._available is not None:
            return self._available
        try:
            import tensorflow_hub  # noqa
            import tensorflow      # noqa
            import soundfile       # noqa
            self._available = True
        except ImportError:
            logger.warning("YAMNet không khả dụng: thiếu tensorflow-hub hoặc soundfile. Bỏ qua YAMNet.")
            self._available = False
        return self._available

    def _load(self):
        """Load YAMNet model và class map (chỉ 1 lần)."""
        if self._model is not None:
            return
        if not self.is_available():
            return

        try:
            import tensorflow_hub as hub
            logger.info("Loading YAMNet from TensorFlow Hub ...")
            self._model = hub.load("https://tfhub.dev/google/yamnet/1")

            # Load class map
            import csv, urllib.request
            class_map_url = (
                "https://raw.githubusercontent.com/tensorflow/models/master/"
                "research/audioset/yamnet/yamnet_class_map.csv"
            )
            local_map = os.path.join(os.path.dirname(__file__), "yamnet_class_map.csv")

            if not os.path.exists(local_map):
                logger.info("  Downloading YAMNet class map ...")
                try:
                    urllib.request.urlretrieve(class_map_url, local_map)
                except Exception:
                    pass

            self._class_names = {}
            if os.path.exists(local_map):
                with open(local_map, newline='', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader)  # skip header
                    for row in reader:
                        if len(row) >= 3:
                            self._class_names[int(row[0])] = row[2]

            logger.info("✅ YAMNet loaded.")
        except Exception as e:
            logger.warning(f"YAMNet load failed: {e}")
            self._model = None

    def _read_audio(self, wav_path: str) -> tuple:
        """Đọc file WAV, resample về 16kHz nếu cần."""
        import soundfile as sf
        audio, sr = sf.read(wav_path, dtype='float32')

        # Convert stereo → mono
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        # Resample về 16kHz nếu cần (YAMNet yêu cầu)
        if sr != 16000:
            try:
                import resampy
                audio = resampy.resample(audio, sr, 16000)
            except ImportError:
                # Fallback: scipy
                try:
                    from scipy.signal import resample
                    n_samples = int(len(audio) * 16000 / sr)
                    audio = resample(audio, n_samples)
                except ImportError:
                    logger.warning("Không thể resample (thiếu resampy/scipy). YAMNet dùng raw audio.")

        return audio.astype(np.float32), 16000

    def classify(self, wav_path: str) -> YAMNetResult:
        """
        Phân loại 1 file WAV.
        Returns: YAMNetResult với music_ratio, speech_ratio, top_class.
        """
        if not self.is_available():
            return self._fallback_result()

        self._load()
        if self._model is None:
            return self._fallback_result()

        try:
            import tensorflow as tf
            audio, _ = self._read_audio(wav_path)

            # YAMNet yêu cầu tensor 1D
            waveform = tf.constant(audio, dtype=tf.float32)
            scores, embeddings, spectrogram = self._model(waveform)

            scores_np = scores.numpy()   # shape: (num_frames, 521)
            frame_count = scores_np.shape[0]

            if frame_count == 0:
                return self._fallback_result()

            # Tính ratio cho từng nhóm class
            # Mỗi frame: lấy class có score cao nhất
            top_class_per_frame = scores_np.argmax(axis=1)   # shape: (num_frames,)

            music_frames  = sum(1 for c in top_class_per_frame if c in YAMNET_MUSIC_CLASSES)
            speech_frames = sum(1 for c in top_class_per_frame if c in YAMNET_SPEECH_CLASSES)

            music_ratio  = round(music_frames  / frame_count, 3)
            speech_ratio = round(speech_frames / frame_count, 3)

            # Top class tổng hợp (theo mean score)
            mean_scores = scores_np.mean(axis=0)
            top_idx     = int(mean_scores.argmax())
            top_name    = self._class_names.get(top_idx, f"class_{top_idx}") if self._class_names else f"class_{top_idx}"
            top_score   = round(float(mean_scores[top_idx]), 3)

            result = YAMNetResult(
                music_ratio  = music_ratio,
                speech_ratio = speech_ratio,
                top_class    = top_name,
                top_score    = top_score,
                is_music     = music_ratio  > self.MUSIC_THRESHOLD,
                is_speech    = speech_ratio > self.SPEECH_THRESHOLD,
                frame_count  = frame_count,
            )

            logger.debug(f"  YAMNet: {result.summary()}")
            return result

        except Exception as e:
            logger.warning(f"YAMNet classify error [{wav_path}]: {e}")
            return self._fallback_result()

    def classify_segments(self, wav_paths: list) -> YAMNetResult:
        """Phân loại nhiều segment, gộp kết quả trung bình."""
        results = [self.classify(p) for p in wav_paths if os.path.exists(p)]
        if not results:
            return self._fallback_result()

        avg_music  = sum(r.music_ratio  for r in results) / len(results)
        avg_speech = sum(r.speech_ratio for r in results) / len(results)
        total_frames = sum(r.frame_count for r in results)

        # Top class từ result có score cao nhất
        best = max(results, key=lambda r: r.top_score)

        return YAMNetResult(
            music_ratio  = round(avg_music, 3),
            speech_ratio = round(avg_speech, 3),
            top_class    = best.top_class,
            top_score    = best.top_score,
            is_music     = avg_music  > self.MUSIC_THRESHOLD,
            is_speech    = avg_speech > self.SPEECH_THRESHOLD,
            frame_count  = total_frames,
        )

    def _fallback_result(self) -> YAMNetResult:
        """Trả về kết quả trung lập khi YAMNet không chạy được."""
        return YAMNetResult(
            music_ratio=0.0, speech_ratio=0.0,
            top_class="unknown", top_score=0.0,
            is_music=False, is_speech=False,
            frame_count=0,
        )


# ── Singleton để giữ model trong RAM ─────────────────────────────────────────
_yamnet_instance: YAMNetClassifier = None

def get_yamnet() -> YAMNetClassifier:
    global _yamnet_instance
    if _yamnet_instance is None:
        _yamnet_instance = YAMNetClassifier()
    return _yamnet_instance

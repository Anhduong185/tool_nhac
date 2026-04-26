"""
audio_pipeline.py — V2.1 Core Audio Pipeline (Fail-Fast)
=========================================================
Module duy nhất xử lý toàn bộ luồng từ audio_id → kết quả cuối cùng.

Thứ tự Fail-Fast:
  [1] DEDUP              → Drop ngay nếu đã có trong DB
  [2] METADATA FILTER    → Drop nếu duration / CDN / blacklist
  [3] USAGE CHECK        → Drop nếu < ngưỡng (TRƯỚC KHI tải file)
  [4] TREND ANALYSIS     → Tính trend_score, tag, gắn priority
  [5] DOWNLOAD + TRIM    → Tải audio, cắt thông minh 2–3 đoạn theo VAD
  [6] VAD                → Drop nếu không có giọng người
  [7] AI ANALYSIS        → Whisper + YAMNet conditional
  [8] FILTER FINAL       → speech_ratio, music_ratio, no_speech_prob
  [9] SCORING            → audio_score tổng hợp
  [10] SAVE              → Lưu DB + snapshot usage
  [11] EXPAND            → Thêm creator mới (qua filter chặt)
"""

import asyncio
import os
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass
from loguru import logger

from models import AudioMetadata
from config import AUDIOS_DIR, MIN_USAGE_COUNT, MAX_DURATION
from filter import is_valid_audio, get_dynamic_min_usage, compute_quality_score
from database import (
    check_duplicate, insert_audio, snapshot_usage,
    get_usage_velocity, add_target_user
)

ROOT_DIR = Path(__file__).resolve().parent

# ── Cấu hình ngưỡng AI V2.1 ──────────────────────────────────────────────────
SPEECH_RATIO_PASS   = 0.85   # > 0.85 → PASS trực tiếp (skip YAMNet)
SPEECH_RATIO_DROP   = 0.55   # < 0.55 → DROP trực tiếp (skip YAMNet)
NO_SPEECH_PROB_MAX  = 0.40   # no_speech_prob > 0.40 → coi là nhạc
MUSIC_RATIO_MAX     = 0.30   # music_ratio > 0.30 → DROP (YAMNet)
MIN_AUDIO_SCORE     = 10.0   # Hạ ngưỡng để không loại oan các audio speech tốt nhưng chưa trending

# ── Ngưỡng Usage theo trend ───────────────────────────────────────────────────
USAGE_EARLY_TREND   = 300    # Early trend dùng ngưỡng thấp hơn

# ── Creator Expansion rule ────────────────────────────────────────────────────
EXPAND_MIN_APPEARANCES = 2   # Creator phải xuất hiện >= 2 lần trong hệ thống


# ── Result container ──────────────────────────────────────────────────────────
@dataclass
class PipelineResult:
    audio: AudioMetadata
    passed: bool
    stage: str          # Bước cuối cùng xử lý
    reason: str
    audio_score: float = 0.0
    speech_ratio: float = 0.0
    music_ratio: float = 0.0
    trend_tag: str = "NORMAL"
    trend_score: float = 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize(value: float, max_val: float) -> float:
    """Chuẩn hoá về thang 0–1."""
    return min(value / max_val, 1.0) if max_val > 0 else 0.0


def compute_audio_score(
    usage_count: int,
    trend_score: float,
    speech_ratio: float,
    music_ratio: float,
    velocity_per_hour: float = 0.0,
) -> float:
    """
    Công thức audio_score V2.1 (Cân bằng lại):
      speech_score   * 0.50 (Quan trọng nhất)
    + usage_score    * 0.20
    + (trend_score*100) * 0.20
    + velocity_score * 0.10
    - music_penalty
    """
    usage_score    = _normalize(math.log10(max(usage_count, 1)) * 10, 50) * 100
    speech_score   = speech_ratio * 100
    velocity_score = _normalize(velocity_per_hour, 100) * 100
    music_penalty  = music_ratio * 50

    raw = (
        speech_score   * 0.50
      + (trend_score * 100) * 0.20
      + usage_score    * 0.20
      + velocity_score * 0.10
      - music_penalty
    )
    return round(max(raw, 0.0), 2)


# ── Module cắt audio thông minh ───────────────────────────────────────────────
class SmartAudioTrimmer:
    """
    Kết hợp VAD + ffmpeg để cắt 2–3 đoạn tập trung vào vùng có giọng người.
    Không cắt cứng theo giây — cắt đúng nơi VAD phát hiện được tiếng nói.
    """

    def __init__(self):
        self._vad_model = None
        self._vad_utils = None

    def _load_vad(self):
        if self._vad_model is None:
            try:
                import torch
                self._vad_model, self._vad_utils = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    force_reload=False,
                    onnx=False,
                    trust_repo=True,
                )
                logger.info("✅ Silero VAD loaded.")
            except Exception as e:
                logger.warning(f"Silero VAD load failed: {e} — sẽ dùng fallback cắt theo thời lượng.")

    def _get_speech_timestamps(self, wav_path: str) -> list:
        """Trả về list [{start, end}] các vùng có giọng người (giây)."""
        if self._vad_model is None:
            return []
        try:
            import torch
            get_speech_timestamps, _, read_audio, *_ = self._vad_utils
            
            # Thử dùng read_audio mặc định của Silero
            try:
                wav = read_audio(wav_path, sampling_rate=16000)
                logger.debug("VAD: Using torchaudio + torchcodec")
            except Exception as e:
                # Nếu fail (lỗi torchaudio/torchcodec), dùng pydub để load thay thế
                logger.debug(f"Silero read_audio failed, using pydub fallback: {e}")
                from pydub import AudioSegment
                import numpy as np
                audio = AudioSegment.from_file(wav_path).set_frame_rate(16000).set_channels(1)
                samples = np.array(audio.get_array_of_samples()).astype(np.float32)
                # Chuẩn hoá về khoảng [-1.0, 1.0]
                wav = torch.from_numpy(samples) / (2**15)

            timestamps = get_speech_timestamps(wav, self._vad_model, sampling_rate=16000)
            # Chuyển từ sample → giây
            return [{"start": t["start"] / 16000, "end": t["end"] / 16000} for t in timestamps]
        except Exception as e:
            logger.error(f"VAD timestamps critical error: {e}")
            return []

    def _fallback_segments(self, duration: float) -> list:
        """Cắt dự phòng khi VAD không hoạt động, theo thời lượng."""
        if duration <= 20:
            return [(0, duration)]
        elif duration <= 40:
            mid1 = duration * 0.33
            mid2 = duration * 0.66
            return [(mid1, min(mid1 + 5, duration)), (mid2, min(mid2 + 5, duration))]
        else:  # 40–59s
            return [
                (8, 13),
                (duration * 0.5, duration * 0.5 + 5),
                (max(duration - 13, 0), max(duration - 8, 0)),
            ]

    def _pick_segments_from_vad(self, timestamps: list, duration: float, n_segments: int = 3) -> list:
        """Chọn N đoạn từ kết quả VAD, phân bố đều theo timeline."""
        if not timestamps:
            return self._fallback_segments(duration)

        # Gộp các đoạn VAD liên tiếp
        merged = [timestamps[0].copy()]
        for ts in timestamps[1:]:
            if ts["start"] - merged[-1]["end"] < 0.5:
                merged[-1]["end"] = ts["end"]
            else:
                merged.append(ts.copy())

        # Lọc đoạn quá ngắn (< 2s)
        merged = [m for m in merged if m["end"] - m["start"] >= 2.0]
        if not merged:
            return self._fallback_segments(duration)

        # Chọn N đoạn phân bố đều
        if len(merged) <= n_segments:
            picks = merged
        else:
            step = len(merged) / n_segments
            picks = [merged[int(i * step)] for i in range(n_segments)]

        # Cắt mỗi đoạn lấy 5s bắt đầu từ điểm giữa
        segments = []
        for p in picks:
            mid = (p["start"] + p["end"]) / 2
            start = max(mid - 2.5, p["start"])
            end   = min(start + 5.0, p["end"], duration)
            if end - start >= 1.5:
                segments.append((round(start, 2), round(end, 2)))

        return segments if segments else self._fallback_segments(duration)

    def trim(self, src_path: str, duration: float) -> Tuple[list, bool]:
        """
        Main entry: chạy VAD rồi cắt file audio.
        Returns:
            (segment_paths: list[str], has_speech: bool)
        """
        self._load_vad()

        # Chuyển về WAV mono 16kHz để VAD chạy chuẩn nhất
        wav_path = src_path.replace(".mp3", "_vad.wav")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", wav_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"ffmpeg wav convert failed: {e}")
            wav_path = src_path

        # Xác định số đoạn cần cắt
        if duration <= 20:
            n_segs = 1
        elif duration <= 40:
            n_segs = 2
        else:
            n_segs = 3

        timestamps = self._get_speech_timestamps(wav_path)
        has_speech = bool(timestamps)

        if not has_speech and self._vad_model is not None:
            # VAD load được nhưng không tìm thấy giọng → DROP
            _rm(wav_path)
            return [], False

        segments = self._pick_segments_from_vad(timestamps, duration, n_segs)

        # Cắt từng đoạn bằng ffmpeg
        segment_paths = []
        for i, (start, end) in enumerate(segments):
            out_path = src_path.replace(".mp3", f"_seg{i}.wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", wav_path,
                     "-ss", str(start), "-to", str(end),
                     "-ac", "1", "-ar", "16000", out_path],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                    segment_paths.append(out_path)
            except Exception as e:
                logger.debug(f"Segment {i} trim failed: {e}")

        _rm(wav_path)
        return segment_paths, True


# ── AI Analyzer ───────────────────────────────────────────────────────────────
class AIAnalyzer:
    """Whisper + YAMNet V2.1 với logic conditional (skip YAMNet khi không cần)."""

    def __init__(self):
        self._whisper = None
        self._yamnet  = None

    def _load_whisper(self):
        if self._whisper is None:
            from ai.speech_classifier import SpeechClassifier
            self._whisper = SpeechClassifier()
            self._whisper._load_model()

    def _load_yamnet(self):
        if self._yamnet is None:
            from ai.yamnet_classifier import get_yamnet
            self._yamnet = get_yamnet()

    def analyze_segments(self, segment_paths: list) -> dict:
        """
        Phân tích nhiều đoạn audio, trả về kết quả tổng hợp.
        Áp dụng Conditional YAMNet: chỉ chạy nếu speech_ratio trong vùng xám.
        """
        self._load_whisper()

        # ── Bước 1: Chạy Whisper cho tất cả segment ──────────────────────
        raw_results = self._whisper.analyze_batch(segment_paths)
        if not raw_results:
            return {"speech_ratio": 0.0, "no_speech_prob": 1.0, "music_ratio": 0.0, "method": "whisper_fail"}

        # Gộp kết quả các segment thành 1 kết quả tổng hợp
        agg = self._whisper.aggregate_segments(raw_results)
        speech_ratio   = agg.speech_ratio
        no_speech_prob = agg.no_speech_prob

        # ── Bước 2: Conditional YAMNet ────────────────────────────────────
        avg_music = 0.0
        method    = "whisper_only"

        if speech_ratio > SPEECH_RATIO_PASS:
            # Rõ ràng là giọng nói → skip YAMNet hoàn toàn
            method = "whisper_pass_direct"
            logger.debug(f"    AI: PASS direct (speech={speech_ratio:.2f} > {SPEECH_RATIO_PASS})")

        elif speech_ratio < SPEECH_RATIO_DROP or no_speech_prob > NO_SPEECH_PROB_MAX:
            # Rõ ràng là nhạc/rác → skip YAMNet, DROP luôn
            method = "whisper_drop_direct"
            logger.debug(f"    AI: DROP direct (speech={speech_ratio:.2f}, no_sp={no_speech_prob:.2f})")

        else:
            # Vùng xám 0.40–0.65 → chạy YAMNet để phán quyết
            self._load_yamnet()
            if self._yamnet and self._yamnet.is_available():
                yamnet_result = self._yamnet.classify_segments(segment_paths)
                avg_music = yamnet_result.music_ratio
                method = "yamnet_confirmed"
                logger.debug(f"    AI: YAMNet {yamnet_result.summary()}")
            else:
                # YAMNet không load được → dùng ngưỡng khắt hơn của Whisper
                method = "yamnet_unavailable"
                logger.debug("    AI: YAMNet unavailable → dùng ngưỡng Whisper")

        return {
            "speech_ratio":   round(speech_ratio, 3),
            "no_speech_prob": round(no_speech_prob, 3),
            "music_ratio":    round(avg_music, 3),
            "method":         method,
        }

    def _get_no_speech_prob(self, path: str) -> float:
        """Lấy no_speech_prob từ Whisper model trực tiếp."""
        try:
            if self._whisper and self._whisper.model:
                segments_iter, info = self._whisper.model.transcribe(
                    path, beam_size=1, vad_filter=False
                )
                probs = [s.no_speech_prob for s in segments_iter]
                return sum(probs) / len(probs) if probs else 0.5
        except Exception:
            pass
        return 0.5

    def _run_yamnet(self, wav_path: str) -> float:
        """Chạy YAMNet, trả về music_ratio (0–1)."""
        try:
            import numpy as np
            import soundfile as sf
            audio_data, sr = sf.read(wav_path)
            if sr != 16000:
                return 0.0
            scores, embeddings, spectrogram = self._yamnet(audio_data)
            scores_np = scores.numpy()
            # Class index 137 = Music trong YAMNet class map
            MUSIC_CLASS_IDX = 137
            music_score = float(scores_np[:, MUSIC_CLASS_IDX].mean())
            return music_score
        except Exception as e:
            logger.debug(f"YAMNet run error: {e}")
            return 0.0


# ── Main Pipeline ──────────────────────────────────────────────────────────────
class AudioPipeline:
    """
    Pipeline xử lý audio V2.1 — tất cả audio từ cả 2 Engine đi qua đây.
    Dùng singleton pattern để giữ AI model trong RAM suốt phiên làm việc.
    """

    _instance = None

    @classmethod
    def get(cls) -> "AudioPipeline":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.trimmer  = SmartAudioTrimmer()
        self.analyzer = AIAnalyzer()
        self._seen_ids: set = set()   # In-memory cache nhanh (dedup)

    async def process(
        self,
        audio: AudioMetadata,
        trend_result=None,      # TrendResult từ trend_detector.py (optional)
        on_accepted=None,       # Callback khi accept
    ) -> PipelineResult:
        """Chạy toàn bộ pipeline cho 1 audio. Thread-safe."""

        async def drop(stage: str, reason: str) -> PipelineResult:
            audio.status = "rejected"
            audio.reason = reason
            await insert_audio(audio)
            return PipelineResult(audio, False, stage, reason)

        # ── [1] DEDUP ──────────────────────────────────────────────────────
        if audio.audio_id in self._seen_ids:
            return await drop("DEDUP", "Trùng lặp (cache)")
        if await check_duplicate(audio.audio_id):
            self._seen_ids.add(audio.audio_id)
            return await drop("DEDUP", "Đã có trong DB")
        self._seen_ids.add(audio.audio_id)

        # ── [2] METADATA FILTER ────────────────────────────────────────────
        ok, reason = is_valid_audio(audio)
        if not ok:
            return await drop("METADATA", reason)

        # ── [3] USAGE CHECK (Fail-fast — TRƯỚC KHI tải file) ────────────
        usage = audio.usage_count if audio.usage_count > 0 else 0
        trend_tag = trend_result.tag if trend_result else "NORMAL"

        # Nếu không có usage, cố gắng lấy từ trang audio
        if usage <= 0 and audio.audio_page_url:
            try:
                from crawler import TikTokCrawler
                tmp_crawler = TikTokCrawler()
                usage = await tmp_crawler.get_accurate_usage(audio.audio_page_url)
                if usage > 0:
                    audio.usage_count = usage
            except Exception as e:
                logger.debug(f"Không thể lấy accurate usage: {e}")

        # Đặc cách nếu video có lượt xem khủng nhưng không lấy được usage âm thanh
        if usage <= 0 and audio.video_views > 20000:
            usage = 500
            audio.usage_count = 500
            logger.info(f"Đặc cách usage cho {audio.audio_id} vì video có {audio.video_views} views")

        # Early Trend được hưởng ngưỡng thấp hơn
        min_usage = USAGE_EARLY_TREND if trend_tag == "EARLY_TREND" else get_dynamic_min_usage(audio.create_time)

        if usage < min_usage:
            audio.reason = f"Usage {usage:,} < {min_usage:,} (tag={trend_tag})"
            return await drop("USAGE_CHECK", audio.reason)

        # Lưu snapshot usage để tính velocity sau
        if usage > 0:
            await snapshot_usage(audio.audio_id, usage)

        # ── [4] TREND ANALYSIS ─────────────────────────────────────────────
        trend_score    = trend_result.trend_score    if trend_result else 0.0
        trend_velocity = trend_result.trend_velocity if trend_result else 0.0

        # ── [5] DOWNLOAD + TRIM ────────────────────────────────────────────
        from audio_processor import download_audio
        dl_ok = await download_audio(audio)
        if not dl_ok or not audio.file_path:
            return await drop("DOWNLOAD", "Download failed")

        # Cắt audio thông minh (VAD + ffmpeg)
        try:
            segment_paths, has_speech_vad = await asyncio.to_thread(
                self.trimmer.trim, audio.file_path, float(audio.duration)
            )
        except Exception as e:
            logger.error(f"Trim error: {e}")
            segment_paths, has_speech_vad = [], False
        finally:
            _rm(audio.file_path)
            audio.file_path = None

        # ── [6] VAD GATE ───────────────────────────────────────────────────
        if not has_speech_vad and not segment_paths:
            return await drop("VAD", "Không phát hiện giọng người (VAD)")

        if not segment_paths:
            # Fallback: nếu VAD không chạy được nhưng has_speech=True → dùng gì?
            # Không có segment → không thể chạy AI → reject an toàn
            return await drop("VAD", "Không cắt được segment")

        # ── [7] AI ANALYSIS + SHAZAM (song song) ──────────────────────────
        try:
            from audio_processor import check_shazam
            # Chạy Whisper/YAMNet và Shazam CÙNG LÚC để tiết kiệm thời gian
            ai_task     = asyncio.to_thread(self.analyzer.analyze_segments, segment_paths)
            # Shazam dùng 1 segment đại diện (segment đầu tiên)
            shazam_path = segment_paths[0] if segment_paths else None
            async def _no_shazam():
                return False
            shazam_task = check_shazam(shazam_path) if shazam_path else _no_shazam()

            ai_result, is_copyrighted = await asyncio.gather(ai_task, shazam_task, return_exceptions=True)

            # Xử lý exception riêng
            if isinstance(ai_result, Exception):
                logger.error(f"AI analyze error: {ai_result}")
                ai_result = {"speech_ratio": 0.0, "no_speech_prob": 1.0, "music_ratio": 0.0, "method": "error"}
            if isinstance(is_copyrighted, Exception):
                logger.debug(f"Shazam error: {is_copyrighted}")
                is_copyrighted = False

        except Exception as e:
            logger.error(f"AI/Shazam error: {e}")
            ai_result = {"speech_ratio": 0.0, "no_speech_prob": 1.0, "music_ratio": 0.0, "method": "error"}
            is_copyrighted = False
        finally:
            for sp in segment_paths:
                _rm(sp)

        speech_ratio   = ai_result["speech_ratio"]
        no_speech_prob = ai_result["no_speech_prob"]
        music_ratio    = ai_result["music_ratio"]
        method         = ai_result["method"]

        audio.speech_ratio = speech_ratio

        # ── [7.5] SHAZAM GATE ─────────────────────────────────────────────
        if is_copyrighted:
            logger.info(f"  🚫 [Shazam] Bản quyền: {audio.audio_name[:50]}")
            return await drop("SHAZAM", f"Bản quyền (Shazam) | speech={speech_ratio:.0%}")

        # ── [8] FILTER FINAL ───────────────────────────────────────────────
        if speech_ratio < SPEECH_RATIO_DROP or "drop_direct" in method:
            return await drop("AI_FILTER", f"speech_ratio={speech_ratio:.2f} < {SPEECH_RATIO_DROP} | method={method}")

        if no_speech_prob > NO_SPEECH_PROB_MAX:
            return await drop("AI_FILTER", f"no_speech_prob={no_speech_prob:.2f} > {NO_SPEECH_PROB_MAX}")

        if music_ratio > MUSIC_RATIO_MAX:
            return await drop("AI_FILTER", f"music_ratio={music_ratio:.2f} > {MUSIC_RATIO_MAX} (YAMNet)")


        # ── [9] SCORING ────────────────────────────────────────────────────
        velocity = await get_usage_velocity(audio.audio_id)
        audio_score = compute_audio_score(
            usage_count      = max(usage, 0),
            trend_score      = trend_score,
            speech_ratio     = speech_ratio,
            music_ratio      = music_ratio,
            velocity_per_hour= velocity,
        )

        # Bỏ chặn theo điểm — giữ lại điểm để tham khảo/sắp xếp trên UI
        audio.ai_score = audio_score


        # ── [10] SAVE ──────────────────────────────────────────────────────
        audio.status       = "accepted"
        audio.is_speech    = True
        audio.ai_score     = audio_score
        audio.speech_ratio = speech_ratio
        audio.reason       = (
            f"✅ speech={speech_ratio:.0%} music={music_ratio:.0%} "
            f"usage={usage:,} score={audio_score:.1f} tag={trend_tag} method={method}"
        )

        # Ghi thêm trend info vào DB (qua các cột mới)
        setattr(audio, "trend_tag",      trend_tag)
        setattr(audio, "trend_score",    round(trend_score, 3))
        setattr(audio, "trend_velocity", round(trend_velocity, 3))
        setattr(audio, "music_ratio",    round(music_ratio, 3))

        await insert_audio(audio)

        logger.success(
            f"🔥 ACCEPTED: {audio.audio_name[:40]} | "
            f"usage={usage:,} score={audio_score:.1f} "
            f"speech={speech_ratio:.0%} tag={trend_tag}"
        )

        if on_accepted:
            on_accepted(audio)

        # ── [11] EXPAND ────────────────────────────────────────────────────
        await self._maybe_expand_creator(audio)

        return PipelineResult(
            audio=audio, passed=True,
            stage="ACCEPTED", reason=audio.reason,
            audio_score=audio_score,
            speech_ratio=speech_ratio,
            music_ratio=music_ratio,
            trend_tag=trend_tag,
            trend_score=trend_score,
        )

    async def _maybe_expand_creator(self, audio: AudioMetadata):
        """
        Thêm creator mới vào danh sách quét (Audio Expansion).
        Rule chặt chẽ: chỉ thêm nếu creator xuất hiện >= 2 lần trong hệ thống.
        """
        username = audio.author_username
        if not username or username == "user":
            return

        try:
            from database import DB_PATH
            import aiosqlite
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM audio_history WHERE LOWER(author_username) = ?",
                    (username.lower(),)
                ) as cur:
                    row = await cur.fetchone()
                    count = row[0] if row else 0

            if count >= EXPAND_MIN_APPEARANCES:
                await add_target_user(username)
                logger.debug(f"  🌱 Expand: @{username} (xuất hiện {count} lần)")
        except Exception as e:
            logger.debug(f"Expand creator error: {e}")


# ── Utility ───────────────────────────────────────────────────────────────────
def _rm(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

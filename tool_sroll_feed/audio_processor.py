import os
import asyncio
from shazamio import Shazam

# ── Faster-Whisper (GPU) thay cho openai-whisper ──────────────────────────────
try:
    from faster_whisper import WhisperModel
    import torch
    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    _COMPUTE = "float16" if _DEVICE == "cuda" else "int8"
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False
    _DEVICE = "cpu"


class AudioProcessor:
    def __init__(self):
        self.shazam = Shazam()
        self.model = None
        if HAS_WHISPER:
            try:
                print(f"🤖 Loading faster-whisper 'tiny' trên {_DEVICE.upper()} ({_COMPUTE})...")
                self.model = WhisperModel("tiny", device=_DEVICE, compute_type=_COMPUTE)
                print("✅ Whisper đã sẵn sàng.")
            except Exception as e:
                print(f"⚠️ Không load được Whisper: {e}")

    # ── Fix 3: faster-whisper speech ratio ──────────────────────────────────────
    def get_speech_ratio(self, file_path: str) -> float:
        """Trả về tỉ lệ speech thực tế (0.0-1.0) bằng faster-whisper + VAD."""
        if not self.model:
            return 1.0  # Fallback: không filter nếu không có model
        try:
            segments, info = self.model.transcribe(file_path, beam_size=1, vad_filter=True)
            speech_duration = sum(s.end - s.start for s in segments)
            return round(speech_duration / info.duration, 3) if info.duration > 0 else 0.0
        except Exception as e:
            print(f"⚠️ Lỗi Whisper: {e}")
            return 1.0

    def is_mostly_speech(self, file_path: str, threshold: float = 0.70) -> tuple:
        """Trả về (is_speech, speech_ratio)."""
        ratio = self.get_speech_ratio(file_path)
        return ratio >= threshold, ratio

    # ── Fix 2: Shazam Full-Scan mỗi 10 giây ───────────────────────────────────
    async def check_copyright_fullscan(self, file_path: str, audio_id: str = None) -> tuple:
        """
        Full-Scan: quét TOÀN BỘ file mỗi 10 giây.
        Không bỏ sót nhạc nền dù nhỏ ở bất kỳ vị trí nào.
        Returns: (is_copyrighted: bool, track_title: str | None)
        """
        from database import get_shazam_cache, save_shazam_cache

        # Kiểm tra cache trước
        if audio_id:
            cache = get_shazam_cache(audio_id)
            if cache:
                print(f"📦 Shazam (Cache): {'⚠️ ' + cache.track_title if cache.is_copyrighted else '✅ Sạch'}")
                return cache.is_copyrighted, cache.track_title

        try:
            from pydub import AudioSegment
            audio_seg = AudioSegment.from_file(file_path).normalize()
            duration_ms = len(audio_seg)
        except Exception as e:
            print(f"⚠️ Không load được audio để Full-Scan: {e}")
            # Fallback: check 1 lần
            return await self._check_once(file_path, audio_id)

        is_copyrighted = False
        track_title = None

        # Quét mỗi 5 giây (overlap 7 giây)
        for start_ms in range(0, duration_ms, 5000):
            end_ms = min(start_ms + 12000, duration_ms)
            if end_ms - start_ms < 3000:
                continue

            chunk = audio_seg[start_ms:end_ms]
            chunk_file = f"{file_path}_chunk_{start_ms}.wav"
            try:
                chunk.export(chunk_file, format="wav")
                
                success_chunks = 0
                for attempt in range(2): # Thử lại 2 lần nếu lỗi/timeout
                    try:
                        out = await asyncio.wait_for(
                            self.shazam.recognize(chunk_file),
                            timeout=15.0
                        )
                        success_chunks += 1
                        
                        if out and (out.get('track') or out.get('matches')):
                            track = out.get('track', {})
                            track_title = track.get('title', 'Unknown')
                            print(f"🚨 Shazam Full-Scan phát hiện nhạc nền tại {start_ms//1000}s: {track_title}")
                            is_copyrighted = True
                            break
                        break # Thành công không có nhạc -> thoát attempt
                    except asyncio.TimeoutError:
                        print(f"⏱️ Shazam timeout tại {start_ms//1000}s, thử lại...")
                        await asyncio.sleep(1)
                    except Exception as e:
                        print(f"⚠️ Shazam lỗi tại {start_ms//1000}s: {e}")
                        break
                        
                if is_copyrighted:
                    break
            finally:
                if os.path.exists(chunk_file):
                    try: os.remove(chunk_file)
                    except: pass

        # Lưu cache
        if audio_id:
            save_shazam_cache(audio_id, is_copyrighted, track_title)

        return is_copyrighted, track_title

    async def _check_once(self, file_path: str, audio_id: str = None) -> tuple:
        """Fallback: check Shazam 1 lần."""
        try:
            out = await asyncio.wait_for(self.shazam.recognize(file_path), timeout=30.0)
            if out and out.get('track'):
                title = out['track'].get('title')
                if audio_id:
                    from database import save_shazam_cache
                    save_shazam_cache(audio_id, True, title)
                return True, title
        except Exception as e:
            print(f"⚠️ Shazam 1-shot lỗi: {e}")
        if audio_id:
            from database import save_shazam_cache
            save_shazam_cache(audio_id, False, None)
        return False, None

    def cleanup(self, file_path: str):
        """Dọn dẹp file sau khi xử lý."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"⚠️ Lỗi xóa file: {e}")

    async def check_copyright_smart(self, file_path: str, audio_id: str = None,
                                     duration: float = 0) -> tuple:
        """
        P2 — Smarter Shazam:
        - Bỏ qua nếu duration < 30s (ngắn quá → ít khả năng nhạc bản quyền)
        - Retry 1 lần với exponential backoff
        - Hard timeout 20s/lần
        - Fallback: cho qua (False) nếu vẫn lỗi
        """
        if duration > 0 and duration < 30:
            print(f"⚡ [Shazam] Skip (duration={duration:.0f}s < 30s) → nhường qua")
            return False, None

        # Check cache trước
        if audio_id:
            try:
                from database import get_shazam_cache
                cache = get_shazam_cache(audio_id)
                if cache:
                    print(f"📦 Shazam (Cache): {'⚠️ ' + cache.track_title if cache.is_copyrighted else '✅ Sạch'}")
                    return cache.is_copyrighted, cache.track_title
            except Exception:
                pass

        for attempt in range(2):
            try:
                out = await asyncio.wait_for(
                    self.shazam.recognize(file_path),
                    timeout=20.0
                )
                is_copy = bool(out and (out.get('track') or out.get('matches')))
                title = out['track'].get('title') if is_copy and out.get('track') else None
                if is_copy:
                    print(f"🚨 [Shazam Smart] Phát hiện nhạc: {title}")
                if audio_id:
                    try:
                        from database import save_shazam_cache
                        save_shazam_cache(audio_id, is_copy, title)
                    except Exception:
                        pass
                return is_copy, title
            except asyncio.TimeoutError:
                if attempt == 0:
                    print(f"⏰ [Shazam Smart] Timeout lần {attempt+1}, thử lại sau 3s...")
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"⚠️ [Shazam Smart] Lỗi: {e}")
                break

        print("⚠️ [Shazam Smart] Hết retry → cho qua (False)")
        return False, None


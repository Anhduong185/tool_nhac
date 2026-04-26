import os
import asyncio
from shazamio import Shazam
import librosa
import sys

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
                    except:
                        pass
                    os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")

# Thử import các thư viện AI nặng, nếu không có thì dùng fallback
try:
    import whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

try:
    import webrtcvad
    HAS_VAD = True
except ImportError:
    HAS_VAD = False

class AudioProcessor:
    def __init__(self):
        self.shazam = Shazam()
        self.model = None
        self.force_cpu = False
        self._load_model()

    def _load_model(self):
        if HAS_WHISPER and self.model is None:
            try:
                device = "cpu" if self.force_cpu else None
                print(f"Loading Whisper 'tiny' model (device={device or 'auto'})...")
                self.model = whisper.load_model("tiny", device=device)
            except Exception as e:
                print(f"Lỗi tải Whisper: {e}")
                if not self.force_cpu:
                    print("Đang chuyển sang nạp bằng CPU...")
                    self.force_cpu = True
                    try:
                        self.model = whisper.load_model("tiny", device="cpu")
                    except:
                        pass

    def get_duration(self, file_path):
        try:
            return librosa.get_duration(path=file_path)
        except Exception as e:
            print(f"Lỗi lấy độ dài: {e}")
            return 0

    def is_voice_only(self, file_path):
        """Kiểm tra xem file có phải chủ yếu là tiếng nói không"""
        # 1. Thử dùng VAD (Siêu nhanh) nếu có
        if HAS_VAD:
            try:
                vad = webrtcvad.Vad(2) # Độ nhạy mức 2
                audio, sample_rate = librosa.load(file_path, sr=16000)
                # Chuyển sang định dạng 16-bit PCM mà VAD yêu cầu
                audio_int = (audio * 32767).astype('int16')
                
                # Kiểm tra một vài khung hình (frames) 30ms
                frame_duration = 30 # ms
                samples_per_frame = int(sample_rate * frame_duration / 1000)
                
                voice_frames = 0
                total_frames = 0
                for i in range(0, len(audio_int) - samples_per_frame, samples_per_frame):
                    frame = audio_int[i:i + samples_per_frame].tobytes()
                    if vad.is_speech(frame, sample_rate):
                        voice_frames += 1
                    total_frames += 1
                
                # Nếu tỉ lệ tiếng người > 10% thì coi như có tiếng nói
                if total_frames > 0 and (voice_frames / total_frames) > 0.1:
                    print(f"🎙️ VAD phát hiện tiếng người ({int(voice_frames/total_frames*100)}%)")
                else:
                    print("🔇 VAD không thấy tiếng người, loại bỏ.")
                    return False
            except Exception as e:
                print(f"Cảnh báo VAD: {e}")

        # 2. Dùng Whisper (Chính xác hơn nhưng chậm hơn) để xác nhận
        if not HAS_WHISPER:
            return True
            
        try:
            self._load_model()
            if not self.model:
                return True
                
            result = self.model.transcribe(file_path)
            text = result.get("text", "").strip()
            return len(text) > 5
        except Exception as e:
            error_msg = str(e).lower()
            if "cublas" in error_msg or "cuda" in error_msg or "cudnn" in error_msg or "cudart" in error_msg:
                if not self.force_cpu:
                    print(f"Lỗi GPU khi nhận diện giọng nói (Thiếu DLL/CUDA). Chuyển qua CPU: {e}")
                    self.force_cpu = True
                    self.model = None
                    return self.is_voice_only(file_path)
            
            print(f"Lỗi nhận diện giọng nói: {e}")
            return True

    async def check_copyright(self, file_path, audio_id=None):
        """Check bản quyền qua Shazam (có cache)"""
        from database import get_shazam_cache, save_shazam_cache
        
        # 1. Check cache trước
        if audio_id:
            cache = get_shazam_cache(audio_id)
            if cache:
                print(f"📦 Shazam (Cache): {cache.track_title or 'Sạch'}")
                return not cache.is_copyrighted

        try:
            # recognize theo chuẩn mới của Shazamio
            out = await self.shazam.recognize(file_path)
            is_copyrighted = False
            title = None
            
            if out and 'track' in out:
                title = out['track'].get('title')
                print(f"🎵 Shazam phát hiện: {title}")
                is_copyrighted = True
            
            # Lưu cache
            if audio_id:
                save_shazam_cache(audio_id, is_copyrighted, title)
                
            return not is_copyrighted
            
        except Exception as e:
            if "system cannot find the file specified" in str(e):
                print("❌ LỖI: Không tìm thấy FFmpeg!")
            else:
                print(f"Lỗi check Shazam: {e}")
            return True

    def cleanup(self, file_path):
        """Dọn dẹp file sau khi xử lý"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Lỗi xóa file: {e}")


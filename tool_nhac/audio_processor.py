import httpx
import aiofiles
from pathlib import Path
from loguru import logger
import asyncio
import tempfile
import os
from models import AudioMetadata
from config import AUDIOS_DIR, SHAZAM_DELAY

async def download_audio(audio: AudioMetadata) -> bool:
    """Tải file âm thanh với cơ chế thử lại (3 lần) và Header chống 403."""
    if not audio.audio_url:
        return False
        
    file_name = f"{audio.audio_id}.mp3"
    file_path = AUDIOS_DIR / file_name
    
    if file_path.exists():
        audio.file_path = str(file_path)
        return True
        
    # Header mô phỏng trình duyệt thật để tránh 403 Forbidden
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
        "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Range": "bytes=0-",
        "Connection": "keep-alive"
    }

    # Sử dụng AudioDownloaderPro để xử lý tải đa phương thức
    from audio_downloader_pro import AudioDownloaderPro
    downloader = AudioDownloaderPro()
    
    # Thử lại tối đa 2 lần (mỗi lần gồm nhiều lớp tải)
    for attempt in range(2):
        try:
            # 1. Thử tải trực tiếp (HTTP/FFmpeg)
            logger.info(f"📥 [Lần {attempt+1}] Đang tải audio cho {audio.audio_id}...")
            
            # Giả lập Namespace cho downloader (vì nó dùng argparse.Namespace)
            class Args: pass
            args = Args()
            args.input = audio.audio_url
            args.output = file_path
            args.format = "mp3"
            args.start = None
            args.duration = None
            args.no_download = False
            
            # Thử lấy link từ page nếu link direct fail
            source_url = audio.audio_url
            success = await downloader._download_direct_ffmpeg(source_url, file_path)
            
            if not success and (audio.audio_page_url or audio.video_url):
                logger.info(f"🔄 Link direct lỗi, thử dùng link trang nguồn (yt-dlp)...")
                success = await downloader.download_video(audio.audio_page_url or audio.video_url, file_path)

            if success and file_path.exists():
                audio.file_path = str(file_path)
                return True
                
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Download attempt {attempt+1} failed: {e}")
            
    return False
            
    return False

async def check_shazam(file_path: str) -> bool:
    """Nhận dạng bản quyền bằng Shazam - check 3 điểm chiến lược (25/50/75%)."""
    import subprocess
    try:
        from shazamio import Shazam
        from pydub import AudioSegment

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(AUDIOS_DIR)) as tf:
            temp_wav = tf.name

        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", file_path, "-ac", "1", "-ar", "44100", temp_wav],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"FFmpeg failed: {e}")
            _rm(temp_wav)
            return False

        try:
            audio = AudioSegment.from_file(temp_wav, format="wav")
            audio = audio.normalize()
            duration_ms = len(audio)
        except Exception as e:
            logger.warning(f"PyDub failed: {e}")
            _rm(temp_wav)
            return False

        shazam = Shazam()
        chunk_len = 12000  # 12 giây cho mỗi lần nhận diện
        
        # Quét 3 điểm chiến lược (đầu, giữa, cuối) thay vì mỗi 5s để tăng tốc độ
        check_positions = [0]
        if duration_ms > 15000:
            check_positions.append(max(0, duration_ms // 2 - 6000))
        if duration_ms > 30000:
            check_positions.append(max(0, duration_ms - 12000))

        is_copyrighted = False
        chunk_files = []
        timeout_count = 0
        total_chunks = len(check_positions)

        try:
            for start_ms in check_positions:
                end_ms = min(start_ms + chunk_len, duration_ms)
                
                chunk = audio[start_ms:end_ms]
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(AUDIOS_DIR)) as cf:
                    chunk_file = cf.name
                chunk_files.append(chunk_file)
                chunk.export(chunk_file, format="wav")

                chunk_timeout = True
                for attempt in range(2): # Thử lại 2 lần nếu lỗi/timeout
                    try:
                        out = await asyncio.wait_for(shazam.recognize(chunk_file), timeout=15.0)
                        chunk_timeout = False
                        
                        if out.get("matches") or "track" in out:
                            is_copyrighted = True
                            track = out.get("track", {})
                            logger.warning(f"🚨 SHAZAM PHÁT HIỆN NHẠC NỀN tại {start_ms//1000}s: {track.get('title')}")
                            break
                        break # Thành công không thấy nhạc, thoát vòng lặp attempt
                    except asyncio.TimeoutError:
                        logger.debug(f"Shazam timeout chunk {start_ms//1000}s, thử lại...")
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.debug(f"Shazam lỗi chunk {start_ms//1000}s: {e}")
                        chunk_timeout = False  # Lỗi khác → không tính là timeout
                        break

                if chunk_timeout:
                    timeout_count += 1
                        
                if is_copyrighted:
                    break

            # Tất cả chunk đều timeout → Shazam không kiểm tra được → reject để an toàn
            if timeout_count == total_chunks and total_chunks > 0:
                logger.warning(f"⚠️ Shazam timeout toàn bộ {total_chunks} chunk → Reject (an toàn)")
                is_copyrighted = True
        finally:
            for cf in chunk_files:
                _rm(cf)
            _rm(temp_wav)

        return is_copyrighted

    except Exception as e:
        logger.error(f"Critical Shazam error: {e}")
        return False


def _rm(path: str):
    """Xóa file an toàn."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

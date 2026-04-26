"""
audio_downloader_pro.py — Advanced Audio Extraction & Processing Utility
=======================================================================
Công cụ chuyên nghiệp sử dụng FFmpeg/FFprobe để tải, trích xuất và xử lý audio.
Đặc biệt tối ưu cho TikTok và kiểm tra bản quyền.

Sử dụng:
    python audio_downloader_pro.py [URL/File] [Options]
"""

import os
import sys
import json
import asyncio
import argparse
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# Cấu hình Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("AudioPro")

class AudioDownloaderPro:
    def __init__(self, ffmpeg_path=None, ffprobe_path=None, ytdlp_path=None):
        # Kiểm tra sự tồn tại của ffmpeg/ffprobe
        self.ffmpeg = ffmpeg_path or self._find_tool("ffmpeg")
        self.ffprobe = ffprobe_path or self._find_tool("ffprobe")
        self.ytdlp = ytdlp_path or self._find_tool("yt-dlp")
        
        if not self.ffmpeg or not self.ffprobe:
            logger.error("❌ Không tìm thấy ffmpeg hoặc ffprobe. Vui lòng cài đặt hoặc để file .exe cùng thư mục.")
            sys.exit(1)

    def _find_tool(self, name):
        """Tìm kiếm tool trong thư mục hiện tại hoặc System PATH."""
        # Thử file .exe nếu là Windows
        ext = ".exe" if os.name == "nt" else ""
        local_path = Path(__file__).parent / f"{name}{ext}"
        if local_path.exists():
            return str(local_path)
        # Thử trong PATH
        from shutil import which
        return which(name)

    async def get_stream_info(self, input_source):
        """Sử dụng ffprobe để đọc thông tin audio stream."""
        cmd = [
            self.ffprobe, "-v", "quiet", 
            "-print_format", "json", 
            "-show_streams", "-show_format", 
            input_source
        ]
        try:
            logger.debug(f"Running ffprobe: {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            data = json.loads(stdout.decode('utf-8'))
            
            audio_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), None)
            return {
                "format": data.get("format", {}),
                "audio": audio_stream
            }
        except Exception as e:
            logger.error(f"❌ Lỗi ffprobe: {e}")
            return None

    async def download_video(self, url, output_path):
        """Tải video bằng yt-dlp làm fallback mạnh mẽ."""
        if not self.ytdlp:
            logger.warning("⚠️ Không thấy yt-dlp. Thử dùng FFmpeg tải trực tiếp...")
            return await self._download_direct_ffmpeg(url, output_path)

        cmd = [
            self.ytdlp, "-f", "bestvideo+bestaudio/best",
            "--no-playlist", "--merge-output-format", "mp4",
            "-o", str(output_path), url
        ]
        logger.info(f"📥 Đang tải bằng yt-dlp: {url}")
        try:
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
            return output_path.exists()
        except Exception as e:
            logger.error(f"❌ Lỗi yt-dlp: {e}")
            return await self._download_direct_ffmpeg(url, output_path)

    async def _download_direct_ffmpeg(self, url, output_path):
        """Tải trực tiếp bằng FFmpeg (fallback)."""
        cmd = [self.ffmpeg, "-y", "-i", url, "-c", "copy", str(output_path)]
        logger.info(f"🚀 Thử tải trực tiếp bằng FFmpeg...")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
            return output_path.exists()
        except Exception as e:
            logger.error(f"❌ FFmpeg tải thất bại: {e}")
            return False

    async def process_audio(self, args):
        """Luồng xử lý chính: Download -> Probe -> Extract/Convert."""
        input_source = args.input
        is_url = input_source.startswith("http")
        temp_video = None

        # 1. DOWNLOAD nếu là URL
        if is_url:
            if args.no_download:
                logger.error("❌ Bạn chọn --no-download nhưng lại cung cấp URL.")
                return
            
            temp_video = Path("temp_download.mp4")
            success = await self.download_video(input_source, temp_video)
            if not success:
                logger.error("❌ Không thể tải video từ URL.")
                return
            input_source = str(temp_video)

        # 2. PROBE thông tin
        info = await self.get_stream_info(input_source)
        if not info or not info["audio"]:
            logger.error("❌ Không tìm thấy Audio Stream trong nguồn này.")
            if temp_video: temp_video.unlink(missing_ok=True)
            return

        codec = info["audio"].get("codec_name", "unknown")
        duration = float(info["format"].get("duration", 0))
        logger.info(f"📊 Thông tin: Codec={codec}, Thời lượng={duration:.2f}s")

        # 3. QUYẾT ĐỊNH PHƯƠNG PHÁP (Copy hay Convert)
        output_ext = args.format.lower()
        output_file = Path(args.output or f"output_{datetime.now().strftime('%H%M%S')}.{output_ext}")
        
        cmd = [self.ffmpeg, "-y"]
        
        # Trim options
        if args.start: cmd.extend(["-ss", args.start])
        if args.duration: cmd.extend(["-t", args.duration])
        
        cmd.extend(["-i", input_source])

        # Logic chuyển đổi
        if output_ext == "aac" and codec == "aac":
            logger.info("⚡ Codec khớp (AAC) -> Stream Copy để giữ nguyên chất lượng.")
            cmd.extend(["-c:a", "copy"])
        elif output_ext == "mp3":
            logger.info("🎼 Chuyển sang MP3 (320k)...")
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif output_ext == "wav":
            logger.info("🔊 Xuất định dạng WAV chuẩn (44100Hz, Stereo)...")
            cmd.extend(["-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2"])
        else:
            # Fallback encode
            cmd.extend(["-c:a", "aac" if output_ext == "aac" else "libmp3lame"])

        cmd.append(str(output_file))

        # 4. CHẠY FFMPEG
        logger.info(f"🛠️ Thực thi: {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                logger.success(f"✨ HOÀN TẤT: {output_file} ({output_file.stat().st_size / 1024:.1f} KB)")
            else:
                logger.error(f"❌ Lỗi FFmpeg: {stderr.decode('utf-8')}")
        finally:
            if temp_video and temp_video.exists():
                temp_video.unlink()

async def run_batch(downloader, args):
    """Xử lý hàng loạt nếu input là đường dẫn tới file .txt"""
    input_source = args.input
    if input_source.endswith(".txt") and os.path.exists(input_source):
        logger.info(f"📁 Phát hiện danh sách batch: {input_source}")
        with open(input_source, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and line.startswith("http")]
        
        logger.info(f"🚀 Bắt đầu xử lý {len(urls)} URLs...")
        for i, url in enumerate(urls):
            logger.info(f"\n--- [Batch {i+1}/{len(urls)}] ---")
            temp_args = argparse.Namespace(**vars(args))
            temp_args.input = url
            # Tạo tên output tự động nếu không có
            if not args.output:
                temp_args.output = f"batch_{i+1}_{datetime.now().strftime('%H%M%S')}.{args.format}"
            await downloader.process_audio(temp_args)
    else:
        await downloader.process_audio(args)

def main():
    parser = argparse.ArgumentParser(description="Advanced Audio Downloader & Processor Pro")
    parser.add_argument("input", help="URL video TikTok/YouTube hoặc file .txt chứa danh sách URLs hoặc file video cục bộ")
    parser.add_argument("--output", "-o", help="Tên file đầu ra (chỉ dùng cho 1 input)")
    parser.add_argument("--format", "-f", default="mp3", choices=["mp3", "aac", "wav"], help="Định dạng (mặc định: mp3)")
    parser.add_argument("--start", "-s", help="Thời điểm bắt đầu (HH:MM:SS hoặc giây)")
    parser.add_argument("--duration", "-d", help="Độ dài cần lấy (giây)")
    parser.add_argument("--no-download", action="store_true", help="Chỉ xử lý file cục bộ, không tải từ web")

    # Custom logger success level
    SUCCESS_LEVEL = 25
    logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")
    def success(self, message, *args, **kws):
        if self.isEnabledFor(SUCCESS_LEVEL):
            self._log(SUCCESS_LEVEL, message, args, **kws)
    logging.Logger.success = success

    args = parser.parse_args()
    
    downloader = AudioDownloaderPro()
    asyncio.run(run_batch(downloader, args))

if __name__ == "__main__":
    main()

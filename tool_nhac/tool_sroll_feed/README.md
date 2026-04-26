# TikTok Scroll Agent

Tool tự động lướt feed TikTok, tìm kiếm và phân loại audio "Original Sound" theo bộ quy tắc tùy chỉnh.

## Cấu trúc
- `main.py`: Luồng chính điều khiển agent.
- `browser.py`: Điều khiển trình duyệt (Playwright) và giả lập người dùng.
- `audio_processor.py`: Xử lý âm thanh (Shazam, Whisper AI, VAD).
- `engine.py`: Bộ lọc quy tắc (duration, usage, year, source).
- `database.py`: Lưu trữ kết quả vào SQLite.
- `config.py`: Cấu hình các ngưỡng và tham số.

## Yêu cầu hệ thống
1. Python 3.8+
2. FFmpeg (bắt buộc để xử lý audio)
3. Cài đặt thư viện:
   ```bash
   pip install -r requirements.txt
   ```
4. Cài đặt browser cho Playwright:
   ```bash
   playwright install chromium
   ```

## Cách chạy
```bash
python main.py
```

## Tính năng
- [x] Tự động cuộn feed.
- [x] Nhận diện "Original Sound".
- [x] Kiểm tra thời lượng (<= 59s).
- [x] Kiểm tra bản quyền qua Shazam.
- [x] Nhận diện giọng nói (Voice-only) bằng Whisper & VAD.
- [x] Phân loại theo năm và số lượt dùng (LSD).
- [x] Chống phát hiện (Playwright Stealth + Human behavior).

# 🎵 TikTok Audio Automation Dashboard V2.1 (All-in-One)

Hệ thống tự động hóa toàn diện giúp tìm kiếm, phân tích và lọc các **âm thanh gốc (Original Sound)** chất lượng cao trên TikTok phục vụ cho mục đích sáng tạo nội dung.

---

## 🌟 Tính năng nổi bật

Hệ thống hoạt động theo mô hình **3-Phase Pipeline** hiện đại:

1.  **Phase 1 & 2: Thu thập đa kênh**
    *   **Creator Miner:** Quét hàng loạt kênh TikTok mục tiêu, tự động bóc tách audio.
    *   **FYP Auto-Harvest:** Tự động lướt bảng tin (FYP) để tìm các âm thanh đang bắt trend.
    *   **LSD Filtering:** Tự động lọc theo số lượng video sử dụng (Usage), thời lượng (< 59s) và kiểm tra bản quyền.
2.  **Phase 3: AI Check Worker (Phân tích chuyên sâu)**
    *   **AI Speech Classifier:** Sử dụng **Whisper** để phân biệt giọng nói thực tế và nhạc nền.
    *   **Silero VAD:** Phát hiện giọng người cực nhanh (0.05s).
    *   **Copyright Detection:** Tích hợp **Shazam** để loại bỏ nhạc có bản quyền.
3.  **Real-time Dashboard UI**
    *   Giao diện điều khiển tập trung, xem log trực tiếp.
    *   **Bộ lọc thông minh:** Lọc kết quả theo ngày và khoảng giờ cụ thể (ví dụ: tìm nhạc cào từ 2h - 6h sáng).
    *   **Export & Copy:** Sao chép hàng loạt link audio/video đã được AI phê duyệt chỉ với 1 click.

---

## 🛠 Cấu trúc Project

```
e:\tool_nhac\
│
├── setup.bat               # File cài đặt tự động (Cực nhanh)
├── tool_nhac\              # Dashboard chính & Engine AI
│   ├── server.py           # Web Server (Chạy cái này để mở Dashboard)
│   ├── main.py             # Engine Keyword Explorer
│   ├── audio_pipeline.py   # Xử lý AI (Whisper, VAD, Shazam)
│   ├── database.py         # Quản lý cơ sở dữ liệu tập trung
│   └── static/             # Giao diện Web (HTML/JS)
│
└── tool_sroll_feed\        # Engine cào FYP & Creator Scanner
    ├── main.py             # FYP Crawler
    └── creator_scanner.py  # Quét kênh tác giả
```

---

## 🚀 Cài đặt nhanh (Recommended)

Nếu bạn vừa clone project về máy mới, chỉ cần chạy file sau:

1.  Click đúp file **`setup.bat`** (Nó sẽ tự tạo venv, cài thư viện và browser).
2.  Chờ thông báo "Setup hoàn tất".

---

## 💻 Cách vận hành

### Bước 1: Kích hoạt môi trường
```bash
call .venv\Scripts\activate
```

### Bước 2: Chạy Dashboard
```bash
cd tool_nhac
python server.py
```

### Bước 3: Truy cập Giao diện
Mở trình duyệt và truy cập: **`http://localhost:8000`**

---

## ⚙️ Quy tắc lọc của AI

Hệ thống tự động chấm điểm **AI Score** cho từng audio dựa trên:
- **Speech Ratio:** Tỷ lệ giọng nói (Ưu tiên giọng người thuần).
- **Usage Count:** Số lượng video đang sử dụng (Đánh giá độ viral).
- **Duration Bonus:** Cộng điểm cho audio từ 15s - 45s (Khung giờ vàng).
- **Originality:** Chỉ chấp nhận "Âm thanh gốc", loại bỏ nhạc thư viện.

---

## 📝 Ghi chú cho máy mới
- Tool yêu cầu **Python 3.10+**.
- File `setup.bat` đã bao gồm việc cài đặt `Playwright` và các thư viện AI nặng.
- Đảm bảo máy có kết nối internet ổn định để tải Model AI trong lần chạy đầu tiên.

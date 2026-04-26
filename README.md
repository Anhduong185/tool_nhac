# 🎵 TikTok Audio Dashboard V2.2 (All-in-One)

Hệ thống tự động hóa khai thác âm thanh TikTok quy mô lớn. Tích hợp AI (Whisper, VAD, Shazam) để lọc nhạc gốc chất lượng cao, loại bỏ nhạc rác và nhạc bản quyền.

---

## 🛠 Hướng dẫn Cài đặt

### 1. Yêu cầu hệ thống
*   **OS:** Windows 10/11.
*   **Python:** 3.10 trở lên.
*   **Hardware:** 
    *   Hỗ trợ tốt nhất cho máy có **GPU NVIDIA** (Sử dụng CUDA để tăng tốc AI).
    *   Vẫn hoạt động ổn định trên máy chỉ có CPU.

### 2. Các bước cài đặt
1.  Tải code về máy.
2.  Chạy file `setup.bat`. Tool sẽ tự động:
    *   Kiểm tra phần cứng (GPU hay CPU).
    *   Tự động cài đặt phiên bản PyTorch phù hợp (giúp tiết kiệm 2-3GB dung lượng nếu dùng CPU).
    *   Cài đặt đầy đủ các thư viện cần thiết.
3.  Cài đặt **FFmpeg**: Đảm bảo máy đã có `ffmpeg` trong PATH hoặc đặt file `ffmpeg.exe` vào thư mục gốc.

---

## 🚀 Hướng dẫn Sử dụng

Để khởi động toàn bộ hệ thống, bạn chỉ cần chạy một lệnh duy nhất:
```powershell
python tool_nhac/server.py
```
Sau đó mở trình duyệt truy cập: **`http://localhost:8000`**

### 1. 🕵️ Creator Miner (Đào Kênh)
*   **Chức năng:** Quét các video từ danh sách kênh mục tiêu để tìm audio gốc.
*   **Cách dùng:** 
    *   Dán danh sách `@username` vào ô nhập liệu hoặc để trống để lấy từ file `creators_list.txt`.
    *   Bấm **"Bật Máy Đào"**.
*   **Ưu điểm:** Luồng xử lý **Cuốn chiếu (Smart Sequential)** giúp quét xong tác giả nào là đẩy audio vào AI Check ngay lập tức, không có thời gian chờ.

### 2. 🔥 FYP Harvester (Lướt Feed)
*   **Chức năng:** Tự động lướt bảng tin TikTok (For You Page) để bắt các âm thanh đang trending.
*   **Cách dùng:** Bấm nút **"Bật FYP"**.
*   **Lưu ý:** Cần đăng nhập tài khoản TikTok trong cửa sổ Chrome hiện lên để đạt hiệu quả cao nhất.

### 3. 🤖 Auto Nurture (Nuôi Nick)
*   **Chức năng:** Tự động đi follow các kênh mục tiêu để tăng độ trust cho tài khoản và làm "đẹp" bảng tin.
*   **Cách dùng:** Nhập `@username` mục tiêu và số lượng follow muốn thực hiện, sau đó bấm **"Chạy"**.

### 4. 🔍 Audio Checker (Kiểm tra đơn)
*   **Chức năng:** Kiểm tra nhanh 1 link video hoặc link nhạc bất kỳ.
*   **Cách dùng:** Dán link vào ô nhập liệu và bấm **"Kiểm tra ngay"**. Tool sẽ báo kết quả Đạt/Loại AI ngay lập tức trên Terminal.

### 5. 🌱 Channel Expander (Mở rộng kênh)
*   **Chức năng:** Tự động tìm kiếm các kênh tương tự dựa trên danh sách kênh bạn đang có.
*   **Cách dùng:** Bấm nút **"Tìm thêm tác giả"** trong tab Miner.

---

## 📂 Cấu trúc Thư mục Quan trọng
*   `tool_nhac/`: Chứa Dashboard và logic xử lý AI.
*   `tool_sroll_feed/`: Chứa engine lướt FYP và browser automation.
*   `creators_list.txt`: Danh sách các kênh mục tiêu để đào.
*   `seen_creators.txt`: Danh sách các kênh đã quét xong (để tránh quét trùng).

---

## 🛡️ Lưu ý Bảo mật
*   **KHÔNG** chia sẻ file `cookies.json` hoặc thư mục `tiktok_session` cho người lạ vì chúng chứa phiên đăng nhập tài khoản của bạn.
*   Project đã cấu hình `.gitignore` chuẩn để không đẩy các dữ liệu nhạy cảm lên GitHub.

---
*Phát triển bởi Antigravity AI Coding Assistant.*

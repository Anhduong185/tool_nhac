import pandas as pd
import os

# Đường dẫn 2 file Excel
HISTORY_FILE = "results_history.xlsx"
SESSION_FILE = "results_session.xlsx"

def init_excel():
    """Khởi tạo file lịch sử và làm sạch file phiên chạy này"""
    # File lịch sử
    if not os.path.exists(HISTORY_FILE):
        df = pd.DataFrame(columns=['LINK TIKTOK', 'LƯỢT SỬ DỤNG (K)', 'AUDIO_ID', 'NGÀY QUÉT'])
        df.to_excel(HISTORY_FILE, index=False)
    
    # File phiên chạy này: Luôn tạo mới (xóa cũ)
    df_session = pd.DataFrame(columns=['LINK TIKTOK', 'LƯỢT SỬ DỤNG (K)', 'AUDIO_ID', 'NGÀY QUÉT'])
    df_session.to_excel(SESSION_FILE, index=False)
    print(f"📁 Đã làm mới file phiên này: {SESSION_FILE}")

def get_existing_links():
    """Lấy danh sách link từ file lịch sử để check trùng"""
    if not os.path.exists(HISTORY_FILE):
        return [], []
    try:
        df = pd.read_excel(HISTORY_FILE)
        links = df['LINK TIKTOK'].dropna().tolist()
        ids = df['AUDIO_ID'].dropna().tolist()
        return links, ids
    except Exception as e:
        print(f"Lỗi đọc file Excel: {e}")
        return [], []

def save_to_excel(link, usage, audio_id):
    """Lưu kết quả vào cả 2 file"""
    from datetime import datetime
    new_data = {
        'LINK TIKTOK': link,
        'LƯỢT SỬ DỤNG (K)': usage,
        'AUDIO_ID': audio_id,
        'NGÀY QUÉT': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # 1. Lưu vào Lịch sử
    try:
        df_hist = pd.read_excel(HISTORY_FILE)
        df_hist = pd.concat([df_hist, pd.DataFrame([new_data])], ignore_index=True)
        df_hist.to_excel(HISTORY_FILE, index=False)
    except:
        pass
        
    # 2. Lưu vào Phiên này
    try:
        df_sess = pd.read_excel(SESSION_FILE)
        df_sess = pd.concat([df_sess, pd.DataFrame([new_data])], ignore_index=True)
        df_sess.to_excel(SESSION_FILE, index=False)
    except:
        pass

import asyncio
from playwright.async_api import async_playwright
import json
from pathlib import Path

COOKIES_FILE = Path("e:/tool_nhac/tool_nhac/cookies.json")

async def main():
    print("🚀 Đang khởi động trình duyệt để đăng nhập TikTok...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome", args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        await page.goto("https://www.tiktok.com/login")
        print("\n" + "="*50)
        print("🚨 HƯỚNG DẪN ĐĂNG NHẬP:")
        print("1. Hãy quét mã QR hoặc đăng nhập bằng tài khoản của bạn trên cửa sổ vừa hiện ra.")
        print("2. Sau khi đăng nhập thành công và thấy trang chủ TikTok (có video chạy), hãy quay lại đây và nhấn Enter.")
        print("="*50 + "\n")
        
        input("👉 Nhấn Enter TẠI ĐÂY sau khi bạn đã đăng nhập thành công: ")

        print("\n⏳ Đang lưu phiên đăng nhập (Cookies)...")
        await context.storage_state(path=str(COOKIES_FILE))
        
        # Verify JSON
        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                json.loads(f.read())
            print(f"✅ Đã lưu cookie thành công tại: {COOKIES_FILE}")
        except Exception as e:
            print(f"❌ Lỗi khi lưu cookie: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

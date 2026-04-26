import asyncio
import random
from playwright.async_api import async_playwright

# Cấu hình
TARGET_CREATOR = "@a1737018"  # Thay tên tác giả vào đây (không cần link đầy đủ)
MAX_FOLLOWS = 200            # CẢNH BÁO: TikTok giới hạn ~200 lượt follow/ngày. Quá con số này sẽ bị block tính năng!

async def main(target_creator, max_follows):
    print(f"🚀 Khởi động Bot Auto-Follow cho danh sách Following của {target_creator}")
    print(f"⚠️ LƯU Ý: Mục tiêu tối đa {max_follows} người. TikTok giới hạn ~200 lượt follow/ngày.")
    
    async with async_playwright() as p:
        # Mở Chrome có giao diện, dùng lại cookie của trình lướt FYP
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="./tiktok_session",
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 720},
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = browser.pages[0]
        
        # 1. Truy cập trang profile
        clean_target = target_creator.strip()
        if not clean_target.startswith('@'):
            clean_target = '@' + clean_target
            
        profile_url = f"https://www.tiktok.com/{clean_target}"
        print(f"🔗 Đang truy cập: {profile_url}")
        await page.goto(profile_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
        # 2. Mở danh sách "Đang theo dõi" (Following)
        try:
            print("⏳ Đang chờ danh sách 'Đang theo dõi' bật lên (tự động click hoặc bạn click tay)...")
            is_open = False
            following_btn = None
            
            # Vòng lặp chờ tối đa 60 giây (mỗi giây kiểm tra 1 lần)
            for wait_time in range(60):
                # 1. Xem danh sách có đang mở sẵn chưa (có thể do user tự click)
                is_open = await page.evaluate('''() => {
                    let dialogs = Array.from(document.querySelectorAll('div[role="dialog"]'));
                    return dialogs.some(d => d.innerText.includes('Following') || d.innerText.includes('Đang theo dõi') || d.innerText.includes('Followers'));
                }''')
                
                if is_open:
                    print(f"✅ Danh sách đã bật mở! (Mất {wait_time}s)")
                    break
                    
                # 2. Nếu chưa mở, thử tự tìm nút Following và click
                selectors = [
                    f'a[href="/{clean_target}/following"]',
                    'a[data-e2e="following"]',
                    'div[data-e2e="following"]',
                    'strong[data-e2e="following-count"]'
                ]
                
                for sel in selectors:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        following_btn = btn
                        break
                        
                if following_btn:
                    await following_btn.click()
                    print(f"✅ Tool đã TỰ ĐỘNG click nút 'Đang theo dõi' (Mất {wait_time}s)")
                    await asyncio.sleep(3)
                    is_open = True
                    break
                    
                # Nếu không tìm thấy gì, chờ 1 giây rồi thử lại
                await asyncio.sleep(1)
                if wait_time % 10 == 0 and wait_time > 0:
                    print(f"   ... Vẫn đang đợi ({wait_time}/60s). Nếu bị vướng Captcha, hãy tự kéo tay đi nhé!")
                    
            if not is_open:
                print("❌ Hết 60s chờ mà vẫn không thấy danh sách. Tool sẽ dừng ở kênh này.")
                return
                
            await asyncio.sleep(2)
        except Exception as e:
            print(f"❌ Lỗi mở danh sách: {e}")
            return

        # 3. Lặp: Cuộn và Follow
        followed_count = 0
        consecutive_errors = 0
        
        while followed_count < max_follows:
            try:
                # Tìm tất cả các nút Follow chưa bấm bằng JS (chính xác hơn query_selector)
                # Chỉ lấy nút có chữ "Follow" hoặc "Theo dõi", không lấy "Following" hay "Đang theo dõi"
                btn_handles = await page.query_selector_all('button')
                unfollowed_btns = []
                for btn in btn_handles:
                    try:
                        if await btn.is_visible():
                            text = (await btn.inner_text()).strip().lower()
                            if text in ["follow", "theo dõi"]:
                                unfollowed_btns.append(btn)
                    except:
                        pass
                
                if not unfollowed_btns:
                    # Nếu trên màn hình hết nút chưa follow -> Cuộn bảng xuống
                    print("🖱️ Hết nút trên màn hình, đang cuộn bảng tìm thêm...")
                    # Scroll thẻ div chứa danh sách
                    await page.evaluate('''() => {
                        const list = document.querySelector('div[data-e2e="user-list"]') || 
                                     document.querySelectorAll('div[style*="overflow-y: scroll"]')[0] ||
                                     document.querySelectorAll('div[role="dialog"] > div > div:nth-child(2)')[0];
                        if (list) list.scrollTop += 1500;
                    }''')
                    await asyncio.sleep(3)
                    consecutive_errors += 1
                    
                    if consecutive_errors > 5:
                        print("🏁 Đã cuộn 5 lần nhưng không tìm thấy ai mới. Có thể đã hết danh sách.")
                        break
                    continue
                
                consecutive_errors = 0 # reset
                
                for btn in unfollowed_btns:
                    if followed_count >= max_follows:
                        break
                        
                    # Bấm follow
                    await btn.click()
                    followed_count += 1
                    print(f"👤 Đã follow người thứ {followed_count}/{max_follows}")
                    
                    # NGHỈ NGƠI: Cực kỳ quan trọng để lách thuật toán chống bot
                    delay = random.uniform(2.5, 5.5)
                    await asyncio.sleep(delay)
                    
                    # Kiểm tra xem có bị block không (thường hiện popup "You're following too fast")
                    toast = await page.query_selector('div[data-e2e="toast"]')
                    if toast:
                        toast_text = await toast.inner_text()
                        if "fast" in toast_text.lower() or "nhanh" in toast_text.lower():
                            print("🚨 TikTok phát hiện tốc độ quá nhanh! Đang tạm nghỉ 30 giây...")
                            await asyncio.sleep(30)
                
            except Exception as e:
                print(f"⚠️ Lỗi trong lúc lặp: {e}")
                await asyncio.sleep(2)
                
        print(f"🎉 Hoàn thành! Đã follow thành công {followed_count} người.")
        await asyncio.sleep(5)
        await browser.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=str, default="@a1737018", help="Target creator username")
    parser.add_argument("--max", type=int, default=200, help="Max follows")
    args = parser.parse_args()
    
    TARGET_CREATOR = args.target
    MAX_FOLLOWS = args.max
    
    asyncio.run(main(args.target, args.max))

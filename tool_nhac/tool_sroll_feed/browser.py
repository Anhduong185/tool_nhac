import time
import asyncio
import random
import re
from playwright.async_api import async_playwright
from config import BROWSER_CONFIG

class TikTokAgent:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None

    async def start(self):
        self.pw = await async_playwright().start()
        
        if BROWSER_CONFIG.get("use_existing_browser"):
            try:
                self.browser = await self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{BROWSER_CONFIG['remote_debugging_port']}")
                self.context = self.browser.contexts[0]
                
                self.page = None
                for p in self.context.pages:
                    if "tiktok.com" in p.url:
                        self.page = p
                        break
                
                if not self.page:
                    print("Không tìm thấy tab TikTok. Đang tạo mới...")
                    self.page = await self.context.new_page()
                    await self.page.goto("https://www.tiktok.com/foryou")
                
            except Exception as e:
                print(f"Lỗi kết nối trình duyệt cũ: {e}")
                await self._launch_new_browser()
        else:
            await self._launch_new_browser()

    async def _launch_new_browser(self):
        self.browser = await self.pw.chromium.launch_persistent_context(
            user_data_dir=BROWSER_CONFIG["user_data_dir"],
            headless=BROWSER_CONFIG["headless"],
            viewport=BROWSER_CONFIG["viewport"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
            ],
            ignore_default_args=["--enable-automation"]
        )
        self.page = self.browser.pages[0]
        
        try:
            from playwright_stealth import stealth_async
            await stealth_async(self.page)
        except:
            pass

    async def go_to_feed(self):
        try:
            await self.page.goto("https://www.tiktok.com/foryou", wait_until="load", timeout=30000)
            await asyncio.sleep(5)
        except Exception as e:
            print(f"⚠️ Cảnh báo khi tải feed: {e}")

    async def scroll(self):
        print("🖱️ Đang lướt xuống video tiếp theo...")
        await self.page.keyboard.press("ArrowDown")
        await asyncio.sleep(random.uniform(3, 6))

    def _calculate_sound_score(self, sound_text):
        """
        Tính điểm tiềm năng của audio gốc:
        - Trả về -1 nếu là "nhạc nền" (loại thẳng)
        +2 nếu có "original"
        +1 nếu có "sound", "son", "âm thanh"
        +1 nếu có dấu "-" (pattern rename phổ biến)
        """
        text = sound_text.lower()
        if "nhạc nền" in text:
            return -1
            
        score = 0
        if "original" in text: score += 2
        if any(k in text for k in ["sound", "son", "âm thanh"]): score += 1
        if "-" in text: score += 1
        return score

    async def get_current_video_info(self):
        try:
            selectors = [
                'section[id^="media-card-"]',
                'div[data-e2e="feed-item-video"]',
                'div[data-e2e="recommend-list-item-container"]'
            ]
            
            video_elements = []
            for selector in selectors:
                video_elements = await self.page.query_selector_all(selector)
                if video_elements: break
            
            if not video_elements:
                videos = await self.page.query_selector_all('video')
                if videos: video_elements = [videos[0]]
                else: return {"is_original": False}

            for el in video_elements:
                if await el.is_visible():
                    # --- KIỂM TRA ĐÃ THÍCH / ĐÃ LƯU ---
                    like_btn = await el.query_selector('button[aria-label*="Unlike"]')
                    fav_btn = await el.query_selector('button[aria-label*="Remove from Favorites"], button[aria-label*="Favorited"]')
                    
                    if like_btn or fav_btn:
                        print("⏭️ Video này bạn đã Thích hoặc Lưu rồi. Bỏ qua.")
                        return {"is_original": False, "skip": True}

                    # --- LẤY THÔNG TIN NHẠC ---
                    sound_el = await el.query_selector('a[href*="/music/"]')
                    if sound_el:
                        sound_text = await sound_el.inner_text()
                        sound_link = await sound_el.get_attribute("href")
                        
                        score = self._calculate_sound_score(sound_text)
                        
                        if score < 0:
                            print(f"⏭️ Bỏ qua nhạc nền: {sound_text}")
                            continue

                        print(f"🎵 Nhạc: {sound_text} (Score: {score})")
                        
                        # --- LẤY LINK VIDEO TỪ FEED ---
                        all_links = await el.query_selector_all('a')
                        video_link = None
                        for link in all_links:
                            href = await link.get_attribute("href")
                            if href and "/video/" in href:
                                video_link = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                                break

                        # Luôn trả về thông tin nếu có link nhạc, không skip ở đây nữa
                        return {
                            "is_original": True, # Đánh dấu là tiềm năng
                            "sound_link": f"https://www.tiktok.com{sound_link}" if sound_link.startswith("/") else sound_link,
                            "video_link": video_link,
                            "sound_score": score
                        }


            return {"is_original": False}
        except Exception as e:
            print(f"Error video info: {e}")
            return {"is_original": False}


    async def extract_audio_details(self, audio_url):
        print(f"🔗 Đang truy cập trang nhạc: {audio_url}")
        try:
            await self.page.goto(audio_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # 1. Lấy lượt sử dụng
            usage_el = await self.page.query_selector('h2[data-e2e="music-video-count"]')
            usage_text = await usage_el.inner_text() if usage_el else "0"
            usage_count = self._parse_count(usage_text)
            
            # 2. Lấy link video ĐẦU TIÊN từ Grid
            video_links = await self.page.query_selector_all('a[href*="/video/"]')
            grid_video_link = None
            first_video_el = None
            
            for link in video_links:
                href = await link.get_attribute("href")
                if href and "/video/" in href:
                    grid_video_link = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                    first_video_el = link
                    break
            
            if not first_video_el:
                print("⚠️ Không tìm thấy video nào trong grid.")
                return None

            # 3. Click vào video để lấy thời lượng (Theo yêu cầu người dùng)
            print("🖱️ Đang click vào video đầu tiên để lấy metadata...")
            await first_video_el.click()
            await asyncio.sleep(3) # Đợi popup load

            # 4. Lấy thời lượng từ popup
            duration = 0
            # Thử nhiều selector cho thời lượng
            duration_selectors = [
                'div[class*="DivSeekBarTimeContainer"]',
                'div:has(> [aria-label="progress bar"])',
                'span[class*="SpanDuration"]'
            ]
            
            duration_text = ""
            for sel in duration_selectors:
                el = await self.page.query_selector(sel)
                if el:
                    duration_text = await el.inner_text()
                    if duration_text: break
            
            if duration_text:
                print(f"🕒 Tìm thấy text thời lượng: {duration_text}")
                duration = self._parse_duration(duration_text)

            audio_id = audio_url.split("/")[-1].split("?")[0]
            
            return {
                "audio_id": audio_id, 
                "usage_count": usage_count, 
                "grid_video_link": grid_video_link,
                "duration": duration,
                "year": 2024,
                "recent_usage": 0,
                "source_type": "original"
            }
        except Exception as e:
            print(f"Error audio details: {e}")
            return None

    def _parse_duration(self, text):
        """
        Parse các định dạng: '00:12', '00:02/00:12', '11.8 s', '1:30'
        """
        try:
            text = text.lower().strip()
            # Trường hợp '11.8 s'
            if 's' in text:
                val = text.replace('s', '').strip()
                return float(val)
            
            # Trường hợp '00:02/00:12'
            if '/' in text:
                text = text.split('/')[-1].strip()
            
            # Trường hợp '00:12' hoặc '1:30'
            parts = text.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            
            return float(re.sub(r'[^\d.]', '', text) or 0)
        except:
            return 0



    async def go_back(self):
        print("⬅️ Đang quay lại Feed...")
        try:
            # Nếu đang ở video popup (URL chứa /video/), go_back có thể chỉ đóng popup
            # Chúng ta thử go_back tối đa 2 lần hoặc dùng go_to_feed nếu không về được
            current_url = self.page.url
            await self.page.go_back(wait_until="domcontentloaded", timeout=5000)
            await asyncio.sleep(1)
            
            # Nếu vẫn ở music page hoặc video page, thử go_back lần nữa hoặc nhảy thẳng về feed
            if "music" in self.page.url or "video" in self.page.url:
                await self.page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded")
            
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Lỗi go_back: {e}. Đang về feed trực tiếp...")
            await self.go_to_feed()



    def _parse_count(self, text):
        text = text.lower().replace("posts", "").replace("video", "").strip()
        if 'm' in text: return int(float(text.replace('m', '')) * 1000000)
        if 'k' in text: return int(float(text.replace('k', '')) * 1000)
        return int(re.sub(r'\D', '', text) or 0)

    async def close(self):
        try:
            if self.browser: await self.browser.close()
        except: pass
        try:
            if self.pw: await self.pw.stop()
        except: pass

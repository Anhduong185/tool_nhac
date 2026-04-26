import time
import asyncio
import random
import re
from playwright.async_api import async_playwright
from config import BROWSER_CONFIG
from engine import FilterEngine

class TikTokAgent:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None
        self._metadata_cache = {}  # audio_id -> TikTok metadata dict
        self._video_music_cache = {} # video_id -> music dict

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
        self.context = await self.pw.chromium.launch_persistent_context(
            user_data_dir=BROWSER_CONFIG["user_data_dir"],
            channel="chrome",
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
        self.browser = self.context # Alias for compatibility
        self.page = self.context.pages[0]
        
        try:
            from playwright_stealth import stealth_async
            await stealth_async(self.page)
        except:
            pass
        
        # Fix 4: Đăng ký intercept API response để đọc metadata ẩn TikTok
        self.page.on("response", self._intercept_api_response)

    async def _intercept_api_response(self, response):
        """
        Intercept API response từ FYP để đọc metadata ẩn TikTok:
        hasLyrics, isCommerce, categoryType, authorName, musicByAuthor.
        Cache lại theo audio_id để filter trước khi mở trang nhạc.
        """
        try:
            if "tiktok.com" not in response.url:
                return
            if "application/json" not in response.headers.get("content-type", ""):
                return
            data = await response.json()
            items = data.get("itemList") or data.get("data") or data.get("aweme_list") or data.get("items") or []
            if not isinstance(items, list) or not items:
                return
            first_item = items[0].get("item", items[0]) if items else {}
            if not (first_item.get("music") or first_item.get("id")):
                return
            print(f"📡 API: {len(items)} videos → {response.url.split('?')[0][-55:]}")
            for item_wrapper in items:
                item = item_wrapper.get("item", item_wrapper)
                music = item.get("music", {})
                audio_id = str(music.get("id", ""))
                if not audio_id:
                    continue
                
                meta_dict = {
                    "hasLyrics":      bool(music.get("hasLyrics", False)),
                    "isCommerce":     bool(music.get("isCommerce") or music.get("commerceInfo") or music.get("is_commerce_music") or False),
                    "isCopyrighted":  bool(music.get("isCopyrighted", False)),
                    "categoryType":   int(music.get("categoryType") or music.get("category") or -1),
                    "authorName":     str(music.get("authorName") or music.get("author") or ""),
                    "musicByAuthor":  bool(music.get("musicByAuthor", True)),
                    "original":       bool(music.get("original", True)),
                }
                self._metadata_cache[audio_id] = meta_dict
                
                # Lưu cả cache theo video_id để dùng làm fallback nếu UI giấu nhạc
                video_id = str(item.get("id", ""))
                if video_id:
                    self._video_music_cache[video_id] = {
                        "audio_id": audio_id,
                        "audio_name": str(music.get("title") or ""),
                        "playUrl": str(music.get("playUrl") or ""),
                        "meta": meta_dict
                    }
        except Exception:
            pass

    async def go_to_feed(self):
        try:
            await self.page.goto("https://www.tiktok.com/foryou", wait_until="load", timeout=30000)
            await asyncio.sleep(5)
            
            # Chỉ tắt nút x (close) của login modal nếu nó hiện lên che màn hình
            try:
                close_btn = await self.page.query_selector('div[data-e2e="modal-close-inner-button"]')
                if close_btn:
                    await close_btn.click()
                    print("✅ Đã tắt bảng popup che màn hình để lướt tiếp")
                    await asyncio.sleep(1)
            except Exception:
                pass
                
        except Exception as e:
            print(f"⚠️ Cảnh báo khi tải feed: {e}")

    async def check_page_health(self) -> bool:
        """
        Kiểm tra trang còn phản hồi không.
        Nếu trang bị đơ hoặc freeze → trả về False để trigger reload.
        """
        try:
            result = await asyncio.wait_for(
                self.page.evaluate("() => document.readyState"),
                timeout=5.0
            )
            return result in ("complete", "interactive")
        except Exception:
            return False

    async def reload_feed(self):
        """
        Reload trang FYP sạch để giải phóng bộ nhớ và reset trạng thái.
        Gọi sau mỗi N video hoặc khi phát hiện trang bị lag.
        """
        print("🔄 [Health] Reloading trang FYP để giải phóng bộ nhớ...")
        try:
            await self.page.goto(
                "https://www.tiktok.com/foryou",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await asyncio.sleep(4)
            print("✅ [Health] Reload thành công, tiếp tục...")
        except Exception as e:
            print(f"⚠️ [Health] Lỗi reload: {e}")

    async def scroll(self):
        print("🖱️ Đang lướt xuống video tiếp theo...")
        await self.page.keyboard.press("ArrowDown")
        await asyncio.sleep(random.uniform(3, 6))

    def _calculate_sound_score(self, sound_text):
        """Sử dụng FilterEngine để đánh giá tên âm thanh"""
        is_original, reason = FilterEngine.is_original_sound(sound_text)
        return 2 if is_original else -1

    async def get_current_video_info(self):
        try:
            await asyncio.sleep(1.5)  # Đợi video load sau scroll

            # ── BƯỚC 1: Lấy link nhạc - retry tối đa 5 lần ────────────────────
            sound_text = ""
            sound_link = ""
            sound_el = None
            for attempt in range(5):
                music_links = await self.page.query_selector_all('a[href*="/music/"]')
                for ml in music_links:
                    if await ml.is_visible():
                        sound_el = ml
                        break
                if sound_el:
                    break
                await asyncio.sleep(1)  # Chờ thêm nếu chưa render

            if sound_el:
                sound_text = (await sound_el.inner_text()).strip()
                sound_link = await sound_el.get_attribute("href") or ""
                # Nếu text rỗng (chưa render ticker), lấy tên từ API cache
                if not sound_text and sound_link:
                    audio_id_temp = sound_link.split("/")[-1].split("-")[-1].split("?")[0]
                    cached_video = next(
                        (v for v in self._video_music_cache.values() 
                         if v.get("audio_id") == audio_id_temp), None
                    )
                    if cached_video:
                        sound_text = cached_video.get("audio_name", "")
                    if not sound_text:
                        # Dùng phần cuối của href làm fallback
                        sound_text = sound_link.split("/")[-1].replace("-", " ").strip()
                print(f"🎼 Found: '{sound_text}' → {sound_link[:50]}")
            else:
                # Debug: liệt kê tất cả href trên trang để chẩn đoán
                all_a = await self.page.query_selector_all('a[href]')
                music_hrefs = []
                for a in all_a[:50]:  # Giới hạn 50
                    h = await a.get_attribute("href") or ""
                    if "/music/" in h or "/sound/" in h:
                        music_hrefs.append(h[:60])
                if music_hrefs:
                    print(f"🔎 Debug hrefs music tìm thấy: {music_hrefs[:3]}")
                else:
                    print(f"🔎 Debug: Không có href /music/ nào trên page!")

            # ── BƯỚC 2: Lấy video link đang hiển thị ──────────────────────────
            video_link = None
            video_id = ""
            video_links = await self.page.query_selector_all('a[href*="/video/"]')
            for vl in video_links:
                if await vl.is_visible():
                    href = await vl.get_attribute("href") or ""
                    cand = href.split("/video/")[-1].split("?")[0]
                    if cand.isdigit() and len(cand) > 15:
                        video_link = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                        video_id = cand
                        break

            # ── BƯỚC 3: Kiểm tra đã thích/lưu chưa ────────────────────────────
            unlike_btn = await self.page.query_selector('button[aria-label*="Unlike"]')
            fav_btn = await self.page.query_selector('button[aria-label*="Remove from Favorites"], button[aria-label*="Favorited"]')
            if unlike_btn or fav_btn:
                print("⏭️ Video đã Thích/Lưu rồi. Bỏ qua.")
                return {"is_original": False, "skip": True}

            # ── BƯỚC 4: Nếu không thấy link nhạc → vào trang video để lấy ──────
            if (not sound_text or not sound_link) and video_link:
                print(f"🔍 Link nhạc ẩn, thử vào trang video {video_id}...")
                try:
                    await self.page.goto(video_link, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    mel = await self.page.query_selector('a[href*="/music/"]')
                    if mel and await mel.is_visible():
                        sound_text = (await mel.inner_text()).strip()
                        sound_link = await mel.get_attribute("href") or ""
                        print(f"✅ Lấy được từ trang video: {sound_text}")
                    await self.page.go_back()
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"⚠️ Lỗi vào trang video: {e}")

            if not sound_text or not sound_link:
                print(f"⚠️ Không tìm thấy nhạc (video {video_id or 'unknown'})")
                return {"is_original": False}

            # ── BƯỚC 5: Lấy meta từ cache API ──────────────────────────────────
            audio_id_from_link = sound_link.split("/")[-1].split("-")[-1].split("?")[0] if sound_link else ""
            meta = self._metadata_cache.get(audio_id_from_link, {})
            play_url = self._video_music_cache.get(video_id, {}).get("playUrl", "") if video_id else ""
            
            # Fallback meta từ video_id cache
            if not meta and video_id and video_id in self._video_music_cache:
                cached = self._video_music_cache[video_id]
                meta = cached.get("meta", {})
                play_url = cached.get("playUrl", "")
                if not sound_text:
                    sound_text = cached.get("audio_name", "")
                    sound_link = f"/music/sound-{cached['audio_id']}"

            # ── BƯỚC 6: Lọc metadata API ────────────────────────────────────────
            if meta.get("isCopyrighted"):
                print(f"⏭️ [Meta] {sound_text} → isCopyrighted")
                return {"is_original": False}
            if meta.get("hasLyrics"):
                print(f"⏭️ [Meta] {sound_text} → hasLyrics")
                return {"is_original": False}
            if not meta.get("original", True):
                print(f"⏭️ [Meta] {sound_text} → not original")
                return {"is_original": False}
            sfx_authors = ["tiktok sound", "tiktok effect", "sound effect", "sfx"]
            if any(p in meta.get("authorName", "").lower() for p in sfx_authors):
                print(f"⏭️ [Meta] {sound_text} → SFX author")
                return {"is_original": False}

            # ── BƯỚC 7: Score ────────────────────────────────────────────────────
            score = self._calculate_sound_score(sound_text)
            if score < 0:
                print(f"⏭️ Bỏ qua nhạc nền: {sound_text}")
                return {"is_original": False}

            print(f"🎵 Nhạc: {sound_text} (Score: {score})")

            sound_link_full = f"https://www.tiktok.com{sound_link}" if sound_link.startswith("/") else sound_link
            return {
                "is_original": True,
                "sound_name": sound_text,
                "sound_link": sound_link_full,
                "video_link": video_link,
                "sound_score": score,
                "play_url": play_url
            }

        except Exception as e:
            print(f"Error video info: {e}")
            return {"is_original": False}




    async def extract_audio_details(self, audio_url, play_url=""):
        print(f"🔗 Đang mở tab mới để truy cập trang nhạc: {audio_url}")
        new_page = await self.context.new_page()
        try:
            await new_page.goto(audio_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            
            # 1. Lấy tên audio
            title_el = await new_page.query_selector('h1[data-e2e="music-title"]')
            details_title = await title_el.inner_text() if title_el else ""

            # 2. Lấy lượt sử dụng
            usage_el = await new_page.query_selector('h2[data-e2e="music-video-count"]')
            usage_text = await usage_el.inner_text() if usage_el else "0"
            usage_count = self._parse_count(usage_text)

            # 3. Lấy URL CDN (để check library music)
            audio_cdn_url = ""
            audio_tag = await new_page.query_selector('audio, video')
            if audio_tag:
                audio_cdn_url = await audio_tag.get_attribute('src')

            page_content = await new_page.content()
            
            # Fallback lấy audio_cdn_url từ source JSON
            if not audio_cdn_url:
                match_url = re.search(r'"playUrl":"(https?[^"]+)"', page_content)
                if match_url:
                    audio_cdn_url = match_url.group(1).encode('utf-8').decode('unicode_escape')

            # Fallback cực mạnh: lấy từ API intercept nãy truyền vào
            if not audio_cdn_url and play_url:
                audio_cdn_url = play_url
                print("♻️ Dùng URL CDN từ API Cache vì trên web bị ẩn.")

            # Tải file mp3 qua Playwright context (có cookie TikTok, tránh 403)
            audio_bytes = b""
            if audio_cdn_url:
                try:
                    api_req_ctx = await self.context.request.get(
                        audio_cdn_url,
                        headers={
                            "Referer": "https://www.tiktok.com/",
                            "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.8",
                        }
                    )
                    if api_req_ctx.ok:
                        audio_bytes = await api_req_ctx.body()
                        print(f"✅ Tải được audio ({len(audio_bytes)//1024}KB) để Shazam kiểm tra")
                    else:
                        print(f"⚠️ CDN trả về {api_req_ctx.status}, bỏ qua Shazam")
                except Exception as e:
                    print(f"⚠️ Lỗi tải audio CDN: {e}")

            # Fallback lấy usage count
            if not usage_count:
                match_count = re.search(r'"videoCount":(\d+)', page_content)
                if match_count:
                    usage_count = int(match_count.group(1))

            # Lấy thời lượng từ source JSON (rất chính xác, không cần click video)
            duration = 0
            match_dur = re.search(r'"duration":(\d+)', page_content)
            if match_dur:
                duration = int(match_dur.group(1))

            # 2. Lấy link video ĐẦU TIÊN từ Grid
            grid_video_link = None
            first_video_el = None
            for _ in range(3):
                video_links = await new_page.query_selector_all('a[href*="/video/"]')
                for link in video_links:
                    href = await link.get_attribute("href")
                    if href and "/video/" in href:
                        grid_video_link = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                        first_video_el = link
                        break
                if first_video_el: break
                await asyncio.sleep(1)
            
            # Nếu không tìm thấy element trong DOM, thử regex
            if not grid_video_link:
                match_vid = re.search(r'"id":"(\d{18,20})"[\s\S]*?"desc":"', page_content)
                if match_vid:
                    grid_video_link = f"https://www.tiktok.com/@user/video/{match_vid.group(1)}"
            
            if not grid_video_link:
                print("⚠️ Không tìm thấy link video nào trong trang nhạc.")
                return None

            if not duration and first_video_el:
                # 3. Click vào video để lấy thời lượng nếu JSON không có
                print("🖱️ Đang click vào video đầu tiên để lấy metadata...")
                try:
                    await first_video_el.click()
                    await asyncio.sleep(3) # Đợi popup load
                    duration_selectors = [
                        'div[class*="DivSeekBarTimeContainer"]',
                        'div:has(> [aria-label="progress bar"])',
                        'span[class*="SpanDuration"]'
                    ]
                    for sel in duration_selectors:
                        el = await new_page.query_selector(sel)
                        if el:
                            duration_text = await el.inner_text()
                            if duration_text:
                                print(f"🕒 Tìm thấy text thời lượng: {duration_text}")
                                duration = self._parse_duration(duration_text)
                                break
                except Exception as e:
                    print(f"Lỗi click video: {e}")

            audio_id = audio_url.split("/")[-1].split("?")[0]
            
            return {
                "audio_id": audio_id, 
                "audio_name": details_title or "",
                "audio_url": audio_cdn_url or "",
                "audio_bytes": audio_bytes,
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
        finally:
            try:
                await new_page.close()
            except Exception:
                pass

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



    async def like_and_save_video(self):
        """
        Tự động thả Tim + Bấm Lưu (Yêu Thích) video hiện tại.
        Mục đích kháng:
          1. Nưới thuật toán TikTok → FYP sẽ đề xuất thêm nội dung tương tự
          2. Dấu hiệu nhớ video này đã được lấy link → lần sau tự động bỏ qua
        """
        liked = False
        saved = False
        try:
            # Thả TIM (Like)
            like_selectors = [
                'button[aria-label="Like"]',
                'button[aria-label*="like" i]:not([aria-label*="Unlike" i])',
                '[data-e2e="like-icon"]',
                'span[data-e2e="like-count"]',
            ]
            for sel in like_selectors:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    try:
                        await btn.click(timeout=3000, force=True)
                        await asyncio.sleep(0.5)
                        liked = True
                        print("❤️ Đã thả Tim")
                    except Exception as e:
                        print(f"⚠️ Lỗi click Tim: {e}")
                    break

            # Đóng popup nếu nó hiện ra cản trở thao tác
            login_popup = await self.page.query_selector('#loginContainer')
            if login_popup and await login_popup.is_visible():
                print("⚠️ Phát hiện Login Popup, đang đóng...")
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(1)
                
            # Bấm LưU / Yêu Thích (Bookmark/Favorite)
            save_selectors = [
                'button[aria-label="Add to Favorites"]',
                'button[aria-label*="favorite" i]:not([aria-label*="Favorited" i])',
                '[data-e2e="collect-icon"]',
                '[data-e2e="undefined-icon"]',
            ]
            for sel in save_selectors:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    try:
                        await btn.click(timeout=3000, force=True)
                        await asyncio.sleep(0.5)
                        saved = True
                        print("🔖 Đã Lưu vào Yêu Thích")
                    except Exception as e:
                        print(f"⚠️ Lỗi click Lưu: {e}")
                    break

            if not liked and not saved:
                print("⚠️ Không tìm thấy nút Like/Save (có thể do giao diện TikTok thay đổi)")

        except Exception as e:
            print(f"⚠️ Lỗi khi Like/Save: {e}")

        return liked, saved

    async def follow_creator(self):
        """
        Bấm nút Follow (Theo dõi) tác giả của video hiện tại (trên luồng FYP) để dạy thuật toán.
        """
        try:
            # Nút follow thường nằm dưới avatar (icon dấu cộng màu đỏ) hoặc kế bên tên tác giả
            follow_selectors = [
                'button[data-e2e="feed-follow"]',
                'div[data-e2e="feed-follow"]',
                '.feed-follow', 
                'div[class*="DivFollowIcon"]',
                'button:has-text("Follow")',
                'button:has-text("Theo dõi")',
            ]
            for sel in follow_selectors:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    try:
                        await btn.click(timeout=3000, force=True)
                        await asyncio.sleep(0.5)
                        print("👤 Đã Follow tác giả để điều hướng thuật toán FYP")
                        return True
                    except Exception as e:
                        print(f"⚠️ Lỗi click Follow: {e}")
                    break
        except Exception as e:
            print(f"⚠️ Lỗi khi Follow: {e}")
        return False

    async def go_back(self):
        # Do we use a new tab for audio extraction now, we don't need to go back on the main page.
        # This function is kept for compatibility but does nothing to the feed.
        pass



    def _parse_count(self, text):
        text = text.lower().strip()
        # Chuyển phẩy thành chấm (VD: 12,4k -> 12.4k) vì TikTok VN dùng phẩy làm dấu thập phân
        if 'k' in text or 'm' in text:
            text = text.replace(',', '.')
            
        if 'm' in text:
            num_str = re.sub(r'[^\d.]', '', text)
            return int(float(num_str or 0) * 1000000)
        if 'k' in text:
            num_str = re.sub(r'[^\d.]', '', text)
            return int(float(num_str or 0) * 1000)
            
        # Không có k/m: xóa hết dấu câu (phân cách hàng nghìn) và chữ cái
        return int(re.sub(r'\D', '', text) or 0)

    async def close(self):
        try:
            if self.browser: await self.browser.close()
        except: pass
        try:
            if self.pw: await self.pw.stop()
        except: pass

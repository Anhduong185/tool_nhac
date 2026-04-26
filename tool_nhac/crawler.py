import json
import asyncio
import re
import urllib.parse
import random
from pathlib import Path
from typing import List, Optional
from playwright.async_api import async_playwright, Page, Response
from playwright_stealth import Stealth
from loguru import logger

from models import AudioMetadata
from config import SCROLL_COUNT, TARGET_AUDIOS, PAGE_LOAD_TIMEOUT, HEADLESS

class TikTokCrawler:
    def __init__(self):
        self.collected_audios: List[AudioMetadata] = []
        self.current_keyword = ""

    async def _intercept_response(self, response: Response):
        # DEBUG: Log tất cả XHR/fetch để phát hiện API endpoint mới của TikTok
        if response.request.resource_type in ("xhr", "fetch") and response.status == 200:
            url = response.url
            if "tiktok.com/api" in url:
                logger.debug(f"  [API] {url[:120]}")

        # Bắt các endpoint tìm kiếm - MỞ RỘNG pattern để không bỏ sót
        search_patterns = [
            "api/search/general/full/",
            "api/search/item",
            "api/search/video",
            "api/search/general",
            "api/post/item_list",
            "api/user/post",
            "api/music/item_list",   # AudioChain: video dùng âm thanh này
            "api/challenge/item_list", # Hashtag search
            "tiktok.com/api/recommend",
        ]
        if any(p in response.url for p in search_patterns):
            try:
                if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                    data = await response.json()
                    self._parse_api_response(data, url=response.url)
            except Exception:
                pass

    def _parse_api_response(self, data: dict, url: str = ""):
        # Thử tất cả các key có thể chứa danh sách video
        item_list = (
            data.get("data") or
            data.get("itemList") or
            data.get("item_list") or
            data.get("items") or
            data.get("videoList") or
            []
        )
        # TikTok đôi khi wrap trong object con
        if not item_list and isinstance(data.get("data"), dict):
            item_list = (
                data["data"].get("itemList") or
                data["data"].get("items") or
                []
            )
        if not isinstance(item_list, list):
            item_list = []

        # Debug: log keys khi không parse được (giúp chẩn đoán AudioChain)
        if not item_list and url:
            top_keys = list(data.keys())[:10]
            logger.debug(f"  [ParseDebug] {url.split('?')[0].split('/')[-2]} → empty item_list. Keys: {top_keys}")
            
        for item_wrapper in item_list:
            item = item_wrapper.get("item", item_wrapper)
            
            if not item or not isinstance(item, dict):
                continue
                
            music = item.get("music")
            if not music:
                continue

            try:
                audio_id = str(music.get("id", ""))
                audio_name = music.get("title", "")
                duration = music.get("duration", 0)

                # === DEBUG: Log toàn bộ music object lần đầu để khám phá field ẩn ===
                if not hasattr(self, '_music_keys_logged'):
                    self._music_keys_logged = True
                    all_fields = {k: v for k, v in music.items() if not isinstance(v, (dict, list))}
                    logger.info(f"[DEBUG] Music all scalar fields: {all_fields}")

                # TikTok có thể đổi tên field -> thử các tên có thể có
                usage_count = (
                    music.get("userCount") or
                    music.get("useCount") or
                    music.get("videoCount") or
                    music.get("playCount") or
                    music.get("authorUsageCount") or
                    music.get("statsCount") or
                    0
                )

                # Lấy view count và likes từ API response
                stats = item.get("stats", {}) or item.get("statsV2", {})
                video_play_count = int(
                    stats.get("playCount") or stats.get("play_count") or
                    stats.get("vvCount") or 0
                )
                video_likes = int(
                    stats.get("diggCount") or stats.get("likeCount") or 0
                )
                create_time = int(item.get("createTime", 0))

                audio_url = music.get("playUrl", "")
                music_id = music.get("id", "")

                # ── IDEA 2: Đọc METADATA ẨN từ TikTok API ────────────────────────
                tiktok_is_original = bool(music.get("original", True))

                # Tên tác giả nhạc - "TikTok" / "TikTok Sounds" = thư viện SFX
                tiktok_author_name = str(music.get("authorName") or music.get("author") or "")

                # categoryType: 1=music/song, 2=voice/speech, 3=sound_effect
                tiktok_category = int(
                    music.get("categoryType") or music.get("category") or
                    music.get("audioType") or -1
                )

                # tta = Text-To-Audio = AI generated voice
                tiktok_is_ai = bool(
                    music.get("tta") or music.get("isAiGen") or
                    music.get("isAiGenerated") or music.get("aiGeneratedContent") or False
                )

                # isCommerce = nhạc thương mại có hợp đồng
                tiktok_is_commerce = bool(music.get("isCommerce") or music.get("commerceInfo") or music.get("is_commerce_music") or False)
                
                # isCopyrighted = nhạc đã đăng ký bản quyền (Contains Music flag)
                tiktok_is_copyrighted = bool(music.get("isCopyrighted") or False)

                # hasLyrics = có lời bài hát → gần như chắc chắn là bài hát
                tiktok_has_lyrics = bool(music.get("hasLyrics") or False)

                # musicByAuthor = True nếu tác giả video tự tạo âm thanh
                tiktok_music_by_author = bool(music.get("musicByAuthor", True))
                # ── End metadata ẩn ─────────────────────────────────────────────

                audio_page_url = f"https://www.tiktok.com/music/{urllib.parse.quote(audio_name)}-{music_id}" if music_id else ""
                author_username = item.get('author', {}).get('uniqueId', 'user')
                video_url = f"https://www.tiktok.com/@{author_username}/video/{item.get('id', '')}"

                if audio_id and audio_url:
                    # Hard reject ngay: TikTok đánh dấu rõ là nhạc thư viện bản quyền
                    if not tiktok_is_original:
                        logger.debug(f"API reject (original=False): {audio_name}")
                        continue

                    # Hard reject: TikTok xác nhận audio chứa nhạc bản quyền
                    if tiktok_is_copyrighted:
                        logger.debug(f"API reject (isCopyrighted): {audio_name}")
                        continue

                    # Hard reject: bài hát có lời → gần như chắc chắn là nhạc
                    if tiktok_has_lyrics:
                        logger.debug(f"API reject (hasLyrics): {audio_name}")
                        continue

                    # Hard reject: tên tác giả nhạc là TikTok library
                    sfx_author_patterns = ["tiktok sound", "tiktok effect", "sound effect", "sfx", "soundeffect"]
                    if any(p in tiktok_author_name.lower() for p in sfx_author_patterns):
                        logger.debug(f"API reject (SFX author: {tiktok_author_name}): {audio_name}")
                        continue

                    from config import MAX_DURATION
                    if int(duration) > MAX_DURATION:
                        continue

                    effective_usage = usage_count if usage_count > 0 else -1

                    audio_meta = AudioMetadata(
                        audio_id=audio_id,
                        audio_name=audio_name,
                        duration=int(duration),
                        usage_count=effective_usage,
                        audio_url=audio_url,
                        audio_page_url=audio_page_url,
                        video_url=video_url,
                        keyword=self.current_keyword,
                        video_views=video_play_count,
                        video_likes=video_likes,
                        create_time=create_time,
                        author_username=author_username,
                        tiktok_is_original=tiktok_is_original,
                        tiktok_author_name=tiktok_author_name,
                        tiktok_category=tiktok_category,
                        tiktok_is_ai=tiktok_is_ai,
                        tiktok_is_commerce=tiktok_is_commerce,
                        tiktok_has_lyrics=tiktok_has_lyrics,
                        tiktok_music_by_author=tiktok_music_by_author,
                    )
                    if not any(a.audio_id == audio_id for a in self.collected_audios):
                        self.collected_audios.append(audio_meta)
                        logger.debug(
                            f"OK: {audio_name} | cat={tiktok_category} ai={tiktok_is_ai} "
                            f"lyrics={tiktok_has_lyrics} byAuthor={tiktok_music_by_author}"
                        )
                        
                        # ── Tự động bắt Tác giả tiềm năng (Global Seeding) ──
                        # Nếu video có nhiều lượt xem/tim, lưu lại tác giả để Creator Scanner quét sau
                        if video_play_count > 100000 or video_likes > 5000:
                            if author_username and author_username != "user":
                                from config import ROOT_DIR
                                creators_file = ROOT_DIR / "creators_list.txt"
                                try:
                                    existing = creators_file.read_text(encoding="utf-8") if creators_file.exists() else ""
                                    if f"@{author_username}" not in existing:
                                        with open(creators_file, "a", encoding="utf-8") as f:
                                            f.write(f"\n@{author_username} # auto-seed from {self.current_keyword}\n")
                                        logger.success(f"🌱 Đã tóm được Tác giả VIP nước ngoài: @{author_username}")
                                except Exception as e:
                                    logger.error(f"Lỗi ghi creator: {e}")
            except Exception as e:
                pass


    # =========================================================================
    # CƠ CHẾ CHỜ CAPTCHA TỰ ĐỘNG & XỬ LÝ LỖI MẠNG
    # =========================================================================
    async def _handle_error_page(self, page):
        """Tự động ấn nút Try again / Thử lại nếu TikTok bị lỗi server."""
        try:
            error_btn = page.locator('button:has-text("Try again"), button:has-text("Thử lại")')
            if await error_btn.count() > 0 and await error_btn.first.is_visible(timeout=1000):
                from loguru import logger
                logger.warning("⚠️ Phát hiện màn hình lỗi 'Something went wrong'. Đang tự động ấn Thử lại...")
                await error_btn.first.click()
                await asyncio.sleep(4)
        except Exception:
            pass

    async def _wait_for_captcha(self, page, context, check_timeout=2000):
        """Phát hiện và dừng tiến trình nếu TikTok bắt giải Captcha."""
        try:
            # Selector phổ biến của TikTok Captcha iframe/container
            captcha_selectors = ["#captcha-verify-container", ".captcha_verify_container", "[id^='secsdk-captcha']", ".captcha_verify_bar"]
            for sel in captcha_selectors:
                if await page.locator(sel).first.is_visible(timeout=check_timeout):
                    from loguru import logger
                    logger.warning(f"🚨 Phát hiện Captcha ({sel})! Vui lòng giải tay... Đang dừng chờ tối đa 5 PHÚT.")
                    await page.locator(sel).first.wait_for(state="hidden", timeout=300000)
                    logger.success("✅ Đã giải xong Captcha! Tiếp tục chạy...")
                    await asyncio.sleep(2)
                    break
        except Exception:
            pass
        
        await self._handle_error_page(page)

        try:
            from config import COOKIES_FILE
            await context.storage_state(path=str(COOKIES_FILE))
        except Exception:
            pass

    # =========================================================================
    # BƯỚC 2: AUDIO CHAIN CRAWLER
    # Từ 1 audio đã accepted → tìm thêm audio mới từ các video cùng dùng âm đó
    # =========================================================================
    async def crawl_audio_chain(self, seed_audio_id: str, seed_audio_name: str) -> List[AudioMetadata]:
        """
        Khai thác trang /music/[id] để thu thập audio mới từ các video
        đang sử dụng âm thanh gốc đó. Mỗi video trong grid có thể chứa 
        audio từ creator khác → mạng lưới audio mở rộng tự động.
        """
        self.collected_audios = []
        self.current_keyword = f"chain:{seed_audio_name[:30]}"

        audio_page_url = f"https://www.tiktok.com/music/{urllib.parse.quote(seed_audio_name)}-{seed_audio_id}"
        logger.info(f"🔗 [AudioChain] Khai thác: {audio_page_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--mute-audio", "--start-minimized", "--disable-dev-shm-usage"]
            )
            from config import COOKIES_FILE
            kwargs = {"viewport": {"width": 1920, "height": 1080}}
            if COOKIES_FILE.exists():
                try:
                    import json
                    json.loads(COOKIES_FILE.read_text())
                    kwargs["storage_state"] = str(COOKIES_FILE)
                except Exception:
                    pass
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media"] else route.continue_())
            page.on("response", self._intercept_response)

            try:
                await page.goto(audio_page_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                await self._wait_for_captcha(page, context)
                await asyncio.sleep(8)
                
                # Scroll để load thêm video trong grid
                for i in range(SCROLL_COUNT // 2):
                    if len(self.collected_audios) >= TARGET_AUDIOS:
                        break
                    logger.debug(f"[AudioChain] Scroll {i+1} (Collected: {len(self.collected_audios)})")
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                    await asyncio.sleep(2.5)
                    await self._wait_for_captcha(page, context, check_timeout=500)

            except Exception as e:
                logger.error(f"AudioChain error for {seed_audio_id}: {e}")
            finally:
                await browser.close()

        logger.success(f"🔗 [AudioChain] {seed_audio_name[:30]} → {len(self.collected_audios)} audio mới")
        return list(self.collected_audios)

    # =========================================================================
    # BƯỚC 3: HASHTAG CRAWLER
    # Crawl trang hashtag viral thay vì chỉ dùng keyword
    # =========================================================================
    async def crawl_hashtag(self, hashtag: str) -> List[AudioMetadata]:
        """
        Crawl trang /tag/[hashtag] trên TikTok.
        Hashtag chính xác hơn keyword vì người dùng tự gán nhãn nội dung.
        Intercept API để bóc audio từ các video trong trang hashtag.
        """
        self.collected_audios = []
        self.current_keyword = f"#{hashtag}"

        url = f"https://www.tiktok.com/tag/{hashtag}"
        logger.info(f"#️⃣ [Hashtag] Crawling: #{hashtag}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--mute-audio", "--start-minimized", "--disable-dev-shm-usage"]
            )
            from config import COOKIES_FILE
            kwargs = {"viewport": {"width": 1920, "height": 1080}}
            if COOKIES_FILE.exists():
                try:
                    import json
                    json.loads(COOKIES_FILE.read_text())
                    kwargs["storage_state"] = str(COOKIES_FILE)
                except Exception:
                    pass
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media"] else route.continue_())
            page.on("response", self._intercept_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                await self._wait_for_captcha(page, context)
                await asyncio.sleep(8)

                for i in range(SCROLL_COUNT):
                    if len(self.collected_audios) >= TARGET_AUDIOS:
                        break
                    logger.info(f"[Hashtag #{hashtag}] Scroll {i+1}/{SCROLL_COUNT} (Collected: {len(self.collected_audios)})")
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                    await asyncio.sleep(2.5)
                    await self._wait_for_captcha(page, context, check_timeout=500)

            except Exception as e:
                logger.error(f"Hashtag crawl error #{hashtag}: {e}")
            finally:
                await browser.close()

        logger.success(f"#️⃣ [Hashtag] #{hashtag} → {len(self.collected_audios)} audios")
        return list(self.collected_audios)

    async def crawl_keyword(self, keyword: str) -> List[AudioMetadata]:
        self.current_keyword = keyword
        self.collected_audios = []
        
        logger.info(f"🌍 [Global Seeder] Bắt đầu Gieo hạt & Gặt hái ngách: '{keyword}'")

        async with async_playwright() as p:
            user_data_dir = str(Path(__file__).resolve().parent.parent / "tool_sroll_feed" / "tiktok_session")
            
            # Sử dụng chung cấu hình Cookie với FYP Tool để Tương tác
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=False,
                viewport={"width": 1920, "height": 1080},
                args=["--mute-audio", "--disable-dev-shm-usage"]
            )
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await Stealth().apply_stealth_async(page)

            # Không chặn Image/Media nữa vì ta cần xem video như người thật để mồi thuật toán
            
            page.on("response", self._intercept_response)
            
            try:
                encoded_keyword = urllib.parse.quote(keyword)
                url = f"https://www.tiktok.com/search/video?q={encoded_keyword}"
                
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                await self._wait_for_captcha(page, browser)
                await asyncio.sleep(5)  # Chờ API trả danh sách video về
                
                # ── DEEP NURTURE (Tương tác sâu) ──
                # Click vào video đầu tiên để mở trình phát toàn màn hình
                first_video = await page.query_selector('div[data-e2e="search_video-item"], a[href*="/video/"]')
                if first_video:
                    logger.info("🎬 Bắt đầu xem và tương tác để dạy thuật toán (Nurture Algorithm)...")
                    await first_video.click()
                    await asyncio.sleep(3)
                    
                    for i in range(15):
                        if len(self.collected_audios) >= 20: break
                        
                        logger.info(f"   👀 Đang xem video {i+1}... (Gieo hạt)")
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        
                        # 1. Thả Tim ngẫu nhiên (60% tỷ lệ)
                        if random.random() < 0.6:
                            try:
                                like_btn = await page.query_selector('span[data-e2e="like-icon"]')
                                if like_btn: await like_btn.click()
                            except: pass
                            
                        # 2. Lưu video ngẫu nhiên (40% tỷ lệ)
                        if random.random() < 0.4:
                            try:
                                save_btn = await page.query_selector('button[aria-label*="Save"], span[data-e2e="undefined-icon"]')
                                if save_btn: await save_btn.click()
                            except: pass
                        
                        # 3. Follow tác giả tiềm năng (đã xử lý thêm vào creators_list ẩn ở bước API rồi, ở đây chỉ cần thả tim mồi thuật toán)
                        
                        # Lướt sang video tiếp theo
                        await page.keyboard.press("ArrowDown")
                        await asyncio.sleep(1.5)
                        await self._wait_for_captcha(page, browser, check_timeout=500)
                else:
                    # Fallback lướt trang bình thường nếu không click được video
                    for i in range(SCROLL_COUNT):
                        if len(self.collected_audios) >= 20: break
                        logger.info(f"Scrolling {i+1}/{SCROLL_COUNT} (Collected: {len(self.collected_audios)})")
                        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                        await asyncio.sleep(1.5)
                        await self._wait_for_captcha(page, browser, check_timeout=500)
                    
            except Exception as e:
                logger.error(f"Error during Global Seeding '{keyword}': {e}")
            finally:
                await browser.close()
                
        logger.success(f"Finished crawling '{keyword}'. Found {len(self.collected_audios)} audios.")
        return list(self.collected_audios)

    async def crawl_user(self, username: str) -> List[AudioMetadata]:
        """Truy quét toàn bộ video từ một kênh người dùng cụ thể."""
        self.collected_audios = []
        self.current_keyword = f"user:{username}"
        url = f"https://www.tiktok.com/@{username}"
        
        logger.info(f"Starting crawl for user: @{username}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, args=["--mute-audio", "--start-minimized"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Stealth!
            await Stealth().apply_stealth_async(page)
            
            # Đăng ký interceptor để bắt các gói tin API trả về danh sách video (item_list)
            page.on("response", self._intercept_response)
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
                
                # Cuộn trang để load thêm video nếu cần
                for i in range(5): # Quét khoảng 2-3 màn hình video mới nhất là đủ
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await asyncio.sleep(2)
                    if len(self.collected_audios) >= 20: 
                        break
                        
            except Exception as e:
                logger.error(f"Error during user crawling '@{username}': {e}")
            finally:
                await browser.close()
                
        logger.success(f"Finished user crawl '@{username}'. Found {len(self.collected_audios)} potential audios.")
        return list(self.collected_audios)

    async def get_accurate_usage(self, audio_page_url: str, page: Optional[Page] = None) -> int:
        """Truy cập trang audio qua Playwright để lấy số lượng video chính xác (vượt bot block)."""
        if not audio_page_url: return 0

        logger.info(f"Checking accurate usage count: {audio_page_url}")
        
        own_browser = page is None
        playwright_ctx = None
        browser = None
        
        try:
            if own_browser:
                playwright_ctx = async_playwright()
                p = await playwright_ctx.__aenter__()
                # TikTok chặn mạnh headless=True, nên phải dùng headless=False nhưng đẩy cửa sổ ra ngoài màn hình
                browser = await p.chromium.launch(
                    headless=False, 
                    args=[
                        "--mute-audio", 
                        "--start-minimized", 
                        "--window-position=-32000,-32000"
                    ]
                )
                page = await browser.new_page()
                from playwright_stealth import Stealth as _Stealth
                await _Stealth().apply_stealth_async(page)

            # Ép trang tải nhanh, tăng timeout lên 60s
            await page.goto(audio_page_url, wait_until="domcontentloaded", timeout=60000)
            
            # Chờ thêm 2s để React/NextJS hydrate và render dữ liệu
            await asyncio.sleep(2)

            for _ in range(15):  # Tăng số lượt thử
                # Lấy HTML nguyên sinh để tìm tag data-e2e
                body_html = await page.evaluate("() => document.body.innerHTML")
                
                found_count = None
                # Regex tìm số lượng video trong tag data-e2e="music-video-count"
                m1 = re.search(r'data-e2e="music-video-count"[^>]*>(?:<[^>]+>)*([\d\.,]+[KkMm]?)', body_html)
                if m1:
                    found_count = m1.group(1)
                
                if not found_count:
                    # Fallback về InnerText nếu data-e2e không có
                    plain_text = await page.evaluate("() => document.body.innerText")
                    usage_patterns = [
                        r"([\d\.,]+[KkMm]?)\s*(?:videos?|posts?|bài\s*viết|publicaciones?|video|post)",
                        r"\"videoCount\"\s*:\s*(\d+)"
                    ]
                    for pattern in usage_patterns:
                        matches = re.finditer(pattern, plain_text, re.IGNORECASE)
                        for match in list(matches)[:10]:
                            raw = match.group(1).replace(",", "").strip()
                            if "K" in raw.upper() or "M" in raw.upper():
                                found_count = raw
                                break
                            elif raw.isdigit() and int(raw) > 0:
                                found_count = raw
                                break
                        if found_count:
                            break
                
                if found_count:
                    raw = found_count.upper()
                    if raw.endswith("K"): return int(float(raw[:-1].replace(",", "")) * 1000)
                    if raw.endswith("M"): return int(float(raw[:-1].replace(",", "")) * 1000000)
                    try: return int(float(raw.replace(",", "")))
                    except: return 0

                # Tăng delay mỗi vòng lặp lên 1s thay vì 0.5s để đợi trang load xong hoàn toàn
                await asyncio.sleep(1.0)

            logger.debug(f"Không tìm được số lượng (Có thể bị Captcha/Block): {audio_page_url}")
            return 0

        except Exception as e:
            logger.warning(f"Failed Playwright get usage: {e}")
            return 0
        finally:
            if own_browser:
                if browser:
                    try: await browser.close()
                    except: pass
                if playwright_ctx:
                    try: await playwright_ctx.__aexit__(None, None, None)
                    except: pass


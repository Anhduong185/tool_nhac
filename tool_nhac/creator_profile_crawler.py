"""
creator_profile_crawler.py — V2.1 Profile Mode Crawler
========================================================
Cào video từ tab "Thịnh hành" (Popular) của kênh TikTok.
Tích hợp:
  - Creator Scoring với recency_score
  - Cool-down list (2 ngày)
  - Scan định kỳ tab Mới nhất cho Creator VIP (mỗi 5 batch)

Được gọi từ main.py như một Engine song song với Trend Hunter.
"""

import asyncio
import json
import re
import random
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from loguru import logger
from playwright.async_api import async_playwright, Page, Response
from playwright_stealth import Stealth as PlaywrightStealth

from models import AudioMetadata
from config import PAGE_LOAD_TIMEOUT, HEADLESS, MAX_DURATION

ROOT_DIR = Path(__file__).resolve().parent

# ── Cấu hình ──────────────────────────────────────────────────────────────────
CREATORS_FILE        = ROOT_DIR / "creators_list.txt"
CREATOR_STATE_FILE   = ROOT_DIR / "data" / "creator_state.json"  # Lưu score + cooldown
MAX_VIDEOS_PER_PROFILE = 30     # Lấy tối đa N video mỗi tab
COOLDOWN_DAYS        = 2        # Ngày nghỉ giữa các lần quét cùng creator
VIP_COOLDOWN_HOURS   = 12       # Creator VIP quét lại sau 12 giờ
SCAN_LATEST_INTERVAL = 5        # Cứ 5 batch thì scan thêm tab Mới nhất
VIP_THRESHOLD        = 0.7      # creator_score > 0.7 → VIP
COOLDOWN_THRESHOLD   = 0.5      # creator_score < 0.5 → vào cooldown


# ── Creator State Manager ─────────────────────────────────────────────────────
class CreatorStateManager:
    """Lưu và đọc trạng thái (score, cooldown, lịch sử) của từng creator."""

    def __init__(self, state_file: Path = CREATOR_STATE_FILE):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, dict] = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding='utf-8'))
            except Exception:
                pass
        return {}

    def _save(self):
        current_data = self._load()
        current_data.update(self._data)
        self.state_file.write_text(
            json.dumps(current_data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    def get(self, username: str) -> dict:
        return self._data.get(username.lower(), {
            "creator_score": 0.5,
            "total_checked": 0,
            "total_passed": 0,
            "avg_audio_score": 0.0,
            "has_recent_pass": False,   # recency_score
            "noise_count": 0,
            "last_crawled": None,
            "cooldown_until": None,
            "tag": "NORMAL",            # "VIP" | "NORMAL" | "COOLDOWN"
            "batch_count": 0,
        })

    def is_in_cooldown(self, username: str) -> bool:
        state = self.get(username.lower())
        cd = state.get("cooldown_until")
        if not cd:
            return False
        try:
            until = datetime.fromisoformat(cd)
            return datetime.now(timezone.utc) < until.replace(tzinfo=timezone.utc) if until.tzinfo is None else datetime.now(timezone.utc) < until
        except Exception:
            return False

    def should_scan_latest(self, username: str, batch_count: int) -> bool:
        """Creator VIP: scan thêm tab Mới nhất mỗi SCAN_LATEST_INTERVAL batch."""
        state = self.get(username.lower())
        return state.get("tag") == "VIP" and (batch_count % SCAN_LATEST_INTERVAL == 0)

    def update_after_crawl(
        self,
        username: str,
        passed: int,
        checked: int,
        avg_audio_score: float,
        noise_count: int,
        has_recent_pass: bool,
    ):
        """Cập nhật state sau khi quét xong 1 creator."""
        username = username.lower()
        prev = self.get(username)

        total_checked = prev["total_checked"] + checked
        total_passed  = prev["total_passed"]  + passed
        total_noise   = prev["noise_count"]   + noise_count

        pass_rate   = total_passed / max(total_checked, 1)
        noise_rate  = total_noise  / max(total_checked, 1)
        recency     = 1.0 if has_recent_pass else 0.0

        # Weighted average cho avg_audio_score
        prev_avg = prev.get("avg_audio_score", 0.0)
        prev_cnt = prev["total_passed"]
        if total_passed > 0:
            new_avg = (prev_avg * prev_cnt + avg_audio_score * passed) / total_passed
        else:
            new_avg = 0.0

        # Công thức creator_score V2.1 (dùng recency_score thay consistency_score)
        creator_score = round(
            pass_rate   * 0.4
          + new_avg     * 0.3   # avg_audio_score (0–1 scale)
          + recency     * 0.2
          - noise_rate  * 0.1,
            3
        )
        creator_score = max(0.0, min(1.0, creator_score))

        # Xác định tag
        if creator_score >= VIP_THRESHOLD:
            tag = "VIP"
            cooldown_hours = VIP_COOLDOWN_HOURS
        elif creator_score < COOLDOWN_THRESHOLD:
            tag = "COOLDOWN"
            cooldown_hours = COOLDOWN_DAYS * 24
        else:
            tag = "NORMAL"
            cooldown_hours = COOLDOWN_DAYS * 24

        cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).isoformat()

        self._data[username] = {
            "creator_score":   creator_score,
            "total_checked":   total_checked,
            "total_passed":    total_passed,
            "avg_audio_score": round(new_avg, 3),
            "has_recent_pass": has_recent_pass,
            "noise_count":     total_noise,
            "last_crawled":    datetime.now(timezone.utc).isoformat(),
            "cooldown_until":  cooldown_until,
            "tag":             tag,
            "batch_count":     prev.get("batch_count", 0) + 1,
        }
        self._save()

        logger.info(
            f"  📊 @{username}: score={creator_score:.2f} tag={tag} "
            f"pass={total_passed}/{total_checked} recency={bool(recency)}"
        )
        return creator_score, tag


# ── Profile Crawler ───────────────────────────────────────────────────────────
class CreatorProfileCrawler:
    def __init__(self):
        self.collected: List[AudioMetadata] = []
        self.state = CreatorStateManager()

    def _on_response(self, response: Response):
        """Intercept API response để bắt danh sách video của profile."""
        # TikTok trả video list qua nhiều endpoint khác nhau
        target_patterns = [
            "api/post/item_list",
            "api/user/post",
            "api/creator/item/list",
            "api/recommend/item_list",  # Endpoint mới hơn
        ]
        # DEBUG: log tất cả API để phát hiện endpoint mới
        if response.request.resource_type in ("xhr", "fetch") and response.status == 200:
            if "tiktok.com/api" in response.url:
                logger.debug(f"  [Profile-API] {response.url[:120]}")
        if not any(p in response.url for p in target_patterns):
            return
        if response.status != 200:
            return

        asyncio.ensure_future(self._parse_response(response))

    async def _parse_response(self, response: Response):
        try:
            if "application/json" not in response.headers.get("content-type", ""):
                return
            data = await response.json()
            items = data.get("itemList") or data.get("items") or data.get("data", {}).get("itemList", [])
            logger.info(f"  [Profile API] Nhận {len(items)} items từ {response.url.split('?')[0]}")
            for item in items:
                self._extract_audio(item)
        except Exception as e:
            logger.error(f"  [Profile API] Lỗi parse JSON: {e} | URL: {response.url.split('?')[0]}")

    def _extract_audio(self, item: dict):
        music = item.get("music", {})
        if not music:
            logger.debug(f"  [Profile] Video {item.get('id')} không có key 'music'. Các key có sẵn: {list(item.keys())}")
            return

        try:
            audio_id   = str(music.get("id", ""))
            audio_name = music.get("title", "")
            duration   = int(music.get("duration") or 0)
            audio_url  = music.get("playUrl", "")

            # Hard-reject ngay lập tức (không tốn thêm bước)
            if not audio_id or not audio_url:
                logger.info(f"  [Profile] Reject no_id_url: {audio_name}")
                return
            # Profile crawler dùng ngưỡng cao hơn (video creator có thể dài 10 phút)
            MAX_PROFILE_DURATION = 600
            if duration > MAX_PROFILE_DURATION:
                logger.info(f"  [Profile] Reject duration={duration}s: {audio_name[:40]}")
                return
            if duration <= 0:
                logger.info(f"  [Profile] Reject duration<=0: {audio_name[:40]}")
                return
            if not music.get("original", True):
                logger.info(f"  [Profile] Reject original=False: {audio_name[:40]}")
                return
            if music.get("isCopyrighted"):
                logger.info(f"  [Profile] Reject isCopyrighted: {audio_name[:40]}")
                return
            if music.get("hasLyrics"):
                logger.info(f"  [Profile] Reject hasLyrics: {audio_name[:40]}")
                return

            author     = item.get("author", {})
            username   = author.get("uniqueId", "user")
            video_id   = item.get("id", "")
            video_url  = f"https://www.tiktok.com/@{username}/video/{video_id}"
            music_id   = music.get("id", "")
            page_url   = f"https://www.tiktok.com/music/{urllib.parse.quote(audio_name)}-{music_id}"

            stats        = item.get("stats", {})
            video_views  = int(stats.get("playCount") or stats.get("vvCount") or 0)
            video_likes  = int(stats.get("diggCount") or 0)
            create_time  = int(item.get("createTime") or 0)

            # ── TIER 1 FILTER: CHỈ LẤY TỪ NĂM 2023 TRỞ LÊN VÀ VIEW/LIKE >= 2000 ──
            if video_views < 2000 and video_likes < 2000:
                logger.debug(f"  [Profile] Reject low engagement (V:{video_views} L:{video_likes}): {audio_name[:40]}")
                return
                
            import datetime
            if create_time > 0:
                dt = datetime.datetime.fromtimestamp(create_time)
                if dt.year < 2023:
                    logger.debug(f"  [Profile] Reject old video ({dt.year}): {audio_name[:40]}")
                    return

            usage_count = int(
                music.get("userCount") or music.get("useCount") or
                music.get("videoCount") or 0
            )

            meta = AudioMetadata(
                audio_id=audio_id,
                audio_name=audio_name,
                duration=duration,
                usage_count=usage_count if usage_count > 0 else -1,
                audio_url=audio_url,
                audio_page_url=page_url,
                video_url=video_url,
                keyword=f"profile:{username}",
                video_views=video_views,
                video_likes=video_likes,
                create_time=create_time,
                author_username=username,
                tiktok_is_original=bool(music.get("original", True)),
                tiktok_author_name=str(music.get("authorName") or ""),
                tiktok_category=int(music.get("categoryType") or -1),
                tiktok_is_ai=bool(music.get("tta") or music.get("isAiGenerated") or False),
                tiktok_is_commerce=bool(music.get("isCommerce") or False),
                tiktok_has_lyrics=bool(music.get("hasLyrics") or False),
                tiktok_music_by_author=bool(music.get("musicByAuthor", True)),
            )

            if not any(a.audio_id == audio_id for a in self.collected):
                self.collected.append(meta)
        except Exception as e:
            logger.error(f"  [Profile] Lỗi extract audio ({audio_id if 'audio_id' in locals() else 'unknown'}): {e}")

    async def _scroll_profile(self, page: Page, username: str, tab: str = "popular"):
        """Mở profile, click tab chỉ định, scroll để load video."""
        url = f"https://www.tiktok.com/@{username}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        except Exception as e:
            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in str(e) or "net::ERR" in str(e):
                logger.warning(f"⚠️ Không thể truy cập @{username} (Kênh có thể bị khóa, đổi tên hoặc lỗi mạng). Bỏ qua kênh này.")
                return
            else:
                raise e
        
        # Chờ Captcha nếu có
        try:
            captcha_selectors = ["#captcha-verify-container", ".captcha_verify_container", "[id^='secsdk-captcha']", ".captcha_verify_bar"]
            for sel in captcha_selectors:
                if await page.locator(sel).first.is_visible(timeout=2000):
                    logger.warning(f"🚨 Phát hiện Captcha ({sel}) tại trang tác giả @{username}! Vui lòng giải tay... Đang dừng chờ tối đa 5 PHÚT.")
                    await page.locator(sel).first.wait_for(state="hidden", timeout=300000)
                    logger.success("✅ Đã giải xong Captcha! Tiếp tục chạy...")
                    await asyncio.sleep(2)
                    try:
                        from config import COOKIES_FILE
                        await page.context.storage_state(path=str(COOKIES_FILE))
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        # Ấn Try again nếu lỗi mạng
        try:
            error_btn = page.locator('button:has-text("Try again"), button:has-text("Thử lại"), button:has-text("Refresh"), button:has-text("Tải lại")')
            if await error_btn.count() > 0 and await error_btn.first.is_visible(timeout=1000):
                logger.warning(f"⚠️ Phát hiện màn hình lỗi tại @{username}. Đang tự động ấn Thử lại...")
                await error_btn.first.click()
                await asyncio.sleep(4)
        except Exception:
            pass

        await asyncio.sleep(random.uniform(6.0, 8.0))

        # TIER 1: Chuyển sang tab Thịnh hành (Popular)
        try:
            popular_btn = page.locator('p:has-text("Popular"), p:has-text("Thịnh hành"), div[role="tab"]:has-text("Popular"), div[role="tab"]:has-text("Thịnh hành")')
            if await popular_btn.count() > 0:
                await popular_btn.first.click()
                logger.info(f"  [Tier 1] Đã chuyển sang tab Thịnh hành của @{username}")
                await asyncio.sleep(3)
        except Exception as e:
            logger.debug(f"  [Tier 1] Lỗi click tab Thịnh hành: {e}")

        # TỰ ĐỘNG BẤM NÚT "GỢI Ý TÀI KHOẢN" (icon người, ngay sau nút Message)
        try:
            # 1. THỬ CLICK TỌA ĐỘ CỨNG THEO USER YÊU CẦU (X=902, Y=118 - Dịch phải 200px từ 702)
            logger.debug("  [Auto-Click] Thử click vào tọa độ cứng X=902, Y=118...")
            await page.mouse.move(890, 125)
            await asyncio.sleep(0.2)
            await page.mouse.move(902, 118, steps=5)
            await asyncio.sleep(0.3)
            await page.mouse.down()
            await asyncio.sleep(0.1)
            await page.mouse.up()
            logger.info("  [Auto-Click] Đã click tọa độ cứng (902, 118), chờ 3s...")
            await asyncio.sleep(3)
            
            clicked = True
            
            # (Giữ lại logic quét SVG như một bản backup nhưng không cần chạy nếu click cứng đã làm)
            # Dùng data-e2e để đảm bảo chỉ lấy nút Follow của Profile chính (tránh thanh bên trái)
            follow_btn = page.locator(
                '[data-e2e="follow-button"], [data-e2e="edit-profile-button"]'
            ).first

            if not clicked and await follow_btn.count() > 0:
                f_box = await follow_btn.bounding_box()
                if f_box:
                    logger.debug(f"  [Auto-Click] Mốc tọa độ Follow y={f_box['y']:.0f}")

                    # Tìm nút gợi ý bằng hình học: Quét tất cả thẻ <svg> trên trang
                    all_svgs = await page.locator('svg').all()
                    icon_candidates = []
                    near_follow_debug = []

                    for svg in all_svgs:
                        try:
                            box = await svg.bounding_box()
                            if not box:
                                continue
                            
                            dy = abs(box['y'] - f_box['y'])
                            dx = box['x'] - (f_box['x'] + f_box['width'])

                            if dy < 60 and -10 < dx < 300:
                                near_follow_debug.append(f"svg_dx={dx:.0f}_dy={dy:.0f}")

                            if dy < 40 and 0 < dx < 150 and box['width'] < 40:
                                icon_candidates.append((svg, box['x']))
                        except Exception:
                            pass

                    logger.debug(
                        f"  [Auto-Click] Near Follow SVGs: {near_follow_debug} | "
                        f"Candidates: {[(round(x[1])) for x in icon_candidates]}"
                    )

                    if icon_candidates:
                        icon_candidates.sort(key=lambda x: x[1])
                        target_svg, bx = icon_candidates[0]
                        # Tọa độ click là tâm của SVG
                        t_box = await target_svg.bounding_box()
                        if t_box:
                            cx = t_box['x'] + t_box['width'] / 2
                            cy = t_box['y'] + t_box['height'] / 2
                            await page.mouse.move(cx - 10, cy + 10)
                            await asyncio.sleep(0.2)
                            await page.mouse.move(cx, cy, steps=8)
                            await asyncio.sleep(0.4)
                            await page.mouse.down()
                            await asyncio.sleep(0.1)
                            await page.mouse.up()
                            clicked = True
                            logger.info(f"  [Auto-Click] Click icon gợi ý tại ({cx:.0f}, {cy:.0f}), chờ 3s...")
                            await asyncio.sleep(3)
                    else:
                        logger.debug("  [Auto-Click] Không tìm thấy icon button — bỏ qua")



            if not clicked:
                logger.debug("  [Auto-Click] Không click được nút gợi ý")
        except Exception as e:
            logger.debug(f"  [Auto-Click] Lỗi: {e}")

        # Thu thập các creator đề xuất trên trang (nhân bản) NGAY TRƯỚC KHI CUỘN
        try:
            suggested_elements = await page.locator('a[href^="/@"]').all()
            new_users = set()
            for el in suggested_elements:
                href = await el.get_attribute("href")
                if href and href.startswith("/@"):
                    u = href.split("?")[0].replace("/@", "").split("/")[0].lower()
                    if u and u != username.lower() and len(u) > 2:
                        new_users.add(u)
            
            if new_users:
                from database import add_target_user, DB_PATH
                import aiosqlite
                added_count = 0
                async with aiosqlite.connect(DB_PATH) as db:
                    for u in new_users:
                        # Check trùng trước khi thêm vào creators_list
                        async with db.execute("SELECT 1 FROM target_users WHERE username = ?", (u,)) as cur:
                            if not await cur.fetchone():
                                await add_target_user(u)
                                added_count += 1
                if added_count > 0:
                    logger.info(f"🌱 Đã tìm thấy và thêm {added_count} creator đề xuất từ trang của @{username}")
        except Exception as e:
            logger.debug(f"Lỗi tìm creator đề xuất: {e}")

        # TIER 1: Scroll để lấy video — thoát sớm nếu không có thêm gì mới
        no_new_streak = 0  # Đếm số lần scroll liên tiếp không tìm thêm được video
        for i in range(15):  # Tối đa 15 lần scroll (~45 giây)
            if len(self.collected) >= 50:  # Đủ 50 video là thoát
                break
            prev_count = len(self.collected)
            logger.info(f"  [Tier 1] Đang cuộn lấy video... ({prev_count} đạt chuẩn)")
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(2.0, 2.5))
            # Early-stop: 3 lần scroll liên tiếp không tìm thêm video nào → dừng
            if len(self.collected) == prev_count:
                no_new_streak += 1
                if no_new_streak >= 3:
                    logger.info(f"  [Tier 1] Không có video mới sau 3 lần scroll → dừng sớm.")
                    break
            else:
                no_new_streak = 0

    async def crawl_profile(
        self,
        username: str,
        batch_count: int = 0,
        page: Optional[Page] = None,
    ) -> List[AudioMetadata]:
        """
        Cào video từ profile một creator.
        - Mặc định: tab Thịnh hành
        - Creator VIP + đúng interval: thêm tab Mới nhất
        """
        self.collected = []

        # Kiểm tra cooldown
        if self.state.is_in_cooldown(username):
            logger.debug(f"  ⏸️  @{username} đang trong cooldown, bỏ qua.")
            return []

        should_also_latest = self.state.should_scan_latest(username, batch_count)
        own_browser = page is None

        try:
            if own_browser:
                playwright = await async_playwright().__aenter__()
                user_data_dir = str(ROOT_DIR.parent / "tool_sroll_feed" / "tiktok_session")
                
                # Dùng chung cấu hình và Cookie (user_data_dir) với các tool khác
                browser = await playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=[
                        "--mute-audio", 
                        "--disable-dev-shm-usage",
                    ]
                )
                page = browser.pages[0] if browser.pages else await browser.new_page()
                await PlaywrightStealth().apply_stealth_async(page)
            
            # Luôn lắng nghe API response dù page do ai tạo
            page.on("response", self._on_response)

            # Theo yêu cầu user: Quét luôn tab mặc định, không chuyển tab Thịnh hành / Mới nhất nữa
            logger.info(f"  🎬 Cào @{username} (tab: Mặc định) ...")
            await self._scroll_profile(page, username, tab="default")
            
        except Exception as e:
            logger.error(f"  ❌ Lỗi cào @{username}: {e}")
        finally:
            if page:
                try:
                    page.remove_listener("response", self._on_response)
                except Exception:
                    pass
                    
            if own_browser:
                try:
                    await browser.close()
                    await playwright.__aexit__(None, None, None)
                except Exception:
                    pass

        logger.success(f"  ✅ @{username}: {len(self.collected)} audio candidates")
        return list(self.collected)

    async def crawl_all(
        self,
        creators_file: Path = CREATORS_FILE,
        batch_count: int = 0,
        limit: Optional[int] = None,
    ) -> List[AudioMetadata]:
        """
        Duyệt toàn bộ danh sách creator trong file, skip cooldown,
        trả về danh sách audio tổng hợp.
        """
        if not creators_file.exists():
            logger.warning(f"Không tìm thấy file: {creators_file}, sẽ chỉ lấy từ Database.")

        usernames = []
        if creators_file.exists():
            usernames.extend([
                line.strip().lstrip('@').lower()
                for line in creators_file.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.startswith('#')
            ])

        import aiosqlite
        from config import DB_PATH
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT username FROM target_users") as cur:
                    rows = await cur.fetchall()
                    for r in rows:
                        usernames.append(r[0].lower())
        except Exception:
            pass

        usernames = list(set(usernames))

        import random
        random.shuffle(usernames)

        # Ưu tiên Creator VIP lên đầu
        vip    = [u for u in usernames if self.state.get(u).get("tag") == "VIP"]
        normal = [u for u in usernames if u not in vip and not self.state.is_in_cooldown(u)]
        
        order  = vip + normal
        if limit:
            order = order[:limit]

        logger.info(f"📋 Tổng {len(order)} creator (VIP={len(vip)}, Normal={len(normal)})")

        all_audios: List[AudioMetadata] = []
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
            page.on("response", self._on_response)  # ← BẮT BUỘC: đăng ký listener để thu thập audio

            for idx, username in enumerate(order, 1):
                logger.info(f"[{idx}/{len(order)}] @{username}")
                self.collected = []  # reset trước mỗi kênh (tránh audio kênh trước lẫn vào)
                audios = await self.crawl_profile(username, batch_count, page=page)
                all_audios.extend(audios)
                logger.info(f"  → Cộng dồn: {len(all_audios)} audio từ {idx} kênh")

                delay = random.uniform(3, 6)
                await asyncio.sleep(delay)

            await browser.close()

        logger.success(f"🏁 Creator Mining: {len(all_audios)} audio candidates từ {len(order)} kênh")
        return all_audios

"""
creator_scanner.py — Quét Audio Từ Trang Tác Giả
==================================================
Tính năng riêng biệt (KHÔNG phụ thuộc vào FYP loop).

Chức năng:
1. Nhận username tác giả (hoặc danh sách)
2. Kiểm tra đã quét chưa (seen_creators.txt) → skip nếu đã quét
3. Vào trang profile → lấy TOÀN BỘ video
4. Lọc audio qua engine hiện tại (filter + dedup)
5. Lưu kết quả → đánh dấu đã quét

Dùng qua API: POST /creator/scan {"username": "@abc"}
           hoặc: POST /creator/scan-batch {"usernames": ["@abc", "@xyz"]}
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Callable

# Khai báo config trực tiếp hoặc qua import động để tránh đụng độ với sys.modules['config'] của server.py
import importlib.util
spec = importlib.util.spec_from_file_location("sroll_config", Path(__file__).parent / "config.py")
sroll_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sroll_config)

FYP_DIR = sroll_config.FYP_DIR
SHARED_CREATORS = sroll_config.SHARED_CREATORS
FYP_RESULT_URL = sroll_config.FYP_RESULT_URL
TOOL_NHAC_DB = sroll_config.TOOL_NHAC_DB
RULES = sroll_config.RULES

from engine import FilterEngine


# ── Paths ──────────────────────────────────────────────────────────────────────
SEEN_CREATORS_FILE = FYP_DIR / "seen_creators.txt"   # Creator đã quét xong
SCAN_LOG_FILE      = FYP_DIR / "scan_log.jsonl"      # Log từng lần quét

# ── Giới hạn quét ──────────────────────────────────────────────────────────────
MAX_VIDEOS_PER_CREATOR = 30    # Tối đa 30 video/lần quét
MIN_GOOD_AUDIO_TO_SAVE = 1     # Ít nhất 1 audio tốt → lưu creator vào shared list
SCROLL_PAUSE           = 2.0   # Giây chờ giữa các video


# ── Seen Creator Store ─────────────────────────────────────────────────────────

def load_seen_creators() -> set:
    """Nạp danh sách creator đã quét từ file."""
    try:
        if SEEN_CREATORS_FILE.exists():
            lines = SEEN_CREATORS_FILE.read_text(encoding="utf-8").splitlines()
            return {l.strip().lstrip("@").lower() for l in lines if l.strip()}
    except Exception:
        pass
    return set()


def mark_creator_seen(username: str):
    """Đánh dấu creator đã quét xong."""
    clean = username.strip().lstrip("@").lower()
    try:
        with open(SEEN_CREATORS_FILE, "a", encoding="utf-8") as f:
            f.write(clean + "\n")
    except Exception as e:
        print(f"⚠️ Không ghi được seen_creators: {e}")


def is_creator_seen(username: str, seen: set = None) -> bool:
    """Kiểm tra creator đã quét chưa."""
    clean = username.strip().lstrip("@").lower()
    if seen is None:
        seen = load_seen_creators()
    return clean in seen


def save_to_shared_creators(username: str):
    """
    Thêm creator chất lượng vào creators_list.txt của tool_nhac.
    → tool_nhac và MarketExpander sẽ dùng creator này.
    """
    clean = username.strip().lstrip("@").lower()
    try:
        existing = set()
        if SHARED_CREATORS.exists():
            existing = {l.strip().lstrip("@").lower()
                        for l in SHARED_CREATORS.read_text(encoding="utf-8").splitlines()
                        if l.strip() and not l.startswith("#")}
        if clean not in existing:
            with open(SHARED_CREATORS, "a", encoding="utf-8") as f:
                f.write(f"@{clean}\n")
            print(f"⭐ Đã thêm @{clean} vào creators_list.txt (tool_nhac sẽ dùng)")
    except Exception as e:
        print(f"⚠️ Lỗi ghi creators_list: {e}")


def log_scan_result(username: str, result: dict):
    """Ghi log kết quả quét ra jsonl."""
    try:
        entry = {
            "username":  username,
            "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            **result,
        }
        with open(SCAN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Core Scanner ───────────────────────────────────────────────────────────────

class CreatorScanner:
    """
    Quét toàn bộ audio từ trang profile một tác giả.
    Dùng lại browser page từ TikTokAgent.
    """

    def __init__(self, page, checked_audio: set,
                 on_result: Callable = None,
                 log_fn: Callable = None):
        """
        Args:
            page:          Playwright page (từ TikTokAgent)
            checked_audio: Set audio_id đã biết (dedup toàn cục)
            on_result:     async callback(entry: dict) khi tìm được audio tốt
            log_fn:        Hàm print/log để stream về dashboard
        """
        self.page         = page
        self.checked_audio = checked_audio
        self.on_result    = on_result
        self.log          = log_fn or print
        self._seen        = load_seen_creators()

    def _log(self, msg: str):
        self.log(f"[CreatorScan] {msg}")

    async def _get_video_links(self, username: str) -> list:
        """Lấy danh sách link video từ trang profile."""
        url = f"https://www.tiktok.com/@{username.lstrip('@')}"
        self._log(f"📂 Đang mở trang @{username}...")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)

        # Scroll để load thêm video
        for _ in range(3):
            await self.page.keyboard.press("End")
            await asyncio.sleep(1.5)

        video_links = await self.page.query_selector_all('a[href*="/video/"]')
        unique = []
        for vl in video_links:
            href = await vl.get_attribute("href") or ""
            vid_id = href.split("/video/")[-1].split("?")[0]
            if vid_id.isdigit() and len(vid_id) > 15:
                full = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                if full not in unique:
                    unique.append(full)

        self._log(f"🎬 Tìm thấy {len(unique)} videos của @{username}")
        return unique[:MAX_VIDEOS_PER_CREATOR]

    async def _extract_audio_from_video(self, video_url: str) -> dict | None:
        """
        Lấy audio_id và metadata từ trang video.
        Không tải file — chỉ lấy metadata.
        """
        try:
            await self.page.goto(video_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1.5)

            # Lấy link nhạc
            music_el = await self.page.query_selector('a[href*="/music/"]')
            if not music_el:
                return None
            music_href = await music_el.get_attribute("href") or ""
            sound_text = (await music_el.inner_text()).strip()

            if not music_href:
                return None

            # Parse audio_id từ href
            audio_id = music_href.split("/")[-1].split("-")[-1].split("?")[0]
            if not audio_id or not audio_id.isdigit():
                return None

            # Lấy usage count từ page content (regex)
            page_content = await self.page.content()
            usage_count = 0
            m = re.search(r'"videoCount":(\d+)', page_content)
            if m:
                usage_count = int(m.group(1))

            # Lấy duration
            duration = 0
            m2 = re.search(r'"duration":(\d+)', page_content)
            if m2:
                duration = int(m2.group(1))

            music_full = f"https://www.tiktok.com{music_href}" if music_href.startswith("/") else music_href

            return {
                "audio_id":    audio_id,
                "audio_name":  sound_text,
                "usage_count": usage_count,
                "audio_link":  music_full,
                "video_link":  video_url,
                "duration":    duration,
                "source":      "creator_scan",
            }

        except Exception as e:
            self._log(f"⚠️ Lỗi parse video {video_url}: {e}")
            return None



    async def scan_creator(self, username: str, force: bool = False) -> dict:
        """
        Quét toàn bộ audio từ trang của 1 creator bằng API Interception.
        """
        clean = username.strip().lstrip("@").lower()
        result = {
            "username":        clean,
            "total_videos":    0,
            "passed":          0,
            "skipped_dup":     0,
            "rejected":        0,
            "added_to_shared": False,
            "audio_found":     [],
        }

        # Check đã quét chưa
        if not force and is_creator_seen(clean, self._seen):
            self._log(f"⏭️ @{clean} đã quét rồi (dùng force=True để quét lại)")
            result["status"] = "already_scanned"
            return result

        self._log(f"🔍 Bắt đầu quét @{clean} bằng API interceptor...")
        
        # Nhập CreatorProfileCrawler từ tool_nhac
        import sys
        from pathlib import Path
        tool_nhac_path = str(Path(__file__).resolve().parent.parent / "tool_nhac")
        if tool_nhac_path not in sys.path:
            sys.path.append(tool_nhac_path)
            
        try:
            from creator_profile_crawler import CreatorProfileCrawler
            crawler = CreatorProfileCrawler()
            audios = await crawler.crawl_profile(clean, page=self.page)
            result["total_videos"] = len(audios)
            
            from crawler import TikTokCrawler
            tmp_crawler = TikTokCrawler()
            
            from filter import get_dynamic_min_usage
            from database import check_duplicate

            for audio in audios:
                audio_id = audio.audio_id
                if audio_id in self.checked_audio:
                    self._log(f"  ⏭️ Audio {audio_id} trùng (cache)")
                    result["skipped_dup"] += 1
                    continue
                    
                # Check DB sâu
                is_dup = await check_duplicate(audio_id)
                if is_dup:
                    self._log(f"  ⏭️ Audio {audio_id} đã có trong DB")
                    self.checked_audio.add(audio_id)
                    result["skipped_dup"] += 1
                    continue

                # 1. Fetch accurate usage if missing
                usage = audio.usage_count if audio.usage_count > 0 else 0
                if usage <= 0 and audio.audio_page_url:
                    try:
                        fetched = await tmp_crawler.get_accurate_usage(audio.audio_page_url)
                        if fetched > 0:
                            usage = fetched
                            audio.usage_count = usage
                    except Exception:
                        pass
                
                # Đặc cách views cao
                if usage <= 0 and audio.video_views > 20000:
                    usage = 500
                    audio.usage_count = 500

                # 2. Filter logic (Chỉ check usage và duration)
                passed = True
                reason = "ok"

                if audio.duration > RULES.get("max_duration", 59):
                    passed = False
                    reason = f"duration {audio.duration}s > 59s"
                else:
                    min_req = get_dynamic_min_usage(audio.create_time)
                    if usage < min_req:
                        passed = False
                        reason = f"Usage {usage:,} < {min_req:,}"
                    else:
                        from engine import FilterEngine
                        is_orig, _ = FilterEngine.is_original_sound(audio.audio_name)
                        if not is_orig:
                            passed = False
                            reason = "not original sound"

                if not passed:
                    self._log(f"  ❌ Loại: {audio.audio_name[:40]} ({reason})")
                    result["rejected"] += 1
                    continue

                self._log(f"  ✅ Pass: {audio.audio_name[:40]} (LSD={audio.usage_count:,})")
                self.checked_audio.add(audio_id)
                result["passed"] += 1
                
                # Cập nhật status thành pending_ai để UI phân biệt Audio chưa check AI
                audio.status = "pending_ai"  
                audio.source_type = "creator_scan"
                try:
                    from database import insert_audio
                    await insert_audio(audio)
                except Exception as e:
                    self._log(f"⚠️ Lỗi lưu DB: {e}")

                info = {
                    "audio_id":    audio.audio_id,
                    "audio_name":  audio.audio_name,
                    "usage_count": audio.usage_count,
                    "audio_link":  audio.audio_page_url,
                    "video_link":  audio.video_url,
                    "duration":    audio.duration,
                    "source":      "creator_scan",
                }
                result["audio_found"].append(info)

                if self.on_result:
                    try:
                        entry = {**info, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                        await self.on_result(entry)
                    except Exception as e:
                        self._log(f"⚠️ on_result lỗi: {e}")
            
            # Đánh dấu đã quét (chỉ khi không có lỗi)
            mark_creator_seen(clean)
            self._seen.add(clean)

            # Nếu có ≥ MIN_GOOD_AUDIO_TO_SAVE audio tốt → add vào shared list
            if result["passed"] >= MIN_GOOD_AUDIO_TO_SAVE:
                save_to_shared_creators(clean)
                result["added_to_shared"] = True

        except Exception as e:
            self._log(f"⚠️ Lỗi crawl profile: {e}")
            result["status"] = "error"
            return result

        result["status"] = "done"
        log_scan_result(clean, result)
        self._log(
            f"✅ @{clean} xong: "
            f"pass={result['passed']} dup={result['skipped_dup']} reject={result['rejected']}"
        )
        return result

    async def scan_batch(self, usernames: list, force: bool = False) -> list:
        """
        Quét nhiều creator — Chia nhỏ ra từng lô (2 tác giả / lô) để chạy xen kẽ Phase 1 và Phase 2.
        - Phase 1: Mở Chrome cào profile 2 tác giả
        - Phase 2: Đóng Chrome, mở 3 Chromium để check LSD cho 2 tác giả đó
        """
        import sys
        from pathlib import Path
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth as _Stealth

        results = []
        if not usernames:
            return results

        tool_nhac_path = str(Path(__file__).resolve().parent.parent / "tool_nhac")
        if tool_nhac_path not in sys.path:
            sys.path.append(tool_nhac_path)

        chunk_size = 2
        for chunk_idx in range(0, len(usernames), chunk_size):
            chunk_users = usernames[chunk_idx:chunk_idx+chunk_size]
            all_raw_audios = []  # [(audio, username)] raw của lô này

            self._log(f"\n{'='*50}")
            self._log(f"📦 XỬ LÝ LÔ {chunk_idx//chunk_size + 1}: {', '.join(chunk_users)}")
            self._log(f"{'='*50}")

            # ── PHASE 1: Cào profile tác giả bằng 1 Chrome có đăng nhập ──────────────
            self._log("🌐 [Phase 1] Mở Chrome có session để cào tác giả...")
            pw1 = await async_playwright().__aenter__()
            user_data_dir = str(Path(__file__).resolve().parent / "tiktok_session")
            try:
                browser1 = await pw1.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    args=["--mute-audio", "--disable-dev-shm-usage"]
                )
                profile_page = browser1.pages[0] if browser1.pages else await browser1.new_page()
                await _Stealth().apply_stealth_async(profile_page)

                for idx, username in enumerate(chunk_users):
                    clean = username.strip().lstrip("@").lower()
                    self._log(f"\n[{idx+1}/{len(chunk_users)}] 📂 Cào tác giả @{clean}...")

                    if not force and is_creator_seen(clean, self._seen):
                        self._log(f"⏭️ @{clean} đã quét rồi, bỏ qua.")
                        results.append({"username": clean, "status": "already_scanned",
                                        "passed": 0, "skipped_dup": 0, "rejected": 0})
                        continue

                    try:
                        from creator_profile_crawler import CreatorProfileCrawler
                        crawler = CreatorProfileCrawler()
                        audios = await crawler.crawl_profile(clean, page=profile_page)
                        self._log(f"  ✅ Cào xong @{clean}: {len(audios)} audio thô")
                        for a in audios:
                            all_raw_audios.append((a, clean))
                    except Exception as e:
                        self._log(f"  ❌ Lỗi cào @{clean}: {e}")

                    await asyncio.sleep(2)

            except Exception as e:
                import traceback as _tb
                self._log(f"❌ [Phase 1] Lỗi mở Chrome: {e}")
                self._log(_tb.format_exc())
            finally:
                try: await browser1.close()
                except: pass
                try: await pw1.__aexit__(None, None, None)
                except: pass

            self._log(f"\n✅ [Phase 1] Lô {chunk_idx//chunk_size + 1} xong. Tổng {len(all_raw_audios)} audio thô cần check LSD.")

            if not all_raw_audios:
                continue

            # ── PHASE 2: Check LSD — 3 Chromium riêng biệt ─────────────────────────
            self._log("🚀 [Phase 2] Khởi động 3 Chromium để check LSD song song...")
            pw2 = await async_playwright().__aenter__()

            async def _block_heavy(route):
                try:
                    if route.request.resource_type in ("image", "media"):
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception: pass

            browsers = []
            lsd_pages = []
            for idx_b in range(3):
                try:
                    b = await pw2.chromium.launch(
                        headless=False,
                        args=["--mute-audio", "--disable-dev-shm-usage"]
                    )
                    ctx = await b.new_context(viewport={"width": 900, "height": 700})
                    p = await ctx.new_page()
                    await p.route("**/*", _block_heavy)
                    await _Stealth().apply_stealth_async(p)
                    browsers.append(b)
                    lsd_pages.append(p)
                    self._log(f"  ✅ Chromium #{idx_b+1} sẵn sàng")
                except Exception as e:
                    import traceback as _tb3
                    self._log(f"  ❌ Không khởi động được Chromium #{idx_b+1}: {e}")
                    self._log(_tb3.format_exc())

            if not lsd_pages:
                self._log("❌ [Phase 2] Không có Chromium nào khả dụng, bỏ qua LSD check")
                await pw2.__aexit__(None, None, None)
                continue

            self._log(f"  ✅ {len(lsd_pages)}/3 Chromium sẵn sàng")

            try:
                from filter import get_dynamic_min_usage
                from database import check_duplicate

                passed_count = 0
                rejected_count = 0
                dup_count = 0

                for i in range(0, len(all_raw_audios), len(lsd_pages)):
                    chunk = all_raw_audios[i:i+len(lsd_pages)]
                    self._log(f"\n[Phase 2] Check LSD nhóm {i+1}–{i+len(chunk)} / {len(all_raw_audios)}...")

                    async def _check_one(audio, creator_name, page):
                        nonlocal passed_count, rejected_count, dup_count
                        audio_id = audio.audio_id

                        if audio_id in self.checked_audio:
                            dup_count += 1
                            return None
                        if await check_duplicate(audio_id):
                            self.checked_audio.add(audio_id)
                            dup_count += 1
                            return None

                        usage = audio.usage_count or 0
                        if usage <= 0 and audio.audio_page_url:
                            try:
                                from crawler import TikTokCrawler
                                tmp = TikTokCrawler()
                                usage = await tmp.get_accurate_usage(audio.audio_page_url, page=page)
                                audio.usage_count = usage
                            except Exception:
                                pass

                        passed = True
                        reason = ""
                        if audio.duration and audio.duration > 59:
                            passed = False
                            reason = f"duration {audio.duration}s > 59s"
                        else:
                            min_req = get_dynamic_min_usage(audio.create_time)
                            if usage < min_req:
                                passed = False
                                reason = f"Usage {usage:,} < {min_req:,}"
                            else:
                                from engine import FilterEngine
                                is_orig, _ = FilterEngine.is_original_sound(audio.audio_name)
                                if not is_orig:
                                    passed = False
                                    reason = "not original sound"

                        if not passed:
                            self._log(f"  ❌ Loại: {audio.audio_name[:40]} ({reason})")
                            rejected_count += 1
                            return None

                        self._log(f"  ✅ Pass: {audio.audio_name[:40]} (LSD={usage:,})")
                        self.checked_audio.add(audio_id)
                        passed_count += 1

                        audio.status = "pending_ai"
                        audio.source_type = "creator_scan"
                        try:
                            from database import insert_audio
                            await insert_audio(audio)
                        except Exception as e:
                            self._log(f"  ⚠️ Lưu DB lỗi: {e}")

                        info = {
                            "audio_id":    audio_id,
                            "audio_name":  audio.audio_name,
                            "usage_count": usage,
                            "audio_link":  audio.audio_page_url,
                            "video_link":  audio.video_url,
                            "source":      "creator_scan",
                            "creator":     creator_name,
                            "created_at":  import_time().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        if self.on_result:
                            try:
                                await self.on_result(info)
                            except Exception as e:
                                self._log(f"  ⚠️ on_result lỗi: {e}")
                        return info

                    import time as _time
                    def import_time():
                        return _time

                    tasks = [
                        _check_one(audio, creator, lsd_pages[j % len(lsd_pages)])
                        for j, (audio, creator) in enumerate(chunk)
                    ]
                    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

                    for r in chunk_results:
                        if r and isinstance(r, dict):
                            results.append(r)
                        elif isinstance(r, Exception):
                            self._log(f"  ⚠️ Exception: {r}")

                    await asyncio.sleep(1)

                seen_in_batch = {c for _, c in all_raw_audios}
                for c in seen_in_batch:
                    mark_creator_seen(c)
                    self._seen.add(c)

                self._log(f"\n🎉 [Phase 2 - Lô {chunk_idx//chunk_size + 1}] Hoàn tất! Pass={passed_count} | Dup={dup_count} | Reject={rejected_count}")

            except Exception as e:
                import traceback as _tb2
                self._log(f"❌ [Phase 2] Lỗi: {e}")
                self._log(_tb2.format_exc())
            finally:
                for b in browsers:
                    try: await b.close()
                    except: pass
                try: await pw2.__aexit__(None, None, None)
                except: pass

        return results


# ── HTTP result reporter (dùng chung với main.py) ─────────────────────────────

async def report_scan_result(entry: dict):
    """Gửi kết quả quét creator về server.py."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(FYP_RESULT_URL, json=entry)
    except Exception:
        print(f"RESULT_JSON:{json.dumps(entry, ensure_ascii=False)}")

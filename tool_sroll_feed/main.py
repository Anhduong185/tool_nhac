import time
import asyncio
import os
import json
import random
from datetime import datetime
from browser import TikTokAgent
from audio_processor import AudioProcessor
from engine import FilterEngine
from database import init_db, save_audio, AudioRecord, get_all_viewed_videos, get_all_audio_ids, save_viewed_video
from config import TEMP_AUDIO_DIR, TOOL_NHAC_DB, FYP_RESULT_URL
from excel_manager import init_excel, get_existing_links, save_to_excel
from market_expander import MarketExpander


async def report_result(entry: dict):
    """
    Gửi kết quả về server.py qua HTTP POST.
    Fallback: print RESULT_JSON nếu server không có mặt.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(FYP_RESULT_URL, json=entry)
    except Exception:
        # Fallback: server.py vẫn đọc được qua stdout nếu HTTP fail
        print(f"RESULT_JSON:{json.dumps(entry, ensure_ascii=False)}")


# ─── Browser Health Monitor Config ─────────────────────────────────────────────
# ─── Config ────────────────────────────────────────────────────────────────────
RELOAD_EVERY_N_VIDEOS  = 50   # Reload trang sau mỗi 50 video
WATCHDOG_TIMEOUT       = 90   # Tối đa 90s/vòng lặp
EXPAND_EVERY_N_VIDEOS  = 25   # Phá bong bóng sau mỗi 25 video
WATCH_TIME_GOOD_AUDIO  = True # Xem thêm nếu audio tốt → nuôi acc

# P3 — Adaptive scroll speed
SCROLL_NORMAL   = (3.0, 6.0)  # seconds khi bình thường
SCROLL_FAST     = (1.0, 2.5)  # seconds khi liên tục miss
MISS_THRESHOLD  = 10          # Sau 10 miss liên tiếp → scroll nhanh

# P4 — Sleep schedule
SLEEP_HOUR_START = 2     # Tắt lúc 2am
SLEEP_HOUR_END   = 7     # Bật lại lúc 7am
SLEEP_CHECK_MIN  = 30    # Kiểm tra lại mỗi 30 phút

async def process_one_video(agent, visited_videos, checked_audio):
    """Xử lý 1 video từ FYP. Trả về True nếu thành công, False nếu skip."""
    await agent.scroll()
    video_info = await agent.get_current_video_info()

    if video_info.get("skip"):
        return

    video_link = video_info.get("video_link")

    if video_link and video_link in visited_videos:
        print(f"⏭️ Video đã xem: {video_link}")
        return

    if not video_info["is_original"]:
        return

    sound_url = video_info["sound_link"]
    audio_id = sound_url.split("/")[-1].split("?")[0]
    score = video_info.get("sound_score", 0)

    if audio_id in checked_audio:
        print(f"⏭️ Audio đã check: {audio_id}")
        if video_link: visited_videos.add(video_link)
        return

    print(f"🔍 Đang phân tích audio: {audio_id} (Score: {score})")

    sound_url = video_info.get("sound_link", "")
    play_url = video_info.get("play_url", "")
    details = await agent.extract_audio_details(sound_url, play_url)
    if not details:
        await agent.go_back()
        return

    if video_link:
        visited_videos.add(video_link)
        save_viewed_video(video_link)
    checked_audio.add(audio_id)

    final_video_link = details.get("grid_video_link") or video_link
    if not final_video_link:
        print("⚠️ Không tìm thấy link video, bỏ qua.")
        await agent.go_back()
        return

    try:
        # Đóng gói thành AudioMetadata của tool_nhac
        import sys
        tool_nhac_path = "e:/tool_nhac/tool_nhac"
        if tool_nhac_path not in sys.path:
            sys.path.append(tool_nhac_path)
            
        from models import AudioMetadata
        from audio_pipeline import AudioPipeline

        duration = details.get('duration', 0)
        audio_obj = AudioMetadata(
            audio_id=audio_id,
            audio_name=details.get('audio_name', ''),
            audio_page_url=sound_url,
            video_url=final_video_link,
            author_username=details.get("author_username", ""),
            duration=duration,
            usage_count=details['usage_count'],
            create_time=int(datetime(details.get('year', 2024), 6, 1).timestamp()),
            video_views=0,
            status="pending"
        )
        
        # Thêm bytes audio trực tiếp để AudioPipeline khỏi cần tải lại!
        audio_bytes = details.get('audio_bytes', b'')
        if audio_bytes:
            temp_file = os.path.join(TEMP_AUDIO_DIR, f"{audio_id}.mp3")
            import aiofiles
            async with aiofiles.open(temp_file, 'wb') as f:
                await f.write(audio_bytes)
            audio_obj.file_path = temp_file

        pipeline = AudioPipeline.get()
        result = await pipeline.process(audio_obj, trend_result=None)
        
        passed = result.passed
        reason = result.reason

        if passed:
            save_to_excel(final_video_link, details['usage_count'], audio_id)
            print(f"✅ ĐẠT: {final_video_link} (Lượt dùng: {details['usage_count']}, Dài: {duration}s)")

            liked, saved_fav = await agent.like_and_save_video()
            followed = await agent.follow_creator()
            
            # Trích xuất username từ link video và ném vào danh sách VIP cho Máy Quét (Creator Scanner)
            # Link format: https://www.tiktok.com/@username/video/123...
            try:
                username = final_video_link.split("/@")[1].split("/")[0].split("?")[0]
                from config import SHARED_CREATORS
                with open(SHARED_CREATORS, "a", encoding="utf-8") as f:
                    f.write(f"@{username}\n")
                print(f"⭐ Đã bắt sống @{username} và tống vào Hàng đợi VIP của Máy Quét!")
            except Exception as e:
                pass
            
            if liked or saved_fav or followed:
                print(f"💞 Đã tương tác (like={liked}, save={saved_fav}, follow={followed}) → Dạy thuật toán xong!")

            # Gửi kết quả về dashboard qua HTTP POST (không dùng stdout nữa)
            entry = {
                "audio_id":    audio_id,
                "audio_name":  details.get("audio_name", "FYP Audio"),
                "usage_count": details['usage_count'],
                "audio_link":  sound_url,
                "video_link":  final_video_link,
                "source":      "fyp",
                "created_at":  time.strftime("%Y-%m-%d %H:%M:%S")
            }
            await report_result(entry)

        else:
            print(f"❌ LOẠI: {audio_id} ({reason})")

        save_audio(AudioRecord(
            audio_id=audio_id, audio_link=sound_url,
            usage_count=details['usage_count'], duration=int(duration),
            original_video_link=final_video_link,
            year=audio_data['year'],
            recent_usage=audio_data['recent_usage'],
            source_type=audio_data['source_type'],
            status="passed" if passed else "rejected",
            rejection_reason=reason if not passed else None
        ))
    except Exception as e:
        print(f"⚠️ Lỗi xử lý: {e}")

    # Nếu pass → xem thêm 3–8s để nuôi acc (watch time signal)
    if passed and WATCH_TIME_GOOD_AUDIO:
        extra = random.uniform(3, 8)
        print(f"⏱️ Watch time +{extra:.1f}s (signal algo)")
        await asyncio.sleep(extra)

    await agent.go_back()
    await asyncio.sleep(random.uniform(*SCROLL_NORMAL))  # scroll speed bình thường
    return passed


async def main():
    try:
        init_db()
        init_excel()

        visited_videos = get_all_viewed_videos()
        checked_audio = get_all_audio_ids()
        excel_links, excel_ids = get_existing_links()
        visited_videos.update(excel_links)
        checked_audio.update(excel_ids)
        
        # Dedup từ tool_nhac DB (realtime — dùng path từ config)
        try:
            import sqlite3
            if TOOL_NHAC_DB.exists():
                conn = sqlite3.connect(str(TOOL_NHAC_DB))
                cursor = conn.cursor()
                cursor.execute("SELECT audio_id FROM audio_history")
                for row in cursor.fetchall():
                    checked_audio.add(row[0])
                conn.close()
                print(f"✅ Đã đồng bộ kho nhạc từ tool_nhac ({len(checked_audio)} audios)!")
            else:
                print(f"⚠️ tool_nhac DB chưa có tại {TOOL_NHAC_DB}")
        except Exception as e:
            print(f"⚠️ Lỗi đồng bộ DB từ tool_nhac: {e}")
            
        print(f"📊 RAM Cache: {len(visited_videos)} videos, {len(checked_audio)} audios đã lưu.")

        agent = TikTokAgent()
        await agent.start()
        await agent.go_to_feed()
        print("✅ Đã sẵn sàng! Đang quét Feed...")

        # V2.1: MarketExpander — phá bong bóng FYP
        expander = MarketExpander(agent.page)
        print(f"🌍 MarketExpander sẵn sàng (kích hoạt mỗi {EXPAND_EVERY_N_VIDEOS} video)")

        video_count = 0
        consecutive_errors = 0
        consecutive_misses = 0   # P3: đếm miss liên tiếp

        # P1: hàm wrapper để truyền vào expander.expand()
        async def process_video_url(url: str, ca: set):
            """Wrapper để MarketExpander gọi xử lý video khi harvest."""
            await process_one_video(agent, ca, ca)

        while True:
            video_count += 1
            print(f"\n--- [Video thứ {video_count}] ---")

            # P4 — Sleep schedule (2am–7am)
            from datetime import datetime
            hour = datetime.now().hour
            if SLEEP_HOUR_START <= hour < SLEEP_HOUR_END:
                print(f"😴 Giờ nghỉ ({SLEEP_HOUR_START}am–{SLEEP_HOUR_END}am), tạm dừng {SLEEP_CHECK_MIN} phút...")
                await asyncio.sleep(SLEEP_CHECK_MIN * 60)
                continue

            # P3 — Adaptive scroll: sau MISS_THRESHOLD miss → scroll nhanh
            scroll_range = SCROLL_FAST if consecutive_misses >= MISS_THRESHOLD else SCROLL_NORMAL
            if consecutive_misses >= MISS_THRESHOLD:
                print(f"⚡ [AdaptiveScroll] {consecutive_misses} miss liên tiếp → scroll nhanh {scroll_range}s")

            # ─── MARKET EXPAND: cứ EXPAND_EVERY_N_VIDEOS video → phá bong bóng ──
            if video_count % EXPAND_EVERY_N_VIDEOS == 0 and video_count > 0:
                exp_no = video_count // EXPAND_EVERY_N_VIDEOS
                print(f"🌍 [Expand #{exp_no}] Đang mở rộng thị trường...")
                use_creator = exp_no % 2 == 1  # lẻ → creator, chẵn → search
                try:
                    result = await asyncio.wait_for(
                        expander.expand(
                            use_creator=use_creator,
                            checked_audio=checked_audio,  # P1: harvest audio
                            process_fn=process_video_url,
                        ),
                        timeout=180  # tăng từ 120 lên 180 vì có harvest
                    )
                    h = result.get('harvested', 0)
                    print(f"✅ [Expand] xem={result['watched']} thu_hoạch={h} audio ({result['strategy']})")
                    if h > 0:
                        consecutive_misses = 0  # reset khi harvest được audio
                except asyncio.TimeoutError:
                    print("⏰ [Expand] Timeout, tiếp tục FYP...")
                except Exception as e:
                    print(f"⚠️ [Expand] Lỗi: {e}")
                await agent.go_to_feed()
                await asyncio.sleep(3)
                continue

            # ─── HEALTH CHECK: Cứ mỗi RELOAD_EVERY_N_VIDEOS video thì reload ──
            if video_count % RELOAD_EVERY_N_VIDEOS == 0:
                print(f"🔄 [Health] Đã xử lý {video_count} video → Reload trang để flush memory...")
                is_healthy = await agent.check_page_health()
                if not is_healthy:
                    print("💀 [Health] Trang đang đơ! Buộc reload...")
                await agent.reload_feed()
                consecutive_errors = 0
                continue

            # ─── WATCHDOG: Giới hạn mỗi vòng lặp tối đa WATCHDOG_TIMEOUT giây ──
            try:
                passed = await asyncio.wait_for(
                    process_one_video(agent, visited_videos, checked_audio),
                    timeout=WATCHDOG_TIMEOUT
                )
                consecutive_errors = 0
                # P3: cập nhật miss counter
                if passed:
                    consecutive_misses = 0
                else:
                    consecutive_misses += 1
                # P3: scroll speed thượng mài (fast/normal)
                await asyncio.sleep(random.uniform(*scroll_range))

            except asyncio.TimeoutError:
                consecutive_errors += 1
                print(f"⏰ [Watchdog] Vòng lặp mất quá {WATCHDOG_TIMEOUT}s (lỗi #{consecutive_errors}) → Reload trang...")
                await agent.reload_feed()

            except asyncio.CancelledError:
                # Bị hủy từ bên ngoài (SIGTERM / server.py dừng) → thoát sạch
                print("⏹ [FYP] Nhận tín hiệu dừng, đang đóng browser...")
                raise

            except Exception as e:
                consecutive_errors += 1
                print(f"💥 [Loop Error #{consecutive_errors}]: {e}")
                if consecutive_errors >= 5:
                    print("🆘 Quá nhiều lỗi liên tiếp → Reload trang để khôi phục...")
                    await agent.reload_feed()
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(3)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("🛑 Đang dừng tool FYP...")
    except Exception as e:
        print(f"💥 Lỗi nghiêm trọng: {e}")
    finally:
        if 'agent' in locals() and agent.browser:
            try: await agent.close()
            except: pass
        print("✅ FYP tool đã dừng sạch.")

async def _run():
    try:
        await main()
    except asyncio.CancelledError:
        pass  # Shutdown sạch — không cần log

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass

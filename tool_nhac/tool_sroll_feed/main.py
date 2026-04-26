import time
import asyncio
import os
import requests
from browser import TikTokAgent
from audio_processor import AudioProcessor
from engine import FilterEngine
from database import init_db, save_audio, get_audio, AudioRecord, get_all_viewed_videos, get_all_audio_ids, save_viewed_video
from config import TEMP_AUDIO_DIR
from excel_manager import init_excel, get_existing_links, save_to_excel

async def main():
    try:
        init_db()
        init_excel()
        
        # 1. KHỞI TẠO CACHE RAM
        visited_videos = get_all_viewed_videos()
        checked_audio = get_all_audio_ids()
        
        # Thêm từ Excel
        excel_links, excel_ids = get_existing_links()
        visited_videos.update(excel_links)
        checked_audio.update(excel_ids)
        
        print(f"📊 RAM Cache: {len(visited_videos)} videos, {len(checked_audio)} audios.")

        agent = TikTokAgent()
        await agent.start()
        
        processor = AudioProcessor()
        
        await agent.go_to_feed()
        print("✅ Đã sẵn sàng! Đang quét Feed...")
        
        video_count = 0
        while True:
            video_count += 1
            print(f"\n--- [Video thứ {video_count}] ---")
            await agent.scroll()
            video_info = await agent.get_current_video_info()
            
            if video_info.get("skip"):
                continue

            video_link = video_info.get("video_link")
            
            # --- LAYER 1: DEDUP VIDEO ---
            if video_link and video_link in visited_videos:
                print(f"⏭️ Video đã xem: {video_link}")
                continue
            
            if not video_info["is_original"]: 
                continue

            sound_url = video_info["sound_link"]
            audio_id = sound_url.split("/")[-1].split("?")[0]
            score = video_info.get("sound_score", 0)

            # --- LAYER 2: DEDUP AUDIO ---
            if audio_id in checked_audio:
                print(f"⏭️ Audio đã check: {audio_id}")
                if video_link: visited_videos.add(video_link)
                continue

            print(f"🔍 Đang phân tích audio: {audio_id} (Score: {score})")

            
            # Truy cập trang audio
            details = await agent.extract_audio_details(sound_url)
            if not details: 
                await agent.go_back()
                continue
            
            # Lưu vết video và audio vào cache RAM để không bao giờ quay lại
            if video_link: 
                visited_videos.add(video_link)
                save_viewed_video(video_link)
            checked_audio.add(audio_id)

            # XÁC ĐỊNH LINK VIDEO CUỐI CÙNG (Ưu tiên Grid video từ audio page)
            final_video_link = details.get("grid_video_link") or video_link
            
            if not final_video_link:
                print("⚠️ Không tìm thấy link video, bỏ qua.")
                await agent.go_back()
                continue

            try:
                duration = details.get('duration', 0)
                
                audio_data = {
                    'duration': duration,
                    'usage_count': details['usage_count'],
                    'is_voice_only': True, # Mặc định True vì không check audio nữa
                    'is_copyrighted': False, # Mặc định False vì không check Shazam nữa
                    'year': details.get('year', 2024),
                    'recent_usage': details.get('recent_usage', 0),
                    'source_type': details.get('source_type', 'original')
                }
                
                passed, reason = FilterEngine.is_valid(audio_data)
                
                if passed:
                    save_to_excel(final_video_link, details['usage_count'], audio_id)
                    print(f"✅ ĐẠT: {final_video_link} (Lượt dùng: {details['usage_count']}, Dài: {duration}s)")
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

            await agent.go_back()
            await asyncio.sleep(1)

                
    except KeyboardInterrupt:
        print("🛑 Đang dừng tool...")
    except Exception as e:
        print(f"💥 Lỗi: {e}")
    finally:
        if 'agent' in locals() and agent.browser:
            try: await agent.close()
            except: pass

if __name__ == "__main__":
    asyncio.run(main())


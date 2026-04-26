import asyncio
from loguru import logger
import sys
import random
from collections import defaultdict
from typing import Callable, Optional
import os

# Ghi log ra file ngay khi module được import (kể cả khi chạy qua server.py)
logger.add(
    "data/logs/system.log",
    rotation="20 MB",
    retention="7 days",
    level="DEBUG",      # DEBUG để thấy [Profile] reject reasons
    encoding="utf-8",
)

from config import KEYWORDS_FILE
from database import (
    init_db, check_duplicate, insert_audio, clear_rejected_audios, 
    get_accepted_results, add_target_user, get_next_target_user, mark_user_crawled,
    sync_from_fyp_db
)
from filter import is_valid_audio, is_valid_audio_async, compute_quality_score
from crawler import TikTokCrawler
from audio_processor import download_audio, check_shazam

from ai.speech_classifier import SpeechClassifier
from ai.smart_ranker import SmartRanker, MIN_ACCEPTABLE_SCORE
from ai.multi_ai_manager import MultiAIManager

# V2.1: Dual Engine imports
from audio_pipeline import AudioPipeline
from creator_profile_crawler import CreatorProfileCrawler
from trend_detector import analyze_trend, get_priority

# ─── Session-level metrics ─────────────────────────────────────────────────────
_metrics = defaultdict(int)
_rejection_reasons = defaultdict(int)

# Tập hợp các audio_id đang được xử lý để tránh lỗi tranh chấp file (Race Condition)
_processing_ids = set()

# Lazy init — tạo khi có event loop, tránh lỗi khi import từ server.py
_processing_lock = None
browser_semaphore = None
download_semaphore = None
ai_semaphore = None


_global_emit = None

def _m(key, n=1): 
    _metrics[key] += n
    if _global_emit:
        _print_metrics("Realtime", emit=_global_emit, clear=False)

def _print_metrics(keyword: str, emit=None, clear=True):
    c  = _metrics["crawled"]
    d  = _metrics["dup"]
    f  = _metrics["filter_reject"]
    s  = _metrics["shazam_reject"]
    sp = _metrics["speech_reject"]
    ul = _metrics["usage_low"]
    uu = _metrics["usage_unknown"]
    ac = _metrics["accepted"]
    
    msg = (f"[{keyword}] Crawled={c} | Dup={d} | Filter={f} | "
           f"Shazam={s} | Speech={sp} | UsageLow={ul} | Unknown={uu} | ✅={ac}")
    
    # Chỉ log ra terminal khi thực sự in chốt block (clear=True) để tránh spam
    if clear:
        logger.info(msg)
    
    # Luôn gửi metrics chi tiết lên UI (Realtime)
    if emit: 
        emit({
            "type": "metrics",
            "keyword": keyword,
            "crawled": c, "dup": d, "filter": f + sp + ul + uu,
            "shazam": s, "accepted": ac
        })
    if clear:
        _metrics.clear()

# ──────────────────────────────────────────────────────────────────────────────

async def process_audio_chain(seed_id: str, seed_name: str, classifier: SpeechClassifier,
                               emit=None, on_result=None, found_ids=None):
    """
    BƯỚC 2: Audio Chain — từ 1 audio đã accepted → crawl trang /music/[id]
    → thu thập audio mới từ các video trong đó → xử lý qua toàn bộ filter pipeline.
    """
    def log(msg):
        logger.info(msg)
        if emit: emit(msg)

    log(f"🔗 [AudioChain] Seed: {seed_name[:40]} ({seed_id})")
    crawler = TikTokCrawler()

    async with browser_semaphore:
        audios = await crawler.crawl_audio_chain(seed_id, seed_name)

    valid_audios = []
    for audio in audios:
        if await check_duplicate(audio.audio_id):
            continue
        is_valid, reason = await is_valid_audio_async(audio)
        if not is_valid:
            audio.status = "rejected"; audio.reason = reason
            await insert_audio(audio)
            continue
        valid_audios.append(audio)

    log(f"🔗 [AudioChain] Lọc xong: {len(valid_audios)}/{len(audios)} hợp lệ")

    from audio_pipeline import AudioPipeline
    pipeline = AudioPipeline.get()
    
    async def process_chain_audio(audio):
        def _on_accept(a):
            if on_result:
                on_result({
                    "audio_id":       a.audio_id,
                    "audio_name":     a.audio_name,
                    "usage_count":    a.usage_count,
                    "audio_page_url": a.audio_page_url,
                    "ai_score":       a.ai_score,
                    "speech_ratio":   a.speech_ratio,
                    "source":         "audio_chain",
                })
            if found_ids is not None:
                found_ids.add(a.audio_id)
                
            # Đào các tác giả có view/lượt dùng âm thanh cao từ audio chain này
            if a.author_username:
                asyncio.create_task(add_target_user(a.author_username))
                
        await pipeline.process(audio, trend_result=None, on_accepted=_on_accept)

    if valid_audios:
        log(f"🔗 [AudioChain] Chạy AI Check (V2.1) cho {len(valid_audios)} video...")
        await asyncio.gather(*[process_chain_audio(a) for a in valid_audios])

# ──────────────────────────────────────────────────────────────────────────────

# ── V2.1: Creator Mining Engine (Profile Mode) ────────────────────────────────
async def process_profile_batch(
    batch_count: int,
    pipeline: AudioPipeline,
    emit=None,
    on_result=None,
    found_ids=None,
):
    """
    Chạy Creator Mining Engine: cào tab Thịnh hành của các kênh trong creators_list.txt
    Rồi đẩy toàn bộ audio vào AudioPipeline V2.1.
    """
    def log(msg):
        logger.info(msg)
        if emit: emit(msg)

    log(f"[V2.1 Profile Engine] Bắt đầu batch {batch_count} ...")

    from pathlib import Path
    creators_file = Path(__file__).resolve().parent / "creators_list.txt"
    if not creators_file.exists():
        log("creators_list.txt chưa có — bỏ qua Profile Engine lần này.")
        return

    profile_crawler = CreatorProfileCrawler()
    audios = await profile_crawler.crawl_all(
        creators_file=creators_file,
        batch_count=batch_count,
        limit=1,   # Tối đa 1 kênh/batch để không quá tải (theo yêu cầu Random 1 tác giả)
    )

    log(f"  Profile Engine: {len(audios)} audio candidates")

    # Đào hết tất cả các video cùng dùng âm thanh gốc (Audio Chain)
    all_candidates = list(audios)
    
    # Tìm các âm thanh gốc (do chính tác giả tạo ra) để dùng làm "mồi"
    original_sounds = [a for a in audios if a.status != "rejected" and ("original" in a.audio_name.lower() or "suara asli" in a.audio_name.lower() or "âm thanh gốc" in a.audio_name.lower() or "เสียงต้นฉบับ" in a.audio_name.lower() or "originalton" in a.audio_name.lower())]
    
    if original_sounds:
        log(f"  [Auto-AudioChain] Tìm thấy {len(original_sounds)} âm thanh gốc. Bắt đầu đào sâu...")
        crawler = TikTokCrawler()
        for seed_audio in original_sounds:
            async with browser_semaphore:
                chain_audios = await crawler.crawl_audio_chain(seed_audio.audio_id, seed_audio.audio_name)
                if chain_audios:
                    all_candidates.extend(chain_audios)
                    log(f"    → Đào được thêm {len(chain_audios)} video đu trend từ '{seed_audio.audio_name[:30]}'")

    # Hàm chạy ngầm song song để kiểm tra toàn bộ audio
    async def process_candidates_background(candidates):
        usage_passed = []
        for audio in candidates:
            # Lọc cơ bản
            if await check_duplicate(audio.audio_id): continue
            
            # Check Usage >= 500
            accurate_usage = audio.usage_count
            if accurate_usage == 0:
                crawler_bg = TikTokCrawler()
                async with browser_semaphore:
                    accurate_usage = await crawler_bg.get_accurate_usage(audio.audio_page_url)
            
            if accurate_usage > 0:
                audio.usage_count = accurate_usage
                
            from config import MIN_USAGE_COUNT
            if audio.usage_count < MIN_USAGE_COUNT:
                audio.status = "rejected"
                await insert_audio(audio)
                continue
                
            usage_passed.append(audio)

        # Chạy AI qua AudioPipeline
        if usage_passed:
            log(f"  [Background] {len(usage_passed)} audios qua Usage Check. Đang chạy AI...")
            from audio_pipeline import AudioPipeline
            pipeline = AudioPipeline.get()
            
            async def _process_with_pipeline(a):
                def _on_accept(accepted_a):
                    if on_result:
                        on_result({
                            "audio_id":       accepted_a.audio_id,
                            "audio_name":     accepted_a.audio_name,
                            "usage_count":    accepted_a.usage_count,
                            "audio_page_url": accepted_a.audio_page_url,
                            "ai_score":       accepted_a.ai_score,
                            "speech_ratio":   accepted_a.speech_ratio,
                            "source":         "profile_chain",
                        })
                await pipeline.process(a, trend_result=None, on_accepted=_on_accept)

            await asyncio.gather(*[_process_with_pipeline(a) for a in usage_passed])
            
        log("  [Background] Hoàn tất lô xử lý ngầm!")

    # Bắn tiến trình chạy ngầm song song!
    log(f"  Profile Engine: Bắt đầu kiểm tra ngầm {len(all_candidates)} audio tổng hợp...")
    asyncio.create_task(process_candidates_background(all_candidates))
    
    _print_metrics("Profile Engine", emit)

# ──────────────────────────────────────────────────────────────────────────────

async def process_keyword(keyword: str, classifier: SpeechClassifier, emit=None, on_result=None, found_ids=None, use_hashtag: str = None):
    def log(msg): 
        logger.info(msg)
        if emit: emit(msg)

    log(f"== BẮT ĐẦU: {keyword} ==")
    crawler = TikTokCrawler()
    
    try:
        async with browser_semaphore:
            if use_hashtag:
                audios = await crawler.crawl_hashtag(use_hashtag)
            else:
                audios = await crawler.crawl_keyword(keyword)
    except asyncio.CancelledError:
        log(f"[{keyword}] Bị hủy, dừng sạch.")
        return
    except Exception as e:
        import traceback
        logger.error(f"[{keyword}] Lỗi crawl: {e}\n{traceback.format_exc()}")
        log(f"[{keyword}] Lỗi crawl: {e}")
        return
    
    _m("crawled", len(audios))

    valid_audios = []

    for audio in audios:
        if await check_duplicate(audio.audio_id):
            _m("dup"); continue

        is_valid, reason = await is_valid_audio_async(audio)
        if not is_valid:
            _m("filter_reject")
            audio.status = "rejected"
            audio.reason = reason
            await insert_audio(audio)
            continue

        valid_audios.append(audio)

    from config import TARGET_AUDIOS
    valid_audios = valid_audios[:TARGET_AUDIOS]

    # ── SỬ DỤNG BỘ LỌC CHUẨN AUDIO PIPELINE (V2.1) ──
    from audio_pipeline import AudioPipeline
    pipeline = AudioPipeline.get()

    async def process_with_pipeline(audio):
        def _on_accept(a):
            if on_result:
                on_result({
                    "audio_id":       a.audio_id,
                    "audio_name":     a.audio_name,
                    "usage_count":    a.usage_count,
                    "audio_page_url": a.audio_page_url,
                    "ai_score":       a.ai_score,
                    "speech_ratio":   a.speech_ratio,
                    "source":         "keyword",
                })
            if found_ids is not None:
                found_ids.add(a.audio_id)

        result = await pipeline.process(audio, trend_result=None, on_accepted=_on_accept)
        
        if result.passed:
            _m("accepted")
        else:
            if result.stage == "DEDUP": _m("dup")
            elif result.stage == "USAGE_CHECK": _m("usage_low")
            elif "Shazam" in result.reason: _m("shazam_reject")
            else: _m("filter_reject")

    # Chạy đồng thời AudioPipeline cho các audio
    if valid_audios:
        log(f"  [Keyword] Bắt đầu xử lý {len(valid_audios)} audio qua Pipeline V2.1...")
        await asyncio.gather(*[process_with_pipeline(a) for a in valid_audios])

    _print_metrics(keyword, emit)

async def run_crawler(target: int = 10, niche: str = "auto", on_log: Optional[Callable[[str], None]] = None, on_result: Optional[Callable[[dict], None]] = None, stop_event: Optional[asyncio.Event] = None):
    global _processing_lock, browser_semaphore, download_semaphore, ai_semaphore, _global_emit
    if browser_semaphore is None:
        _processing_lock = asyncio.Lock()
        browser_semaphore = asyncio.Semaphore(6)
        download_semaphore = asyncio.Semaphore(10)
        ai_semaphore = asyncio.Semaphore(5)

    def emit(data):
        if isinstance(data, str):
            logger.info(data)
            if on_log: on_log(data)
        else:
            if on_log: on_log(data)
            
    _global_emit = emit

    await init_db()
    from database import load_authors_from_sources
    await load_authors_from_sources()
    # BƯỚC 1: Sync dedup từ tool_sroll_feed
    n = await sync_from_fyp_db()
    if n: emit(f"✅ [Bridge] Đã sync {n} audio IDs từ FYP tool → Tránh trùng lặp")
    import config as cfg
    cfg.MIN_USAGE_COUNT = 500

    if not KEYWORDS_FILE.exists():
        emit("Lỗi: File keywords.txt không tồn tại!")
        return

    base_keywords = [line.strip() for line in open(KEYWORDS_FILE, "r", encoding="utf-8") if line.strip() and not line.strip().startswith("#")]
    emit(f"Nạp {len(base_keywords)} từ khóa. Mục tiêu: {target} audio.")

    classifier = SpeechClassifier()
    classifier._load_model()
    emit("AI model đã sẵn sàng. Bắt đầu crawl...")

    ai_manager = MultiAIManager()
    if ai_manager.groq.api_key or ai_manager.gemini.api_key:
        emit("✨ Hệ thống AI (Groq/Gemini) đã sẵn sàng.")
    else:
        emit("ℹ️ Không tìm thấy AI API Key, sử dụng bộ từ khóa tĩnh.")

    # V2.1: Khởi tạo AudioPipeline singleton (giữ AI model trong RAM)
    pipeline = AudioPipeline.get()
    emit("🚀 [V2.1] Dual Engine: Profile Mining + Global Seeder sẵn sàng.")

    run_count = 0
    found_ids: set = set()

    while True:
        if stop_event and stop_event.is_set():
            emit("⏹ Đã dừng theo yêu cầu.")
            return

        # Tự động cập nhật danh sách tác giả từ file Excel/Google Sheet mỗi vòng lặp
        from database import load_authors_from_sources
        await load_authors_from_sources()
        
        run_count += 1
        if run_count > 1 and run_count % 3 == 0 and cfg.MIN_USAGE_COUNT > 100:
            cfg.MIN_USAGE_COUNT = max(cfg.MIN_USAGE_COUNT // 2, 100)
            emit(f"[➡] Nới lỏng ngưỡng MIN_USAGE → {cfg.MIN_USAGE_COUNT}")

        # IDEA 5: Crash Recovery - Bọc toàn bộ vòng lặp trong try/except
        try:
            if niche != "auto":
                seed_kw = niche
                if ai_manager.groq.api_key or ai_manager.gemini.api_key:
                    emit(f"🤖 Đang nhờ AI bốc từ khóa cho ngách: '{seed_kw}'...")
                    smart_keywords = await ai_manager.expand_keyword(seed_kw)
                    keyword_list = smart_keywords[:2] if smart_keywords else [seed_kw]
                else:
                    keyword_list = [seed_kw]
            else:
                if ai_manager.groq.api_key or ai_manager.gemini.api_key:
                    seed_kw = random.choice(base_keywords)
                    emit(f"🤖 [Auto-Expand] Đang nhờ AI tối ưu hóa: '{seed_kw}'...")
                    smart_keywords = await ai_manager.expand_keyword(seed_kw)
                    keyword_list = smart_keywords[:2] if smart_keywords else random.sample(base_keywords, min(2, len(base_keywords)))
                else:
                    keyword_list = random.sample(base_keywords, min(2, len(base_keywords)))

            emit(f"[Vòng {run_count}] Đang quét: {', '.join(keyword_list)}")

            tasks = []
            for kw in keyword_list:
                tasks.append(process_keyword(kw, classifier, emit, on_result=on_result, found_ids=found_ids))

            # V2.1: Profile Engine — chạy mỗi vòng (Engine chủ lực 60%)
            tasks.append(process_profile_batch(
                batch_count=run_count,
                pipeline=pipeline,
                emit=emit,
                on_result=on_result,
                found_ids=found_ids,
            ))

            # BƯỚC 3: Hashtag Crawler (luân phiên mỗi 2 vòng — Exploration Engine 15%)
            VIRAL_HASHTAGS = [
                "voiceover", "originalvoice", "storytelling", "pov",
                "comedyvideo", "reactionvideo", "myvoice",
                "thamtu", "kechuyen", "tamsu",
                "ceritalucu", "suaraasli",
                "historia", "comedia",
                "gamingreaction", "aivoice",
            ]
            if run_count % 2 == 0:
                hashtag = random.choice(VIRAL_HASHTAGS)
                tasks.append(process_keyword(f"#{hashtag}", classifier, emit,
                    on_result=on_result, found_ids=found_ids,
                    use_hashtag=hashtag))

            # BƯỚC 2: Audio Chain (mỗi 3 vòng, dùng 1 audio đã accepted)
            if run_count % 3 == 0:
                accepted = await get_accepted_results(limit=20)
                if accepted:
                    seed = random.choice(accepted)
                    tasks.append(process_audio_chain(
                        seed["audio_id"], seed["audio_name"],
                        classifier, emit, on_result=on_result, found_ids=found_ids
                    ))

            # Chạy tuần tự để tránh lỗi khóa Profile (Chromium chỉ cho phép 1 persistent context tại một thời điểm)
            for task_coro in tasks:
                await task_coro

            _print_metrics(f"Tổng kết Vòng {run_count}", emit=emit)
            total = len(found_ids)
            emit(f"[Vòng {run_count}] Đã có {total}/{target} audio hợp lệ")
            if total >= target:
                emit(f"✅ Đủ {target} audio! Hoàn tất.")
                return
        except Exception as e:
            # IDEA 5: Auto-recovery thay vì crash toàn bộ
            import traceback
            logger.error(f"💥 Lỗi vòng {run_count}: {e}\n{traceback.format_exc()}")
            if emit: emit(f"⚠️ Lỗi vòng {run_count}: {e} — Tự khôi phục sau 30s")
            await asyncio.sleep(30)
            continue
            
        await asyncio.sleep(2)

async def main():
    logger.add("data/logs/system.log", rotation="20 MB", level="INFO")
    try:
        await run_crawler(target=50)
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

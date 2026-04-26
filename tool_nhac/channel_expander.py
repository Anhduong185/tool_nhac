"""
channel_expander.py — V2.1 Creator Mining Engine
=================================================
Nhân bản kênh TikTok từ file Excel cũ bằng cách khai thác "Suggested Accounts".
Input:  Link kênh tiktok check trùng .xlsx
Output: creators_list.txt (danh sách kênh mới cùng ngách)

Cách chạy:
    python channel_expander.py
    python channel_expander.py --max-per-seed 15 --output my_creators.txt
"""

import asyncio
import argparse
import re
import random
from pathlib import Path
from typing import Set, List
from loguru import logger
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth

# ── Cấu hình ──────────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).resolve().parent
EXCEL_FILE  = ROOT_DIR / "Link kênh tiktok check trùng .xlsx"
OUTPUT_FILE = ROOT_DIR / "creators_list.txt"
EXISTING_FILE = ROOT_DIR / "Link kênh tiktok check trùng .xlsx"  # để filter không re-add

MAX_SUGGESTED_PER_SEED = 20   # Lấy tối đa N gợi ý từ mỗi kênh
DELAY_BETWEEN_PROFILES = (3, 7)  # Sleep ngẫu nhiên giữa các profile (giây)
PAGE_TIMEOUT = 30_000           # ms


# ── Đọc danh sách kênh gốc từ file Excel ──────────────────────────────────────
def load_seeds_from_excel(excel_path: Path) -> List[str]:
    """Đọc tất cả username/link TikTok từ file Excel."""
    try:
        import pandas as pd
        df = pd.read_excel(excel_path)
        seeds: Set[str] = set()

        full_text = df.to_string()
        # Bắt @username
        for m in re.findall(r'@([a-zA-Z0-9_.]+)', full_text):
            seeds.add(m.lower())
        # Bắt link tiktok.com/@username
        for m in re.findall(r'tiktok\.com/@([a-zA-Z0-9_.]+)', full_text):
            seeds.add(m.lower())
        # Cột thuần text (không có space, không có /)
        for col in df.columns:
            for cell in df[col].dropna().astype(str):
                c = cell.strip().lstrip('@').lower()
                if c and ' ' not in c and '/' not in c and len(c) > 2:
                    seeds.add(c)

        seeds.discard('user')
        seeds.discard('')
        logger.info(f"📋 Đọc được {len(seeds)} kênh gốc từ Excel.")
        return list(seeds)
    except Exception as e:
        logger.error(f"Lỗi đọc Excel: {e}")
        return []


# ── Đọc danh sách kênh đã có (tránh thêm trùng) ───────────────────────────────
def load_existing_creators(output_path: Path) -> Set[str]:
    existing: Set[str] = set()
    if output_path.exists():
        for line in output_path.read_text(encoding='utf-8').splitlines():
            u = line.strip().lstrip('@').lower()
            if u:
                existing.add(u)
    return existing


# ── Playwright: Lấy Suggested Accounts từ một profile ─────────────────────────
async def get_suggested_accounts(page: Page, username: str, max_count: int) -> List[str]:
    """
    Vào profile TikTok, click nút mũi tên gợi ý tài khoản,
    thu thập danh sách username được đề xuất.
    """
    url = f"https://www.tiktok.com/@{username}"
    suggested: List[str] = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await asyncio.sleep(random.uniform(2, 4))

        # ── Thử lấy Suggested bằng cách đọc section "Others you may like" ──
        # Không cuộn trang để giữ đúng tọa độ chuẩn của các nút trên header
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

        # Cách 1: Click nút dropdown gợi ý (nút mũi tên nhỏ cạnh Follow)
        clicked = False
        
        # Logic click dựa vào vị trí nút Follow/Tin nhắn (Bỏ qua CSS selector vì TikTok hay đổi)
        if not clicked:
            try:
                # Tìm nút Follow hoặc Tin nhắn làm mốc
                follow_cands = await page.get_by_text(re.compile(r'Follow|Tin nhắn|Message', re.I)).all()
                follow_btn = None
                
                for c in follow_cands:
                    if await c.is_visible():
                        box = await c.bounding_box()
                        if box and 20 < box['height'] < 60 and 50 < box['width'] < 200:
                            follow_btn = c
                            break
                
                if follow_btn:
                    f_box = await follow_btn.bounding_box()
                    if f_box:
                        logger.info(f"  [Auto-Click] Mốc tọa độ y={f_box['y']:.0f}")
                        
                        # Tìm trực tiếp TẤT CẢ các thẻ SVG trên trang (bất kể nó nằm trong div hay button)
                        all_svgs = await page.locator('svg').all()
                        candidates = []
                        for svg in all_svgs:
                            try:
                                box = await svg.bounding_box()
                                if not box: continue
                                # Nằm cùng hàng ngang (sai số 30px) và nằm TRONG DẢI CHỨA NÚT FOLLOW
                                # Nằm BÊN PHẢI nút mốc
                                if abs(box['y'] - f_box['y']) < 30 and box['x'] > f_box['x'] + f_box['width'] - 10:
                                    candidates.append((svg, box['x']))
                            except Exception:
                                pass
                        
                        if candidates:
                            # Sắp xếp từ trái sang phải
                            candidates.sort(key=lambda x: x[1])
                            # SVG ĐẦU TIÊN bên phải chính là icon Gợi ý!
                            target_btn, target_x = candidates[0]
                            t_box = await target_btn.bounding_box()
                            
                            if t_box:
                                cx = t_box['x'] + t_box['width']/2
                                cy = t_box['y'] + t_box['height']/2
                            else:
                                # Tọa độ tĩnh (User yêu cầu)
                                cx, cy = 670, 270
                        else:
                            logger.info("  [Auto-Click] Không tìm thấy SVG, dùng tọa độ tĩnh.")
                            cx, cy = 670, 270
                    else:
                        logger.info("  [Auto-Click] Mất mốc Follow, dùng tọa độ tĩnh.")
                        cx, cy = 670, 270
                else:
                    logger.info("  [Auto-Click] Không thấy chữ Follow, dùng tọa độ tĩnh.")
                    cx, cy = 670, 270

                # ----------------------------------------------------
                # VẼ CHẤM ĐỎ VÀ CLICK CHUỘT
                # CẬP NHẬT: Ưu tiên dùng tọa độ tĩnh
                cx = 735
                cy = 140
                # ----------------------------------------------------
                
                # Vẽ một chấm đỏ chót lên màn hình để User nhìn thấy tool click ở đâu
                await page.evaluate(f'''() => {{
                    let dot = document.createElement("div");
                    dot.style.position = "fixed";
                    dot.style.left = "{cx-15}px"; // Canh giữa chấm đỏ (30/2 = 15)
                    dot.style.top = "{cy-15}px";
                    dot.style.width = "30px"; // Phóng to gấp đôi
                    dot.style.height = "30px";
                    dot.style.backgroundColor = "red";
                    dot.style.borderRadius = "50%";
                    dot.style.zIndex = "999999";
                    dot.style.border = "3px solid yellow"; // Viền vàng dày hơn
                    dot.style.boxShadow = "0px 0px 10px 5px rgba(255,0,0,0.5)"; // Thêm hiệu ứng phát sáng
                    dot.style.pointerEvents = "none"; // XUYÊN THỦNG: Cho phép chuột click xuyên qua chấm đỏ
                    document.body.appendChild(dot);
                    // Giữ chấm đỏ trong 30 giây
                    setTimeout(() => dot.remove(), 30000);
                }}''')
                
                logger.info(f"  [Auto-Click] Đã khóa mục tiêu tại (x={cx:.0f}, y={cy:.0f}). Đang mô phỏng click thật...")
                
                # Mô phỏng Y HỆT tay người thật: Rê chuột từ từ vào -> bấm xuống -> nhả lên
                await page.mouse.move(cx - 50, cy + 50) # Rê từ ngoài vào
                await asyncio.sleep(0.2)
                await page.mouse.move(cx, cy, steps=10) # Rê từ từ vào trúng hồng tâm
                await asyncio.sleep(0.5)
                await page.mouse.down()
                await asyncio.sleep(0.1) # Giữ chuột 0.1s
                await page.mouse.up()
                
            except Exception as e:
                logger.info(f"  [Auto-Click] Lỗi click: {e}")

        # Đợi danh sách Gợi ý xổ xuống hoàn toàn (đã test thành công, trả lại 5s cho tool chạy nhanh)
        logger.info("  [Auto-Click] Đang chờ 5s để lấy danh sách...")
        await asyncio.sleep(5)

        # Cách 2: Đọc trực tiếp từ DOM các link @username trong section gợi ý
        # TikTok thường render list này trong một carousel hoặc grid
        try:
            # Lấy tất cả các thẻ <a> có href bắt đầu bằng /@ trên toàn bộ trang
            # Nhờ việc click xổ xuống ở trên, các thẻ này đã hiện ra
            elements = await page.locator('a[href^="/@"]').all()
            for el in elements:
                href = await el.get_attribute('href') or ''
                # Chỉ lấy các link trỏ đến profile, bỏ qua link trỏ đến video
                if '/video/' not in href and '/photo/' not in href:
                    m = re.search(r'/@([a-zA-Z0-9_.]+)', href)
                    if m:
                        u = m.group(1).lower()
                        if u != username and u not in suggested:
                            suggested.append(u)
        except Exception:
            pass
        if not suggested:
            try:
                html = await page.evaluate("() => document.body.innerHTML")
                # TikTok thường có section "Suggest" với các link /@username
                # Lấy toàn bộ link rồi lọc
                all_links = re.findall(r'/@([a-zA-Z0-9_.]{3,30})"', html)
                seen = set()
                for u in all_links:
                    ul = u.lower()
                    if ul != username and ul not in seen and ul != 'user':
                        seen.add(ul)
                        # Chỉ lấy những user xuất hiện lặp lại trong HTML
                        # (để hạn chế bắt video owner ngẫu nhiên)
                        if all_links.count(u) >= 2 and len(suggested) < max_count:
                            suggested.append(ul)
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"  ⚠️  @{username}: {e}")

    return suggested[:max_count]


# ── Main ──────────────────────────────────────────────────────────────────────
async def expand_creators(
    max_per_seed: int = MAX_SUGGESTED_PER_SEED,
    output_path: Path = OUTPUT_FILE,
    seeds: List[str] = None,
):
    """
    Vòng lặp chính: duyệt từng kênh gốc, lấy Suggested Accounts,
    ghi kết quả vào file output.
    """
    if seeds is None:
        seeds = load_seeds_from_excel(EXCEL_FILE)

    if not seeds:
        logger.error("Không có kênh gốc nào để xử lý. Thoát.")
        return

    existing = load_existing_creators(output_path)
    logger.info(f"📂 Đã có {len(existing)} kênh trong '{output_path.name}'. Sẽ bỏ qua trùng.")

    # Cũng loại bỏ kênh gốc (đã hết audio rồi)
    existing.update(seeds)

    new_creators: Set[str] = set()
    total_seeds = len(seeds)

    async with async_playwright() as p:
        user_data_dir = str(ROOT_DIR.parent / "tool_sroll_feed" / "tiktok_session")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            headless=False,
            args=["--mute-audio"],
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await Stealth().apply_stealth_async(page)

        for idx, seed in enumerate(seeds, 1):
            logger.info(f"[{idx}/{total_seeds}] Đang xử lý @{seed} ...")
            
            suggested = await get_suggested_accounts(page, seed, max_per_seed)
            
            added_this = 0
            for u in suggested:
                if u not in existing and u not in new_creators:
                    new_creators.add(u)
                    added_this += 1

            logger.success(f"  ✅ @{seed} → +{added_this} kênh mới (tổng mới: {len(new_creators)})")

            # Ghi trực tiếp sau mỗi seed để không mất data nếu bị gián đoạn
            if new_creators:
                _append_to_file(output_path, new_creators - existing)
                existing.update(new_creators)

            # Anti-bot delay
            delay = random.uniform(*DELAY_BETWEEN_PROFILES)
            logger.debug(f"  💤 Nghỉ {delay:.1f}s ...")
            await asyncio.sleep(delay)

        await browser.close()

    logger.success(
        f"\n🎉 Hoàn thành! Đã thêm {len(new_creators)} kênh mới vào '{output_path.name}'."
    )


def _append_to_file(path: Path, usernames: Set[str]):
    """Ghi thêm username vào file (không xóa nội dung cũ)."""
    with open(path, 'a', encoding='utf-8') as f:
        for u in sorted(usernames):
            f.write(f"{u}\n")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TikTok Creator Expander V2.1")
    parser.add_argument("--max-per-seed", type=int, default=MAX_SUGGESTED_PER_SEED,
                        help=f"Số kênh gợi ý tối đa mỗi seed (default: {MAX_SUGGESTED_PER_SEED})")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE),
                        help=f"File output (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    logger.add("data/logs/channel_expander.log", rotation="5 MB", retention="7 days")
    asyncio.run(expand_creators(
        max_per_seed=args.max_per_seed,
        output_path=Path(args.output),
    ))

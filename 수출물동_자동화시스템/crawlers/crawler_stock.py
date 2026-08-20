# -*- coding: utf-8 -*-
"""
crawler_stock.py
재고 데이터 자동 추출 - Display Stock by Bin (ZWMRE30150)
변경 기준값: BA (기본값 DFZ)

실행:
    python crawler_stock.py               # BA=DFZ
    python crawler_stock.py --ba DFZ
    python crawler_stock.py --ba ABC --headless
"""

import argparse
import shutil
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from crawler_base import (
    log, today_folder, open_browser, ensure_logged_in,
    get_sap_frame, find_latest_xlsx, click_export_spreadsheet,
    PORTAL_URL, TIMEOUT_MS,
)

SCREEN_NAME  = "Display Stock by bin"
OUTPUT_NAME  = "display stock by bin (1).xlsx"  # import_raw.py 기대 파일명


# ─────────────────────────────────────────────────────────
def navigate_and_query(page, ba: str):
    """포털 검색 → ZWMRE30150 진입 → BA 입력 → 조회"""

    # 1) 상단 검색 버튼
    log(f"포털 검색: '{SCREEN_NAME}'")
    try:
        btn = page.locator(
            "button[title*='Search'], button[title*='검색'], "
            "#headerSearch, .sapUiHLayoutSearch"
        ).first
        btn.click(timeout=8_000)
    except PWTimeout:
        try:
            page.keyboard.press("F2")
        except Exception:
            pass

    # 2) 검색어 입력
    try:
        inp = page.locator(
            "input[type='text'][id*='search'], "
            "input[placeholder*='Search'], input[placeholder*='검색']"
        ).first
        inp.wait_for(state="visible", timeout=8_000)
        inp.fill(SCREEN_NAME)
        inp.press("Enter")
        log("  검색어 입력 완료")
        time.sleep(2)
    except PWTimeout:
        log("  [WARN] 검색 input 없음")

    # 3) 검색 결과 클릭
    try:
        page.get_by_text("Display Stock by Bin", exact=False).first.click(timeout=8_000)
        log("  검색 결과 클릭")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    except PWTimeout:
        log("  [WARN] 검색 결과 클릭 실패")

    # 4) SAP iframe 진입
    frame = get_sap_frame(page)

    # 5) BA 입력
    log(f"  BA 필드 입력: {ba}")
    try:
        ba_inp = frame.locator(
            "input[aria-label='BA'], input[title='BA'], "
            "input[id*='BA'], input[name*='BA']"
        ).first
        ba_inp.wait_for(state="visible", timeout=15_000)
        ba_inp.triple_click()
        ba_inp.fill(ba)
    except PWTimeout:
        log("  [WARN] BA 필드 미감지 → 좌표 클릭")
        frame.page.mouse.click(375, 200)
        frame.page.keyboard.type(ba)

    # 6) Go 버튼 클릭
    log("  Go(조회) 클릭")
    try:
        go = frame.locator(
            "button:has-text('Go'), button[title='Go'], "
            "button[aria-label='Go'], button[id*='execute']"
        ).first
        go.click(timeout=8_000)
    except PWTimeout:
        frame.page.keyboard.press("Enter")

    log("  데이터 로딩 대기...")
    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    time.sleep(3)


# ─────────────────────────────────────────────────────────
def run(ba: str = "DFZ", headless: bool = False) -> str:
    out_dir  = today_folder()
    out_path = out_dir / OUTPUT_NAME
    log(f"=== 재고 추출 시작 (BA={ba}) → {out_path} ===")

    with sync_playwright() as p:
        ctx, page = open_browser(p, headless=headless)
        page.set_default_timeout(TIMEOUT_MS)

        page.goto(PORTAL_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        ensure_logged_in(page)

        navigate_and_query(page, ba)

        frame    = get_sap_frame(page)
        download = click_export_spreadsheet(page, frame)

        if download:
            log(f"  다운로드: {download.suggested_filename}")
            download.save_as(str(out_path))
        else:
            log("  Playwright 다운로드 미감지 → 폴백 탐색")
            found = find_latest_xlsx(out_dir)
            if found and found != out_path:
                shutil.move(str(found), str(out_path))

        ctx.close()

    log(f"=== 완료: {out_path} ===")
    return str(out_path)


# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba",       default="DFZ")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    result = run(ba=args.ba, headless=args.headless)
    print(f"\n최종 파일: {result}")

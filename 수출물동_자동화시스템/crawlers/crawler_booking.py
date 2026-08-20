# -*- coding: utf-8 -*-
"""
crawler_booking.py
선복(Booking Progress Details) 데이터 자동 추출

실행:
    python crawler_booking.py
    python crawler_booking.py --headless

TODO: 사용자로부터 포털 화면명/경로, 조회 조건 확인 후 업데이트 필요
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

SCREEN_NAME = "Booking Progress"        # ← 포털 검색어 (확인 필요)
OUTPUT_NAME = "1779169640580_BookingProgressDetails.xlsx"  # import_raw.py 기대 파일명


def navigate_and_query(page):
    log(f"포털 검색: '{SCREEN_NAME}'")
    try:
        btn = page.locator(
            "button[title*='Search'], button[title*='검색'], #headerSearch"
        ).first
        btn.click(timeout=8_000)
    except PWTimeout:
        page.keyboard.press("F2")

    try:
        inp = page.locator(
            "input[type='text'][id*='search'], input[placeholder*='Search']"
        ).first
        inp.wait_for(state="visible", timeout=8_000)
        inp.fill(SCREEN_NAME)
        inp.press("Enter")
        time.sleep(2)
    except PWTimeout:
        log("  [WARN] 검색 input 없음")

    try:
        page.get_by_text(SCREEN_NAME, exact=False).first.click(timeout=8_000)
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    except PWTimeout:
        log("  [WARN] 화면 링크 클릭 실패")

    frame = get_sap_frame(page)

    # ── 조회 조건 입력 ──────────────────────────────────────
    # TODO: 기간, Shipper 등 필터 확인 후 코드 추가
    # ────────────────────────────────────────────────────────

    log("  Go(조회) 클릭")
    try:
        go = frame.locator(
            "button:has-text('Go'), button[title='Go'], button[id*='execute']"
        ).first
        go.click(timeout=8_000)
    except PWTimeout:
        frame.page.keyboard.press("Enter")

    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    time.sleep(3)


def run(headless: bool = False) -> str:
    out_dir  = today_folder()
    out_path = out_dir / OUTPUT_NAME
    log(f"=== 선복 추출 시작 → {out_path} ===")

    with sync_playwright() as p:
        ctx, page = open_browser(p, headless=headless)
        page.set_default_timeout(TIMEOUT_MS)

        page.goto(PORTAL_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        ensure_logged_in(page)

        navigate_and_query(page)

        frame    = get_sap_frame(page)
        download = click_export_spreadsheet(page, frame)

        if download:
            download.save_as(str(out_path))
        else:
            found = find_latest_xlsx(out_dir)
            if found and found != out_path:
                shutil.move(str(found), str(out_path))

        ctx.close()

    log(f"=== 완료: {out_path} ===")
    return str(out_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    result = run(headless=args.headless)
    print(f"\n최종 파일: {result}")

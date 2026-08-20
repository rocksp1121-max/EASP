# -*- coding: utf-8 -*-
"""
crawler_base.py
모든 포털 크롤러가 공유하는 공통 유틸
"""

import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import Page, Frame, Download
    from playwright.sync_api import TimeoutError as PWTimeout
except ImportError:
    raise ImportError("playwright 미설치: pip install playwright && python -m playwright install chromium")

PORTAL_URL    = "https://logistics-lge.singlex.com/irj/portal"
BASE_DIR      = Path(__file__).parent.parent   # crawlers/ 상위 = 설치 루트
RAW_ROOT      = BASE_DIR / "Raw"
USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
TIMEOUT_MS    = 60_000


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def today_folder() -> Path:
    folder = RAW_ROOT / datetime.today().strftime("%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def open_browser(playwright, headless: bool = False):
    """Edge persistent context(SSO 세션 재사용) 또는 Chromium 신규 실행"""
    if USER_DATA_DIR.exists():
        log(f"Edge 프로파일 사용: {USER_DATA_DIR}")
        ctx = playwright.chromium.launch_persistent_context(
            user_data_dir    = str(USER_DATA_DIR),
            channel          = "msedge",
            headless         = headless,
            accept_downloads = True,
            args             = ["--start-maximized"],
            no_viewport      = True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return ctx, page
    else:
        log("Edge 프로파일 없음 → Chromium 실행 (로그인 필요)")
        browser = playwright.chromium.launch(headless=headless, accept_downloads=True)
        page    = browser.new_page()
        return browser, page


def ensure_logged_in(page):
    """로그인 페이지 감지 시 수동 로그인 대기"""
    if "logon" in page.url.lower() or "login" in page.url.lower():
        log("[대기] 로그인 필요 – 브라우저에서 로그인 후 Enter 입력")
        input("  >> 로그인 완료 후 Enter를 누르세요...")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)


def get_sap_frame(page):
    """SAP 콘텐츠 iframe 반환 – 없으면 page 반환"""
    try:
        for f in page.frames:
            if any(k in f.url for k in ("ZWMR", "ZPPM", "ZSMM", "irj/go")):
                return f
        if len(page.frames) > 1:
            return page.frames[1]
    except Exception:
        pass
    return page


def find_latest_xlsx(out_dir: Path, max_age_sec: int = 120) -> Path | None:
    """Z:\\ 또는 Downloads에서 최근 xlsx 파일 탐색 (폴백)"""
    candidates = [Path(r"Z:\\"), Path.home() / "Downloads"]
    pat = re.compile(r"\.(xlsx|xls)$", re.IGNORECASE)
    now = time.time()
    for folder in candidates:
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if pat.search(f.name) and (now - f.stat().st_mtime) < max_age_sec:
                log(f"  폴백 파일 발견: {f}")
                dst = out_dir / f.name
                shutil.copy2(str(f), str(dst))
                return dst
    return None


def click_export_spreadsheet(page, frame) -> Download | None:
    """Export 드롭다운 → Spreadsheet → Export/OK 클릭 → Download 객체 반환"""
    # 1) Export/Spreadsheet 버튼 탐색
    selectors = [
        "button[title*='Export']", "button[aria-label*='Export']",
        "button[title*='Spreadsheet']", "button[aria-label*='Spreadsheet']",
        "button[id*='export']",
    ]
    clicked = False
    for sel in selectors:
        try:
            btn = frame.locator(sel).first
            btn.wait_for(state="visible", timeout=4_000)
            btn.click()
            log(f"  내보내기 버튼 클릭: {sel}")
            clicked = True
            break
        except PWTimeout:
            continue

    if not clicked:
        log("  [WARN] 내보내기 버튼 미감지 – 우측 드롭다운 화살표 좌표 클릭")
        frame.page.mouse.click(396, 204)
        time.sleep(1)

    # 2) Spreadsheet 메뉴 항목
    time.sleep(1)
    try:
        item = page.get_by_text("Spreadsheet", exact=True).first
        item.wait_for(state="visible", timeout=6_000)
        item.click()
        log("  'Spreadsheet' 메뉴 선택")
    except PWTimeout:
        frame.page.mouse.click(396, 240)
    time.sleep(1)

    # 3) Export As 다이얼로그 → OK/Export
    try:
        ok_btn = page.locator("button:has-text('Export'), button:has-text('OK')").first
        ok_btn.wait_for(state="visible", timeout=6_000)
        with page.expect_download(timeout=30_000) as dl_info:
            ok_btn.click()
        return dl_info.value
    except PWTimeout:
        log("  [WARN] Export 다이얼로그 미감지")
        return None

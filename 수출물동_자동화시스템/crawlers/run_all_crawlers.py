# -*- coding: utf-8 -*-
"""
run_all_crawlers.py
4개 데이터소스 순차 크롤링 → Raw/{MMDD}/ 에 저장
이후 import_raw.py 자동 실행

실행:
    python run_all_crawlers.py
    python run_all_crawlers.py --ba DFZ --headless --skip-import
"""

import argparse
import subprocess
import sys
from pathlib import Path

# crawlers 폴더를 path에 추가 (import_raw.py가 상위 폴더에 있으므로)
CRAWLERS_DIR = Path(__file__).parent
BASE_DIR     = CRAWLERS_DIR.parent

def log(msg):
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run_crawler(script: str, extra_args: list = None) -> bool:
    cmd = [sys.executable, str(CRAWLERS_DIR / script)] + (extra_args or [])
    log(f"실행: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(CRAWLERS_DIR))
    ok = result.returncode == 0
    log(f"  → {'성공' if ok else '실패'} (exit {result.returncode})")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba",          default="DFZ",   help="재고 BA 코드")
    ap.add_argument("--headless",    action="store_true")
    ap.add_argument("--skip-import", action="store_true", help="import_raw.py 실행 건너뜀")
    ap.add_argument("--only",        choices=["stock","so","prod","booking"],
                    help="특정 크롤러만 실행")
    args = ap.parse_args()

    headless_flag = ["--headless"] if args.headless else []

    results = {}

    # ── 1. 재고 (Display Stock by Bin) ──────────────────────
    if not args.only or args.only == "stock":
        log("=" * 50)
        log("1/4  재고 (Display Stock by Bin)")
        results["stock"] = run_crawler(
            "crawler_stock.py",
            ["--ba", args.ba] + headless_flag
        )

    # ── 2. SO (Sales Order Progress) ────────────────────────
    if not args.only or args.only == "so":
        log("=" * 50)
        log("2/4  SO (Sales Order Progress)")
        results["so"] = run_crawler("crawler_so.py", headless_flag)

    # ── 3. 생산계획 (PS Order) ───────────────────────────────
    if not args.only or args.only == "prod":
        log("=" * 50)
        log("3/4  생산계획 (PS Order)")
        results["prod"] = run_crawler("crawler_production.py", headless_flag)

    # ── 4. 선복 (Booking Progress) ──────────────────────────
    if not args.only or args.only == "booking":
        log("=" * 50)
        log("4/4  선복 (Booking Progress Details)")
        results["booking"] = run_crawler("crawler_booking.py", headless_flag)

    # ── 결과 요약 ────────────────────────────────────────────
    log("=" * 50)
    log("크롤링 결과 요약:")
    for name, ok in results.items():
        status = "✓ 성공" if ok else "✗ 실패"
        log(f"  {name:10s}: {status}")

    all_ok = all(results.values())

    # ── 5. import_raw.py 자동 실행 ──────────────────────────
    if not args.skip_import:
        log("=" * 50)
        if all_ok:
            log("모든 크롤러 성공 → import_raw.py 실행")
        else:
            log("[WARN] 일부 크롤러 실패 → 그래도 import_raw.py 실행 시도")

        import_script = BASE_DIR / "import_raw.py"
        if import_script.exists():
            result = subprocess.run([sys.executable, str(import_script)])
            log(f"import_raw.py 완료 (exit {result.returncode})")
        else:
            log(f"[ERR] {import_script} 없음")
    else:
        log("import_raw.py 건너뜀 (--skip-import)")

    log("=" * 50)
    log("전체 완료")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

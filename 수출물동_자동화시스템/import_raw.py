# -*- coding: utf-8 -*-
"""
import_raw.py
Raw/{MMDD}/ 폴더의 raw 파일 4종을 xlsm 해당 시트에 덮어쓰기

사용법:
    python import_raw.py              # 오늘 날짜 폴더 자동 탐색
    python import_raw.py --date 0519  # 날짜 직접 지정
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import xlwings as xw

# 이 스크립트 기준 상대 경로
BASE_DIR = Path(__file__).parent
RAW_ROOT = BASE_DIR / "Raw"

FILE_SHEET = {
    'Display Sales Order Progress.xlsx':         'SO',
    '생산계획(PS Order).xlsx':                   '생산계획',
    'display stock by bin (1).xlsx':             '재고',
    '1779169640580_BookingProgressDetails.xlsx': '선복',
}

def find_raw_dir(date_str: str) -> Path:
    """Raw/{date} 폴더 반환. 없으면 가장 최근 날짜 폴더 사용."""
    target = RAW_ROOT / date_str
    if target.exists():
        return target

    # 최근 폴더 자동 탐색
    subdirs = sorted(
        [d for d in RAW_ROOT.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True
    )
    if subdirs:
        print(f"[INFO] Raw/{date_str} 없음 → 최근 폴더 사용: {subdirs[0].name}")
        return subdirs[0]

    print(f"[ERR] Raw 폴더가 비어 있습니다: {RAW_ROOT}")
    sys.exit(1)

def find_wb(app):
    for wb in app.books:
        if '수출물동' in wb.name or '장입계획' in wb.name:
            return wb
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.today().strftime('%m%d'),
                    help='Raw 날짜 폴더 (예: 0519). 기본값: 오늘')
    args = ap.parse_args()

    raw_dir = find_raw_dir(args.date)
    print(f"Raw 폴더: {raw_dir}")

    try:
        app = xw.apps.active
    except Exception as e:
        print(f'[ERR] Excel 연결 실패: {e}')
        sys.exit(1)

    wb = find_wb(app)
    if wb is None:
        print('[ERR] 수출물동_장입계획_자동화.xlsm 을 열고 다시 실행하세요.')
        sys.exit(1)
    print(f'워크북 연결: {wb.name}')

    for filename, sheet_name in FILE_SHEET.items():
        path = raw_dir / filename
        if not path.exists():
            print(f'[없음] {filename}')
            continue

        print(f'\n[읽기] {filename}')
        try:
            df = pd.read_excel(path, sheet_name=0, header=0, dtype=str).fillna('')
            print(f'  → {len(df):,}행 × {len(df.columns)}열')
        except Exception as e:
            print(f'  [ERR] 읽기 오류: {e}')
            continue

        try:
            ws = wb.sheets[sheet_name]
        except Exception as e:
            print(f'  [ERR] 시트 [{sheet_name}] 없음: {e}')
            continue

        print(f'  → [{sheet_name}] 시트 초기화 중...')
        ws.clear_contents()
        ws.range('A1').value = [df.columns.tolist()] + df.values.tolist()
        print(f'  → [{sheet_name}] 기록 완료 ({len(df):,}행)')

    wb.save()
    print('\n===== import_raw 완료 / 파일 저장됨 =====')

if __name__ == '__main__':
    main()

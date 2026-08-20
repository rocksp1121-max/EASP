# -*- coding: utf-8 -*-
"""
ref_data.py — 통합 기준정보 로더 (data/ref_*.csv)
- F.dest → 지역(권역)
- 선사 약어 → (선사명, 선사코드)
step_processor / step5_optimizer 등에서 import 하여 표준 기준 적용.
"""
import os
import csv
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

_fdest = {}      # F.dest → 지역
_carrier = {}    # 선사 → (선사명, 선사코드)
_country = {}    # 2자리 국가코드 → 지역 (region_of 폴백용)


def _load_csv(name):
    p = DATA_DIR / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _init():
    for r in _load_csv("ref_fdest.csv"):
        k = (r.get("F.dest") or "").strip()
        if k:
            _fdest[k] = (r.get("지역") or "").strip()
    for r in _load_csv("ref_carrier.csv"):
        k = (r.get("선사") or "").strip()
        if k:
            _carrier[k] = ((r.get("선사명") or "").strip(), (r.get("선사코드") or "").strip())
    for r in _load_csv("ref_country.csv"):
        k = (r.get("국가코드") or "").strip().upper()
        if k:
            _country[k] = (r.get("지역") or "").strip()


_init()


def reload():
    """기준정보 CSV 파일들을 다시 읽어 메모리 dict 갱신."""
    _fdest.clear(); _carrier.clear(); _country.clear()
    _init()


def region_of(fdest):
    """F.dest → 지역. ref_fdest 우선, 미등록이면 2자리 국가코드 폴백."""
    k = (fdest or "").strip()
    if not k:
        return ""
    v = _fdest.get(k)
    if v:
        return v
    return _country.get(k[:2].upper(), "")     # 폴백: USONT→US→북미직거래선


def carrier_code(line):
    v = _carrier.get((line or "").strip())
    return v[1] if v else ""


def has_fdest(fdest):
    return (fdest or "").strip() in _fdest


def has_carrier(line):
    return (line or "").strip() in _carrier


def stats():
    return {"fdest": len(_fdest), "carrier": len(_carrier)}

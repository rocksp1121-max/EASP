# -*- coding: utf-8 -*-
"""
step_processor.py
STEP 1~4 (DATA 준비): Raw\\{MMDD}\\ 4종을 읽어 MST(42컬럼)를 생성/보강한다.
- STEP1 SO     : 신규주문 추가 + 잔량 갱신 (key: SO No+Item No+Request Batch)
- STEP2 생산계획 : 제번/생산라인/생산일자/Planner remark 매핑 (key: SO No+Item No)
- STEP3 재고    : Model별 가용재고 합산 → 재고 (key: Model)
- STEP4 선복    : 부킹/ETD/마감일/모선명/반입지/선사/포워더 (key: F.Dest)

Flask 없이 단독 실행 가능:
    python step_processor.py --date 0519
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

import ref_data   # 통합 기준정보 (F.dest→지역, 선사→선사코드)


def _norm_week(w):
    """주차 정규화: '26W21' / 'W21' / '21' → 'W21' (W%02d)"""
    s = str(w or "").strip().upper()
    m = re.search(r"W(\d{1,2})", s) or re.search(r"(\d{1,2})$", s)
    return f"W{int(m.group(1)):02d}" if m else ""

BASE_DIR = Path(__file__).parent
RAW_ROOT = BASE_DIR / "Raw"

# mst_data.js 와 동일한 42컬럼 순서
MST_HEADER = [
    "비고", "사업부", "S", "특이사항", "작업번호(Inst No)", "CNTR NO", "권역", "작업일자",
    "생산일자", "Line", "P/S Order", "SO No", "Request Batch", "Customer PO", "PO Receiving ORG",
    "Contract No", "Item No", "RSD", "주차", "Model", "SO Qty", "Remaining SO Qty", "Price Term",
    "Ship to Party Name", "F.Dest", "장입수량", "CBM", "컨대수", "재고", "포워더", "선사코드", "선사",
    "반입지", "마감일", "ETD", "부킹번호", "모선명", "특이사항", "Planner remark", "리마크", "SLM", "SLMW",
]
# 컬럼명 → 인덱스 (중복 '특이사항'은 첫 위치)
IDX = {}
for i, h in enumerate(MST_HEADER):
    if h not in IDX:
        IDX[h] = i
NCOL = len(MST_HEADER)


# ─────────────────────────── 유틸 ───────────────────────────
def _s(v):
    """문자열 정리 (nan/None → '')"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none"):
        return ""
    return s


def _num(v):
    """숫자 변환 (실패 시 '')"""
    s = _s(v)
    if s == "":
        return ""
    try:
        f = float(s.replace(",", ""))
        return int(f) if f.is_integer() else round(f, 3)
    except ValueError:
        return ""


def _date(v):
    """다양한 입력 → 'YYYY-MM-DD' (실패 시 원본 문자열)"""
    s = _s(v)
    if s == "":
        return ""
    # '2026-02-01 00:00:00' 형태 우선
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:  # pandas 파서 fallback
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return s


def _week(date_str):
    """YYYY-MM-DD → 'Wxx' (ISO 주차)"""
    try:
        return "W%02d" % datetime.strptime(date_str, "%Y-%m-%d").isocalendar()[1]
    except Exception:
        return ""


def status_of(row):
    """행 상태 계산 (mst_data.js statusOf 와 동일 규칙)"""
    inst = row[IDX["작업번호(Inst No)"]]
    load = row[IDX["장입수량"]]
    book = row[IDX["부킹번호"]]
    stock = row[IDX["재고"]]
    if _s(inst):
        return "canary"          # 작업완료
    if isinstance(load, (int, float)) and load > 0:
        return "pink"            # 계획확정
    if _s(book):
        return "yellow"          # 계획대상(부킹 배정)
    if isinstance(stock, (int, float)) and stock > 0:
        return "yellow"          # 계획대상(재고 보유)
    return "white"               # 초기


def find_raw_dir(date_str):
    target = RAW_ROOT / date_str
    if target.exists():
        return target
    subs = sorted([d for d in RAW_ROOT.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True) \
        if RAW_ROOT.exists() else []
    return subs[0] if subs else None


def _read(raw_dir, filename, header=0):
    path = Path(raw_dir) / filename
    if not path.exists():
        raise FileNotFoundError(filename)
    return pd.read_excel(path, header=header, dtype=str).fillna("")


# ─────────────────────────── MST 저장소 ───────────────────────────
# STEP1 재실행 시 raw로 덮어쓰지 않는 운영자 입력 컬럼 (= 인라인 편집 가능 컬럼)
PRESERVED_COLS = {IDX["비고"], IDX["S"], IDX["특이사항"], IDX["작업번호(Inst No)"], IDX["CNTR NO"], IDX["작업일자"]}


class MSTStore:
    def __init__(self):
        self.rows = []          # 각 행: 길이 42 리스트
        self.key2idx = {}       # (SO,Item,Batch) → rows 인덱스
        self.booking_pol = {}   # 부킹번호 → Booking POL (LOAD_PORT 출력용)

    # ── 영속화 (JSON) ──
    def save(self, path):
        """rows + booking_pol을 JSON으로 저장. key2idx는 로드 시 재구성."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "rows": self.rows,
            "booking_pol": self.booking_pol,
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return len(self.rows)

    def load(self, path):
        """JSON에서 복원. key2idx는 rows 순회로 재구성."""
        p = Path(path)
        if not p.exists():
            return False
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        self.rows = payload.get("rows", [])
        self.booking_pol = payload.get("booking_pol", {})
        self.key2idx = {}
        for i, row in enumerate(self.rows):
            so = _s(row[IDX["SO No"]])
            item = _s(row[IDX["Item No"]])
            batch = _s(row[IDX["Request Batch"]])
            if so:
                self.key2idx[self._key(so, item, batch)] = i
        return True

    @staticmethod
    def _key(so, item, batch):
        return (_s(so), _s(item), _s(batch))

    def _blank(self):
        return [""] * NCOL

    # ---- STEP1: SO Update (운영자 입력 컬럼 보존: PRESERVED_COLS) ----
    def step1_so(self, raw_dir):
        df = _read(raw_dir, "Display Sales Order Progress.xlsx")
        new, updated = 0, 0
        seen_keys = set()
        for _, r in df.iterrows():
            so = _s(r.get("SO No"))
            if not so:
                continue
            item, batch = _s(r.get("Item No")), _s(r.get("Request Batch"))
            key = self._key(so, item, batch)
            seen_keys.add(key)
            rsd = _date(r.get("Ship. Request Date"))
            model = _s(r.get("Model"))
            fdest = _s(r.get("F.Dest"))
            if key in self.key2idx:
                # 기존 행: raw 컬럼만 갱신, PRESERVED_COLS(비고/S/작업번호/CNTR NO/작업일자) 보존
                row = self.rows[self.key2idx[key]]
                row[IDX["사업부"]] = _s(r.get("B.A"))
                row[IDX["Customer PO"]] = _s(r.get("Customer PO"))
                row[IDX["PO Receiving ORG"]] = _s(r.get("PO Receiving ORG"))
                row[IDX["Contract No"]] = _s(r.get("Contract No"))
                row[IDX["RSD"]] = rsd
                row[IDX["주차"]] = _week(rsd)
                row[IDX["Model"]] = model
                row[IDX["SO Qty"]] = _num(r.get("SO Qty"))
                row[IDX["Remaining SO Qty"]] = _num(r.get("Rem. CC Qty"))
                row[IDX["Price Term"]] = _s(r.get("Price Term"))
                row[IDX["Ship to Party Name"]] = _s(r.get("Ship to Party Name"))
                row[IDX["F.Dest"]] = fdest
                row[IDX["권역"]] = ref_data.region_of(fdest)
                row[IDX["CBM"]] = _num(r.get("CBM"))
                updated += 1
            else:                                          # 신규주문 추가
                row = self._blank()
                row[IDX["사업부"]] = _s(r.get("B.A"))
                row[IDX["SO No"]] = so
                row[IDX["Item No"]] = _num(item) if item else ""
                row[IDX["Request Batch"]] = _num(batch) if batch else ""
                row[IDX["Customer PO"]] = _s(r.get("Customer PO"))
                row[IDX["PO Receiving ORG"]] = _s(r.get("PO Receiving ORG"))
                row[IDX["Contract No"]] = _s(r.get("Contract No"))
                row[IDX["RSD"]] = rsd
                row[IDX["주차"]] = _week(rsd)
                row[IDX["Model"]] = model
                row[IDX["SO Qty"]] = _num(r.get("SO Qty"))
                row[IDX["Remaining SO Qty"]] = _num(r.get("Rem. CC Qty"))
                row[IDX["Price Term"]] = _s(r.get("Price Term"))
                row[IDX["Ship to Party Name"]] = _s(r.get("Ship to Party Name"))
                row[IDX["F.Dest"]] = fdest
                row[IDX["권역"]] = ref_data.region_of(fdest)   # 통합기준 F.dest→지역
                row[IDX["CBM"]] = _num(r.get("CBM"))
                row[IDX["SLM"]] = so + _s(item) + model
                self.key2idx[key] = len(self.rows)
                self.rows.append(row)
                new += 1
        # 삭제/이상 주문: 완료여부≠Y 인데 이번 Raw에 없는 건 (운영자 비고 보호)
        deleted = 0
        for key, i in self.key2idx.items():
            if key not in seen_keys and _s(self.rows[i][IDX["S"]]).upper() != "Y":
                if not _s(self.rows[i][IDX["비고"]]):       # 운영자 비고는 덮어쓰지 않음
                    self.rows[i][IDX["비고"]] = "삭제확인필요"
                deleted += 1
        # 기준정보 검증: 통합기준에 없는 F.Dest (오타/신규 의심)
        unknown_fdest = sorted({_s(rw[IDX["F.Dest"]]) for rw in self.rows
                                if _s(rw[IDX["F.Dest"]]) and not ref_data.has_fdest(_s(rw[IDX["F.Dest"]]))})
        return {"step": "STEP1 SO", "new": new, "updated": updated,
                "deleted_check": deleted, "total": len(self.rows),
                "unknown_fdest": len(unknown_fdest)}

    # ---- STEP2: 생산계획 ----
    def step2_production(self, raw_dir):
        df = _read(raw_dir, "생산계획(PS Order).xlsx")
        # SO No+Item No → 생산정보 (첫 매칭 사용)
        prod = {}
        for _, r in df.iterrows():
            so = _s(r.get("SO No.")) or _s(r.get("SO No"))
            if not so:
                continue
            item = _s(r.get("Item No.")) or _s(r.get("Item No"))
            k = (so, item)
            if k not in prod:
                prod[k] = r
        matched = 0
        for row in self.rows:
            k = (_s(row[IDX["SO No"]]), _s(row[IDX["Item No"]]))
            if k in prod:
                r = prod[k]
                row[IDX["P/S Order"]] = _s(r.get("P/S Order"))
                row[IDX["Line"]] = _s(r.get("Production Line"))
                row[IDX["생산일자"]] = _date(r.get("Prod. End Time"))
                row[IDX["Planner remark"]] = _s(r.get("Planner Remarks"))
                row[IDX["SLMW"]] = _s(row[IDX["SLM"]]) + _s(r.get("P/S Order"))
                matched += 1
        return {"step": "STEP2 생산계획", "matched": matched,
                "unmatched": len(self.rows) - matched, "total": len(self.rows)}

    # ---- STEP3: 재고 ----
    def step3_stock(self, raw_dir):
        df = _read(raw_dir, "display stock by bin (1).xlsx")
        stock = {}
        for _, r in df.iterrows():
            model = _s(r.get("Model"))
            if not model:
                continue
            q = _num(r.get("Avl.Qty"))
            if isinstance(q, (int, float)):
                stock[model] = stock.get(model, 0) + q
        matched = 0
        for row in self.rows:
            m = _s(row[IDX["Model"]])
            if m in stock:
                row[IDX["재고"]] = stock[m]
                matched += 1
        return {"step": "STEP3 재고", "models": len(stock),
                "matched": matched, "total": len(self.rows)}

    # ---- STEP4: 선복(Booking) — F.Dest + 주차 일치 우선 매칭 ----
    def step4_booking(self, raw_dir):
        df = _read(raw_dir, "1779169640580_BookingProgressDetails.xlsx", header=0)
        df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
        # 선복 다단 헤더 — Carrier/Forwarder 위치를 라벨행에서 탐색 (Confirm 블록 첫 occurrence)
        carrier_col = forwarder_col = None
        for ri in range(min(3, len(df))):
            for ci, v in enumerate(df.iloc[ri]):
                s = _s(v)
                if s == "Carrier" and carrier_col is None:
                    carrier_col = ci
                elif s == "Forwarder" and forwarder_col is None:
                    forwarder_col = ci
            if carrier_col is not None and forwarder_col is not None:
                break

        # F.Dest → [(week, row), ...] 로 인덱싱 (모든 부킹 보존)
        bookings_by_dest = {}
        for _, r in df.iterrows():
            bno = _s(r.get("Booking No."))
            dest = _s(r.get("F.Dest"))
            if not bno or not dest:           # 서브헤더/빈행 skip
                continue
            wk = _norm_week(r.get("Ship Req. Week"))
            bookings_by_dest.setdefault(dest, []).append((wk, r))
            # Booking POL 저장 (LOAD_PORT 출력용)
            pol = _s(r.get("Booking POL"))
            if pol and bno not in self.booking_pol:
                self.booking_pol[bno] = pol

        matched = 0
        matched_by_week = 0
        matched_by_near = 0
        unknown_carrier = set()
        for row in self.rows:
            d = _s(row[IDX["F.Dest"]])
            if d not in bookings_by_dest:
                continue
            candidates = bookings_by_dest[d]
            target = _s(row[IDX["주차"]])     # STEP1 RSD→주차 ('Wxx')

            # 1) 정확 주차 일치
            chosen = None
            for wk, br in candidates:
                if wk and wk == target:
                    chosen = br; matched_by_week += 1; break
            # 2) 인접 주차 (±N) — target_week 존재 시
            if chosen is None and target:
                try:
                    t = int(target.lstrip("W"))
                    best, best_d = None, 999
                    for wk, br in candidates:
                        if not wk: continue
                        try:
                            d_ = abs(int(wk.lstrip("W")) - t)
                            if d_ < best_d:
                                best, best_d = br, d_
                        except ValueError:
                            pass
                    if best is not None:
                        chosen = best; matched_by_near += 1
                except ValueError:
                    pass
            # 3) 최종 폴백: 첫 부킹
            if chosen is None:
                chosen = candidates[0][1]

            r = chosen
            carrier   = _s(r.iloc[carrier_col])   if carrier_col   is not None else ""
            forwarder = _s(r.iloc[forwarder_col]) if forwarder_col is not None else ""
            row[IDX["부킹번호"]] = _s(r.get("Booking No."))
            row[IDX["ETD"]]    = _date(r.get("U.ETD") or r.get("I.ETD"))
            row[IDX["마감일"]] = _date(r.get("Doc. Closing"))
            row[IDX["모선명"]] = _s(r.get("Vessel Name"))
            row[IDX["반입지"]] = _s(r.get("CNTR Carry-In"))
            row[IDX["선사"]]   = carrier
            row[IDX["선사코드"]] = ref_data.carrier_code(carrier)
            row[IDX["포워더"]]  = forwarder
            if carrier and not ref_data.has_carrier(carrier):
                unknown_carrier.add(carrier)
            matched += 1
        total_bookings = sum(len(v) for v in bookings_by_dest.values())
        return {"step": "STEP4 선복", "bookings": total_bookings,
                "matched": matched, "matched_by_week": matched_by_week,
                "matched_by_near": matched_by_near, "total": len(self.rows),
                "unknown_carrier": len(unknown_carrier)}

    def to_payload(self):
        """mst_data.js 와 동일 포맷: 각 행 끝에 status 추가"""
        return {
            "mst_header": MST_HEADER,
            "mst_rows": [row + [status_of(row)] for row in self.rows],
        }


# ─────────────────────────── 단독 실행 ───────────────────────────
def run_all(date_str):
    raw_dir = find_raw_dir(date_str)
    if raw_dir is None:
        print("[ERR] Raw 폴더 없음:", RAW_ROOT)
        return None
    print("Raw 폴더:", raw_dir)
    mst = MSTStore()
    for fn in (mst.step1_so, mst.step2_production, mst.step3_stock, mst.step4_booking):
        try:
            print("  ", fn(raw_dir))
        except FileNotFoundError as e:
            print(f"   [없음] {e}")
        except Exception as e:
            print(f"   [ERR] {fn.__name__}: {e!r}")
    return mst


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.today().strftime("%m%d"))
    args = ap.parse_args()
    mst = run_all(args.date)
    if mst:
        from collections import Counter
        payload = mst.to_payload()
        dist = Counter(r[-1] for r in payload["mst_rows"])
        print("\n총 MST 행:", len(payload["mst_rows"]))
        print("상태 분포:", dict(dist))
        # 샘플 3행 (주요 컬럼)
        show = ["SO No", "Item No", "Model", "F.Dest", "RSD", "재고", "부킹번호", "ETD", "P/S Order"]
        print("\n샘플 3행:")
        for row in payload["mst_rows"][:3]:
            print("  ", {c: row[IDX[c]] for c in show}, "| status:", row[-1])

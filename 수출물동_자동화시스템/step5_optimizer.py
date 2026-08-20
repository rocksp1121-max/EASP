# -*- coding: utf-8 -*-
"""
step5_optimizer.py  (STEP 5: 장입계획 최적화 — 휴리스틱)
- 목적: ETD/마감 임박 우선
- 혼적: 실제 패턴 (F.Dest 그룹 + FFD + dead space 채움)
- 알고리즘: 휴리스틱 (First-Fit Decreasing)

입력 : step_processor.MSTStore (STEP1~4 완료된 rows, 42컬럼)
출력 : rows 갱신(장입수량/컨대수 → 계획확정) + 컨테이너 목록 + Templete 양식
"""
import os
import csv
from pathlib import Path
from datetime import datetime

from step_processor import MST_HEADER, IDX, _s, _num   # 컬럼 인덱스/유틸 재사용

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# 40HC 사용가능 적재 CBM (근사) — CBM 폴백용
CNTR_CBM = {"40HC": 68.0, "40FT": 60.0, "20FT": 28.0}
DEFAULT_CAP = 50          # 용량 전혀 못 구할 때 보수적 기본값


class Capacity:
    """모델별 컨테이너 적재가능 수량 + 단위 CBM 룩업 (+ 폴백)"""
    def __init__(self):
        self.by_model = {}   # 'Model.Suffix' → (c40HC, cbm_per_unit)
        self.by_code = {}    # 'Model_Code'    → c40HC
        self._load()

    def _load(self):
        f1 = DATA_DIR / "load_capacity.csv"
        if f1.exists():
            with open(f1, encoding="utf-8-sig") as fp:
                for r in csv.DictReader(fp):
                    try:
                        qty = int(r.get("c40HC") or 0)
                        cbm = float(r.get("cbm") or 0)
                        self.by_model[r["Model"]] = (qty, cbm)
                    except ValueError:
                        pass
        f2 = DATA_DIR / "load_capacity_code.csv"
        if f2.exists():
            with open(f2, encoding="utf-8-sig") as fp:
                for r in csv.DictReader(fp):
                    try:
                        self.by_code[r["Model_Code"]] = int(r["c40HC"] or 0)
                    except ValueError:
                        pass

    def of(self, model, cbm=0.0, qty=0):
        """모델 1컨(40HC) 적재가능 수량. 폴백: code → CBM기반 → 기본값"""
        m = _s(model)
        v = self.by_model.get(m)
        if v and v[0] > 0:
            return v[0], "master"
        code = m.split(".")[0]
        if code in self.by_code and self.by_code[code] > 0:
            return self.by_code[code], "code"
        # CBM 기반: 단위CBM = 주문CBM/주문수량 → 68 / 단위CBM
        if cbm and qty:
            unit = float(cbm) / float(qty)
            if unit > 0:
                est = int(CNTR_CBM["40HC"] / unit)
                if est > 0:
                    return est, "cbm"
        return DEFAULT_CAP, "default"

    def cbm_unit_of(self, model):
        """master 단위 CBM (없으면 0 → 호출자가 SO Raw 기반으로 폴백 계산)"""
        v = self.by_model.get(_s(model))
        return v[1] if v else 0.0


class Affinity:
    """혼적 조합빈도 (mix_affinity.csv) — 같은 컨에 자주 실리는 모델쌍 점수"""
    def __init__(self):
        self.score = {}        # frozenset({a,b}) → 누적 빈도
        self._load()

    def _load(self):
        f = DATA_DIR / "mix_affinity.csv"
        if not f.exists():
            return
        with open(f, encoding="utf-8-sig", newline="") as fp:
            for r in csv.DictReader(fp):
                models = [m.strip() for m in (r.get("모델목록") or "").split("/") if m.strip()]
                try:
                    freq = int(float(r.get("빈도") or 0))
                except ValueError:
                    freq = 0
                if not freq or len(models) < 2:
                    continue
                for i in range(len(models)):
                    for j in range(i + 1, len(models)):
                        k = frozenset((models[i], models[j]))
                        self.score[k] = self.score.get(k, 0) + freq

    def pair(self, a, b):
        if not a or not b or a == b:
            return 0
        return self.score.get(frozenset((a, b)), 0)

    def container_score(self, c, model):
        """컨테이너 내 기존 모델들과 신규 모델의 affinity 최댓값"""
        if not c.lines:
            return 0
        return max(self.pair(model, ln["model"]) for ln in c.lines)


def _post_merge_dest(open_cntrs):
    """같은 F.Dest 내 저적재 컨테이너를 다른 컨에 흡수 시도 (저적재 → 더 찬 컨으로)"""
    merged = 0
    candidates = sorted(open_cntrs, key=lambda c: c.used)
    for src in candidates:
        if src.used >= 0.5 or not src.lines:
            break
        moves = []
        for ln in src.lines:
            placed = False
            for t in open_cntrs:
                if t is src or not t.lines:
                    continue
                fit = t.fit_qty(ln["cap"], ln["cbm_unit"])
                if fit >= ln["qty"]:
                    moves.append((ln, t)); placed = True; break
            if not placed:
                moves = None; break
        if moves:
            for ln, t in moves:
                t.add(ln["ri"], ln["model"], ln["qty"], ln["cap"], ln["cbm_unit"])
            src.lines = []
            src.used = 0.0
            src.used_cbm = 0.0
            merged += 1
    return [c for c in open_cntrs if c.lines], merged


def _date_key(s):
    """마감일/ETD 문자열 → 정렬키 (없으면 먼 미래)"""
    s = _s(s)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.max


class Container:
    __slots__ = ("gno", "fdest", "spec", "booking", "lines", "used", "used_cbm", "cbm_limit", "case")
    def __init__(self, gno, fdest, spec="40HC", booking=""):
        self.gno = gno
        self.fdest = fdest
        self.spec = spec
        self.booking = booking                       # 그룹 키: 컨=(F.Dest, 부킹) 1:1 보장
        self.lines = []     # [{'ri':행idx,'model':..,'qty':..,'cap':..,'frac':..,'cbm_unit':..}]
        self.used = 0.0                              # 수량 분수 합 (0~1)
        self.used_cbm = 0.0                          # 사용 CBM
        self.cbm_limit = CNTR_CBM.get(spec, 68.0)    # 40HC=68
        self.case = ""                               # Single/Case1_CBM/Case2_Dead (사후 분류)

    def remaining(self):
        return max(0.0, 1.0 - self.used)

    def remaining_cbm(self):
        return max(0.0, self.cbm_limit - self.used_cbm)

    def add(self, ri, model, qty, cap, cbm_unit=0.0):
        frac = qty / cap if cap else 0
        self.lines.append({"ri": ri, "model": model, "qty": qty, "cap": cap,
                           "frac": frac, "cbm_unit": cbm_unit})
        self.used += frac
        self.used_cbm += qty * cbm_unit

    def fit_qty(self, cap, cbm_unit):
        """이 컨테이너에 들어갈 수 있는 최대 수량 (수량·CBM 이중 제약)"""
        by_qty = int(self.remaining() * cap + 1e-9)
        if cbm_unit > 0:
            by_cbm = int(self.remaining_cbm() / cbm_unit + 1e-9)
            return min(by_qty, by_cbm)
        return by_qty

    def cbm_fill(self):
        return self.used_cbm / self.cbm_limit if self.cbm_limit else 0


def optimize(store, spec="40HC"):
    """store.rows 를 갱신하고 컨테이너 목록/요약 반환"""
    cap = Capacity()
    aff = Affinity()
    rows = store.rows

    # 1) 계획대상 아이템 추출
    #    가용 = min(잔량, 재고) + (생산일자 ≤ ETD 일 때 잔량-재고)
    items = []
    from_production = 0
    for ri, row in enumerate(rows):
        if _s(row[IDX["작업번호(Inst No)"]]):       # 이미 작업완료 → 제외
            continue
        fdest = _s(row[IDX["F.Dest"]])
        if not fdest:
            continue
        rem = int(_num(row[IDX["Remaining SO Qty"]]) or _num(row[IDX["SO Qty"]]) or 0)
        stock = int(_num(row[IDX["재고"]]) or 0)
        # B-3: 재고 부족분도 생산일자가 ETD 이전이면 가용
        avail_stock = min(rem, stock)
        addl = 0
        if rem > stock:
            prod = _s(row[IDX["생산일자"]])
            etd = _s(row[IDX["ETD"]])
            if prod and etd and prod <= etd:        # ISO date 문자열 비교
                addl = rem - stock
        avail = avail_stock + addl
        if avail <= 0:
            continue
        if addl > 0:
            from_production += 1
        model = _s(row[IDX["Model"]])
        so_cbm = _num(row[IDX["CBM"]]) or 0
        so_qty_raw = _num(row[IDX["SO Qty"]]) or 0
        capacity, src = cap.of(model, so_cbm, so_qty_raw)
        # 단위 CBM: master 값 우선, 없으면 주문 총CBM/수량 폴백
        cbm_unit = cap.cbm_unit_of(model)
        if not cbm_unit and so_cbm > 0 and so_qty_raw > 0:
            cbm_unit = so_cbm / so_qty_raw
        items.append({
            "ri": ri, "fdest": fdest, "model": model, "avail": avail,
            "cap": max(1, capacity), "cap_src": src, "cbm_unit": cbm_unit,
            "due": _date_key(row[IDX["마감일"]] or row[IDX["ETD"]]),
            "booking": _s(row[IDX["부킹번호"]]),       # 그룹 키 일부
        })

    # 2) (F.Dest, 부킹) 그룹 → 그룹은 마감/ETD 임박 순, 그룹 내는 잔량 큰 순(FFD)
    #    컨테이너=부킹 1:1 보장 (한 컨에 다른 부킹 SO 섞이지 않음)
    groups = {}
    for it in items:
        key = (it["fdest"], it["booking"])           # 부킹 없는 건도 자기 그룹 (key=...,"")
        groups.setdefault(key, []).append(it)
    ordered_keys = sorted(groups, key=lambda k: min(x["due"] for x in groups[k]))

    containers = []
    gno = 0
    merged_total = 0
    for (dest, booking) in ordered_keys:
        glist = sorted(groups[(dest, booking)], key=lambda x: (x["due"], -x["avail"]))
        open_cntrs = []      # 해당 (F.Dest, 부킹) 의 열린 컨테이너들
        for it in glist:
            qty_left = it["avail"]
            cbm_u = it["cbm_unit"]
            model = it["model"]
            # 2-1) Dead space 채울 컨테이너 우선순위: 동일모델 ▶ affinity 높음 ▶ 나머지
            order = sorted(
                open_cntrs,
                key=lambda c: (
                    0 if any(ln["model"] == model for ln in c.lines) else 1,
                    -aff.container_score(c, model),
                ),
            )
            for c in order:
                if qty_left <= 0:
                    break
                fit = c.fit_qty(it["cap"], cbm_u)
                if fit > 0:
                    take = min(qty_left, fit)
                    c.add(it["ri"], model, take, it["cap"], cbm_u)
                    qty_left -= take
            # 2-2) 남으면 새 컨테이너 (필요한 만큼 반복)
            while qty_left > 0:
                gno += 1
                c = Container(gno, dest, spec, booking=booking)
                fit = c.fit_qty(it["cap"], cbm_u)        # 빈 컨 최대 = cap 또는 CBM 제약
                take = min(qty_left, max(1, fit))
                c.add(it["ri"], model, take, it["cap"], cbm_u)
                qty_left -= take
                open_cntrs.append(c)
        # 2-3) 저적재 컨테이너 post-merge (같은 부킹 내에서만 — open_cntrs 가 이미 그 범위)
        open_cntrs, merged = _post_merge_dest(open_cntrs)
        merged_total += merged
        containers.extend(open_cntrs)

    # 병합으로 비워진 GROUP_NO 갭 제거 → 1..N 재번호
    for i, c in enumerate(containers, 1):
        c.gno = i

    # 컨테이너별 Case 사후분류 (혼적_Case분류 컨벤션과 일치)
    for c in containers:
        nmodels = len({ln["model"] for ln in c.lines})
        if nmodels == 1:
            c.case = "Single"
        elif nmodels == 2 and c.cbm_fill() > c.used:
            c.case = "Case1_CBM"       # 2종이 CBM으로 묶임
        else:
            c.case = "Case2_Dead"      # 다종 dead-space

    # 3) rows 갱신: 주문별 장입수량 합 / 컨대수 합 → 계획확정
    agg = {}   # ri → [load_sum, cntr_sum]
    for c in containers:
        for ln in c.lines:
            a = agg.setdefault(ln["ri"], [0, 0.0])
            a[0] += ln["qty"]
            a[1] += ln["frac"]
    for ri, (load, frac) in agg.items():
        rows[ri][IDX["장입수량"]] = load
        rows[ri][IDX["컨대수"]] = round(frac, 3)

    # 4) 요약 (수량·CBM 이중 적재율, Case 분포)
    from collections import Counter
    n = len(containers)
    qty_fill = (sum(c.used     for c in containers) / n * 100) if n else 0
    cbm_fill = (sum(c.cbm_fill() for c in containers) / n * 100) if n else 0
    case_dist = dict(Counter(c.case for c in containers))
    summary = {
        "step": "STEP5 장입계획",
        "planned_orders": len(agg),
        "containers": n,
        "avg_fill_pct": round(qty_fill, 1),         # 수량 기준 (UI 호환 유지)
        "avg_cbm_fill_pct": round(cbm_fill, 1),     # CBM 기준
        "mixed_containers": n - case_dist.get("Single", 0),
        "case_dist": case_dist,
        "merged_low_fill": merged_total,            # post-merge로 절감된 컨테이너 수
        "from_production": from_production,         # B-3: 생산예정으로 가용 추가된 행수
        "cap_src": _src_dist(items),
    }
    return containers, summary


def _src_dist(items):
    from collections import Counter
    return dict(Counter(it["cap_src"] for it in items))


def to_template(containers):
    """Templete 양식 행 생성 (컨테이너=GROUP_NO, 모델별 1행)"""
    fields = ["GROUP_NO", "VBELN", "POSNR", "LOAD_TYPE", "MATNR", "FDEST", "CNTR_SPEC",
              "LOAD_QTY_CNTR", "CNTR_QTY", "REMARK"]
    out = []
    # 주문정보(SO/Item)는 store 없이 컨테이너 line에서 model만 알므로, 호출부에서 store로 보강
    for c in containers:
        for ln in c.lines:
            out.append({"GROUP_NO": c.gno, "MATNR": ln["model"], "FDEST": c.fdest,
                        "CNTR_SPEC": c.spec, "LOAD_QTY_CNTR": ln["qty"],
                        "CNTR_QTY": round(ln["frac"], 3), "_ri": ln["ri"]})
    return fields, out


# ── 단독 실행 (STEP1~4 → STEP5) ──
if __name__ == "__main__":
    import argparse
    from step_processor import run_all
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.today().strftime("%m%d"))
    args = ap.parse_args()

    store = run_all(args.date)
    if not store:
        raise SystemExit("STEP1~4 실패")
    containers, summary = optimize(store)
    print("\n===== STEP5 장입계획 결과 =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    # 샘플 컨테이너 3개
    print("\n샘플 컨테이너:")
    for c in containers[:3]:
        models = ", ".join(f"{ln['model']}×{ln['qty']}" for ln in c.lines[:4])
        print(f"  G{c.gno} [{c.fdest}/{c.spec}] 적재율 {c.used*100:.0f}% · {len(c.lines)}품목 · {models}{' ...' if len(c.lines)>4 else ''}")
    # 혼적 예시
    mixed = [c for c in containers if len({ln['model'] for ln in c.lines}) > 1]
    print(f"\n혼적 컨테이너 예시 ({len(mixed)}개 중 2개):")
    for c in mixed[:2]:
        items = " / ".join(f"{ln['model']}×{ln['qty']}" for ln in c.lines)
        print(f"  G{c.gno} [{c.fdest}] 적재율 {c.used*100:.0f}%: {items}")

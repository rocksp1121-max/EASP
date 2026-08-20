# -*- coding: utf-8 -*-
"""
app.py  —  EASP (수출 자동 출하 계획) — Export Auto Shipment Planning Flask 서버
실행: python app.py
접속: http://localhost:5000
"""

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response

BASE_DIR     = Path(__file__).parent.parent          # 수출물동_자동화시스템\
CRAWLERS_DIR = BASE_DIR / "crawlers"
IMPORT_RAW   = BASE_DIR / "import_raw.py"

# STEP 1~4 처리 모듈 + STEP5 옵티마이저 (BASE_DIR 에 위치)
sys.path.insert(0, str(BASE_DIR))
import step_processor
import step5_optimizer

app = Flask(__name__, static_folder='.', static_url_path='')

# ── STEP 처리 상태 (서버 보관 MST + STEP5 컨테이너) ──
_mst_store = None
_containers = None          # STEP5 장입계획 결과 (컨테이너 목록)
_mst_lock = threading.Lock()

# 영속 저장 경로
MST_PERSIST = BASE_DIR / "mst_current.json"
SNAPSHOTS_DIR = BASE_DIR / "snapshots"


def _save_mst_persisted():
    """현재 MST를 mst_current.json + snapshots/YYYYMMDD/mst.json 양쪽 저장."""
    if _mst_store is None:
        return
    _mst_store.save(MST_PERSIST)
    today = datetime.today().strftime("%Y%m%d")
    _mst_store.save(SNAPSHOTS_DIR / today / "mst.json")


# 시작 시 자동 복원
if MST_PERSIST.exists():
    _mst_store = step_processor.MSTStore()
    if _mst_store.load(MST_PERSIST):
        print(f"  [영속] mst_current.json 로드: {len(_mst_store.rows):,}행")
    else:
        _mst_store = None

# ── 크롤링 상태 (스레드 공유) ──────────────────────
_crawl_state = {
    "running": False,
    "ba_codes": [],
    "logs": [],
    "last_result": None,
}
_lock = threading.Lock()


def _log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": msg, "level": level}
    with _lock:
        _crawl_state["logs"].append(entry)
    print(f"[{ts}] {msg}")


# ── 정적 파일 (index.html) ────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ── 크롤링 API ─────────────────────────────────────
@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    data = request.get_json(force=True)
    ba_codes: list = data.get("ba_codes", ["DFZ"])

    with _lock:
        if _crawl_state["running"]:
            return jsonify({"ok": False, "message": "이미 크롤링 중입니다."}), 409
        _crawl_state["running"] = True
        _crawl_state["ba_codes"] = ba_codes
        _crawl_state["logs"] = []
        _crawl_state["last_result"] = None

    # 백그라운드 스레드로 실행 (블로킹 방지)
    t = threading.Thread(target=_run_crawl, args=(ba_codes,), daemon=True)
    t.start()

    return jsonify({
        "ok": True,
        "message": f"크롤링 시작 — BA: {', '.join(ba_codes)}",
        "ba_codes": ba_codes,
    })


@app.route("/api/crawl/status", methods=["GET"])
def api_crawl_status():
    with _lock:
        return jsonify({
            "running": _crawl_state["running"],
            "ba_codes": _crawl_state["ba_codes"],
            "logs": _crawl_state["logs"][-50:],   # 최근 50줄
            "result": _crawl_state["last_result"],
        })


# ── 크롤링 실제 실행 (백그라운드) ─────────────────
def _run_crawl(ba_codes: list):
    try:
        _log(f"크롤링 시작 — BA {len(ba_codes)}건: {', '.join(ba_codes)}")

        # BA 코드별로 재고 크롤러 순차 실행
        # (SO, 생산계획, 선복은 BA 무관 — 1회만 실행)
        stock_ok = True
        for i, ba in enumerate(ba_codes, 1):
            _log(f"[{i}/{len(ba_codes)}] 재고 크롤링 BA={ba}")
            ret = subprocess.run(
                [sys.executable, str(CRAWLERS_DIR / "crawler_stock.py"), "--ba", ba],
                cwd=str(CRAWLERS_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in ret.stdout.splitlines():
                _log(f"  {line}")
            if ret.returncode != 0:
                _log(f"  [오류] BA={ba} 재고 크롤링 실패", "error")
                stock_ok = False

        # SO 크롤러
        _log("SO 크롤링 시작")
        _run_single_crawler("crawler_so.py")

        # 생산계획 크롤러
        _log("생산계획 크롤링 시작")
        _run_single_crawler("crawler_production.py")

        # 선복 크롤러
        _log("선복 크롤링 시작")
        _run_single_crawler("crawler_booking.py")

        # import_raw.py 실행
        _log("import_raw.py 실행 중...")
        if IMPORT_RAW.exists():
            ret = subprocess.run(
                [sys.executable, str(IMPORT_RAW)],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in ret.stdout.splitlines():
                _log(f"  {line}")
            if ret.returncode == 0:
                _log("import_raw.py 완료", "ok")
            else:
                _log("import_raw.py 오류", "error")

        _log("전체 크롤링 완료", "ok")
        with _lock:
            _crawl_state["last_result"] = {
                "ok": True,
                "message": f"완료 — BA {len(ba_codes)}건 처리",
                "finished_at": datetime.now().isoformat(),
            }

    except Exception as e:
        _log(f"크롤링 예외: {e}", "error")
        with _lock:
            _crawl_state["last_result"] = {"ok": False, "message": str(e)}
    finally:
        with _lock:
            _crawl_state["running"] = False


def _run_single_crawler(script_name: str) -> bool:
    ret = subprocess.run(
        [sys.executable, str(CRAWLERS_DIR / script_name)],
        cwd=str(CRAWLERS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in ret.stdout.splitlines():
        _log(f"  {line}")
    ok = ret.returncode == 0
    if not ok:
        _log(f"  [오류] {script_name} 실패", "error")
    return ok


# ── 크롤링 상태 SSE (Server-Sent Events) ──────────
@app.route("/api/crawl/stream")
def api_crawl_stream():
    """실시간 로그 스트리밍 (EventSource)"""
    from flask import Response
    import time

    def generate():
        sent = 0
        while True:
            with _lock:
                logs = _crawl_state["logs"]
                new_logs = logs[sent:]
                running  = _crawl_state["running"]
                result   = _crawl_state["last_result"]
            for entry in new_logs:
                yield f"data: {entry['ts']} {entry['msg']}\n\n"
            sent += len(new_logs)
            if not running and sent > 0:
                yield f"data: [DONE] {result}\n\n"
                break
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Raw 파일 현황 API ──────────────────────────────
@app.route("/api/raw/status", methods=["GET"])
def api_raw_status():
    from datetime import date
    today   = date.today().strftime("%m%d")
    raw_dir = BASE_DIR / "Raw" / today

    files = {
        "so":      "Display Sales Order Progress.xlsx",
        "prod":    "생산계획(PS Order).xlsx",
        "stock":   "display stock by bin (1).xlsx",
        "booking": "1779169640580_BookingProgressDetails.xlsx",
    }

    status = {}
    for key, fname in files.items():
        fpath = raw_dir / fname
        if fpath.exists():
            import pandas as pd
            try:
                df = pd.read_excel(fpath, nrows=1)
                df_full = pd.read_excel(fpath)
                rows = len(df_full)
            except Exception:
                rows = -1
            status[key] = {"exists": True, "rows": rows, "file": fname}
        else:
            status[key] = {"exists": False, "rows": 0, "file": fname}

    return jsonify({"ok": True, "date": today, "files": status})


# ── STEP 1~4 (DATA 준비) API ───────────────────────
def _resolve_raw(date_str=None):
    return step_processor.find_raw_dir(date_str or datetime.today().strftime("%m%d"))


@app.route("/api/step/<step>", methods=["POST"])
def api_step(step):
    """STEP 실행. step = '1'|'2'|'3'|'4'|'5'|'all'. STEP1/all 은 MST 재생성, 5는 장입계획."""
    global _mst_store, _containers
    data = request.get_json(silent=True) or {}
    raw_dir = _resolve_raw(data.get("date"))
    if raw_dir is None:
        return jsonify({"ok": False, "message": "Raw 폴더가 없습니다. Raw/MMDD/ 에 파일을 넣으세요."}), 400
    try:
        with _mst_lock:
            if step == "all":
                # 영속 MST 있으면 그 위에서 변동 반영 (없으면 fresh)
                if _mst_store is None:
                    _mst_store = step_processor.MSTStore()
                _containers = None                          # DATA 재준비 → 기존 장입계획 무효화
                results = [
                    _mst_store.step1_so(raw_dir),
                    _mst_store.step2_production(raw_dir),
                    _mst_store.step3_stock(raw_dir),
                    _mst_store.step4_booking(raw_dir),
                ]
                _save_mst_persisted()
                return jsonify({"ok": True, "results": results, "total": len(_mst_store.rows),
                                "raw_dir": raw_dir.name})
            if step == "1":
                if _mst_store is None:
                    _mst_store = step_processor.MSTStore()
                _containers = None
                res = _mst_store.step1_so(raw_dir)
            elif step == "5":
                if _mst_store is None:
                    return jsonify({"ok": False, "message": "STEP1~4(DATA 준비)를 먼저 실행하세요."}), 409
                _containers, res = step5_optimizer.optimize(_mst_store)
            else:
                if _mst_store is None:
                    return jsonify({"ok": False, "message": "STEP1(SO Update)을 먼저 실행하세요."}), 409
                fn = {"2": _mst_store.step2_production,
                      "3": _mst_store.step3_stock,
                      "4": _mst_store.step4_booking}.get(step)
                if fn is None:
                    return jsonify({"ok": False, "message": f"알 수 없는 STEP: {step}"}), 400
                res = fn(raw_dir)
            _save_mst_persisted()
        return jsonify({"ok": True, "result": res, "total": len(_mst_store.rows),
                        "raw_dir": raw_dir.name})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "message": f"Raw 파일 없음: {e}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/mst", methods=["GET"])
def api_mst():
    """현재 서버 보관 MST 반환 (mst_data.js 동일 포맷)."""
    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "built": False,
                            "message": "아직 STEP을 실행하지 않았습니다."})
        payload = _mst_store.to_payload()
    return jsonify({"ok": True, "built": True, **payload})


@app.route("/api/raw/info", methods=["GET"])
def api_raw_info():
    """현재 Raw 폴더 경로 + 파일 존재 여부 + 행 수."""
    raw_root = step_processor.RAW_ROOT
    today_str = datetime.today().strftime("%m%d")
    used = step_processor.find_raw_dir(today_str)   # today→latest 폴백 적용된 실사용 폴더

    expected = {
        "so":    "Display Sales Order Progress.xlsx",
        "prod":  "생산계획(PS Order).xlsx",
        "stock": "display stock by bin (1).xlsx",
        "book":  "1779169640580_BookingProgressDetails.xlsx",
    }
    files = {}
    for key, fn in expected.items():
        p = (used / fn) if used else None
        if p and p.exists():
            try:
                import pandas as pd
                rows = len(pd.read_excel(p, dtype=str, nrows=10000).fillna(""))
            except Exception:
                rows = None
            files[key] = {"filename": fn, "present": True, "rows": rows, "size_kb": p.stat().st_size // 1024}
        else:
            files[key] = {"filename": fn, "present": False, "rows": None, "size_kb": 0}
    return jsonify({
        "ok": True,
        "raw_root": str(raw_root),
        "today": today_str,
        "today_dir": str(raw_root / today_str),
        "used_dir": str(used) if used else None,
        "used_label": used.name if used else None,
        "files": files,
    })


@app.route("/api/raw/open", methods=["POST"])
def api_raw_open():
    """탐색기로 Raw 폴더 열기 (Windows). 없으면 생성."""
    raw_root = step_processor.RAW_ROOT
    today_str = datetime.today().strftime("%m%d")
    target = raw_root / today_str
    target.mkdir(parents=True, exist_ok=True)
    try:
        # subprocess.Popen으로 explorer 직접 호출 — os.startfile은 서버 컨텍스트에서 조용히 실패하는 케이스가 있음
        subprocess.Popen(["explorer", str(target)], shell=False)
        return jsonify({"ok": True, "path": str(target)})
    except Exception as e:
        return jsonify({"ok": False, "message": f"{type(e).__name__}: {e}", "path": str(target)}), 500


@app.route("/api/mst/row", methods=["PUT"])
def api_mst_row_edit():
    """단일 MST 셀 인라인 편집. PRESERVED_COLS만 허용 + 영속 저장."""
    data = request.get_json(silent=True) or {}
    ri = data.get("ri")
    col = data.get("col")
    value = data.get("value", "")
    if not isinstance(ri, int) or not isinstance(col, int):
        return jsonify({"ok": False, "message": "ri/col은 정수"}), 400
    if col not in step_processor.PRESERVED_COLS:
        return jsonify({"ok": False, "message": "이 컬럼은 편집 불가"}), 403
    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "message": "MST가 없습니다."}), 409
        if ri < 0 or ri >= len(_mst_store.rows):
            return jsonify({"ok": False, "message": "잘못된 행 인덱스"}), 400
        _mst_store.rows[ri][col] = str(value)
        _save_mst_persisted()
    return jsonify({"ok": True, "ri": ri, "col": col, "value": value})


@app.route("/api/mst/reset", methods=["POST"])
def api_mst_reset():
    global _mst_store, _containers
    with _mst_lock:
        _mst_store = None
        _containers = None
    return jsonify({"ok": True})


@app.route("/api/containers", methods=["GET"])
def api_containers():
    """STEP5 장입계획 — 컨테이너별 적재 결과 (최적화 탭용)."""
    IDX, S = step_processor.IDX, step_processor._s
    with _mst_lock:
        if _containers is None or _mst_store is None:
            return jsonify({"ok": False, "built": False,
                            "message": "STEP5(장입계획)를 먼저 실행하세요."})
        rows = _mst_store.rows
        out = []
        for c in _containers:
            lines = [{
                "so": S(rows[ln["ri"]][IDX["SO No"]]),
                "item": S(rows[ln["ri"]][IDX["Item No"]]),
                "model": ln["model"], "qty": ln["qty"], "frac": round(ln["frac"], 3),
            } for ln in c.lines]
            out.append({"gno": c.gno, "fdest": c.fdest, "spec": c.spec,
                        "booking": c.booking,        # 컨=부킹 1:1 (STEP5 v2)
                        "fill": round(c.used * 100, 1),
                        "cbm_fill": round(c.cbm_fill() * 100, 1),
                        "case": c.case,
                        "nmodels": len({l["model"] for l in c.lines}), "lines": lines})
    from collections import Counter
    case_dist = dict(Counter(c["case"] for c in out))
    summary = {"containers": len(out),
               "avg_fill": round(sum(c["fill"] for c in out) / len(out), 1) if out else 0,
               "avg_cbm_fill": round(sum(c["cbm_fill"] for c in out) / len(out), 1) if out else 0,
               "mixed": sum(1 for c in out if c["nmodels"] > 1),
               "case_dist": case_dist}
    return jsonify({"ok": True, "built": True, "summary": summary, "containers": out})


@app.route("/api/version", methods=["GET"])
def api_version():
    """배포본 실행 정보 반환."""
    app_file = Path(__file__).resolve()
    return jsonify({
        "ok": True,
        "deploy_dir": str(BASE_DIR),
        "app_file": str(app_file),
        "last_modified": int(app_file.stat().st_mtime),
        "last_modified_iso": datetime.fromtimestamp(app_file.stat().st_mtime).isoformat(),
        "offline_ready": True,
        "message": "배포본 기준 Flask 서버 실행 중",
    })


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """운영 대시보드 — KPI · 분포 · 예외 · 저적재"""
    import ref_data
    from collections import Counter
    IDX, S, NUM = step_processor.IDX, step_processor._s, step_processor._num
    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "built": False, "message": "STEP을 먼저 실행하세요."})
        rows = _mst_store.rows

        # MST 통계
        status_dist = dict(Counter(step_processor.status_of(r) for r in rows))
        region_cnt = Counter(S(r[IDX["권역"]]) or "(미등록)" for r in rows)
        fdest_cnt = Counter(S(r[IDX["F.Dest"]]) for r in rows if S(r[IDX["F.Dest"]]))

        # 예외 1·2: 기준정보 미등록
        unknown_fdest_raw = sorted({S(r[IDX["F.Dest"]]) for r in rows
                                    if S(r[IDX["F.Dest"]]) and not ref_data.has_fdest(S(r[IDX["F.Dest"]]))})
        # 미등록 F.Dest를 country-prefix 폴백으로 자동분류 가능 vs 진짜 미상
        auto_resolved = {}
        truly_unknown = []
        for fd in unknown_fdest_raw:
            r_region = ref_data.region_of(fd)   # ref_fdest miss → country prefix 폴백
            if r_region:
                auto_resolved[fd] = r_region
            else:
                truly_unknown.append(fd)
        unknown_carrier = sorted({S(r[IDX["선사"]]) for r in rows
                                  if S(r[IDX["선사"]]) and not ref_data.has_carrier(S(r[IDX["선사"]]))})

        # 예외 3: 용량 미매칭 모델
        capm = step5_optimizer.Capacity()
        unknown_capacity = sorted({S(r[IDX["Model"]]) for r in rows
                                   if S(r[IDX["Model"]])
                                   and S(r[IDX["Model"]]) not in capm.by_model
                                   and S(r[IDX["Model"]]).split(".")[0] not in capm.by_code})

        # 예외 4: Critical — 재고부족 + 생산일자도 ETD 후(또는 없음) → 사람손 필요
        critical = []
        for r in rows:
            if step_processor.status_of(r) not in ("white", "yellow"):
                continue
            rem = int(NUM(r[IDX["Remaining SO Qty"]]) or NUM(r[IDX["SO Qty"]]) or 0)
            stock = int(NUM(r[IDX["재고"]]) or 0)
            if rem <= stock:
                continue
            etd, prod = S(r[IDX["ETD"]]), S(r[IDX["생산일자"]])
            if not etd:
                continue
            if not prod or prod > etd:
                critical.append({
                    "so": S(r[IDX["SO No"]]), "item": S(r[IDX["Item No"]]),
                    "model": S(r[IDX["Model"]]), "fdest": S(r[IDX["F.Dest"]]),
                    "rsd": S(r[IDX["RSD"]]), "etd": etd,
                    "prod": prod or "-", "short": rem - stock,
                })
        critical.sort(key=lambda x: (x["etd"], -x["short"]))

        # 컨테이너 통계 (STEP5 실행됐을 때)
        cntr_stats = None
        if _containers:
            n = len(_containers)
            avg_qty = sum(c.used for c in _containers) / n * 100 if n else 0
            avg_cbm = sum(c.cbm_fill() for c in _containers) / n * 100 if n else 0
            case_dist = dict(Counter(c.case for c in _containers))
            low_fill = [
                {"gno": c.gno, "fdest": c.fdest,
                 "qty_fill": round(c.used * 100, 1),
                 "cbm_fill": round(c.cbm_fill() * 100, 1),
                 "nmodels": len({l["model"] for l in c.lines})}
                for c in _containers if c.used < 0.5
            ]
            low_fill.sort(key=lambda x: x["qty_fill"])
            cntr_by_region = Counter(
                (ref_data.region_of(c.fdest) or "(미등록)") for c in _containers
            )
            # 부킹별 컨테이너 사용량 추적 + 혼합부킹 컨테이너 카운트
            book_usage = Counter()
            mixed_book_cntrs = 0
            for c in _containers:
                bks = [S(rows[ln["ri"]][IDX["부킹번호"]]) for ln in c.lines]
                bks = [b for b in bks if b]
                if not bks:
                    continue
                bc = Counter(bks)
                if len(bc) > 1:
                    mixed_book_cntrs += 1
                rep, _ = bc.most_common(1)[0]    # 대표 부킹 = 다수결
                book_usage[rep] += 1
            booking_top = [{"booking": b, "containers": n_} for b, n_ in book_usage.most_common(10)]
            cntr_stats = {
                "containers": n,
                "avg_qty_fill": round(avg_qty, 1),
                "avg_cbm_fill": round(avg_cbm, 1),
                "case_dist": case_dist,
                "low_fill_count": len(low_fill),
                "low_fill": low_fill[:50],
                "by_region": dict(cntr_by_region.most_common(12)),
                "booking_top": booking_top,
                "mixed_booking_cntrs": mixed_book_cntrs,
                "booking_total": len(book_usage),
            }

    return jsonify({
        "ok": True, "built": True,
        "mst": {
            "total": len(rows),
            "status_dist": status_dist,
            "region_top": dict(region_cnt.most_common(12)),
            "fdest_top": fdest_cnt.most_common(15),
        },
        "exceptions": {
            "unknown_fdest": truly_unknown,                   # 진짜 미상만 (자동분류 안 됨)
            "unknown_fdest_total": len(unknown_fdest_raw),
            "auto_resolved_fdest": auto_resolved,             # 자동분류된 것 {F.dest: 지역}
            "unknown_carrier": unknown_carrier,
            "unknown_capacity": unknown_capacity[:200],
            "unknown_capacity_total": len(unknown_capacity),
            "critical": critical[:80],
            "critical_total": len(critical),
        },
        "containers": cntr_stats,
    })


def _csv_response(fields, rows, filename):
    """rows: list of dict — UTF-8 BOM CSV로 응답"""
    import io, csv as csvmod
    buf = io.StringIO(); buf.write("﻿")
    w = csvmod.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/export/<dataset>", methods=["GET"])
def api_export(dataset):
    """전 화면 데이터를 raw CSV로 export (Excel UTF-8 BOM)."""
    import ref_data
    from collections import Counter
    IDX, S, NUM = step_processor.IDX, step_processor._s, step_processor._num
    today = datetime.today().strftime("%Y%m%d")

    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "message": "STEP을 먼저 실행하세요."}), 409
        rows = _mst_store.rows

        # ── MST 42컬럼 + status 전체 ──
        if dataset == "mst":
            fields = list(step_processor.MST_HEADER) + ["status"]
            out = []
            for r in rows:
                d = {h: r[i] if i < len(r) else "" for i, h in enumerate(step_processor.MST_HEADER)}
                d["status"] = step_processor.status_of(r)
                out.append(d)
            return _csv_response(fields, out, f"MST_{today}.csv")

        # ── SO 데이터 (SO No 별 집계) ──
        if dataset == "so":
            agg = {}
            for r in rows:
                so = S(r[IDX["SO No"]])
                if not so: continue
                a = agg.setdefault(so, {"SO No": so, "F.Dest": S(r[IDX["F.Dest"]]),
                                         "SO Qty 합계": 0, "Rem Qty 합계": 0, "Item 수": 0,
                                         "최초 RSD": S(r[IDX["RSD"]]),
                                         "부킹번호": S(r[IDX["부킹번호"]]),
                                         "ETD": S(r[IDX["ETD"]])})
                a["SO Qty 합계"]   += int(NUM(r[IDX["SO Qty"]]) or 0)
                a["Rem Qty 합계"]  += int(NUM(r[IDX["Remaining SO Qty"]]) or 0)
                a["Item 수"]        += 1
            return _csv_response(
                ["SO No","F.Dest","SO Qty 합계","Rem Qty 합계","Item 수","최초 RSD","부킹번호","ETD"],
                list(agg.values()), f"SO_aggregated_{today}.csv")

        # ── 선복(부킹) 집계 ──
        if dataset == "booking":
            agg = {}
            for r in rows:
                bk = S(r[IDX["부킹번호"]]) or "(미지정)"
                a = agg.setdefault(bk, {"부킹번호": bk, "ETD": S(r[IDX["ETD"]]),
                                         "주요 F.Dest": S(r[IDX["F.Dest"]]),
                                         "SO 건수": 0, "총 SO Qty": 0,
                                         "총 장입수량": 0, "총 컨대수": 0.0,
                                         "선사": S(r[IDX["선사"]]),
                                         "선사코드": S(r[IDX["선사코드"]])})
                a["SO 건수"]       += 1
                a["총 SO Qty"]     += int(NUM(r[IDX["SO Qty"]]) or 0)
                a["총 장입수량"]   += int(NUM(r[IDX["장입수량"]]) or 0)
                a["총 컨대수"]     += float(NUM(r[IDX["컨대수"]]) or 0)
            for a in agg.values():
                a["총 컨대수"] = round(a["총 컨대수"], 3)
            return _csv_response(
                ["부킹번호","ETD","주요 F.Dest","SO 건수","총 SO Qty","총 장입수량","총 컨대수","선사","선사코드"],
                list(agg.values()), f"Booking_{today}.csv")

        # ── 장입계획 결과 (장입수량>0 raw 행) ──
        if dataset == "result":
            fields = ["SO No","Item No","Model","F.Dest","부킹번호","ETD","SO Qty",
                      "Remaining SO Qty","장입수량","컨대수","상태","권역","마감일","모선명","선사","선사코드"]
            out = []
            for r in rows:
                if not (NUM(r[IDX["장입수량"]]) or 0):
                    continue
                d = {f: S(r[IDX[f]]) if f != "상태" else step_processor.status_of(r) for f in fields if f in IDX}
                d["상태"] = step_processor.status_of(r)
                out.append(d)
            return _csv_response(fields, out, f"LoadingPlan_Result_{today}.csv")

        # ── 컨테이너 적재 결과 (flattened: 컨×라인) ──
        if dataset == "containers":
            if not _containers:
                return jsonify({"ok": False, "message": "STEP5를 먼저 실행하세요."}), 409
            fields = ["GROUP_NO","F.Dest","부킹번호","CNTR_SPEC","Case","컨_수량fill%","컨_CBM_fill%",
                      "모델수","SO No","Item No","Model","장입수량","CNTR_frac"]
            out = []
            for c in _containers:
                nmodels = len({l["model"] for l in c.lines})
                for ln in c.lines:
                    r = rows[ln["ri"]]
                    out.append({
                        "GROUP_NO": c.gno, "F.Dest": c.fdest, "부킹번호": c.booking,
                        "CNTR_SPEC": c.spec, "Case": c.case,
                        "컨_수량fill%": round(c.used*100, 1),
                        "컨_CBM_fill%": round(c.cbm_fill()*100, 1),
                        "모델수": nmodels,
                        "SO No": S(r[IDX["SO No"]]), "Item No": S(r[IDX["Item No"]]),
                        "Model": ln["model"], "장입수량": ln["qty"],
                        "CNTR_frac": round(ln["frac"], 3),
                    })
            return _csv_response(fields, out, f"Containers_{today}.csv")

        # ── 대시보드 데이터셋들 ──
        if dataset == "dashboard_critical":
            out = []
            for r in rows:
                if step_processor.status_of(r) not in ("white", "yellow"):
                    continue
                rem = int(NUM(r[IDX["Remaining SO Qty"]]) or NUM(r[IDX["SO Qty"]]) or 0)
                stock = int(NUM(r[IDX["재고"]]) or 0)
                if rem <= stock: continue
                etd, prod = S(r[IDX["ETD"]]), S(r[IDX["생산일자"]])
                if not etd: continue
                if prod and prod <= etd: continue
                out.append({"SO No": S(r[IDX["SO No"]]), "Item No": S(r[IDX["Item No"]]),
                            "Model": S(r[IDX["Model"]]), "F.Dest": S(r[IDX["F.Dest"]]),
                            "RSD": S(r[IDX["RSD"]]), "ETD": etd,
                            "생산일": prod or "", "부족수량": rem - stock})
            out.sort(key=lambda x: (x["ETD"], -x["부족수량"]))
            return _csv_response(["SO No","Item No","Model","F.Dest","RSD","ETD","생산일","부족수량"],
                                 out, f"Critical_{today}.csv")

        if dataset == "dashboard_low_fill":
            if not _containers:
                return jsonify({"ok": False, "message": "STEP5를 먼저 실행하세요."}), 409
            out = [{"GROUP_NO": c.gno, "F.Dest": c.fdest, "부킹번호": c.booking,
                    "Case": c.case, "수량fill%": round(c.used*100,1),
                    "CBM_fill%": round(c.cbm_fill()*100,1),
                    "모델수": len({l["model"] for l in c.lines})}
                   for c in _containers if c.used < 0.5]
            out.sort(key=lambda x: x["수량fill%"])
            return _csv_response(["GROUP_NO","F.Dest","부킹번호","Case","수량fill%","CBM_fill%","모델수"],
                                 out, f"LowFill_Containers_{today}.csv")

        if dataset == "dashboard_unknown_fdest":
            out = []
            for r in rows:
                fd = S(r[IDX["F.Dest"]])
                if not fd or ref_data.has_fdest(fd): continue
                out.append({"F.dest": fd, "자동분류_지역": ref_data.region_of(fd) or ""})
            # dedupe
            seen = set(); dedup = []
            for d in out:
                if d["F.dest"] in seen: continue
                seen.add(d["F.dest"]); dedup.append(d)
            return _csv_response(["F.dest","자동분류_지역"], sorted(dedup, key=lambda x: x["F.dest"]),
                                 f"Unknown_FDest_{today}.csv")

        if dataset == "dashboard_unknown_carrier":
            unknown = sorted({S(r[IDX["선사"]]) for r in rows
                              if S(r[IDX["선사"]]) and not ref_data.has_carrier(S(r[IDX["선사"]]))})
            return _csv_response(["선사약어"], [{"선사약어": c} for c in unknown],
                                 f"Unknown_Carrier_{today}.csv")

        if dataset == "dashboard_unknown_capacity":
            capm = step5_optimizer.Capacity()
            unknown = sorted({S(r[IDX["Model"]]) for r in rows
                              if S(r[IDX["Model"]])
                              and S(r[IDX["Model"]]) not in capm.by_model
                              and S(r[IDX["Model"]]).split(".")[0] not in capm.by_code})
            return _csv_response(["Model"], [{"Model": m} for m in unknown],
                                 f"Unknown_Capacity_{today}.csv")

        if dataset == "dashboard_booking_usage":
            if not _containers:
                return jsonify({"ok": False, "message": "STEP5를 먼저 실행하세요."}), 409
            usage = Counter(c.booking for c in _containers if c.booking)
            mixed_dest_per_book = {}
            for c in _containers:
                if c.booking:
                    mixed_dest_per_book.setdefault(c.booking, set()).add(c.fdest)
            out = [{"부킹번호": b, "컨테이너수": n,
                    "F.Dest수": len(mixed_dest_per_book.get(b, set()))}
                   for b, n in usage.most_common()]
            return _csv_response(["부킹번호","컨테이너수","F.Dest수"], out,
                                 f"Booking_Usage_{today}.csv")

        if dataset == "dashboard_region_dist":
            cnt = Counter(S(r[IDX["권역"]]) or "(미등록)" for r in rows)
            out = [{"지역": k, "행수": v} for k, v in cnt.most_common()]
            return _csv_response(["지역","행수"], out, f"Region_Dist_{today}.csv")

        if dataset == "dashboard_fdest_top":
            cnt = Counter(S(r[IDX["F.Dest"]]) for r in rows if S(r[IDX["F.Dest"]]))
            out = [{"F.Dest": k, "행수": v} for k, v in cnt.most_common()]
            return _csv_response(["F.Dest","행수"], out, f"FDest_Dist_{today}.csv")

    return jsonify({"ok": False, "message": f"알 수 없는 dataset: {dataset}"}), 400


# ── 기준정보 CRUD (MST 관리 탭용) ─────────────────────
REF_TABLES = {
    "fdest":       {"file": "ref_fdest.csv",       "fields": ["F.dest", "지역"],          "key": "F.dest"},
    "carrier":     {"file": "ref_carrier.csv",     "fields": ["선사", "선사명", "선사코드"], "key": "선사"},
    "country":     {"file": "ref_country.csv",     "fields": ["국가코드", "국가명", "지역"], "key": "국가코드"},
    "transporter": {"file": "ref_transporter.csv", "fields": ["운송사", "담당자"],         "key": "운송사"},
}

def _ref_path(table):
    return BASE_DIR / "data" / REF_TABLES[table]["file"]


@app.route("/api/ref/<table>", methods=["GET"])
def api_ref_list(table):
    if table not in REF_TABLES:
        return jsonify({"ok": False, "message": f"알 수 없는 테이블: {table}"}), 400
    import csv as csvmod
    spec = REF_TABLES[table]
    p = _ref_path(table)
    rows = []
    if p.exists():
        with open(p, encoding="utf-8-sig", newline="") as fp:
            rows = list(csvmod.DictReader(fp))
    return jsonify({"ok": True, "fields": spec["fields"], "key": spec["key"], "rows": rows})


@app.route("/api/ref/<table>", methods=["POST"])
def api_ref_add(table):
    if table not in REF_TABLES:
        return jsonify({"ok": False, "message": f"알 수 없는 테이블: {table}"}), 400
    import csv as csvmod
    import ref_data
    spec = REF_TABLES[table]
    data = request.get_json(silent=True) or {}
    row = {f: (data.get(f) or "").strip() for f in spec["fields"]}
    if not row[spec["key"]]:
        return jsonify({"ok": False, "message": f"{spec['key']}는 필수입니다."}), 400
    # 중복 체크
    p = _ref_path(table)
    if p.exists():
        with open(p, encoding="utf-8-sig", newline="") as fp:
            for ex in csvmod.DictReader(fp):
                if ex.get(spec["key"]) == row[spec["key"]]:
                    return jsonify({"ok": False, "message": f"이미 등록: {row[spec['key']]}"}), 409
    with open(p, "a", encoding="utf-8-sig", newline="") as fp:
        w = csvmod.DictWriter(fp, fieldnames=spec["fields"], extrasaction="ignore")
        w.writerow(row)
    ref_data.reload()
    return jsonify({"ok": True, "added": row})


@app.route("/api/ref/<table>/<path:key>", methods=["DELETE"])
def api_ref_delete(table, key):
    if table not in REF_TABLES:
        return jsonify({"ok": False, "message": f"알 수 없는 테이블: {table}"}), 400
    import csv as csvmod
    import ref_data
    spec = REF_TABLES[table]
    p = _ref_path(table)
    if not p.exists():
        return jsonify({"ok": False, "message": "파일 없음"}), 404
    with open(p, encoding="utf-8-sig", newline="") as fp:
        rows = list(csvmod.DictReader(fp))
    before = len(rows)
    rows = [r for r in rows if r.get(spec["key"]) != key]
    if len(rows) == before:
        return jsonify({"ok": False, "message": f"행 없음: {key}"}), 404
    with open(p, "w", encoding="utf-8-sig", newline="") as fp:
        w = csvmod.DictWriter(fp, fieldnames=spec["fields"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    ref_data.reload()
    return jsonify({"ok": True, "deleted": key, "remaining": len(rows)})


# ──── 모델 치수 (LWH) — 양이 많아 일반 ref CRUD와 분리 처리 ────
DIMENSIONS_FIELDS = ["Model", "L_mm", "W_mm", "H_mm", "Volume_m3", "Weight_kg", "Dept", "UpdatedAt", "Source"]
DIMENSIONS_PATH = BASE_DIR / "data" / "ref_dimensions.csv"


@app.route("/api/ref/dimensions/import", methods=["POST"])
def api_dimensions_import():
    """LWH SAP export Excel 다중 업로드 → ref_dimensions.csv upsert."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "message": "파일을 선택하세요."}), 400
    import dimensions_importer
    tmp_dir = BASE_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    paths = []
    try:
        for f in files:
            name = f.filename or "uploaded.xlsx"
            p = tmp_dir / name
            f.save(str(p))
            paths.append(p)
        result = dimensions_importer.import_files([str(p) for p in paths])
        dimensions_importer.reload()
        return jsonify({"ok": True, "stats": result})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    finally:
        for p in paths:
            try: p.unlink()
            except: pass


@app.route("/api/ref/dimensions/stats", methods=["GET"])
def api_dimensions_stats():
    import dimensions_importer
    return jsonify({"ok": True, **dimensions_importer.stats()})


@app.route("/api/ref/dimensions/lookup", methods=["POST"])
def api_dimensions_lookup():
    """여러 모델 일괄 LWH 조회. body={models:[...]} → {model: {L_mm, W_mm, H_mm, Volume_m3, Weight_kg, Dept}}."""
    import dimensions_importer
    data = request.get_json(silent=True) or {}
    models = data.get("models") or []
    out = {}
    for m in models:
        r = dimensions_importer.lookup(m)
        if r:
            out[m] = {
                "L_mm":      int(float(r.get("L_mm") or 0)),
                "W_mm":      int(float(r.get("W_mm") or 0)),
                "H_mm":      int(float(r.get("H_mm") or 0)),
                "Volume_m3": float(r.get("Volume_m3") or 0),
                "Weight_kg": float(r.get("Weight_kg") or 0),
                "Dept":      r.get("Dept") or "",
            }
    return jsonify({"ok": True, "dimensions": out, "missing": [m for m in models if m not in out]})


@app.route("/api/ref/dimensions", methods=["GET"])
def api_dimensions_list():
    """검색/페이징. ?q= 모델 또는 부서 검색. ?limit= 기본 500, 최대 5000."""
    import csv as csvmod
    q = (request.args.get("q") or "").lower().strip()
    try:
        limit = min(max(int(request.args.get("limit", 500)), 1), 5000)
    except (TypeError, ValueError):
        limit = 500
    rows = []
    total = 0
    if DIMENSIONS_PATH.exists():
        with open(DIMENSIONS_PATH, encoding="utf-8-sig", newline="") as fp:
            for r in csvmod.DictReader(fp):
                total += 1
                if q and q not in (r.get("Model","") or "").lower() and q not in (r.get("Dept","") or "").lower():
                    continue
                if len(rows) < limit:
                    rows.append(r)
    return jsonify({"ok": True, "fields": DIMENSIONS_FIELDS, "key": "Model",
                    "rows": rows, "total": total, "limit": limit, "shown": len(rows)})


@app.route("/api/ref/dimensions", methods=["POST"])
def api_dimensions_upsert():
    """단건 추가/수정 (upsert). 운영자 직접 입력용."""
    import csv as csvmod
    import datetime as _dt
    import dimensions_importer
    data = request.get_json(silent=True) or {}
    model = (data.get("Model") or "").strip()
    if not model:
        return jsonify({"ok": False, "message": "Model 필수"}), 400
    try:
        L = int(float(data.get("L_mm") or 0))
        W = int(float(data.get("W_mm") or 0))
        H = int(float(data.get("H_mm") or 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "L/W/H 숫자(mm) 필수"}), 400
    if not (L > 0 and W > 0 and H > 0):
        return jsonify({"ok": False, "message": "L/W/H 모두 양수여야 합니다"}), 400
    try:
        vol = float(data.get("Volume_m3") or 0)
        wt  = float(data.get("Weight_kg") or 0)
    except (TypeError, ValueError):
        vol = 0.0; wt = 0.0
    dept = (data.get("Dept") or "").strip()
    today = _dt.date.today().isoformat()

    existing = {}
    if DIMENSIONS_PATH.exists():
        with open(DIMENSIONS_PATH, encoding="utf-8-sig", newline="") as fp:
            existing = {r["Model"]: r for r in csvmod.DictReader(fp) if r.get("Model")}
    is_new = model not in existing
    existing[model] = {
        "Model": model, "L_mm": L, "W_mm": W, "H_mm": H,
        "Volume_m3": round(vol, 6), "Weight_kg": round(wt, 3),
        "Dept": dept, "UpdatedAt": today, "Source": "manual",
    }
    DIMENSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DIMENSIONS_PATH, "w", encoding="utf-8-sig", newline="") as fp:
        w = csvmod.DictWriter(fp, fieldnames=DIMENSIONS_FIELDS, extrasaction="ignore")
        w.writeheader()
        for m in sorted(existing.keys()):
            w.writerow(existing[m])
    dimensions_importer.reload()
    return jsonify({"ok": True, "action": "inserted" if is_new else "updated", "model": model})


@app.route("/api/ref/dimensions/<path:model>", methods=["DELETE"])
def api_dimensions_delete(model):
    import csv as csvmod
    import dimensions_importer
    if not DIMENSIONS_PATH.exists():
        return jsonify({"ok": False, "message": "파일 없음"}), 404
    with open(DIMENSIONS_PATH, encoding="utf-8-sig", newline="") as fp:
        rows = list(csvmod.DictReader(fp))
    before = len(rows)
    rows = [r for r in rows if r.get("Model") != model]
    if len(rows) == before:
        return jsonify({"ok": False, "message": f"행 없음: {model}"}), 404
    with open(DIMENSIONS_PATH, "w", encoding="utf-8-sig", newline="") as fp:
        w = csvmod.DictWriter(fp, fieldnames=DIMENSIONS_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    dimensions_importer.reload()
    return jsonify({"ok": True, "deleted": model, "remaining": len(rows)})


@app.route("/api/ref/fdest/auto_merge", methods=["POST"])
def api_ref_fdest_auto_merge():
    """현재 MST의 미등록 F.Dest 중 country-prefix 자동분류 가능 항목을 ref_fdest.csv에 append."""
    import ref_data
    IDX, S = step_processor.IDX, step_processor._s
    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "message": "STEP을 먼저 실행하세요."}), 409
        rows = _mst_store.rows
    # 추가할 항목 수집
    to_add = []
    seen = set()
    for r in rows:
        fd = S(r[IDX["F.Dest"]])
        if not fd or fd in seen or ref_data.has_fdest(fd):
            continue
        seen.add(fd)
        region = ref_data.region_of(fd)
        if region:
            to_add.append({"F.dest": fd, "지역": region})
    if not to_add:
        return jsonify({"ok": True, "added": 0, "message": "자동분류 가능한 미등록 F.Dest 없음"})
    # ref_fdest.csv에 append
    import csv as csvmod
    ref_path = BASE_DIR / "data" / "ref_fdest.csv"
    with open(ref_path, "a", encoding="utf-8-sig", newline="") as fp:
        w = csvmod.DictWriter(fp, fieldnames=["F.dest", "지역"], extrasaction="ignore")
        for row in to_add:
            w.writerow(row)
    ref_data.reload()
    return jsonify({"ok": True, "added": len(to_add)})


@app.route("/api/ref/fdest_suggestions", methods=["GET"])
def api_fdest_suggestions():
    """현재 MST에서 미등록 F.Dest 중 country-prefix로 자동분류 가능한 항목을 CSV로."""
    IDX, S = step_processor.IDX, step_processor._s
    with _mst_lock:
        if _mst_store is None:
            return jsonify({"ok": False, "message": "STEP을 먼저 실행하세요."}), 409
        rows = _mst_store.rows
    unknown = {S(r[IDX["F.Dest"]]) for r in rows
               if S(r[IDX["F.Dest"]]) and not ref_data.has_fdest(S(r[IDX["F.Dest"]]))}
    suggestions = []
    for fd in sorted(unknown):
        region = ref_data.region_of(fd)
        if region:
            suggestions.append({"F.dest": fd, "지역": region, "source": "auto_country_prefix"})
    import io, csv as csvmod
    buf = io.StringIO(); buf.write("﻿")
    w = csvmod.DictWriter(buf, fieldnames=["F.dest", "지역", "source"])
    w.writeheader(); w.writerows(suggestions)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=ref_fdest_suggested.csv"})


@app.route("/api/template/xlsx", methods=["GET"])
def api_template_xlsx():
    """ZWMRE50110 양식 그대로 데이터 채워서 .xlsx 반환 (운영 업로드용).

    ⚠⚠⚠ 원본 템플릿 보호 규칙 ⚠⚠⚠
    `templates/ZWMRE50110.xlsx` 파일은 **절대로 디스크에 다시 쓰지 않는다.**
    - openpyxl.load_workbook(): 디스크 → 메모리 read-only 로딩
    - wb.save(BytesIO): 메모리 버퍼에만 저장 (디스크 X)
    - wb.save(tpl_path) 같은 코드는 절대 금지
    """
    import io, openpyxl
    IDX, S = step_processor.IDX, step_processor._s
    with _mst_lock:
        if _containers is None or _mst_store is None:
            return jsonify({"ok": False, "message": "STEP5를 먼저 실행하세요."}), 409
        rows = _mst_store.rows
        booking_pol = _mst_store.booking_pol

        # LOAD_QTY_TOTAL = (SO, Item) 별 모든 컨테이너 라인 합산
        so_item_total = {}
        for c in _containers:
            for ln in c.lines:
                r = rows[ln["ri"]]
                k = (S(r[IDX["SO No"]]), S(r[IDX["Item No"]]))
                so_item_total[k] = so_item_total.get(k, 0) + ln["qty"]

        # 템플릿: 매 요청마다 fresh load → 디스크 원본 절대 변경 안 됨
        tpl_path = BASE_DIR / "templates" / "ZWMRE50110.xlsx"
        if not tpl_path.exists():
            return jsonify({"ok": False, "message": "ZWMRE50110.xlsx 템플릿 누락"}), 500
        wb = openpyxl.load_workbook(tpl_path)        # READ-ONLY 의도
        ws = wb.active

        out_row = 5   # R1=header, R2=length, R3=field desc., R4=Start Line → R5부터 실제 데이터
        for c in _containers:
            for ln in c.lines:
                r = rows[ln["ri"]]
                so = S(r[IDX["SO No"]]); item = S(r[IDX["Item No"]])
                qty = ln["qty"]
                cells = [
                    "",                                          # A Field (라벨열)
                    c.gno,                                       # B GROUP_NO
                    so,                                          # C VBELN
                    item,                                        # D POSNR
                    "ODL",                                       # E LOAD_TYPE
                    ln["model"],                                 # F MATNR
                    c.fdest,                                     # G FDEST
                    c.spec,                                      # H CNTR_SPEC
                    so_item_total.get((so, item), qty),          # I LOAD_QTY_TOTAL
                    qty,                                         # J LOAD_QTY_CNTR
                    round(ln["frac"], 3),                        # K CNTR_QTY
                    "CT",                                        # L PACK_TYPE (default 카톤)
                    qty,                                         # M PACK_QTY (= per-CNTR qty)
                    S(r[IDX["Request Batch"]]),                  # N REQ_BATCH
                    S(r[IDX["선사코드"]]),                        # O SHIP_LINE
                    S(r[IDX["포워더"]]),                          # P FORWARDER
                    booking_pol.get(c.booking, ""),              # Q LOAD_PORT (Booking POL)
                    S(r[IDX["리마크"]]),                          # R REMARK
                ]
                for ci, v in enumerate(cells, 1):
                    ws.cell(row=out_row, column=ci, value=v)
                out_row += 1

        buf = io.BytesIO()
        wb.save(buf); buf.seek(0)                    # ⚠ BytesIO — 디스크 X
    today = datetime.today().strftime("%Y%m%d")
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="ZWMRE50110_{today}.xlsx"'})


@app.route("/api/template", methods=["GET"])
def api_template():
    """Templete 직장입계획 양식 (장입계획 결과 Export용)."""
    IDX, S = step_processor.IDX, step_processor._s
    fields = ["GROUP_NO", "VBELN", "POSNR", "LOAD_TYPE", "MATNR", "FDEST", "CNTR_SPEC",
              "LOAD_QTY_CNTR", "CNTR_QTY", "REQ_BATCH", "SHIP_LINE", "FORWARDER", "REMARK"]
    with _mst_lock:
        if _containers is None or _mst_store is None:
            return jsonify({"ok": False, "message": "STEP5를 먼저 실행하세요."}), 409
        rows = _mst_store.rows
        out = []
        for c in _containers:
            for ln in c.lines:
                r = rows[ln["ri"]]
                out.append({
                    "GROUP_NO": c.gno, "VBELN": S(r[IDX["SO No"]]), "POSNR": S(r[IDX["Item No"]]),
                    "LOAD_TYPE": "ODL", "MATNR": ln["model"], "FDEST": c.fdest, "CNTR_SPEC": c.spec,
                    "LOAD_QTY_CNTR": ln["qty"], "CNTR_QTY": round(ln["frac"], 3),
                    "REQ_BATCH": S(r[IDX["Request Batch"]]), "SHIP_LINE": S(r[IDX["선사코드"]]),
                    "FORWARDER": S(r[IDX["포워더"]]), "REMARK": S(r[IDX["리마크"]]),
                })
    return jsonify({"ok": True, "fields": fields, "rows": out})


if __name__ == "__main__":
    # Flask 준비되면 브라우저 자동 오픈 (별도 스레드에서 소켓 폴링)
    import webbrowser as _wb, threading as _th, socket as _sk, time as _tm
    def _wait_and_open():
        for _ in range(30):
            try:
                with _sk.create_connection(("127.0.0.1", 5000), timeout=1):
                    _wb.open("http://127.0.0.1:5000")
                    return
            except OSError:
                _tm.sleep(1)
    _th.Thread(target=_wait_and_open, daemon=True).start()
    print("=" * 55)
    print("  EASP - Export Auto Shipment Planning")
    print("  http://localhost:5000")
    print("=" * 55)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)

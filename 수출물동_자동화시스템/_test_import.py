# -*- coding: utf-8 -*-
import sys, time, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import dimensions_importer as di

t0 = time.time()
result = di.import_files([
    r'C:\Users\13912\Downloads\EXPORT_20260612_160144.xlsx',
    r'C:\Users\13912\Downloads\EXPORT_20260612_160314.xlsx',
])
elapsed = time.time() - t0

print(f'=== Import 결과 (소요 {elapsed:.1f}초) ===')
print(f"총 처리 행: {result['total']:,}")
print(f"신규 INSERT: {result['inserted']:,}")
print(f"UPDATE 보강: {result['updated']:,}")
print(f"SKIP (기존):  {result['skipped']:,}")
print()
for f in result['files']:
    print(f"[{f['source']}]")
    print(f"  행수 {f['total']:,} / 신규 {f['inserted']:,} / 보강 {f['updated']:,} / skip {f['skipped']:,}")
print()

s = di.stats()
print('=== ref_dimensions.csv 통계 ===')
print(f"고유 모델: {s['total']:,}")
for d, n in sorted(s['by_dept'].items(), key=lambda x: -x[1])[:12]:
    print(f"  {n:>6,}  {d}")

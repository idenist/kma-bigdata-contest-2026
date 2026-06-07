import pandas as pd
import numpy as np
from pyproj import Transformer

# ============================================================
# 1. 데이터 로드
# ============================================================
FIRE_PATH   = "D:/prep/01. (공간데이터) 산불발생이력데이터(행안부&산림청, 2011~2025)/forest_fire_all_4326.csv"
MASTER_PATH = "D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet"
OUT_PATH    = "D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_label_master.parquet"

fire_df   = pd.read_csv(FIRE_PATH)
master_df = pd.read_parquet(MASTER_PATH)

print(f"산불 데이터: {len(fire_df)}건")
print(f"master_grid: {len(master_df)}행")

# ============================================================
# 2. 필터링: 강원도(42·51) + 봄철(2~5월) + 2020~2024
# ============================================================
fire_df['ctprvn_cd'] = pd.to_numeric(fire_df['ctprvn_cd'], errors='coerce')
fire_df['occu_year'] = pd.to_numeric(fire_df['occu_year'],  errors='coerce')
fire_df['occu_mt']   = pd.to_numeric(fire_df['occu_mt'],    errors='coerce')

mask = (
    fire_df['ctprvn_cd'].isin([42, 51]) &
    fire_df['occu_mt'].isin([2, 3, 4, 5]) &
    fire_df['occu_year'].between(2020, 2024)
)
fire = fire_df[mask].copy().reset_index(drop=True)
print(f"\n필터링 후 산불 건수: {len(fire)}건")
print(fire['occu_year'].value_counts().sort_index())

# ============================================================
# 3. 좌표 변환: WGS84(4326) → EPSG:5179
# ============================================================
transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)

xs, ys = transformer.transform(fire['longitude'].values, fire['latitude'].values)
fire['x_5179'] = xs
fire['y_5179'] = ys

fire['gx'] = np.floor(fire['x_5179'] / 100).astype(int)
fire['gy'] = np.floor(fire['y_5179'] / 100).astype(int)

# ============================================================
# 4. 500m 원형 버퍼 오프셋 목록 생성
#    100m 격자 기준 반경 5칸 = 500m
# ============================================================
BUFFER = 5
offsets = [
    (dx, dy)
    for dx in range(-BUFFER, BUFFER + 1)
    for dy in range(-BUFFER, BUFFER + 1)
    if dx ** 2 + dy ** 2 <= BUFFER ** 2
]
print(f"\n원형 버퍼 내 격자 오프셋 수: {len(offsets)}개")

# ============================================================
# 5. 버퍼 내 master_grid 격자 검색 → fire=1 목록 수집
# ============================================================
master_id_set = set(master_df['grid_id'].values)

matched_grid_ids = set()
per_fire = []

for _, row in fire.iterrows():
    gx, gy = int(row['gx']), int(row['gy'])
    hits = []
    for dx, dy in offsets:
        cid = f"{gx + dx}_{gy + dy}"
        if cid in master_id_set:
            hits.append(cid)
            matched_grid_ids.add(cid)
    per_fire.append({
        'occu_year':     row['occu_year'],
        'occu_mt':       row['occu_mt'],
        'matched_grids': len(hits)
    })

pf = pd.DataFrame(per_fire)
n_matched_fires = (pf['matched_grids'] > 0).sum()

print("\n=== 500m 버퍼 매칭 결과 ===")
print(f"총 산불 건수          : {len(fire)}건")
print(f"1개 이상 격자 매칭    : {n_matched_fires}건  ({n_matched_fires/len(fire)*100:.1f}%)")
print(f"매칭된 고유 격자 수   : {len(matched_grid_ids)}개  (fire=1 후보)")

print("\n=== 연도별 매칭 현황 ===")
print(pf.groupby('occu_year')['matched_grids'].agg(
    산불건수='count', 매칭총격자수='sum', 건당평균격자='mean'
).round(1).to_string())

# ============================================================
# 6. master_grid 에 fire_label 컬럼 추가
# ============================================================
master_df['fire_label'] = master_df['grid_id'].isin(matched_grid_ids).astype(int)

vc = master_df['fire_label'].value_counts()
print("\n=== 타겟 변수 분포 ===")
print(f"fire=0 (화재 없음): {vc.get(0, 0):,}개")
print(f"fire=1 (화재 인근): {vc.get(1, 0):,}개")
print(f"화재 격자 비율    : {master_df['fire_label'].mean()*100:.3f}%")

# ============================================================
# 7. 저장
# ============================================================
master_df.to_parquet(OUT_PATH, index=False, engine='pyarrow')
print(f"\n저장 완료: {OUT_PATH}")

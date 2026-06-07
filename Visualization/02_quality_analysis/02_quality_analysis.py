import pandas as pd
import numpy as np

df = pd.read_parquet("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet")

# ============================================================
# 1. 결측치 분석
# ============================================================

missing_count = df.isnull().sum()
missing_ratio = df.isnull().mean() * 100

missing_summary = pd.DataFrame({
    '결측치 수': missing_count,
    '결측치 비율(%)': missing_ratio.round(2)
}).sort_values('결측치 수', ascending=False)

print("=== 1. 결측치 분석 ===")
print(missing_summary[missing_summary['결측치 수'] > 0].to_string())

# ============================================================
# 2. 이상치 분석 (IQR)
# ============================================================

outlier_cols = ['pole_count', 'elevation', 'slope', 'nearest_road_dist', 'nearest_river_dist']

print("\n=== 2. 이상치 분석 (IQR 기준) ===")

outlier_summary = []
for col in outlier_cols:
    Q1 = df[col].quantile(0.25)
    Q3 = df[col].quantile(0.75)
    IQR = Q3 - Q1
    lower = Q1 - 1.5 * IQR
    upper = Q3 + 1.5 * IQR
    outlier_count = ((df[col] < lower) | (df[col] > upper)).sum()
    outlier_ratio = outlier_count / len(df) * 100
    outlier_summary.append({
        '컬럼': col,
        'Q1': round(Q1, 3),
        'Q3': round(Q3, 3),
        'IQR': round(IQR, 3),
        '하한': round(lower, 3),
        '상한': round(upper, 3),
        '이상치 수': outlier_count,
        '이상치 비율(%)': round(outlier_ratio, 2)
    })

outlier_df = pd.DataFrame(outlier_summary)
print(outlier_df.to_string(index=False))

# ============================================================
# 3. 중복 분석
# ============================================================

print("\n=== 3. 중복 분석 ===")
print(f"전체 행 기준 중복: {df.duplicated().sum()}건")
print(f"grid_id 기준 중복: {df.duplicated(subset='grid_id').sum()}건")

import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

df = pd.read_parquet("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet")

num_cols = [
    'pole_count', 'elevation', 'slope',
    'aspect_sin', 'aspect_cos',
    'nearest_road_dist', 'nearest_river_dist',
    'age_class_code', 'tree_height_code'
]

# ============================================================
# 1. 분포 통계량 테이블 (평균, 중앙값, 왜도, 첨도)
# ============================================================
records = []
for col in num_cols:
    data = df[col].dropna().astype(float)
    records.append({
        '컬럼': col,
        '평균': round(data.mean(), 3),
        '중앙값': round(data.median(), 3),
        '왜도': round(float(stats.skew(data)), 3),
        '첨도': round(float(stats.kurtosis(data)), 3),
        '왜도 해석': '우편향' if stats.skew(data) > 0.5
                    else ('좌편향' if stats.skew(data) < -0.5 else '대칭')
    })

dist_df = pd.DataFrame(records).set_index('컬럼')
print("=== 분포 통계량 ===")
print(dist_df.to_string())

# ============================================================
# 2. 수치형 변수 히스토그램
# ============================================================
fig, axes = plt.subplots(3, 3, figsize=(16, 12))
fig.suptitle('Variable Distribution - Histogram', fontsize=14, fontweight='bold')

for ax, col in zip(axes.flatten(), num_cols):
    data = df[col].dropna().astype(float)
    skew_val = round(float(stats.skew(data)), 2)

    ax.hist(data, bins=50, color='steelblue', alpha=0.75, edgecolor='white')
    ax.axvline(data.mean(), color='tomato', linestyle='--', linewidth=1.5, label=f'평균: {data.mean():.2f}')
    ax.axvline(data.median(), color='orange', linestyle='-', linewidth=1.5, label=f'중앙값: {data.median():.2f}')

    bias = '우편향' if skew_val > 0.5 else ('좌편향' if skew_val < -0.5 else '대칭')
    ax.set_title(f'{col}\n왜도: {skew_val}  ({bias})', fontsize=9)
    ax.set_xlabel(col, fontsize=8)
    ax.set_ylabel('Count', fontsize=8)
    ax.legend(fontsize=7)

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/03_distribution_hist.png",
            dpi=150, bbox_inches='tight')
plt.show()

# ============================================================
# 3. 범주형 변수 불균형 확인
# ============================================================
cat_cols = {
    'is_forest': {0: '비산림', 1: '산림'},
    'forest_type_code': {0: '무립목', 1: '침엽', 2: '활엽', 3: '혼효', 4: '죽림'},
    'forest_origin_code': {0: '미분류', 1: '인공림', 2: '천연림'}
}

fig2, axes2 = plt.subplots(1, 3, figsize=(15, 5))
fig2.suptitle('Categorical Variable Distribution (Imbalance Check)', fontsize=13, fontweight='bold')

for ax, (col, label_map) in zip(axes2, cat_cols.items()):
    counts = df[col].value_counts().sort_index()
    labels = [label_map.get(k, str(k)) for k in counts.index]
    ratios = (counts / counts.sum() * 100).round(1)

    bars = ax.bar(labels, counts.values, color='steelblue', alpha=0.75, edgecolor='white')
    for bar, ratio in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 300,
                f'{ratio}%', ha='center', fontsize=9)
    ax.set_title(col, fontsize=11)
    ax.set_ylabel('Count')
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/03_distribution_cat.png",
            dpi=150, bbox_inches='tight')
plt.show()
print("저장 완료: 03_distribution_hist.png, 03_distribution_cat.png")

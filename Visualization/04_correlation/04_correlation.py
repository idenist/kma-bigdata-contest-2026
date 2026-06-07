import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

df = pd.read_parquet("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet")

num_cols = [
    'pole_count', 'is_forest',
    'elevation', 'slope', 'aspect_sin', 'aspect_cos',
    'nearest_road_dist', 'nearest_river_dist',
    'forest_type_code', 'age_class_code', 'tree_height_code'
]

# ============================================================
# Pearson 상관계수 행렬
# ============================================================
corr = df[num_cols].astype(float).corr(method='pearson')

print("=== Pearson 상관계수 행렬 ===")
print(corr.round(3).to_string())

# 상관계수 절댓값 0.3 이상 쌍 출력
print("\n=== 주목할 상관관계 (|r| >= 0.3) ===")
pairs = (
    corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    .stack()
    .reset_index()
)
pairs.columns = ['변수1', '변수2', '상관계수']
pairs['절댓값'] = pairs['상관계수'].abs()
notable = pairs[pairs['절댓값'] >= 0.3].sort_values('절댓값', ascending=False)
print(notable[['변수1', '변수2', '상관계수']].to_string(index=False))

# ============================================================
# 히트맵 시각화
# ============================================================
fig, ax = plt.subplots(figsize=(12, 10))

mask = np.triu(np.ones_like(corr, dtype=bool), k=1)

sns.heatmap(
    corr,
    annot=True,
    fmt='.2f',
    cmap='RdBu_r',
    center=0,
    vmin=-1, vmax=1,
    mask=mask,
    square=True,
    linewidths=0.5,
    ax=ax,
    annot_kws={'size': 9}
)

ax.set_title('Pearson Correlation Heatmap - prep_master_grid', fontsize=13, fontweight='bold', pad=15)
plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/04_correlation_heatmap.png",
            dpi=150, bbox_inches='tight')
plt.show()
print("\n저장 완료: 04_correlation_heatmap.png")

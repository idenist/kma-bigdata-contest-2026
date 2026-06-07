import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

BASE = "D:/prep/003. 전처리 데이터(preprocessing_data)/prep"

df = pd.read_parquet(f"{BASE}/05_fire_label_master.parquet")

num_cols = [
    'pole_count', 'is_forest',
    'elevation', 'slope', 'aspect_sin', 'aspect_cos',
    'nearest_road_dist', 'nearest_river_dist',
    'forest_type_code', 'age_class_code', 'tree_height_code',
    'fire_label'
]

# ============================================================
# Pearson 상관계수 행렬
# ============================================================
corr = df[num_cols].astype(float).corr(method='pearson')

print("=== Pearson 상관계수 행렬 ===")
print(corr.round(3).to_string())

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

print("\n=== fire_label과의 상관관계 (절댓값 순) ===")
target_corr = corr['fire_label'].drop('fire_label').abs().sort_values(ascending=False)
print(target_corr.round(4).to_string())

# ============================================================
# Figure 1: 전체 상관계수 히트맵 (fire_label 포함)
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(20, 8),
                         gridspec_kw={'width_ratios': [2, 1]})

mask = np.triu(np.ones_like(corr, dtype=bool), k=1)

# fire_label 행·열 강조를 위한 색상 배열
annot_kws = {'size': 8}
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
    ax=axes[0],
    annot_kws=annot_kws
)
axes[0].set_title('Pearson Correlation Heatmap\n(타겟변수 fire_label 포함)',
                  fontsize=12, fontweight='bold', pad=12)

# fire_label 행·열 테두리 강조
fl_idx = num_cols.index('fire_label')
axes[0].add_patch(plt.Rectangle(
    (0, fl_idx), len(num_cols), 1,
    fill=False, edgecolor='black', lw=2.5, clip_on=False
))
axes[0].add_patch(plt.Rectangle(
    (fl_idx, 0), 1, len(num_cols),
    fill=False, edgecolor='black', lw=2.5, clip_on=False
))

# ============================================================
# Figure 2: fire_label과의 개별 상관계수 바 차트
# ============================================================
target_corr_signed = corr['fire_label'].drop('fire_label').sort_values()
colors = ['#d73027' if v > 0 else '#4575b4' for v in target_corr_signed]

bars = axes[1].barh(target_corr_signed.index, target_corr_signed.values,
                    color=colors, edgecolor='white', height=0.6)
axes[1].axvline(0, color='black', linewidth=0.8)
axes[1].set_xlabel('Pearson r', fontsize=10)
axes[1].set_title('fire_label 상관계수\n(특징별)', fontsize=12, fontweight='bold', pad=12)
axes[1].set_xlim(-0.35, 0.35)

for bar, val in zip(bars, target_corr_signed.values):
    x = val + (0.008 if val >= 0 else -0.008)
    ha = 'left' if val >= 0 else 'right'
    axes[1].text(x, bar.get_y() + bar.get_height() / 2,
                 f'{val:.4f}', va='center', ha=ha, fontsize=8)

plt.tight_layout()
plt.savefig(f"{BASE}/04_correlation/04_correlation_heatmap.png", dpi=150, bbox_inches='tight')
plt.show()
print("\n저장 완료: 04_correlation_heatmap.png")

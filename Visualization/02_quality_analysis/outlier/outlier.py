import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import matplotlib

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

df = pd.read_parquet("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet")

outlier_cols = ['pole_count', 'elevation', 'slope', 'nearest_road_dist', 'nearest_river_dist']

# IQR 경계값 계산
def iqr_bounds(series):
    Q1 = series.quantile(0.25)
    Q3 = series.quantile(0.75)
    IQR = Q3 - Q1
    return Q1 - 1.5 * IQR, Q3 + 1.5 * IQR

# ============================================================
# Figure 1: Boxplot (5개 컬럼 한눈에)
# ============================================================
fig1, axes = plt.subplots(1, 5, figsize=(18, 5))
fig1.suptitle('Outlier Analysis - Boxplot (IQR)', fontsize=14, fontweight='bold')

for ax, col in zip(axes, outlier_cols):
    ax.boxplot(df[col].dropna(), vert=True, patch_artist=True,
               boxprops=dict(facecolor='steelblue', alpha=0.6),
               flierprops=dict(marker='.', color='tomato', markersize=2, alpha=0.3))
    lower, upper = iqr_bounds(df[col].dropna())
    out_count = ((df[col] < lower) | (df[col] > upper)).sum()
    out_ratio = out_count / len(df) * 100
    ax.set_title(f'{col}\n이상치: {out_count:,}건 ({out_ratio:.2f}%)', fontsize=9)
    ax.set_ylabel(col, fontsize=8)

plt.tight_layout()
fig1.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/na_outlier/outlier_boxplot.png",
             dpi=150, bbox_inches='tight')
plt.show()

# ============================================================
# Figure 2: 주요 이상치 컬럼 분포 상세 (pole_count, nearest_river_dist)
# ============================================================
fig2, axes = plt.subplots(1, 2, figsize=(14, 5))
fig2.suptitle('Outlier Detail - Distribution (pole_count / nearest_river_dist)',
              fontsize=13, fontweight='bold')

for ax, col, color in zip(axes,
                           ['pole_count', 'nearest_river_dist'],
                           ['steelblue', 'seagreen']):
    data = df[col].dropna()
    lower, upper = iqr_bounds(data)

    ax.hist(data, bins=60, color=color, alpha=0.7, edgecolor='white')
    ax.axvline(upper, color='tomato', linestyle='--', linewidth=1.5,
               label=f'IQR 상한: {upper:.1f}')
    ax.axvline(data.median(), color='orange', linestyle='-', linewidth=1.5,
               label=f'중앙값: {data.median():.1f}')
    out_count = ((data < lower) | (data > upper)).sum()
    out_ratio = out_count / len(df) * 100
    ax.set_title(f'{col}  (이상치: {out_count:,}건, {out_ratio:.2f}%)', fontsize=11)
    ax.set_xlabel(col)
    ax.set_ylabel('Count')
    ax.legend(fontsize=9)

plt.tight_layout()
fig2.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/na_outlier/outlier_dist.png",
             dpi=150, bbox_inches='tight')
plt.show()

print("저장 완료: outlier_boxplot.png, outlier_dist.png")

import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

# ============================================================
# 1. 데이터 로드 및 필터링
# ============================================================
FIRE_PATH = "D:/prep/01. (공간데이터) 산불발생이력데이터(행안부&산림청, 2011~2025)/forest_fire_all_4326.csv"
fire_df = pd.read_csv(FIRE_PATH)

fire = fire_df[
    fire_df['ctprvn_cd'].isin([42, 51]) &
    fire_df['occu_mt'].isin([2, 3, 4, 5]) &
    fire_df['occu_year'].between(2020, 2024)
].copy().reset_index(drop=True)

fire['amount_ha'] = fire['amount'] / 10000
print(f"분석 대상: {len(fire)}건 (강원도, 봄철 2~5월, 2020~2024)")

# ============================================================
# 2. Figure 1: 연도별 · 월별 산불 건수 분포
# ============================================================
year_cnt  = fire.groupby('occu_year').size()
month_cnt = fire.groupby('occu_mt').size().reindex([2, 3, 4, 5])
month_labels = {2: '2월', 3: '3월', 4: '4월', 5: '5월'}

fig1, axes = plt.subplots(1, 2, figsize=(13, 5))
fig1.suptitle('강원도 봄철 산불 발생 분포 (2020~2024)', fontsize=14, fontweight='bold')

# 연도별
ax = axes[0]
bars = ax.bar(year_cnt.index.astype(str), year_cnt.values,
              color='#E07B54', edgecolor='white', linewidth=0.8)
for bar, v in zip(bars, year_cnt.values):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
            str(v), ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('연도별 산불 건수', fontsize=12)
ax.set_xlabel('연도')
ax.set_ylabel('건수')
ax.set_ylim(0, year_cnt.max() * 1.2)
ax.grid(axis='y', linestyle='--', alpha=0.4)

# 월별
ax = axes[1]
month_x = [month_labels[m] for m in month_cnt.index]
bars = ax.bar(month_x, month_cnt.values,
              color='#5B8DB8', edgecolor='white', linewidth=0.8)
for bar, v in zip(bars, month_cnt.values):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
            str(v), ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('월별 산불 건수', fontsize=12)
ax.set_xlabel('월')
ax.set_ylabel('건수')
ax.set_ylim(0, month_cnt.max() * 1.2)
ax.grid(axis='y', linestyle='--', alpha=0.4)

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_yearly_monthly.png",
            dpi=150, bbox_inches='tight')
plt.close()
print("저장: 05_fire_yearly_monthly.png")

# ============================================================
# 3. Figure 2: 피해면적 분포 (로그 스케일)
# ============================================================
ha = fire['amount_ha']

print(f"\n=== 피해면적 기초통계 (ha) ===")
print(f"건수    : {len(ha)}")
print(f"최솟값  : {ha.min():.2f} ha")
print(f"중앙값  : {ha.median():.2f} ha")
print(f"평균    : {ha.mean():.2f} ha")
print(f"최댓값  : {ha.max():.2f} ha")
print(f"1 ha 미만: {(ha < 1).sum()}건")
print(f"100 ha 이상: {(ha >= 100).sum()}건")
print(f"1,000 ha 이상: {(ha >= 1000).sum()}건")

log_ha = np.log10(ha[ha > 0])

fig2, axes = plt.subplots(1, 2, figsize=(13, 5))
fig2.suptitle('강원도 봄철 산불 피해면적 분포 (2020~2024)', fontsize=14, fontweight='bold')

# 로그 히스토그램
ax = axes[0]
ax.hist(log_ha, bins=25, color='#C85250', edgecolor='white', linewidth=0.6, alpha=0.85)
ax.set_xlabel('log10(피해면적, ha)', fontsize=11)
ax.set_ylabel('건수', fontsize=11)
ax.set_title('피해면적 분포 (로그 스케일)', fontsize=12)
ax.xaxis.set_major_formatter(mticker.FuncFormatter(
    lambda v, _: f'10^{v:.0f} ({10**v:.0f}ha)' if v == round(v) else ''))
ax.grid(axis='y', linestyle='--', alpha=0.4)

# 구간별 누적 비율
ax = axes[1]
bins_ha    = [0, 1, 10, 100, 1000, 1e9]
bin_labels = ['~1 ha', '1~10 ha', '10~100 ha', '100~1,000 ha', '1,000 ha~']
bin_counts = pd.cut(ha, bins=bins_ha, labels=bin_labels, right=False).value_counts()
bin_counts = bin_counts.reindex(bin_labels)
bars = ax.bar(bin_labels, bin_counts.values,
              color='#C85250', edgecolor='white', linewidth=0.8, alpha=0.85)
for bar, v in zip(bars, bin_counts.values):
    pct = v / len(ha) * 100
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
            f'{v}건\n({pct:.1f}%)', ha='center', va='bottom', fontsize=9)
ax.set_title('피해면적 구간별 건수', fontsize=12)
ax.set_xlabel('피해면적 구간')
ax.set_ylabel('건수')
ax.set_ylim(0, bin_counts.max() * 1.35)
ax.tick_params(axis='x', labelsize=9)
ax.grid(axis='y', linestyle='--', alpha=0.4)

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_damage_area.png",
            dpi=150, bbox_inches='tight')
plt.close()
print("저장: 05_fire_damage_area.png")

# ============================================================
# 4. Figure 3: 발생원인 분포 (상위 8개 + 기타)
# ============================================================
resn_cnt = fire['resn'].value_counts()
top8     = resn_cnt.head(8)
others   = pd.Series({'기타': resn_cnt.iloc[8:].sum()})
resn_plot = pd.concat([top8, others])

fig3, ax = plt.subplots(figsize=(12, 6))
fig3.suptitle('강원도 봄철 산불 발생원인 분포 (2020~2024)', fontsize=14, fontweight='bold')

colors = ['#4472C4'] * 8 + ['#A5A5A5']
bars = ax.bar(range(len(resn_plot)), resn_plot.values,
              color=colors[:len(resn_plot)], edgecolor='white', linewidth=0.7)
for bar, v in zip(bars, resn_plot.values):
    pct = v / len(fire) * 100
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
            f'{v}건\n({pct:.1f}%)', ha='center', va='bottom', fontsize=9)
ax.set_xticks(range(len(resn_plot)))
ax.set_xticklabels(resn_plot.index, rotation=30, ha='right', fontsize=9)
ax.set_ylabel('건수')
ax.set_ylim(0, resn_plot.max() * 1.3)
ax.grid(axis='y', linestyle='--', alpha=0.4)

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_cause.png",
            dpi=150, bbox_inches='tight')
plt.close()
print("저장: 05_fire_cause.png")

print("\n=== 전체 완료 ===")

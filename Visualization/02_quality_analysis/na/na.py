import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

df = pd.read_parquet("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/prep_master_grid.parquet")

# 결측치 수 / 비율 집계
missing = pd.DataFrame({
    '결측치 수': df.isnull().sum(),
    '결측치 비율(%)': (df.isnull().mean() * 100).round(2)
}).query('`결측치 수` > 0').sort_values('결측치 비율(%)', ascending=True)

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
fig.suptitle('Missing Value Analysis - prep_master_grid', fontsize=14, fontweight='bold')

# 결측치 수 (좌)
bars1 = axes[0].barh(missing.index, missing['결측치 수'], color='steelblue')
axes[0].set_title('Missing Value Count')
axes[0].set_xlabel('Count')
axes[0].xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
for bar, val in zip(bars1, missing['결측치 수']):
    axes[0].text(bar.get_width() + 500, bar.get_y() + bar.get_height() / 2,
                 f'{val:,}', va='center', fontsize=9)

# 결측치 비율 (우)
bars2 = axes[1].barh(missing.index, missing['결측치 비율(%)'], color='tomato')
axes[1].set_title('Missing Value Ratio (%)')
axes[1].set_xlabel('Ratio (%)')
axes[1].set_xlim(0, 40)
for bar, val in zip(bars2, missing['결측치 비율(%)']):
    axes[1].text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                 f'{val}%', va='center', fontsize=9)

plt.tight_layout()
plt.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/na_outlier/na_analysis.png",
            dpi=150, bbox_inches='tight')
plt.show()
print("저장 완료: na_analysis.png")

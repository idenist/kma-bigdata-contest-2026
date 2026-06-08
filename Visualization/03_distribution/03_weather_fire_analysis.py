import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import os

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

BASE_W    = "D:/prep/003. 전처리 데이터(preprocessing_data)/raw/grid_date_master"
FIRE_PATH = "D:/prep/01. (공간데이터) 산불발생이력데이터(행안부&산림청, 2011~2025)/forest_fire_all_4326.csv"
OUT_DIR   = "D:/prep/003. 전처리 데이터(preprocessing_data)/prep/03_distribution"

# 1. 산불 발생 날짜 목록 (강원도 봄철 2020~2024)
fire_df = pd.read_csv(FIRE_PATH)
fire_gw = fire_df[
    fire_df['ctprvn_cd'].isin([42, 51]) &
    fire_df['occu_mt'].isin([2, 3, 4, 5]) &
    fire_df['occu_year'].between(2020, 2024)
].copy()
fire_dates = set(pd.to_datetime(fire_gw['occu_date']).dt.strftime('%Y-%m-%d'))
print(f"산불 발생 고유 날짜: {len(fire_dates)}일")

# 2. 기상 데이터 일별 집계 (전체 격자 평균, 2020~2024 봄철)
months = [f"month={y}-{m:02d}" for y in range(2020, 2025) for m in [2, 3, 4, 5]]
daily_rows = []

for m in months:
    path = os.path.join(BASE_W, m)
    df = pd.read_parquet(path, engine='fastparquet',
                         columns=['date', 'ta_mean', 'hm_mean', 'wind_ws_mean', 'rn_day_mean'])
    agg = df.groupby('date').agg(
        ta_mean      = ('ta_mean',      'mean'),
        hm_mean      = ('hm_mean',      'mean'),
        wind_ws_mean = ('wind_ws_mean', 'mean'),
        rn_day_mean  = ('rn_day_mean',  'mean'),
    ).reset_index()
    daily_rows.append(agg)
    print(f"  {m} 완료")

daily = pd.concat(daily_rows, ignore_index=True)
daily['date'] = pd.to_datetime(daily['date']).dt.strftime('%Y-%m-%d')

for col in ['ta_mean', 'hm_mean', 'wind_ws_mean', 'rn_day_mean']:
    daily[col] = daily[col] / 10

daily = daily.sort_values('date').reset_index(drop=True)
daily['fire_day'] = daily['date'].isin(fire_dates).astype(int)

print(f"\n전체 봄철 일수: {len(daily)}일")
print(f"산불 발생일: {daily['fire_day'].sum()}일  |  미발생일: {(daily['fire_day']==0).sum()}일")

# 3. Figure 1: 산불 발생일 vs 미발생일 기상 비교
vars_info = [
    ('ta_mean',      '기온 (C)',    '#E07B54'),
    ('hm_mean',      '상대습도 (%)', '#5B8DB8'),
    ('wind_ws_mean', '풍속 (m/s)',  '#4CAF50'),
    ('rn_day_mean',  '강수량 (mm)', '#9B59B6'),
]

fire_data   = daily[daily['fire_day'] == 1]
nofire_data = daily[daily['fire_day'] == 0]

fig1, axes = plt.subplots(1, 4, figsize=(16, 6))
fig1.suptitle('산불 발생일 vs 미발생일 기상 조건 비교 (강원도 봄철 2020~2024)',
              fontsize=13, fontweight='bold', y=1.01)

for ax, (col, label, color) in zip(axes, vars_info):
    data_f  = fire_data[col].dropna()
    data_nf = nofire_data[col].dropna()

    bp = ax.boxplot(
        [data_nf, data_f],
        labels=['미발생일\n(n={})'.format(len(data_nf)),
                '발생일\n(n={})'.format(len(data_f))],
        patch_artist=True,
        medianprops=dict(color='black', linewidth=2),
        whiskerprops=dict(linewidth=1.2),
        capprops=dict(linewidth=1.2),
        flierprops=dict(marker='o', markersize=3, alpha=0.4),
        widths=0.45
    )
    bp['boxes'][0].set_facecolor('#D0D0D0')
    bp['boxes'][1].set_facecolor(color)
    bp['boxes'][1].set_alpha(0.85)

    med_nf = np.median(data_nf)
    med_f  = np.median(data_f)
    diff   = med_f - med_nf
    sign   = '+' if diff >= 0 else ''
    ax.set_title(f'{label}\n중앙값 차이: {sign}{diff:.2f}', fontsize=10)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    ax.tick_params(labelsize=9)

plt.tight_layout()
plt.savefig(f"{OUT_DIR}/03_weather_fire_compare.png", dpi=150, bbox_inches='tight')
plt.close()
print("저장: 03_weather_fire_compare.png")

# 4. Figure 2: 연속 무강수일 분석
daily_s = daily.sort_values('date').reset_index(drop=True)
daily_s['dry'] = (daily_s['rn_day_mean'] < 1.0).astype(int)

streak = 0
dry_streak = []
for d in daily_s['dry']:
    streak = streak + 1 if d == 1 else 0
    dry_streak.append(streak)
daily_s['dry_streak'] = dry_streak

fire_streak   = daily_s[daily_s['fire_day'] == 1]['dry_streak']
nofire_streak = daily_s[daily_s['fire_day'] == 0]['dry_streak']

print(f"\n=== 연속 무강수일 통계 ===")
print(f"미발생일  중앙값: {nofire_streak.median():.1f}일  평균: {nofire_streak.mean():.1f}일")
print(f"발생일    중앙값: {fire_streak.median():.1f}일  평균: {fire_streak.mean():.1f}일")

bins     = [0, 3, 7, 14, 21, 999]
labels_b = ['1~3일', '4~7일', '8~14일', '15~21일', '22일+']
daily_s['streak_bin'] = pd.cut(
    daily_s['dry_streak'].clip(lower=1),
    bins=bins, labels=labels_b, right=True
)
group = daily_s.groupby('streak_bin', observed=True).agg(
    total     = ('fire_day', 'count'),
    fire_days = ('fire_day', 'sum')
).reset_index()
group['fire_rate'] = group['fire_days'] / group['total'] * 100

print("\n=== 연속 무강수일 구간별 산불 발생률 ===")
print(group.to_string(index=False))

fig2, axes2 = plt.subplots(1, 2, figsize=(14, 6))
fig2.suptitle('연속 무강수일과 산불 발생 관계 (강원도 봄철 2020~2024)',
              fontsize=13, fontweight='bold')

ax = axes2[0]
bp2 = ax.boxplot(
    [nofire_streak, fire_streak],
    labels=['미발생일\n(n={})'.format(len(nofire_streak)),
            '발생일\n(n={})'.format(len(fire_streak))],
    patch_artist=True,
    medianprops=dict(color='black', linewidth=2),
    flierprops=dict(marker='o', markersize=3, alpha=0.4),
    widths=0.45
)
bp2['boxes'][0].set_facecolor('#D0D0D0')
bp2['boxes'][1].set_facecolor('#E07B54')
bp2['boxes'][1].set_alpha(0.85)
ax.set_ylabel('연속 무강수일 수', fontsize=11)
ax.set_title(
    f'연속 무강수일 분포\n발생일 중앙값 {fire_streak.median():.0f}일 vs 미발생일 {nofire_streak.median():.0f}일',
    fontsize=10
)
ax.grid(axis='y', linestyle='--', alpha=0.4)

ax = axes2[1]
med_rate   = group['fire_rate'].median()
colors_bar = ['#E07B54' if r >= med_rate else '#D0D0D0' for r in group['fire_rate']]
bars = ax.bar(group['streak_bin'].astype(str), group['fire_rate'],
              color=colors_bar, edgecolor='white', linewidth=0.7)
for bar, row in zip(bars, group.itertuples()):
    ax.text(bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.3,
            f"{row.fire_rate:.1f}%\n({int(row.fire_days)}/{int(row.total)}일)",
            ha='center', va='bottom', fontsize=9)
ax.set_xlabel('연속 무강수일 구간', fontsize=11)
ax.set_ylabel('산불 발생일 비율 (%)', fontsize=11)
ax.set_title('구간별 산불 발생률', fontsize=10)
ax.set_ylim(0, group['fire_rate'].max() * 1.45)
ax.grid(axis='y', linestyle='--', alpha=0.4)

plt.tight_layout()
plt.savefig(f"{OUT_DIR}/03_weather_dry_spell.png", dpi=150, bbox_inches='tight')
plt.close()
print("저장: 03_weather_dry_spell.png")
print("\n=== 전체 완료 ===")
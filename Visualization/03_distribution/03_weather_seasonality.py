import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

RAW_PATH = "D:/prep/003. 전처리 데이터(preprocessing_data)/raw/grid_date_master"

MONTHS = [
    '2020-02', '2020-03', '2020-04', '2020-05',
    '2021-02', '2021-03', '2021-04', '2021-05',
    '2022-02', '2022-03', '2022-04', '2022-05',
    '2023-02', '2023-03', '2023-04', '2023-05',
    '2024-02', '2024-03', '2024-04', '2024-05',
    '2025-02', '2025-03', '2025-04', '2025-05',
]

# ============================================================
# 1. 월별 일평균 집계 (전 격자 공간 평균)
# ============================================================
records = []
for m in MONTHS:
    print(f"로드 중: {m}", end='\r')
    df = pd.read_parquet(
        RAW_PATH,
        filters=[('month', '==', m)],
        engine='fastparquet',
        columns=['date', 'ta_mean', 'hm_mean', 'wind_ws_mean', 'rn_day_mean']
    )
    daily = df.groupby('date')[['ta_mean', 'hm_mean', 'wind_ws_mean', 'rn_day_mean']].mean()
    daily['month'] = m
    records.append(daily)

agg = pd.concat(records).reset_index()
agg['date'] = pd.to_datetime(agg['date'])

# 스케일 변환 (원본값 ÷10)
# wind_ws_mean: 벡터 합성 및 최댓값 분포 검증 결과 ÷10 확인 (2026-06-07)
agg['ta_mean']       = agg['ta_mean'] / 10
agg['hm_mean']       = agg['hm_mean'] / 10
agg['wind_ws_mean']  = agg['wind_ws_mean'] / 10
agg['rn_day_mean']   = agg['rn_day_mean'] / 10

agg['year']       = agg['date'].dt.year
agg['month_num']  = agg['date'].dt.month
agg['month_name'] = agg['date'].dt.month.map({2:'2월', 3:'3월', 4:'4월', 5:'5월'})

print(f"\n집계 완료: {len(agg)}행")
print(agg[['date','ta_mean','hm_mean','wind_ws_mean','rn_day_mean']].describe().round(2))

# ============================================================
# 2. 시계열: 연도별 일평균 추이
# ============================================================
fig1, axes = plt.subplots(4, 1, figsize=(14, 14), sharex=True)
fig1.suptitle('기상 변수 일평균 시계열 (2020~2025 봄철)', fontsize=14, fontweight='bold')

vars_info = [
    ('ta_mean',       '기온 (°C)',       'tomato'),
    ('hm_mean',       '상대습도 (%)',     'steelblue'),
    ('wind_ws_mean',  '풍속 (m/s)',       'seagreen'),
    ('rn_day_mean',   '강수량 (mm)',      'mediumpurple'),
]

for ax, (col, label, color) in zip(axes, vars_info):
    ax.plot(agg['date'], agg[col], color=color, linewidth=0.8, alpha=0.85)
    ax.set_ylabel(label, fontsize=10)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    for yr in range(2020, 2026):
        ax.axvline(pd.Timestamp(f'{yr}-02-01'), color='gray', linestyle=':', linewidth=0.7, alpha=0.5)

axes[-1].set_xlabel('날짜', fontsize=10)
plt.tight_layout()
fig1.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/04_weather_timeseries.png",
             dpi=150, bbox_inches='tight')
plt.show()

# ============================================================
# 3. 계절성: 월별 분포 박스플롯
# ============================================================
month_order = ['2월', '3월', '4월', '5월']

fig2, axes = plt.subplots(2, 2, figsize=(13, 9))
fig2.suptitle('기상 변수 월별 분포 (계절성, 2020~2025)', fontsize=13, fontweight='bold')

for ax, (col, label, color) in zip(axes.flatten(), vars_info):
    data_by_month = [agg[agg['month_name'] == m][col].dropna().values for m in month_order]
    bp = ax.boxplot(data_by_month, labels=month_order, patch_artist=True,
                    boxprops=dict(facecolor=color, alpha=0.5),
                    medianprops=dict(color='black', linewidth=1.5),
                    flierprops=dict(marker='.', markersize=3, alpha=0.4))
    ax.set_title(label, fontsize=11)
    ax.set_xlabel('월')
    ax.set_ylabel(label, fontsize=9)
    ax.grid(axis='y', linestyle='--', alpha=0.4)

plt.tight_layout()
fig2.savefig("D:/prep/003. 전처리 데이터(preprocessing_data)/prep/04_weather_monthly.png",
             dpi=150, bbox_inches='tight')
plt.show()

print("저장 완료: 04_weather_timeseries.png, 04_weather_monthly.png")

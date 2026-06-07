import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

# ============================================================
# 1. 데이터 로드
# ============================================================
df = pd.read_parquet(
    "D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_label_master.parquet",
    columns=['grid_id', 'fire_label']
)

# grid_id → 중심 좌표 복원 (EPSG:5179, 단위: m)
df[['gx', 'gy']] = df['grid_id'].str.split('_', expand=True).astype(int)
df['cx'] = df['gx'] * 100 + 50
df['cy'] = df['gy'] * 100 + 50

fire0 = df[df['fire_label'] == 0]
fire1 = df[df['fire_label'] == 1]

print(f"fire=0: {len(fire0):,}개 | fire=1: {len(fire1):,}개")

# ============================================================
# 2. 시각화
# ============================================================
fig, ax = plt.subplots(figsize=(10, 12))

# fire=0: 너무 많으므로 랜덤 5만개 샘플로 배경 표시
sample0 = fire0.sample(n=min(50000, len(fire0)), random_state=42)
ax.scatter(sample0['cx'], sample0['cy'],
           c='#CCCCCC', s=0.3, alpha=0.4, linewidths=0, label='fire=0 (샘플 5만)')

# fire=1: 전체 표시
ax.scatter(fire1['cx'], fire1['cy'],
           c='#D62728', s=4, alpha=0.7, linewidths=0, label=f'fire=1 ({len(fire1):,}개)')

ax.set_title('강원도 전력설비 격자 화재 레이블 분포\n(500m 버퍼 매칭, 2020~2024 봄철)',
             fontsize=13, fontweight='bold')
ax.set_xlabel('X 좌표 (EPSG:5179, m)', fontsize=10)
ax.set_ylabel('Y 좌표 (EPSG:5179, m)', fontsize=10)
ax.legend(fontsize=10, loc='upper right')
ax.set_aspect('equal')
ax.grid(linestyle='--', alpha=0.3)

ax.xaxis.set_major_formatter(
    matplotlib.ticker.FuncFormatter(lambda v, _: f'{v/1e6:.2f}M'))
ax.yaxis.set_major_formatter(
    matplotlib.ticker.FuncFormatter(lambda v, _: f'{v/1e6:.2f}M'))

plt.tight_layout()
plt.savefig(
    "D:/prep/003. 전처리 데이터(preprocessing_data)/prep/05_fire_label_map.png",
    dpi=150, bbox_inches='tight'
)
plt.close()
print("저장 완료: 05_fire_label_map.png")

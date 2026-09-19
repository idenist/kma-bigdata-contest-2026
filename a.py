import pandas as pd
from pathlib import Path

path = Path("output/risk/pole_risk_score.csv")
backup_path = Path("output/risk/pole_risk_score_full_backup.csv")

keep_cols = [
    "pole_id",
    "lon",
    "lat",
    "pole_risk_score",
    "pole_risk_pct",
    "decision",
]

df = pd.read_csv(path)

# 백업 저장
df.to_csv(backup_path, index=False, encoding="utf-8-sig")

# 필요한 컬럼만 남기기
missing = [c for c in keep_cols if c not in df.columns]
if missing:
    raise ValueError(f"없는 컬럼: {missing}")

df_out = df[keep_cols]
df_out.to_csv(path, index=False, encoding="utf-8-sig")

print("완료")
print("원본 백업:", backup_path)
print("최종 컬럼:", df_out.columns.tolist())
print("row 수:", len(df_out))
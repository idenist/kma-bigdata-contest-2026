import math
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import mysql.connector
from dotenv import load_dotenv

"""
forest_fmi.csv 데이터를 MySQL에 적재하는 스크립트

규칙:
1. forest_fmi.csv 전체 562,276행 적재 (FMI == 0 포함, 분석 쿼리에서 WHERE FMI > 0 사용)
2. 소수점 형태 정수값(2.0 → 2) 변환
3. 결측값(NaN) → NULL 처리
4. 중복 기본키는 ON DUPLICATE KEY UPDATE로 갱신
"""

load_dotenv()

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "1234"),
    "database": os.getenv("MYSQL_DATABASE", "kma_fire_risk"),
}

BASE_DIR = Path(__file__).resolve().parent.parent
FOREST_FMI_CSV = Path(os.getenv("FOREST_FMI_CSV", BASE_DIR / "forest_fmi.csv"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50000"))

TABLE_NAME = "forest_fmi"

COLUMNS = [
    "polygon_id",
    "STORUNST",
    "FROR_CD",
    "FRTP_CD",
    "FRTP_NM",
    "FMI",
    "KOFTR_NM",
    "AGCLS_CD",
    "AGCLS_NM",
    "DMCLS_CD",
    "DMCLS_NM",
    "DNST_CD",
    "DNST_NM",
    "HEIGHT",
    "HEIGHT_NM",
    "갱신년도",
    "Shape_Area",
    "centroid_lon",
    "centroid_lat",
]

PK = ["polygon_id"]

INT_COLUMNS = {"polygon_id", "STORUNST", "FROR_CD", "FRTP_CD", "FMI", "AGCLS_CD", "DMCLS_CD", "HEIGHT", "갱신년도"}
FLOAT_COLUMNS = {"Shape_Area", "centroid_lon", "centroid_lat"}


def get_connection():
    return mysql.connector.connect(**MYSQL_CONFIG)


def normalize_value(value) -> Optional[str]:
    if pd.isna(value):
        return None
    value = str(value).strip()
    if value == "" or value.lower() in {"nan", "none", "null"}:
        return None
    return value


def normalize_int(value) -> Optional[str]:
    v = normalize_value(value)
    if v is None:
        return None
    try:
        return str(int(float(v)))
    except (ValueError, OverflowError):
        return None


def normalize_float(value) -> Optional[str]:
    v = normalize_value(value)
    if v is None:
        return None
    try:
        float(v)
        return v
    except ValueError:
        return None


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    print(f"CSV 로드 완료: {len(df):,}행")

    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {missing}")

    return df[COLUMNS].copy()


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in INT_COLUMNS:
            df[col] = df[col].map(normalize_int)
        elif col in FLOAT_COLUMNS:
            df[col] = df[col].map(normalize_float)
        else:
            df[col] = df[col].map(normalize_value)

    before = len(df)
    df = df.dropna(subset=PK)
    after = len(df)
    if before != after:
        print(f"기본키 결측 행 제거: {before - after}건")

    return df


def make_upsert_sql() -> str:
    col_sql = ", ".join(f"`{c}`" for c in COLUMNS)
    placeholder_sql = ", ".join(["%s"] * len(COLUMNS))
    update_cols = [c for c in COLUMNS if c not in PK]
    update_sql = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)
    return f"""
        INSERT INTO `{TABLE_NAME}` ({col_sql})
        VALUES ({placeholder_sql})
        ON DUPLICATE KEY UPDATE {update_sql}
    """


def insert_dataframe(conn, df: pd.DataFrame):
    if df.empty:
        print("적재할 데이터가 없습니다.")
        return

    sql = make_upsert_sql()
    rows = [
        tuple(None if (isinstance(v, float) and math.isnan(v)) else v for v in row)
        for row in df[COLUMNS].itertuples(index=False, name=None)
    ]
    cursor = conn.cursor()

    total = len(rows)
    for start in range(0, total, BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        cursor.executemany(sql, batch)
        conn.commit()
        print(f"[{TABLE_NAME}] {min(start + BATCH_SIZE, total):,}/{total:,}건 적재 완료")

    cursor.close()


def main():
    print(f"파일 경로: {FOREST_FMI_CSV}")
    df = load_csv(FOREST_FMI_CSV)
    df = prepare_dataframe(df)
    print(f"적재 대상 행 수: {len(df):,}")

    conn = get_connection()
    try:
        insert_dataframe(conn, df)
    finally:
        conn.close()

    print("\n임상도 데이터 적재 완료")


if __name__ == "__main__":
    main()

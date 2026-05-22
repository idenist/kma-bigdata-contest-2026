import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import mysql.connector
from dotenv import load_dotenv

"""
CSV 데이터를 MySQL에 적재하는 스크립트

규칙:
1. AWS지점정보.csv에서 LAW_ADDR이 '강원특별자치도'로 시작하는 STN_ID 추출
2. 5개 테이블 모두 해당 STN_ID 데이터만 적재
3. REQUEST_로 시작하는 컬럼은 적재 대상에서 제외
4. CSV 컬럼명 끝의 '='는 제거
5. HHMM 계열 시간 코드는 문자열로 유지하고 4자리로 보정
6. 중복 기본키는 ON DUPLICATE KEY UPDATE로 갱신
"""

load_dotenv()

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "kma_fire_risk"),
}

DATA_DIR = Path(os.getenv("DATA_DIR", "./data/raw"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10000"))

FILE_CONFIG = {
    "station_info": {
        "path": Path(os.getenv("STATION_INFO_CSV", DATA_DIR / "AWS지점정보.csv")),
        "columns": [
            "STN_ID", "LAT", "LON", "STN_SP", "HT", "HT_WD", "LAU", "STN_AD",
            "STN_KO", "STN_EN", "FCT_ID", "LAW_ID", "BASIN", "LAW_ADDR"
        ],
        "pk": ["STN_ID", "LAT", "LON"],
    },
    "precipitation": {
        "path": Path(os.getenv("PRECIPITATION_CSV", DATA_DIR / "강수량_20191101_20251231.csv")),
        "columns": [
            "STN_ID", "LAT", "LON", "TMA", "ALTD", "RN_DSUM", "RN_MAX_1HR",
            "RN_MAX_1HR_OCUR_TMA", "RN_MAX_6HR", "RN_MAX_6HR_OCUR_TMA",
            "RN_MAX_10M", "RN_MAX_10M_OCUR_TMA"
        ],
        "pk": ["STN_ID", "LAT", "LON", "TMA"],
    },
    "temperature": {
        "path": Path(os.getenv("TEMPERATURE_CSV", DATA_DIR / "기온_20191101_20251231.csv")),
        "columns": [
            "STN_ID", "LAT", "LON", "TMA", "ALTD", "TA_DAVG", "TMX_DD",
            "TMX_OCUR_TMA", "TMN_DD", "TMN_OCUR_TMA", "MRNG_TMN",
            "MRNG_TMN_OCUR_TMA", "DYTM_TMX", "DYTM_TMX_OCUR_TMA",
            "NGHT_TMN", "NGHT_TMN_OCUR_TMA"
        ],
        "pk": ["STN_ID", "LAT", "LON", "TMA"],
    },
    "wind": {
        "path": Path(os.getenv("WIND_CSV", DATA_DIR / "바람_20191101_20251231.csv")),
        "columns": [
            "STN_ID", "LAT", "LON", "TMA", "ALTD", "WS_DAVG", "WS_INS_MAX",
            "WS_INS_MAX_OCUR_TMA", "WD_INS_MAX", "WS_MAX", "WS_MAX_OCUR_TMA",
            "WD_MAX", "WD_FRQ", "WS_MIX", "WD_MIX"
        ],
        "pk": ["STN_ID", "LAT", "LON", "TMA"],
    },
    "humidity": {
        "path": Path(os.getenv("HUMIDITY_CSV", DATA_DIR / "습도_20191101_20251231.csv")),
        "columns": [
            "STN_ID", "LAT", "LON", "TMA", "ALTD", "RHM_AVG", "RHM_MIN",
            "RHM_MIN_OCUR_TMA"
        ],
        "pk": ["STN_ID", "LAT", "LON", "TMA"],
    },
}

DATE_CODE_COLUMNS = {"TMA"}
HHMM_CODE_COLUMNS = {
    "RN_MAX_1HR_OCUR_TMA",
    "RN_MAX_6HR_OCUR_TMA",
    "RN_MAX_10M_OCUR_TMA",
    "TMX_OCUR_TMA",
    "TMN_OCUR_TMA",
    "MRNG_TMN_OCUR_TMA",
    "DYTM_TMX_OCUR_TMA",
    "NGHT_TMN_OCUR_TMA",
    "WS_INS_MAX_OCUR_TMA",
    "WS_MAX_OCUR_TMA",
    "RHM_MIN_OCUR_TMA",
}

trim_warning_counts: dict[str, int] = {}


def get_connection():
    return mysql.connector.connect(**MYSQL_CONFIG)


def clean_column_name(col: str) -> str:
    """
    CSV 컬럼명을 MySQL 컬럼명과 맞도록 정리.
    예:
        RN_MAX_10M_OCUR_TMA= -> RN_MAX_10M_OCUR_TMA
        WD_MIX= -> WD_MIX
        Unnamed: 0 -> UNNAMED_0
    """
    col = str(col).strip()
    col = col.replace("=", "")
    col = re.sub(r"[^0-9A-Za-z_가-힣]+", "_", col)
    col = re.sub(r"_+", "_", col)
    col = col.strip("_")
    return col


def normalize_value(value) -> Optional[str]:
    """
    pandas NaN, 결측 문자열, 빈 문자열을 DB NULL로 변환.
    API 응답에서 값 뒤에 붙는 '='는 제거한다.
    """
    if pd.isna(value):
        return None

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return None

    value = value.rstrip("=").strip()
    value = re.sub(r"\.0$", "", value)

    if value == "":
        return None

    return value


def normalize_station_id(value) -> Optional[str]:
    value = normalize_value(value)
    if value is None:
        return None
    return re.sub(r"\.0$", "", value).strip()


def fix_code(value, length: int, col_name: str) -> Optional[str]:
    """
    TMA, HHMM 계열 코드 보정.
    - 530 -> 0530
    - 0 -> 0000
    - 0700= -> 0700
    - -999 -> -999

    테이블에서 HHMM 계열 컬럼 길이가 4이므로, 4자를 초과하는 비정상 값은 잘라서 DB 에러를 방지한다.
    """
    value = normalize_value(value)

    if value is None:
        return None

    if value.isdigit():
        value = value.zfill(length)

    if len(value) > length:
        trim_warning_counts[col_name] = trim_warning_counts.get(col_name, 0) + 1
        value = value[:length]

    return value


def read_csv_as_str(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df.columns = [clean_column_name(c) for c in df.columns]

    drop_cols = [
        c for c in df.columns
        if c.upper().startswith("UNNAMED") or c.upper().startswith("REQUEST_")
    ]

    if drop_cols:
        df = df.drop(columns=drop_cols)
        print(f"제외한 컬럼: {drop_cols}")

    return df


def get_gangwon_station_ids(station_path: Path) -> set[str]:
    station_df = read_csv_as_str(station_path)

    if "STN_ID" not in station_df.columns:
        raise ValueError("관측소 파일에 STN_ID 컬럼이 없습니다.")

    gangwon_df = station_df[
        station_df["LAW_ADDR"].fillna("").astype(str).str.startswith("강원특별자치도")
    ].copy()

    station_ids = set(
        gangwon_df["STN_ID"]
        .map(normalize_station_id)
        .dropna()
        .tolist()
    )

    print(f"강원특별자치도 STN_ID 개수: {len(station_ids)}")

    return station_ids


def filter_gangwon_stations(df: pd.DataFrame, station_ids: set[str]) -> pd.DataFrame:
    if "STN_ID" not in df.columns:
        raise ValueError("STN_ID 컬럼이 없습니다.")

    df = df.copy()
    df["STN_ID"] = df["STN_ID"].map(normalize_station_id)

    return df[df["STN_ID"].isin(station_ids)].copy()


def prepare_table_dataframe(df: pd.DataFrame, table_name: str, columns: list[str]) -> pd.DataFrame:
    df = df.copy()

    # REQUEST_ 컬럼은 어떤 경우에도 적재 대상에서 제외한다.
    request_cols = [c for c in df.columns if c.upper().startswith("REQUEST_")]
    if request_cols:
        df = df.drop(columns=request_cols)

    for col in columns:
        if col not in df.columns:
            df[col] = None

    df = df[columns]

    for col in df.columns:
        if col in DATE_CODE_COLUMNS:
            df[col] = df[col].map(lambda x: fix_code(x, 8, col))
        elif col in HHMM_CODE_COLUMNS:
            df[col] = df[col].map(lambda x: fix_code(x, 4, col))
        else:
            df[col] = df[col].map(normalize_value)

    pk_cols = FILE_CONFIG[table_name]["pk"]
    before = len(df)
    df = df.dropna(subset=pk_cols)
    after = len(df)

    if before != after:
        print(f"[{table_name}] 기본키 결측 행 제거: {before - after}건")

    return df


def make_upsert_sql(table_name: str, columns: list[str], pk: list[str]) -> str:
    col_sql = ", ".join(f"`{col}`" for col in columns)
    placeholder_sql = ", ".join(["%s"] * len(columns))

    update_cols = [col for col in columns if col not in pk]

    if update_cols:
        update_sql = ", ".join(
            f"`{col}` = VALUES(`{col}`)" for col in update_cols
        )
    else:
        update_sql = f"`{pk[0]}` = `{pk[0]}`"

    return f"""
        INSERT INTO `{table_name}` ({col_sql})
        VALUES ({placeholder_sql})
        ON DUPLICATE KEY UPDATE {update_sql}
    """


def insert_dataframe(conn, table_name: str, df: pd.DataFrame, columns: list[str], pk: list[str], batch_size: int = BATCH_SIZE):
    if df.empty:
        print(f"[{table_name}] 적재할 데이터가 없습니다.")
        return

    sql = make_upsert_sql(table_name, columns, pk)
    rows = [tuple(row) for row in df[columns].itertuples(index=False, name=None)]

    cursor = conn.cursor()

    total = len(rows)
    for start in range(0, total, batch_size):
        batch = rows[start:start + batch_size]
        cursor.executemany(sql, batch)
        conn.commit()
        print(f"[{table_name}] {min(start + batch_size, total)}/{total}건 적재 완료")

    cursor.close()


def load_table(conn, table_name: str, config: dict, station_ids: set[str]):
    path = config["path"]
    columns = config["columns"]
    pk = config["pk"]

    print(f"\n[{table_name}] 파일 로드: {path}")

    df = read_csv_as_str(path)
    df = filter_gangwon_stations(df, station_ids)
    df = prepare_table_dataframe(df, table_name, columns)

    print(f"[{table_name}] 강원특별자치도 필터 후 행 수: {len(df)}")

    insert_dataframe(conn, table_name, df, columns, pk)


def print_trim_warnings():
    if not trim_warning_counts:
        return

    print("\n[주의] 길이가 초과되어 잘린 시간 코드가 있습니다.")
    for col, count in sorted(trim_warning_counts.items()):
        print(f"- {col}: {count}건")
    print("원본 CSV에서 해당 컬럼 값을 확인하는 것을 권장합니다.")


def main():
    station_path = FILE_CONFIG["station_info"]["path"]
    station_ids = get_gangwon_station_ids(station_path)

    conn = get_connection()

    try:
        load_table(conn, "station_info", FILE_CONFIG["station_info"], station_ids)

        for table_name in ["precipitation", "temperature", "wind", "humidity"]:
            load_table(conn, table_name, FILE_CONFIG[table_name], station_ids)

    finally:
        conn.close()

    print_trim_warnings()
    print("\n전체 데이터 적재 완료")


if __name__ == "__main__":
    main()

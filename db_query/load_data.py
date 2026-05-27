# load_data.py

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv


# =========================
# 기본 설정
# =========================

DATA_DIR = Path("./data/gangwon_weather")

FILE_TABLE_MAP = {
    "AWS지점정보_강원특별자치도.csv": "station_info",
    "습도_20191101_20251231_강원특별자치도.csv": "humidity",
    "강수량_20191101_20251231_강원특별자치도.csv": "precipitation",
    "기온_20191101_20251231_강원특별자치도.csv": "temperature",
    "바람_20191101_20251231_강원특별자치도.csv": "wind",
}

TABLE_COLUMNS = {
    "station_info": [
        "STN_ID",
        "STN_KO",
        "LAW_ADDR",
    ],

    "humidity": [
        "TMA",
        "STN_ID",
        "LAT",
        "LON",
        "ALTD",
        "RHM_AVG",
        "RHM_MIN",
        "RHM_MIN_OCUR_TMA",
    ],

    "precipitation": [
        "TMA",
        "STN_ID",
        "LAT",
        "LON",
        "ALTD",
        "RN_DSUM",
        "RN_MAX_1HR",
        "RN_MAX_1HR_OCUR_TMA",
        "RN_MAX_6HR",
        "RN_MAX_6HR_OCUR_TMA",
        "RN_MAX_10M",
        "RN_MAX_10M_OCUR_TMA",
    ],

    "temperature": [
        "TMA",
        "STN_ID",
        "LAT",
        "LON",
        "ALTD",
        "TA_DAVG",
        "TMX_DD",
        "TMX_OCUR_TMA",
        "TMN_DD",
        "TMN_OCUR_TMA",
        "MRNG_TMN",
        "MRNG_TMN_OCUR_TMA",
        "DYTM_TMX",
        "DYTM_TMX_OCUR_TMA",
        "NGHT_TMN",
        "NGHT_TMN_OCUR_TMA",
    ],

    "wind": [
        "TMA",
        "STN_ID",
        "LAT",
        "LON",
        "ALTD",
        "WS_DAVG",
        "WS_INS_MAX",
        "WS_INS_MAX_OCUR_TMA",
        "WD_INS_MAX",
        "WS_MAX",
        "WS_MAX_OCUR_TMA",
        "WD_MAX",
        "WD_FRQ",
        "WS_MIX",
        "WD_MIX",
    ],
}


# 컬럼별 결측치 코드
MISSING_VALUE_MAP = {
    # humidity
    "RHM_AVG": [-99.9],
    "RHM_MIN": [-99.9],
    "RHM_MIN_OCUR_TMA": [-99.9, "-99.9"],

    # precipitation
    "RN_DSUM": [-99.9],
    "RN_MAX_1HR": [-99.9],
    "RN_MAX_1HR_OCUR_TMA": [-99.9, "-99.9"],
    "RN_MAX_6HR": [-99.9],
    "RN_MAX_6HR_OCUR_TMA": [-999, "-999"],
    "RN_MAX_10M": [-99.9],
    "RN_MAX_10M_OCUR_TMA": [-99.9, "-99.9"],

    # temperature
    "TA_DAVG": [-99.9],
    "TMX_DD": [-99.9],
    "TMX_OCUR_TMA": [-99.9, "-99.9"],
    "TMN_DD": [-99.9],
    "TMN_OCUR_TMA": [-99.9, "-99.9"],
    "MRNG_TMN": [-99.9],
    "MRNG_TMN_OCUR_TMA": [-99.9, "-99.9"],
    "DYTM_TMX": [-99.9],
    "DYTM_TMX_OCUR_TMA": [-99.9, "-99.9"],
    "NGHT_TMN": [-99.9],
    "NGHT_TMN_OCUR_TMA": [-99.9, "-99.9"],

    # wind
    "WS_DAVG": [-99.9],
    "WS_INS_MAX": [-99.9],
    "WS_INS_MAX_OCUR_TMA": [-99.9, "-99.9"],
    "WD_INS_MAX": [-99.9],
    "WS_MAX": [-99.9],
    "WS_MAX_OCUR_TMA": [-99.9, "-99.9"],
    "WD_MAX": [-99.9],
    "WD_FRQ": [-99.9],
    "WS_MIX": [-999],
    "WD_MIX": [-99.9],
}


# 문자형으로 유지해야 하는 컬럼
STRING_COLUMNS = {
    "TMA",
    "RHM_MIN_OCUR_TMA",
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
    "STN_KO",
    "LAW_ADDR",
}


def get_env_variable(name: str) -> str:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        raise ValueError(f".env 파일에 {name} 값이 없습니다.")

    return value


def get_connection():
    return pymysql.connect(
        host=get_env_variable("MYSQL_HOST"),
        port=int(get_env_variable("MYSQL_PORT")),
        user=get_env_variable("MYSQL_USER"),
        password=get_env_variable("MYSQL_PASSWORD"),
        database=get_env_variable("MYSQL_DATABASE"),
        charset="utf8mb4",
        autocommit=False,
    )


def read_csv_safely(file_path: Path) -> pd.DataFrame:
    """
    CSV 인코딩 문제를 대비해서 utf-8-sig 우선, 실패 시 cp949로 읽는다.
    """
    try:
        return pd.read_csv(file_path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(file_path, encoding="cp949")


def clean_dataframe(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """
    테이블에 필요한 컬럼만 남기고,
    결측치 코드를 NULL로 변환한다.
    """
    columns = TABLE_COLUMNS[table_name]

    missing_columns = [col for col in columns if col not in df.columns]
    if missing_columns:
        raise ValueError(
            f"{table_name} 테이블 적재에 필요한 컬럼이 CSV에 없습니다: {missing_columns}"
        )

    df = df[columns].copy()

    # STN_ID는 정수형으로 변환
    if "STN_ID" in df.columns:
        df["STN_ID"] = pd.to_numeric(df["STN_ID"], errors="coerce").astype("Int64")

    # 문자형 컬럼은 문자열로 유지
    for col in STRING_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    # 컬럼별 결측치 코드 치환
    for col, missing_values in MISSING_VALUE_MAP.items():
        if col in df.columns:
            df[col] = df[col].replace(missing_values, np.nan)

    # 빈 문자열도 NULL 처리
    df = df.replace("", np.nan)

    # pandas NA, NaN을 pymysql이 넣을 수 있는 None으로 변환
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)

    return df


def insert_dataframe(conn, table_name: str, df: pd.DataFrame, chunk_size: int = 5000):
    """
    DataFrame을 MySQL 테이블에 INSERT한다.
    중복 PK가 있으면 기존 행을 갱신한다.
    """
    columns = TABLE_COLUMNS[table_name]

    column_sql = ", ".join([f"`{col}`" for col in columns])
    placeholder_sql = ", ".join(["%s"] * len(columns))

    update_sql = ", ".join([
        f"`{col}` = VALUES(`{col}`)"
        for col in columns
        if col not in ["TMA", "STN_ID"]
    ])

    # station_info는 PK가 STN_ID 하나
    if table_name == "station_info":
        update_sql = ", ".join([
            f"`{col}` = VALUES(`{col}`)"
            for col in columns
            if col != "STN_ID"
        ])

    insert_sql = f"""
        INSERT INTO `{table_name}` ({column_sql})
        VALUES ({placeholder_sql})
        ON DUPLICATE KEY UPDATE
        {update_sql};
    """

    records = list(df.itertuples(index=False, name=None))

    if not records:
        print(f"{table_name}: 적재할 데이터가 없습니다.")
        return

    with conn.cursor() as cursor:
        for start in range(0, len(records), chunk_size):
            chunk = records[start:start + chunk_size]
            cursor.executemany(insert_sql, chunk)

    print(f"{table_name}: {len(records):,}행 적재 완료")


def load_table(conn, file_name: str, table_name: str):
    file_path = DATA_DIR / file_name

    if not file_path.exists():
        raise FileNotFoundError(f"파일이 존재하지 않습니다: {file_path}")

    print(f"\n파일 로딩 중: {file_path}")
    raw_df = read_csv_safely(file_path)

    cleaned_df = clean_dataframe(raw_df, table_name)

    print(f"{table_name}: CSV 행 수 {len(raw_df):,}, 정제 후 행 수 {len(cleaned_df):,}")

    insert_dataframe(conn, table_name, cleaned_df)


def main():
    load_dotenv()

    conn = get_connection()

    try:
        # 외래키 때문에 station_info를 먼저 넣어야 함
        load_order = [
            ("AWS지점정보_강원특별자치도.csv", "station_info"),
            ("습도_20191101_20251231_강원특별자치도.csv", "humidity"),
            ("강수량_20191101_20251231_강원특별자치도.csv", "precipitation"),
            ("기온_20191101_20251231_강원특별자치도.csv", "temperature"),
            ("바람_20191101_20251231_강원특별자치도.csv", "wind"),
        ]

        for file_name, table_name in load_order:
            load_table(conn, file_name, table_name)

        conn.commit()
        print("\n전체 데이터 적재 완료")

    except Exception as e:
        conn.rollback()
        print("\n데이터 적재 중 오류가 발생하여 rollback 했습니다.")
        raise e

    finally:
        conn.close()


if __name__ == "__main__":
    main()
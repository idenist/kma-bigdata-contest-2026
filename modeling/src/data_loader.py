from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

GRID_DATE_MASTER_PATH = (
    PROJECT_ROOT / "processed" / "grid_date_master"
)


# data_scale = 10 적용 대상 컬럼
# 풍향 sin/cos, id, date, month는 제외
SCALE_10_COLUMNS = [
    "ta_mean",
    "ta_max",
    "hm_mean",
    "hm_min",
    "td_mean",
    "td_min",
    "wind_ws_mean",
    "wind_ws_max",
    "wind_uu_mean",
    "wind_vv_mean",
    "rn_day_mean",
    "rn_day_max",
]


def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _build_select_clause(
    columns: Optional[list[str]],
    apply_scale: bool,
) -> str:
    """
    SELECT 절 생성.
    apply_scale=True이면 SCALE_10_COLUMNS에 포함된 컬럼만 /10 처리.
    """

    if columns is None:
        # 전체 컬럼을 읽을 때 사용할 기본 컬럼 목록
        # 실제 데이터 컬럼에 맞게 필요하면 수정
        columns = [
            "grid_id",
            "kma_nx",
            "kma_ny",
            "ta_mean",
            "ta_max",
            "hm_mean",
            "hm_min",
            "td_mean",
            "td_min",
            "wind_ws_mean",
            "wind_ws_max",
            "wind_uu_mean",
            "wind_vv_mean",
            "wind_wd_sin_mean",
            "wind_wd_cos_mean",
            "rn_day_mean",
            "rn_day_max",
            "date",
            "month",
        ]

    select_exprs = []

    for col in columns:
        if apply_scale and col in SCALE_10_COLUMNS:
            select_exprs.append(f"CAST({col} AS DOUBLE) / 10.0 AS {col}")
        else:
            select_exprs.append(col)

    return ",\n            ".join(select_exprs)


def query_weather(
    months: Optional[list[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    grid_ids: Optional[list[str]] = None,
    columns: Optional[list[str]] = None,
    apply_scale: bool = True,
) -> pd.DataFrame:
    """
    grid_date_master에서 날씨 데이터를 조회한다.

    Parameters
    ----------
    months:
        예: ["2025-02", "2025-03"]
    start_date:
        예: "2025-03-01"
    end_date:
        예: "2025-03-31"
    grid_ids:
        특정 grid_id만 조회할 때 사용
    columns:
        필요한 컬럼만 조회.
        None이면 기본 전체 컬럼을 조회.
    apply_scale:
        True이면 data_scale=10 적용 대상 컬럼을 /10 처리.

    Returns
    -------
    pd.DataFrame
    """

    parquet_glob = str(
        GRID_DATE_MASTER_PATH / "**" / "*.parquet"
    ).replace("\\", "/")

    select_clause = _build_select_clause(
        columns=columns,
        apply_scale=apply_scale,
    )

    where_clauses = []
    params = {}

    if months:
        where_clauses.append("month IN $months")
        params["months"] = months

    if start_date:
        where_clauses.append("date >= $start_date")
        params["start_date"] = start_date

    if end_date:
        where_clauses.append("date <= $end_date")
        params["end_date"] = end_date

    if grid_ids:
        where_clauses.append("grid_id IN $grid_ids")
        params["grid_ids"] = grid_ids

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    sql = f"""
        SELECT
            {select_clause}
        FROM read_parquet(
            '{parquet_glob}',
            hive_partitioning = true
        )
        {where_sql}
    """

    con = get_connection()

    try:
        return con.execute(sql, params).df()
    finally:
        con.close()
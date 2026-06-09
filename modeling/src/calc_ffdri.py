# src/calc_ffdri.py

from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import calendar
import re

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]

WEATHER_DIR = PROJECT_ROOT / "processed" / "grid_date_master"
MASTER_PATH = PROJECT_ROOT / "processed" / "master_grid.parquet"
OUTPUT_DIR = PROJECT_ROOT / "processed" / "ffdri"


def list_available_months() -> list[str]:
    """
    processed/grid_date_master/month=YYYY-MM 폴더 목록을 읽어
    ['2020-02', '2020-03', ...] 형태로 반환한다.
    """
    months = []

    for path in WEATHER_DIR.glob("month=*"):
        if not path.is_dir():
            continue

        name = path.name
        match = re.fullmatch(r"month=(\d{4}-\d{2})", name)
        if match:
            months.append(match.group(1))

    return sorted(months)


def month_start_end(month: str) -> tuple[str, str]:
    """
    '2025-03' -> ('2025-03-01', '2025-03-31')
    """
    year, mon = map(int, month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]

    start = date(year, mon, 1)
    end = date(year, mon, last_day)

    return start.isoformat(), end.isoformat()


def lookback_start(month: str, days: int = 4) -> str:
    """
    실효습도 계산을 위해 월 시작일보다 4일 전부터 읽는다.
    """
    year, mon = map(int, month.split("-"))
    start = date(year, mon, 1)
    return (start - timedelta(days=days)).isoformat()


def calc_and_save_ffdri_month(month: str) -> None:
    """
    특정 월의 DWI, FMI, TMI, FFDRI를 계산하여 parquet으로 저장한다.
    """
    target_start, target_end = month_start_end(month)
    read_start = lookback_start(month, days=4)

    weather_glob = str(WEATHER_DIR / "**" / "*.parquet").replace("\\", "/")
    master_path = str(MASTER_PATH).replace("\\", "/")
    output_path = OUTPUT_DIR / f"month={month}"
    output_path.mkdir(parents=True, exist_ok=True)

    output_file = str(output_path / "part-0.parquet").replace("\\", "/")

    con = duckdb.connect()

    sql = f"""
    COPY (
        WITH weather_scaled AS (
            SELECT
                grid_id,
                CAST(date AS DATE) AS date,

                -- 식별자
                kma_nx,
                kma_ny,

                -- data_scale = 10 적용
                CAST(ta_mean AS DOUBLE) / 10.0 AS ta_mean,
                CAST(ta_max AS DOUBLE) / 10.0 AS ta_max,
                CAST(hm_mean AS DOUBLE) / 10.0 AS hm_mean,
                CAST(hm_min AS DOUBLE) / 10.0 AS hm_min,
                CAST(td_mean AS DOUBLE) / 10.0 AS td_mean,
                CAST(td_min AS DOUBLE) / 10.0 AS td_min,
                CAST(wind_ws_mean AS DOUBLE) / 10.0 AS wind_ws_mean,
                CAST(wind_ws_max AS DOUBLE) / 10.0 AS wind_ws_max,
                CAST(wind_uu_mean AS DOUBLE) / 10.0 AS wind_uu_mean,
                CAST(wind_vv_mean AS DOUBLE) / 10.0 AS wind_vv_mean,
                wind_wd_sin_mean,
                wind_wd_cos_mean,
                CAST(rn_day_mean AS DOUBLE) / 10.0 AS rn_day_mean,
                CAST(rn_day_max AS DOUBLE) / 10.0 AS rn_day_max,

                month
            FROM read_parquet(
                '{weather_glob}',
                hive_partitioning = true,
                union_by_name = true
            )
            WHERE CAST(date AS DATE) BETWEEN DATE '{read_start}' AND DATE '{target_end}'
        ),

        weather_lag AS (
            SELECT
                *,

                -- 실효습도 계산용: 오늘~4일 전 습도
                LAG(hm_mean, 1) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag1,
                LAG(hm_mean, 2) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag2,
                LAG(hm_mean, 3) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag3,
                LAG(hm_mean, 4) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag4,

                -- RNE 계산용: 오늘~2일 전 강수 등급
                CASE
                    WHEN rn_day_mean < 1 THEN 0
                    WHEN rn_day_mean < 5 THEN 1
                    WHEN rn_day_mean < 10 THEN 2
                    ELSE 3
                END AS rn_grade
            FROM weather_scaled
        ),

        weather_derived AS (
            SELECT
                *,

                -- 실효습도 EH
                -- 월 초 등 이전 4일치가 없는 경우에는 현재 습도로 대체
                (
                    0.300 * hm_mean
                    + 0.210 * COALESCE(hm_lag1, hm_mean)
                    + 0.147 * COALESCE(hm_lag2, hm_mean)
                    + 0.103 * COALESCE(hm_lag3, hm_mean)
                    + 0.072 * COALESCE(hm_lag4, hm_mean)
                ) AS effective_humidity,

                (
                    rn_grade
                    + COALESCE(LAG(rn_grade, 1) OVER (PARTITION BY grid_id ORDER BY date), 0)
                    + COALESCE(LAG(rn_grade, 2) OVER (PARTITION BY grid_id ORDER BY date), 0)
                ) AS rne_temp
            FROM weather_lag
        ),

        dwi_base AS (
            SELECT
                *,

                CASE
                    WHEN rne_temp < 2 THEN 1.0
                    WHEN rne_temp < 3 THEN 0.5
                    WHEN rne_temp < 4 THEN 0.4
                    WHEN rne_temp < 5 THEN 0.3
                    WHEN rne_temp < 6 THEN 0.2
                    ELSE 0.1
                END AS rne,

                -- 봄철 PreDWI: 1~6월 공식
                1.0 / (
                    1.0 + EXP(
                        -(
                            2.706
                            + 0.088 * ta_mean
                            - 0.055 * hm_mean
                            - 0.023 * effective_humidity
                            - 0.104 * wind_ws_mean
                        )
                    )
                ) AS predwi

            FROM weather_derived
        ),

        dwi_calc AS (
            SELECT
                *,

                CASE
                    WHEN predwi <= 0.1183 THEN 1
                    WHEN predwi <= 0.1878 THEN 2
                    WHEN predwi <= 0.2571 THEN 3
                    WHEN predwi <= 0.3320 THEN 4
                    WHEN predwi <= 0.4089 THEN 5
                    WHEN predwi <= 0.4932 THEN 6
                    WHEN predwi <= 0.5861 THEN 7
                    WHEN predwi <= 0.6862 THEN 8
                    WHEN predwi <= 0.7820 THEN 9
                    ELSE 10
                END AS predwi_class,

                (
                    CASE
                        WHEN predwi <= 0.1183 THEN 1
                        WHEN predwi <= 0.1878 THEN 2
                        WHEN predwi <= 0.2571 THEN 3
                        WHEN predwi <= 0.3320 THEN 4
                        WHEN predwi <= 0.4089 THEN 5
                        WHEN predwi <= 0.4932 THEN 6
                        WHEN predwi <= 0.5861 THEN 7
                        WHEN predwi <= 0.6862 THEN 8
                        WHEN predwi <= 0.7820 THEN 9
                        ELSE 10
                    END
                ) * rne AS dwi

            FROM dwi_base
        ),

        master_static AS (
            SELECT
                grid_id,

                -- 임상
                is_forest,
                forest_type_code,

                CASE
                    WHEN forest_type_code = 1 THEN 10  -- 침엽수림
                    WHEN forest_type_code = 2 THEN 2   -- 활엽수림
                    WHEN forest_type_code = 3 THEN 3   -- 혼효림
                    WHEN forest_type_code = 4 THEN 1   -- 죽림
                    ELSE 0
                END AS fmi,

                -- 지형
                elevation,
                slope,
                aspect_sin,
                aspect_cos,

                -- aspect_sin, aspect_cos 기준 방위각
                -- 0=N, 90=E, 180=S, 270=W
                (
                    DEGREES(ATAN2(aspect_sin, aspect_cos)) + 360
                ) % 360 AS aspect_deg

            FROM read_parquet('{master_path}', union_by_name = true)
        ),

        terrain_calc AS (
            SELECT
                *,

                CASE
                    WHEN elevation >= 876 THEN 1
                    WHEN elevation >= 628 THEN 2
                    WHEN elevation >= 380 THEN 3
                    WHEN elevation >= 132 THEN 4
                    ELSE 5
                END AS elevation_index,

                CASE
                    -- N: 337.5~360 or 0~22.5
                    WHEN aspect_deg >= 337.5 OR aspect_deg < 22.5 THEN 2.5

                    -- NE
                    WHEN aspect_deg >= 22.5 AND aspect_deg < 67.5 THEN 4.5

                    -- E
                    WHEN aspect_deg >= 67.5 AND aspect_deg < 112.5 THEN 1.5

                    -- SE
                    WHEN aspect_deg >= 112.5 AND aspect_deg < 157.5 THEN 4.0

                    -- S
                    WHEN aspect_deg >= 157.5 AND aspect_deg < 202.5 THEN 4.0

                    -- SW
                    WHEN aspect_deg >= 202.5 AND aspect_deg < 247.5 THEN 5.0

                    -- W
                    WHEN aspect_deg >= 247.5 AND aspect_deg < 292.5 THEN 2.5

                    -- NW
                    WHEN aspect_deg >= 292.5 AND aspect_deg < 337.5 THEN 4.5

                    ELSE 0
                END AS aspect_index,

                (
                    CASE
                        WHEN elevation >= 876 THEN 1
                        WHEN elevation >= 628 THEN 2
                        WHEN elevation >= 380 THEN 3
                        WHEN elevation >= 132 THEN 4
                        ELSE 5
                    END
                    +
                    CASE
                        WHEN aspect_deg >= 337.5 OR aspect_deg < 22.5 THEN 2.5
                        WHEN aspect_deg >= 22.5 AND aspect_deg < 67.5 THEN 4.5
                        WHEN aspect_deg >= 67.5 AND aspect_deg < 112.5 THEN 1.5
                        WHEN aspect_deg >= 112.5 AND aspect_deg < 157.5 THEN 4.0
                        WHEN aspect_deg >= 157.5 AND aspect_deg < 202.5 THEN 4.0
                        WHEN aspect_deg >= 202.5 AND aspect_deg < 247.5 THEN 5.0
                        WHEN aspect_deg >= 247.5 AND aspect_deg < 292.5 THEN 2.5
                        WHEN aspect_deg >= 292.5 AND aspect_deg < 337.5 THEN 4.5
                        ELSE 0
                    END
                ) AS tmi

            FROM master_static
        ),

        joined AS (
            SELECT
                d.grid_id,
                d.date,

                d.kma_nx,
                d.kma_ny,

                -- 원 기상값
                d.ta_mean,
                d.ta_max,
                d.hm_mean,
                d.hm_min,
                d.td_mean,
                d.td_min,
                d.wind_ws_mean,
                d.wind_ws_max,
                d.wind_uu_mean,
                d.wind_vv_mean,
                d.wind_wd_sin_mean,
                d.wind_wd_cos_mean,
                d.rn_day_mean,
                d.rn_day_max,

                -- DWI 관련
                d.effective_humidity,
                d.rne_temp,
                d.rne,
                d.predwi,
                d.predwi_class,
                d.dwi,

                -- FMI/TMI 관련
                t.is_forest,
                t.forest_type_code,
                t.fmi,
                t.elevation,
                t.slope,
                t.aspect_sin,
                t.aspect_cos,
                t.aspect_deg,
                t.elevation_index,
                t.aspect_index,
                t.tmi,

                -- 일가중치
                CASE
                    WHEN EXTRACT('month' FROM d.date) = 1 THEN 0.85
                    WHEN EXTRACT('month' FROM d.date) = 2 THEN 0.85

                    WHEN EXTRACT('month' FROM d.date) = 3
                         AND EXTRACT('day' FROM d.date) BETWEEN 1 AND 10 THEN 0.90
                    WHEN EXTRACT('month' FROM d.date) = 3
                         AND EXTRACT('day' FROM d.date) BETWEEN 11 AND 20 THEN 0.95
                    WHEN EXTRACT('month' FROM d.date) = 3
                         AND EXTRACT('day' FROM d.date) BETWEEN 21 AND 31 THEN 1.00

                    WHEN EXTRACT('month' FROM d.date) = 4
                         AND EXTRACT('day' FROM d.date) BETWEEN 1 AND 10 THEN 1.00
                    WHEN EXTRACT('month' FROM d.date) = 4
                         AND EXTRACT('day' FROM d.date) BETWEEN 11 AND 20 THEN 0.95
                    WHEN EXTRACT('month' FROM d.date) = 4
                         AND EXTRACT('day' FROM d.date) BETWEEN 21 AND 30 THEN 0.90

                    WHEN EXTRACT('month' FROM d.date) = 5 THEN 0.85
                    WHEN EXTRACT('month' FROM d.date) = 6 THEN 0.80
                    WHEN EXTRACT('month' FROM d.date) = 7 THEN 0.33
                    WHEN EXTRACT('month' FROM d.date) = 8 THEN 0.33
                    WHEN EXTRACT('month' FROM d.date) = 9 THEN 0.50
                    WHEN EXTRACT('month' FROM d.date) = 10 THEN 0.61
                    WHEN EXTRACT('month' FROM d.date) = 11 THEN 0.78
                    WHEN EXTRACT('month' FROM d.date) = 12 THEN 0.83
                    ELSE 1.00
                END AS day_weight

            FROM dwi_calc d
            LEFT JOIN terrain_calc t
            ON d.grid_id = t.grid_id
            WHERE d.date BETWEEN DATE '{target_start}' AND DATE '{target_end}'
        )

        SELECT
            *,

            -- 산림청 FFDRI 공식
            (
                7.0 * dwi
                + 1.5 * fmi
                + 1.5 * tmi
            ) * day_weight AS ffdri

        FROM joined
    )
    TO '{output_file}'
    (FORMAT PARQUET);
    """

    try:
        con.execute(sql)
    finally:
        con.close()

    print(f"[완료] {month} → {output_file}")


def calc_and_save_ffdri_all() -> None:
    """
    grid_date_master에 존재하는 모든 month=YYYY-MM 폴더를 대상으로 FFDRI 계산.
    """
    months = list_available_months()

    if not months:
        raise FileNotFoundError(f"월별 parquet 폴더를 찾지 못했습니다: {WEATHER_DIR}")

    print(f"처리 대상 월: {months}")

    for month in months:
        calc_and_save_ffdri_month(month)


if __name__ == "__main__":
    calc_and_save_ffdri_all()
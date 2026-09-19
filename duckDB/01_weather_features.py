from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    build_date_where,
    connect,
    copy_options,
    default_duckdb_path,
    parquet_columns,
    prepare_export_path,
    print_table_summary,
    project_path,
    qname,
    require_columns,
    require_table,
    source_sql,
    sql_path,
    table_columns,
)


SCALE10_COLS = {
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
}


def value_expr(col: str, scale: str) -> str:
    expr = f"TRY_CAST({col} AS DOUBLE)"
    return f"({expr} / 10.0)" if scale == "divide10" and col in SCALE10_COLS else expr


def predwi_class_case(predwi_col: str = "predwi") -> str:
    return f"""
    CASE
        WHEN {predwi_col} <= 0.1183 THEN 1
        WHEN {predwi_col} <= 0.1878 THEN 2
        WHEN {predwi_col} <= 0.2571 THEN 3
        WHEN {predwi_col} <= 0.3320 THEN 4
        WHEN {predwi_col} <= 0.4089 THEN 5
        WHEN {predwi_col} <= 0.4932 THEN 6
        WHEN {predwi_col} <= 0.5861 THEN 7
        WHEN {predwi_col} <= 0.6862 THEN 8
        WHEN {predwi_col} <= 0.7820 THEN 9
        ELSE 10
    END
    """


def day_weight_case() -> str:
    return """
    CASE
        WHEN month_num IN (1, 2) THEN 0.85
        WHEN month_num = 3 AND day_num <= 10 THEN 0.90
        WHEN month_num = 3 AND day_num <= 20 THEN 0.95
        WHEN month_num = 3 THEN 1.00
        WHEN month_num = 4 AND day_num <= 10 THEN 1.00
        WHEN month_num = 4 AND day_num <= 20 THEN 0.95
        WHEN month_num = 4 THEN 0.90
        WHEN month_num = 5 THEN 0.85
        WHEN month_num = 6 THEN 0.80
        WHEN month_num IN (7, 8) THEN 0.33
        WHEN month_num = 9 THEN 0.50
        WHEN month_num = 10 THEN 0.61
        WHEN month_num = 11 THEN 0.78
        WHEN month_num = 12 THEN 0.83
        ELSE 1.00
    END
    """


def main() -> None:
    parser = argparse.ArgumentParser(description="Create weather-derived daily features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--source-table", default="grid_date_master")
    parser.add_argument("--source-parquet")
    parser.add_argument("--output-table", default="feat_weather_daily")
    parser.add_argument("--export-parquet-dir", help="Directory for partitioned Parquet export.")
    parser.add_argument("--parquet-only", action="store_true", help="Export Parquet without creating the DuckDB table.")
    parser.add_argument("--parquet-compression", default="ZSTD", choices=["ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4"])
    parser.add_argument("--parquet-compression-level", type=int, default=6)
    parser.add_argument("--overwrite-parquet", action="store_true")
    parser.add_argument("--append-parquet", action="store_true", help="Append to an existing partitioned Parquet directory.")
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--weather-scale", choices=["divide10", "none"], default="divide10")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    args = parser.parse_args()

    duckdb_path = project_path(__file__, args.duckdb_path)
    source_parquet = project_path(__file__, args.source_parquet) if args.source_parquet else None
    con = connect(duckdb_path, args.threads, args.memory_limit)
    if not args.source_parquet:
        require_table(con, args.source_table)
    source_columns = parquet_columns(con, source_parquet) if source_parquet else table_columns(con, args.source_table)
    require_columns(
        source_columns,
        [
            "grid_id",
            "date",
            "ta_mean",
            "ta_max",
            "hm_mean",
            "hm_min",
            "wind_ws_mean",
            "wind_ws_max",
            "wind_wd_sin_mean",
            "wind_wd_cos_mean",
            "rn_day_mean",
            "rn_day_max",
        ],
        args.source_table,
    )

    where_sql = build_date_where(args.months, args.start_date, args.end_date)
    source = source_sql(args.source_table, source_parquet)

    select_sql = f"""
        WITH weather_base AS (
            SELECT
                grid_id,
                CAST(date AS DATE) AS date,
                strftime(CAST(date AS DATE), '%Y-%m') AS month,
                CAST(strftime(CAST(date AS DATE), '%m') AS INTEGER) AS month_num,
                CAST(strftime(CAST(date AS DATE), '%d') AS INTEGER) AS day_num,
                {value_expr("ta_mean", args.weather_scale)} AS ta_mean,
                {value_expr("ta_max", args.weather_scale)} AS ta_max,
                {value_expr("hm_mean", args.weather_scale)} AS hm_mean,
                {value_expr("hm_min", args.weather_scale)} AS hm_min,
                {value_expr("td_mean", args.weather_scale)} AS td_mean,
                {value_expr("td_min", args.weather_scale)} AS td_min,
                {value_expr("wind_ws_mean", args.weather_scale)} AS wind_ws_mean,
                {value_expr("wind_ws_max", args.weather_scale)} AS wind_ws_max,
                {value_expr("wind_uu_mean", args.weather_scale)} AS wind_uu_mean,
                {value_expr("wind_vv_mean", args.weather_scale)} AS wind_vv_mean,
                TRY_CAST(wind_wd_sin_mean AS DOUBLE) AS wind_wd_sin_mean,
                TRY_CAST(wind_wd_cos_mean AS DOUBLE) AS wind_wd_cos_mean,
                {value_expr("rn_day_mean", args.weather_scale)} AS rn_day_mean,
                {value_expr("rn_day_max", args.weather_scale)} AS rn_day_max
            FROM {source}
            {where_sql}
        ),
        lagged AS (
            SELECT
                *,
                LAG(date, 1) OVER (PARTITION BY grid_id ORDER BY date) AS lag_date1,
                LAG(date, 2) OVER (PARTITION BY grid_id ORDER BY date) AS lag_date2,
                LAG(date, 3) OVER (PARTITION BY grid_id ORDER BY date) AS lag_date3,
                LAG(date, 4) OVER (PARTITION BY grid_id ORDER BY date) AS lag_date4,
                LAG(hm_mean, 1) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag1,
                LAG(hm_mean, 2) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag2,
                LAG(hm_mean, 3) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag3,
                LAG(hm_mean, 4) OVER (PARTITION BY grid_id ORDER BY date) AS hm_lag4,
                LAG(rn_day_mean, 1) OVER (PARTITION BY grid_id ORDER BY date) AS rn_lag1_raw,
                LAG(rn_day_mean, 2) OVER (PARTITION BY grid_id ORDER BY date) AS rn_lag2_raw
            FROM weather_base
        ),
        derived_base AS (
            SELECT
                *,
                CASE WHEN date_diff('day', lag_date1, date) = 1 THEN hm_lag1 ELSE hm_mean END AS hm_lag1_used,
                CASE WHEN date_diff('day', lag_date2, date) = 2 THEN hm_lag2 ELSE hm_mean END AS hm_lag2_used,
                CASE WHEN date_diff('day', lag_date3, date) = 3 THEN hm_lag3 ELSE hm_mean END AS hm_lag3_used,
                CASE WHEN date_diff('day', lag_date4, date) = 4 THEN hm_lag4 ELSE hm_mean END AS hm_lag4_used,
                CASE WHEN rn_day_mean < 1 THEN 0 WHEN rn_day_mean < 5 THEN 1 WHEN rn_day_mean < 10 THEN 2 ELSE 3 END AS rn_class,
                CASE
                    WHEN date_diff('day', lag_date1, date) = 1 THEN
                        CASE WHEN rn_lag1_raw < 1 THEN 0 WHEN rn_lag1_raw < 5 THEN 1 WHEN rn_lag1_raw < 10 THEN 2 ELSE 3 END
                    ELSE 0
                END AS rn_class1,
                CASE
                    WHEN date_diff('day', lag_date2, date) = 2 THEN
                        CASE WHEN rn_lag2_raw < 1 THEN 0 WHEN rn_lag2_raw < 5 THEN 1 WHEN rn_lag2_raw < 10 THEN 2 ELSE 3 END
                    ELSE 0
                END AS rn_class2
            FROM lagged
        ),
        weather_calc AS (
            SELECT
                *,
                0.3 * (
                    hm_mean
                    + 0.7 * hm_lag1_used
                    + POW(0.7, 2) * hm_lag2_used
                    + POW(0.7, 3) * hm_lag3_used
                    + POW(0.7, 4) * hm_lag4_used
                ) AS effective_humidity,
                rn_class + rn_class1 + rn_class2 AS rne_temp,
                {day_weight_case()} AS day_weight,
                (DEGREES(ATAN2(wind_wd_sin_mean, wind_wd_cos_mean)) + 360.0) % 360.0 AS wind_theta_deg
            FROM derived_base
        ),
        rne_calc AS (
            SELECT
                *,
                CASE
                    WHEN rne_temp < 2 THEN 1.0
                    WHEN rne_temp < 3 THEN 0.5
                    WHEN rne_temp < 4 THEN 0.4
                    WHEN rne_temp < 5 THEN 0.3
                    WHEN rne_temp < 6 THEN 0.2
                    ELSE 0.1
                END AS rne
            FROM weather_calc
        ),
        predwi_calc AS (
            SELECT
                *,
                1.0 / (1.0 + EXP(-(2.706 + 0.088*ta_mean - 0.055*hm_mean - 0.023*effective_humidity - 0.104*wind_ws_mean))) AS predwi
            FROM rne_calc
        ),
        dwi_calc AS (
            SELECT
                *,
                {predwi_class_case("predwi")} AS predwi_class
            FROM predwi_calc
        )
        SELECT
            grid_id, date, month, month_num, day_num,
            ta_mean, ta_max, hm_mean, hm_min, td_mean, td_min,
            wind_ws_mean, wind_ws_max, wind_uu_mean, wind_vv_mean,
            wind_wd_sin_mean, wind_wd_cos_mean, wind_theta_deg,
            rn_day_mean, rn_day_max,
            effective_humidity,
            rn_class, rn_class1, rn_class2, rne_temp, rne,
            predwi, predwi_class,
            CAST(predwi_class AS DOUBLE) * rne AS dwi,
            (CAST(predwi_class AS DOUBLE) * rne) / 10.0 AS dwi_n,
            day_weight
        FROM dwi_calc
        """

    if args.parquet_only and not args.export_parquet_dir:
        raise ValueError("--parquet-only requires --export-parquet-dir")

    if not args.parquet_only:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {qname(args.output_table)} AS
            {select_sql}
            """
        )
        print(f"[DONE] {args.output_table}")
        print_table_summary(con, args.output_table, "date")

    if args.export_parquet_dir:
        export_path = project_path(__file__, args.export_parquet_dir)
        if not args.append_parquet:
            prepare_export_path(export_path, args.overwrite_parquet, args.months)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            COPY ({select_sql})
            TO '{sql_path(export_path)}'
            ({copy_options(args.parquet_compression, args.parquet_compression_level, True, args.append_parquet)})
            """
        )
        print(f"[EXPORT] {export_path}")
    con.close()


if __name__ == "__main__":
    main()

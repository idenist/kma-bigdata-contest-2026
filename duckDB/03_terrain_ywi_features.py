from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    build_date_where,
    connect,
    default_duckdb_path,
    default_master_grid_path,
    parquet_columns,
    print_table_summary,
    qname,
    require_columns,
    require_table,
    sql_path,
    sql_string_list,
)


EAST_COAST_YANGGAN = ["고성군", "속초시", "양양군", "강릉시", "동해시", "삼척시"]
INNER_YANGGAN = ["인제군", "양구군", "평창군", "정선군"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create terrain and Yanggan wind features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--master-grid", default=str(default_master_grid_path(__file__)))
    parser.add_argument("--weather-table", default="feat_weather_daily")
    parser.add_argument("--output-table", default="feat_terrain_ywi_daily")
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--wind-direction-mode", choices=["from", "to"], default="from")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    con = connect(Path(args.duckdb_path), args.threads, args.memory_limit)
    require_table(con, args.weather_table)
    master_path = Path(args.master_grid)
    require_columns(
        parquet_columns(con, master_path),
        ["grid_id", "elevation", "slope", "aspect_sin", "aspect_cos", "city_name"],
        str(master_path),
    )

    where_sql = build_date_where(args.months, args.start_date, args.end_date)
    west_angle = 270.0 if args.wind_direction_mode == "from" else 90.0

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {qname(args.output_table)} AS
        WITH static_grid AS (
            SELECT
                grid_id,
                TRY_CAST(elevation AS DOUBLE) AS elevation,
                TRY_CAST(slope AS DOUBLE) AS slope,
                TRY_CAST(aspect_sin AS DOUBLE) AS aspect_sin_raw,
                TRY_CAST(aspect_cos AS DOUBLE) AS aspect_cos_raw,
                city_name
            FROM read_parquet('{sql_path(master_path)}', union_by_name=true)
        ),
        joined AS (
            SELECT
                w.grid_id, w.date, w.month,
                w.wind_ws_max, w.wind_theta_deg, w.effective_humidity,
                s.elevation, s.slope, s.aspect_sin_raw, s.aspect_cos_raw, s.city_name
            FROM {qname(args.weather_table)} w
            INNER JOIN static_grid s USING (grid_id)
            {where_sql}
        ),
        terrain AS (
            SELECT
                *,
                (DEGREES(ATAN2(aspect_sin_raw, aspect_cos_raw)) + 360.0) % 360.0 AS aspect_deg,
                LEAST(GREATEST(slope / 35.0, 0.0), 1.0) AS slope_n
            FROM joined
        ),
        ywi_base AS (
            SELECT
                *,
                SIN(RADIANS(aspect_deg)) AS aspect_sin,
                COS(RADIANS(aspect_deg)) AS aspect_cos,
                CASE
                    WHEN REPLACE(TRIM(city_name), ' ', '') IN ({sql_string_list(EAST_COAST_YANGGAN)}) THEN 1.0
                    WHEN REPLACE(TRIM(city_name), ' ', '') IN ({sql_string_list(INNER_YANGGAN)}) THEN 0.5
                    ELSE 0.0
                END AS Rm,
                LEAST(GREATEST((wind_ws_max - 7.0) / 4.0, 0.0), 1.0) AS Ws,
                GREATEST(0.0, COS(RADIANS(wind_theta_deg - {west_angle}))) AS Dr,
                LEAST(GREATEST((40.0 - effective_humidity) / 15.0, 0.0), 1.0) AS Da
            FROM terrain
        ),
        tmi_base AS (
            SELECT
                *,
                Rm * Ws * Dr * Da AS ywi,
                LEAST(
                    GREATEST(
                        0.5 * LEAST(GREATEST(elevation / 1000.0, 0.0), 1.0)
                        + 0.5 * ((1.0 - COS(RADIANS(aspect_deg))) / 2.0),
                        0.0
                    ),
                    1.0
                ) AS tmi_base_n
            FROM ywi_base
        )
        SELECT
            grid_id, date, month,
            elevation, slope, aspect_sin, aspect_cos, aspect_deg,
            tmi_base_n,
            0.55 * tmi_base_n + 0.25 * slope_n + 0.20 * ywi AS tmi_p,
            ywi, Rm, Ws, Dr, Da
        FROM tmi_base
        """
    )
    print("[DONE] feat_terrain_ywi_daily")
    print_table_summary(con, args.output_table, "date")
    print("[NOTE] tmi_base_n uses elevation/aspect proxy until official FFDRI elevation_index/aspect_index is added.")
    con.close()


if __name__ == "__main__":
    main()

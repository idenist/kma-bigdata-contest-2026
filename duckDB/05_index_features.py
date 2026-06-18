from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    build_date_where,
    connect,
    copy_options,
    default_duckdb_path,
    prepare_export_path,
    print_table_summary,
    project_path,
    qname,
    require_table,
    source_sql,
    sql_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create FFDRI and P-FFDRI index features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--weather-table", default="feat_weather_daily")
    parser.add_argument("--forest-table", default="feat_forest_static")
    parser.add_argument("--terrain-table", default="feat_terrain_ywi_daily")
    parser.add_argument("--power-table", default="feat_power_access_static")
    parser.add_argument("--output-table", default="feat_pffdri_daily")
    parser.add_argument("--weather-parquet")
    parser.add_argument("--forest-parquet")
    parser.add_argument("--terrain-parquet")
    parser.add_argument("--power-parquet")
    parser.add_argument("--export-parquet-dir", help="Directory for partitioned Parquet export.")
    parser.add_argument("--parquet-only", action="store_true", help="Export Parquet without creating the DuckDB table.")
    parser.add_argument("--parquet-compression", default="ZSTD", choices=["ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4"])
    parser.add_argument("--parquet-compression-level", type=int, default=6)
    parser.add_argument("--overwrite-parquet", action="store_true")
    parser.add_argument("--append-parquet", action="store_true", help="Append to an existing partitioned Parquet directory.")
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    args = parser.parse_args()

    duckdb_path = project_path(__file__, args.duckdb_path)
    weather_parquet = project_path(__file__, args.weather_parquet) if args.weather_parquet else None
    forest_parquet = project_path(__file__, args.forest_parquet) if args.forest_parquet else None
    terrain_parquet = project_path(__file__, args.terrain_parquet) if args.terrain_parquet else None
    power_parquet = project_path(__file__, args.power_parquet) if args.power_parquet else None
    con = connect(duckdb_path, args.threads, args.memory_limit)
    for table, parquet_path in [
        (args.weather_table, weather_parquet),
        (args.forest_table, forest_parquet),
        (args.terrain_table, terrain_parquet),
        (args.power_table, power_parquet),
    ]:
        if not parquet_path:
            require_table(con, table)

    where_sql = build_date_where(args.months, args.start_date, args.end_date)
    weather_source = source_sql(args.weather_table, weather_parquet)
    forest_source = source_sql(args.forest_table, forest_parquet)
    terrain_source = source_sql(args.terrain_table, terrain_parquet)
    power_source = source_sql(args.power_table, power_parquet)

    select_sql = f"""
        SELECT
            w.grid_id,
            w.date,
            w.month,
            w.day_weight,
            w.dwi,
            w.dwi_n,
            f.fmi,
            f.fmi_n,
            t.tmi_base_n,
            t.tmi_p,
            p.pei,
            ((7.0 * w.dwi) + (1.5 * f.fmi) + (1.5 * (t.tmi_base_n * 10.0))) * w.day_weight AS ffdri,
            100.0 * (
                0.55 * w.dwi_n
                + 0.10 * f.fmi_n
                + 0.15 * t.tmi_p
                + 0.20 * p.pei
            ) * w.day_weight AS pffdri
        FROM {weather_source} w
        LEFT JOIN {forest_source} f USING (grid_id)
        LEFT JOIN {terrain_source} t USING (grid_id, date)
        LEFT JOIN {power_source} p USING (grid_id)
        {where_sql}
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

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
    table_exists,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Join feature tables into final EDA/modeling dataset.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--weather-table", default="feat_weather_daily")
    parser.add_argument("--forest-table", default="feat_forest_static")
    parser.add_argument("--terrain-table", default="feat_terrain_ywi_daily")
    parser.add_argument("--power-table", default="feat_power_access_static")
    parser.add_argument("--index-table", default="feat_pffdri_daily")
    parser.add_argument("--target-table", default="target_fire_static")
    parser.add_argument("--output-table", default="final_feature_daily")
    parser.add_argument("--weather-parquet")
    parser.add_argument("--forest-parquet")
    parser.add_argument("--terrain-parquet")
    parser.add_argument("--power-parquet")
    parser.add_argument("--index-parquet")
    parser.add_argument("--target-parquet")
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--export-parquet")
    parser.add_argument("--export-parquet-dir", help="Directory for partitioned Parquet export.")
    parser.add_argument("--parquet-only", action="store_true", help="Export Parquet without creating the DuckDB table.")
    parser.add_argument("--parquet-compression", default="ZSTD", choices=["ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4"])
    parser.add_argument("--parquet-compression-level", type=int, default=6)
    parser.add_argument("--overwrite-parquet", action="store_true")
    parser.add_argument("--append-parquet", action="store_true", help="Append to an existing partitioned Parquet directory.")
    parser.add_argument("--without-target", action="store_true", help="Do not join fire target columns.")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    args = parser.parse_args()

    duckdb_path = project_path(__file__, args.duckdb_path)
    weather_parquet = project_path(__file__, args.weather_parquet) if args.weather_parquet else None
    forest_parquet = project_path(__file__, args.forest_parquet) if args.forest_parquet else None
    terrain_parquet = project_path(__file__, args.terrain_parquet) if args.terrain_parquet else None
    power_parquet = project_path(__file__, args.power_parquet) if args.power_parquet else None
    index_parquet = project_path(__file__, args.index_parquet) if args.index_parquet else None
    target_parquet = project_path(__file__, args.target_parquet) if args.target_parquet else None
    con = connect(duckdb_path, args.threads, args.memory_limit)
    for table, parquet_path in [
        (args.weather_table, weather_parquet),
        (args.forest_table, forest_parquet),
        (args.terrain_table, terrain_parquet),
        (args.power_table, power_parquet),
        (args.index_table, index_parquet),
    ]:
        if not parquet_path:
            require_table(con, table)

    include_target = (not args.without_target) and (bool(target_parquet) or table_exists(con, args.target_table))
    target_select = ", tg.fire_label, tg.occu_year, tg.occu_mt, tg.occu_date" if include_target else ""
    weather_source = source_sql(args.weather_table, weather_parquet)
    forest_source = source_sql(args.forest_table, forest_parquet)
    terrain_source = source_sql(args.terrain_table, terrain_parquet)
    power_source = source_sql(args.power_table, power_parquet)
    index_source = source_sql(args.index_table, index_parquet)
    target_source = source_sql(args.target_table, target_parquet)
    target_join = f"LEFT JOIN {target_source} tg USING (grid_id)" if include_target else ""
    where_sql = build_date_where(args.months, args.start_date, args.end_date)

    select_sql = f"""
        SELECT
            w.grid_id,
            w.date,
            w.month,
            w.day_weight,
            w.ta_mean,
            w.ta_max,
            w.hm_mean,
            w.hm_min,
            w.wind_ws_mean,
            w.wind_ws_max,
            w.wind_wd_sin_mean,
            w.wind_wd_cos_mean,
            w.rn_day_mean,
            w.rn_day_max,
            w.effective_humidity,
            w.rne,
            w.dwi,
            w.dwi_n,
            f.is_forest,
            f.forest_type_code,
            f.fmi,
            f.fmi_n,
            f.age_class_code,
            f.tree_height_code,
            t.elevation,
            t.slope,
            t.aspect_sin,
            t.aspect_cos,
            t.aspect_deg,
            t.tmi_base_n,
            t.tmi_p,
            t.ywi,
            t.Rm,
            t.Ws,
            t.Dr,
            t.Da,
            p.pole_count,
            p.pole_n,
            p.nearest_road_dist,
            p.nearest_river_dist,
            p.road_prox,
            p.river_far,
            p.pei,
            i.ffdri,
            i.pffdri
            {target_select}
        FROM {weather_source} w
        LEFT JOIN {forest_source} f USING (grid_id)
        LEFT JOIN {terrain_source} t USING (grid_id, date)
        LEFT JOIN {power_source} p USING (grid_id)
        LEFT JOIN {index_source} i USING (grid_id, date)
        {target_join}
        {where_sql}
        """

    if args.parquet_only and not (args.export_parquet or args.export_parquet_dir):
        raise ValueError("--parquet-only requires --export-parquet or --export-parquet-dir")

    if not args.parquet_only:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {qname(args.output_table)} AS
            {select_sql}
            """
        )
        print(f"[DONE] {args.output_table}")
        print_table_summary(con, args.output_table, "date")

    if args.export_parquet:
        export_path = project_path(__file__, args.export_parquet)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if args.overwrite_parquet and export_path.exists():
            export_path.unlink()
        export_source = qname(args.output_table) if not args.parquet_only else f"({select_sql})"
        con.execute(
            f"""
            COPY {export_source}
            TO '{sql_path(export_path)}'
            ({copy_options(args.parquet_compression, args.parquet_compression_level, False)})
            """
        )
        print(f"[EXPORT] {export_path}")

    if args.export_parquet_dir:
        export_dir = project_path(__file__, args.export_parquet_dir)
        if not args.append_parquet:
            prepare_export_path(export_dir, args.overwrite_parquet, args.months)
        export_dir.parent.mkdir(parents=True, exist_ok=True)
        export_source = qname(args.output_table) if not args.parquet_only else f"({select_sql})"
        con.execute(
            f"""
            COPY {export_source}
            TO '{sql_path(export_dir)}'
            ({copy_options(args.parquet_compression, args.parquet_compression_level, True, args.append_parquet)})
            """
        )
        print(f"[EXPORT] {export_dir}")

    con.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    build_date_where,
    connect,
    default_duckdb_path,
    print_table_summary,
    qname,
    require_table,
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
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--export-parquet", default="data/final_feature_daily.parquet")
    parser.add_argument("--without-target", action="store_true", help="Do not join fire target columns.")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    con = connect(Path(args.duckdb_path), args.threads, args.memory_limit)
    for table in [args.weather_table, args.forest_table, args.terrain_table, args.power_table, args.index_table]:
        require_table(con, table)

    include_target = (not args.without_target) and table_exists(con, args.target_table)
    target_select = ", tg.fire_label, tg.occu_year, tg.occu_mt, tg.occu_date" if include_target else ""
    target_join = f"LEFT JOIN {qname(args.target_table)} tg USING (grid_id)" if include_target else ""
    where_sql = build_date_where(args.months, args.start_date, args.end_date)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {qname(args.output_table)} AS
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
        FROM {qname(args.weather_table)} w
        LEFT JOIN {qname(args.forest_table)} f USING (grid_id)
        LEFT JOIN {qname(args.terrain_table)} t USING (grid_id, date)
        LEFT JOIN {qname(args.power_table)} p USING (grid_id)
        LEFT JOIN {qname(args.index_table)} i USING (grid_id, date)
        {target_join}
        {where_sql}
        """
    )

    print("[DONE] final_feature_daily")
    print_table_summary(con, args.output_table, "date")

    if args.export_parquet:
        export_path = Path(args.export_parquet)
        if not export_path.is_absolute():
            export_path = Path(__file__).resolve().parent.parent / export_path
        export_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            COPY {qname(args.output_table)}
            TO '{sql_path(export_path)}'
            (FORMAT PARQUET, COMPRESSION SNAPPY)
            """
        )
        print(f"[EXPORT] {export_path}")

    con.close()


if __name__ == "__main__":
    main()

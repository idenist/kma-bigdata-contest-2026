from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import build_date_where, connect, default_duckdb_path, print_table_summary, qname, require_table


def main() -> None:
    parser = argparse.ArgumentParser(description="Create FFDRI and P-FFDRI index features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--weather-table", default="feat_weather_daily")
    parser.add_argument("--forest-table", default="feat_forest_static")
    parser.add_argument("--terrain-table", default="feat_terrain_ywi_daily")
    parser.add_argument("--power-table", default="feat_power_access_static")
    parser.add_argument("--output-table", default="feat_pffdri_daily")
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    con = connect(Path(args.duckdb_path), args.threads, args.memory_limit)
    for table in [args.weather_table, args.forest_table, args.terrain_table, args.power_table]:
        require_table(con, table)

    where_sql = build_date_where(args.months, args.start_date, args.end_date)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {qname(args.output_table)} AS
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
        FROM {qname(args.weather_table)} w
        LEFT JOIN {qname(args.forest_table)} f USING (grid_id)
        LEFT JOIN {qname(args.terrain_table)} t USING (grid_id, date)
        LEFT JOIN {qname(args.power_table)} p USING (grid_id)
        {where_sql}
        """
    )
    print("[DONE] feat_pffdri_daily")
    print_table_summary(con, args.output_table, "date")
    con.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    connect,
    default_duckdb_path,
    default_master_grid_path,
    parquet_columns,
    print_table_summary,
    qname,
    require_columns,
    sql_path,
)


DATE_CANDIDATES = ["occu_date", "date", "fire_date", "발생일자"]
YEAR_CANDIDATES = ["occu_year", "year", "발생연도"]
MONTH_CANDIDATES = ["occu_mt", "month", "발생월"]


def first_existing(cols: set[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in cols}
    for candidate in candidates:
        if candidate in cols:
            return candidate
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create fire target table separated from feature tables.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--master-grid", default=str(default_master_grid_path(__file__)))
    parser.add_argument("--fire-history", help="Optional fire history parquet path. If omitted, uses fire_label from master_grid.")
    parser.add_argument("--output-table", default="target_fire_static")
    parser.add_argument("--export-parquet", help="Optional target parquet export path")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    con = connect(Path(args.duckdb_path), args.threads, args.memory_limit)

    if args.fire_history:
        fire_path = Path(args.fire_history)
        cols = parquet_columns(con, fire_path)
        require_columns(cols, ["grid_id"], str(fire_path))
        date_col = first_existing(cols, DATE_CANDIDATES)
        year_col = first_existing(cols, YEAR_CANDIDATES)
        month_col = first_existing(cols, MONTH_CANDIDATES)

        occu_date_expr = f"MIN(CAST({qname(date_col)} AS DATE))" if date_col else "NULL::DATE"
        occu_year_expr = f"MIN(TRY_CAST({qname(year_col)} AS INTEGER))" if year_col else "NULL::INTEGER"
        occu_mt_expr = f"MIN(TRY_CAST({qname(month_col)} AS INTEGER))" if month_col else "NULL::INTEGER"

        con.execute(
            f"""
            CREATE OR REPLACE TABLE {qname(args.output_table)} AS
            SELECT
                grid_id,
                1 AS fire_label,
                {occu_year_expr} AS occu_year,
                {occu_mt_expr} AS occu_mt,
                {occu_date_expr} AS occu_date
            FROM read_parquet('{sql_path(fire_path)}', union_by_name=true)
            GROUP BY grid_id
            """
        )
    else:
        master_path = Path(args.master_grid)
        cols = parquet_columns(con, master_path)
        require_columns(cols, ["grid_id", "fire_label"], str(master_path))
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {qname(args.output_table)} AS
            SELECT
                grid_id,
                TRY_CAST(fire_label AS INTEGER) AS fire_label,
                NULL::INTEGER AS occu_year,
                NULL::INTEGER AS occu_mt,
                NULL::DATE AS occu_date
            FROM read_parquet('{sql_path(master_path)}', union_by_name=true)
            """
        )

    print("[DONE] target_fire_static")
    print_table_summary(con, args.output_table)

    if args.export_parquet:
        export_path = Path(args.export_parquet)
        if not export_path.is_absolute():
            export_path = Path.cwd() / export_path
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

from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    connect,
    copy_options,
    default_duckdb_path,
    default_master_grid_path,
    parquet_columns,
    prepare_export_path,
    print_table_summary,
    project_path,
    qname,
    require_columns,
    sql_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create static forest features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--master-grid", default=str(default_master_grid_path(__file__)))
    parser.add_argument("--output-table", default="feat_forest_static")
    parser.add_argument("--export-parquet", help="Parquet file export path.")
    parser.add_argument("--parquet-only", action="store_true", help="Export Parquet without creating the DuckDB table.")
    parser.add_argument("--parquet-compression", default="ZSTD", choices=["ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4"])
    parser.add_argument("--parquet-compression-level", type=int, default=6)
    parser.add_argument("--overwrite-parquet", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    args = parser.parse_args()

    con = connect(project_path(__file__, args.duckdb_path), args.threads, args.memory_limit)
    master_path = project_path(__file__, args.master_grid)
    require_columns(
        parquet_columns(con, master_path),
        ["grid_id", "is_forest", "forest_type_code", "age_class_code", "tree_height_code"],
        str(master_path),
    )

    select_sql = f"""
        WITH base AS (
            SELECT
                grid_id,
                TRY_CAST(is_forest AS INTEGER) AS is_forest,
                TRY_CAST(forest_type_code AS INTEGER) AS forest_type_code,
                TRY_CAST(age_class_code AS INTEGER) AS age_class_code,
                TRY_CAST(tree_height_code AS INTEGER) AS tree_height_code
            FROM read_parquet('{sql_path(master_path)}', union_by_name=true)
        )
        SELECT DISTINCT
            *,
            CASE
                WHEN is_forest != 1 THEN 0.0
                WHEN forest_type_code = 1 THEN 10.0
                WHEN forest_type_code = 2 THEN 2.0
                WHEN forest_type_code = 3 THEN 3.0
                ELSE 0.0
            END AS fmi,
            CASE
                WHEN is_forest != 1 THEN 0.0
                WHEN forest_type_code = 1 THEN 1.0
                WHEN forest_type_code = 2 THEN 0.2
                WHEN forest_type_code = 3 THEN 0.3
                ELSE 0.0
            END AS fmi_n
        FROM base
        """

    if args.parquet_only and not args.export_parquet:
        raise ValueError("--parquet-only requires --export-parquet")

    if not args.parquet_only:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {qname(args.output_table)} AS
            {select_sql}
            """
        )
        print(f"[DONE] {args.output_table}")
        print_table_summary(con, args.output_table)

    if args.export_parquet:
        export_path = project_path(__file__, args.export_parquet)
        prepare_export_path(export_path, args.overwrite_parquet, None)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            COPY ({select_sql})
            TO '{sql_path(export_path)}'
            ({copy_options(args.parquet_compression, args.parquet_compression_level, False)})
            """
        )
        print(f"[EXPORT] {export_path}")
    con.close()


if __name__ == "__main__":
    main()

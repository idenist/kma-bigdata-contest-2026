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


def main() -> None:
    parser = argparse.ArgumentParser(description="Create power/accessibility PEI features.")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--master-grid", default=str(default_master_grid_path(__file__)))
    parser.add_argument("--output-table", default="feat_power_access_static")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    con = connect(Path(args.duckdb_path), args.threads, args.memory_limit)
    master_path = Path(args.master_grid)
    require_columns(
        parquet_columns(con, master_path),
        ["grid_id", "pole_count", "nearest_road_dist", "nearest_river_dist", "is_forest"],
        str(master_path),
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {qname(args.output_table)} AS
        WITH base AS (
            SELECT
                grid_id,
                COALESCE(TRY_CAST(pole_count AS DOUBLE), 0.0) AS pole_count,
                TRY_CAST(nearest_road_dist AS DOUBLE) AS nearest_road_dist,
                TRY_CAST(nearest_river_dist AS DOUBLE) AS nearest_river_dist,
                TRY_CAST(is_forest AS INTEGER) AS is_forest
            FROM read_parquet('{sql_path(master_path)}', union_by_name=true)
        ),
        stats AS (
            SELECT
                NULLIF(QUANTILE_CONT(pole_count, 0.95), 0.0) AS pole_q95,
                NULLIF(QUANTILE_CONT(nearest_road_dist, 0.95), 0.0) AS road_q95,
                NULLIF(QUANTILE_CONT(nearest_river_dist, 0.95), 0.0) AS river_q95
            FROM base
        ),
        norm AS (
            SELECT
                b.*,
                LEAST(GREATEST(b.pole_count / COALESCE(s.pole_q95, 1.0), 0.0), 1.0) AS pole_n,
                1.0 - LEAST(GREATEST(b.nearest_road_dist / COALESCE(s.road_q95, 1.0), 0.0), 1.0) AS road_prox,
                LEAST(GREATEST(b.nearest_river_dist / COALESCE(s.river_q95, 1.0), 0.0), 1.0) AS river_far,
                CASE WHEN b.is_forest = 1 THEN 1.0 ELSE 0.0 END AS forest_contact
            FROM base b
            CROSS JOIN stats s
        )
        SELECT
            grid_id,
            pole_count,
            pole_n,
            nearest_road_dist,
            nearest_river_dist,
            road_prox,
            river_far,
            forest_contact,
            0.45 * pole_n + 0.20 * road_prox + 0.20 * river_far + 0.15 * forest_contact AS pei
        FROM norm
        """
    )
    print("[DONE] feat_power_access_static")
    print_table_summary(con, args.output_table)
    con.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from pffdri_common import (
    build_date_where,
    connect,
    default_duckdb_path,
    parquet_source,
    prepare_export_path,
    project_path,
    sql_path,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FINAL_ROOT = Path("output") / "final" / "final_feature_daily"
DEFAULT_TARGET_DIR = Path("output") / "target"
DEFAULT_OUTPUT_DIR = Path("output") / "eda"
DEFAULT_GRID_SIZES = ("500m", "1km", "2km")


def parse_grid_sizes(value: str) -> list[str]:
    labels: list[str] = []
    for item in value.split(","):
        label = item.strip().lower()
        if not label:
            continue
        label = label.replace("1000m", "1km").replace("2000m", "2km")
        if label not in {"500m", "1km", "2km"}:
            raise ValueError(f"unsupported grid size label: {item}")
        labels.append(label)
    if not labels:
        raise ValueError("at least one grid size is required")
    return labels


def ensure_parquet_exists(path: Path, name: str) -> None:
    if path.is_file() and path.suffix.lower() == ".parquet":
        return
    if path.is_dir() and any(path.rglob("*.parquet")):
        return
    raise FileNotFoundError(f"{name} parquet not found: {path}")


def prefixed_columns(alias: str, columns: list[str]) -> str:
    return ",\n                ".join(f"{alias}.{column}" for column in columns)


def target_null_columns() -> str:
    return """
                NULL::VARCHAR AS target_grid_id,
                NULL::INTEGER AS target_grid_size_m,
                NULL::BIGINT AS target_grid_x,
                NULL::BIGINT AS target_grid_y,
                0 AS fire_label,
                0 AS fire_count,
                NULL::VARCHAR AS fire_objt_ids,
                NULL::VARCHAR AS fire_addresses,
                NULL::DOUBLE AS fire_amount_sum,
                NULL::DOUBLE AS event_lon_mean,
                NULL::DOUBLE AS event_lat_mean,
                NULL::DOUBLE AS event_x5179_mean,
                NULL::DOUBLE AS event_y5179_mean
    """.strip()


def target_columns(alias: str = "t") -> str:
    names = [
        "target_grid_id",
        "target_grid_size_m",
        "target_grid_x",
        "target_grid_y",
        "fire_label",
        "fire_count",
        "fire_objt_ids",
        "fire_addresses",
        "fire_amount_sum",
        "event_lon_mean",
        "event_lat_mean",
        "event_x5179_mean",
        "event_y5179_mean",
    ]
    return ",\n                ".join(f"{alias}.{name}" for name in names)


def append_condition(where_sql: str, condition: str) -> str:
    if where_sql.strip():
        return f"{where_sql}\n            AND {condition}"
    return f"WHERE {condition}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create compact EDA-ready samples by joining final features with daily fire targets."
    )
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--final-root", default=str(DEFAULT_FINAL_ROOT))
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--grid-sizes", default=",".join(DEFAULT_GRID_SIZES))
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--negative-ratio", type=int, default=10)
    parser.add_argument("--max-negative-rows", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20260619)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    args = parser.parse_args()

    final_root = project_path(__file__, args.final_root)
    target_dir = project_path(__file__, args.target_dir)
    output_dir = project_path(__file__, args.output_dir)
    duckdb_path = project_path(__file__, args.duckdb_path)
    grid_sizes = parse_grid_sizes(args.grid_sizes)

    ensure_parquet_exists(final_root, "final_feature_daily")
    output_dir.mkdir(parents=True, exist_ok=True)

    con = connect(duckdb_path, args.threads, args.memory_limit)
    final_source = parquet_source(final_root)
    where_sql = build_date_where(args.months, args.start_date, args.end_date)
    final_columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {final_source}").fetchall()]
    final_select = prefixed_columns("f", final_columns)

    final_count = con.execute(f"SELECT COUNT(*) FROM {final_source} {where_sql}").fetchone()[0]
    if final_count == 0:
        raise ValueError("No final feature rows found for the requested date/month filter.")

    for index, label in enumerate(grid_sizes, 1):
        print(f"[PROGRESS] EDA grid_size {index}/{len(grid_sizes)} {label}")
        target_path = target_dir / f"target_fire_daily_{label}.parquet"
        ensure_parquet_exists(target_path, f"target_fire_daily_{label}")
        target_source = parquet_source(target_path)

        positive_count = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {final_source} f
            INNER JOIN {target_source} t USING (date, grid_id)
            {where_sql}
            """
        ).fetchone()[0]

        negative_limit = min(args.max_negative_rows, positive_count * args.negative_ratio)
        negative_threshold = 0 if negative_limit <= 0 else max(1, int((negative_limit / final_count) * 1_000_000))

        positive_output = output_dir / f"eda_positive_{label}.parquet"
        sample_output = output_dir / f"eda_sample_{label}.parquet"
        label_counts_output = output_dir / f"eda_label_counts_{label}.csv"
        monthly_output = output_dir / f"eda_monthly_counts_{label}.csv"

        for path in [positive_output, sample_output, label_counts_output, monthly_output]:
            prepare_export_path(path, args.overwrite, None)

        positive_sql = f"""
            SELECT
                {final_select},
                {target_columns("t")}
            FROM {final_source} f
            INNER JOIN {target_source} t USING (date, grid_id)
            {where_sql}
        """

        negative_sql = f"""
            SELECT
                {final_select},
                {target_null_columns()}
            FROM {final_source} f
            ANTI JOIN {target_source} t USING (date, grid_id)
            {append_condition(where_sql, f"MOD(HASH(f.grid_id, f.date, {args.seed}), 1000000) < {negative_threshold}")}
        """

        sample_sql = f"""
            WITH eda_sample AS (
                {positive_sql}
                UNION ALL
                {negative_sql}
            )
            SELECT *
            FROM eda_sample
        """

        con.execute(
            f"""
            COPY ({positive_sql})
            TO '{sql_path(positive_output)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 6)
            """
        )
        con.execute(
            f"""
            COPY ({sample_sql})
            TO '{sql_path(sample_output)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 6)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT
                    fire_label,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT grid_id) AS grid_count,
                    COUNT(DISTINCT date) AS date_count,
                    MIN(date) AS min_date,
                    MAX(date) AS max_date,
                    AVG(pffdri) AS avg_pffdri,
                    AVG(ffdri) AS avg_ffdri
                FROM read_parquet('{sql_path(sample_output)}', union_by_name=true)
                GROUP BY fire_label
                ORDER BY fire_label DESC
            )
            TO '{sql_path(label_counts_output)}'
            (FORMAT CSV, HEADER TRUE, DELIMITER ',')
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT
                    month,
                    fire_label,
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT grid_id) AS grid_count,
                    AVG(pffdri) AS avg_pffdri,
                    AVG(ffdri) AS avg_ffdri
                FROM read_parquet('{sql_path(sample_output)}', union_by_name=true)
                GROUP BY month, fire_label
                ORDER BY month, fire_label DESC
            )
            TO '{sql_path(monthly_output)}'
            (FORMAT CSV, HEADER TRUE, DELIMITER ',')
            """
        )

        sample_counts = con.execute(
            f"""
            SELECT fire_label, COUNT(*) AS row_count
            FROM read_parquet('{sql_path(sample_output)}', union_by_name=true)
            GROUP BY fire_label
            ORDER BY fire_label DESC
            """
        ).fetchall()
        print(
            f"[EXPORT] {label}: positive={positive_count:,}, "
            f"negative_limit={negative_limit:,}, sample_counts={sample_counts}, path={sample_output}"
        )

    con.close()


if __name__ == "__main__":
    main()

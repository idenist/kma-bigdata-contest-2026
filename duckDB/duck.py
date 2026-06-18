r"""
Load grid_date_master parquet files into a persistent DuckDB table.

Directory example:
    grid_date_master/
      month=2020-02/
        part0.parquet
        part1.parquet
        ...

Default behavior:
    - Recursively finds part*.parquet
    - Keeps files whose part number is <= --max-part
    - Preserves hive partition column such as month from month=YYYY-MM folders
    - Creates or replaces a DuckDB table

Portable default when this file is placed at:
    <project_root>/duckDB/duck.py

Then this command is enough:
    python duckDB/duck.py --max-part 721

Default paths:
    parquet root: <project_root>/data/grid_date_master
    duckdb path : <project_root>/duckDB/pffdri.duckdb

You can still override paths with --parquet-root and --duckdb-path.

Install:
    pip install duckdb
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import duckdb


# Supports both common parquet chunk names:
#   part0.parquet
#   part.0.parquet
PART_RE = re.compile(r"^part\.?(\d+)\.parquet$", re.IGNORECASE)


def collect_part_files(parquet_root: Path, max_part: int) -> list[Path]:
    if not parquet_root.exists():
        raise FileNotFoundError(f"Parquet root does not exist: {parquet_root}")

    files: list[tuple[str, int, Path]] = []
    for path in parquet_root.rglob("*.parquet"):
        match = PART_RE.match(path.name)
        if not match:
            continue
        part_no = int(match.group(1))
        if part_no <= max_part:
            month_key = next((p.name for p in path.parents if p.name.startswith("month=")), "")
            files.append((month_key, part_no, path))

    files.sort(key=lambda x: (x[0], x[1], str(x[2])))
    return [p for _, _, p in files]


def to_duckdb_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def sql_array(paths: list[Path]) -> str:
    return "[" + ", ".join(f"'{to_duckdb_path(p)}'" for p in paths) + "]"


def default_project_paths() -> tuple[Path, Path]:
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    parquet_root = project_root / "data" / "grid_date_master"
    duckdb_path = script_path.parent / "pffdri.duckdb"
    return parquet_root, duckdb_path


def main() -> None:
    default_parquet_root, default_duckdb_path = default_project_paths()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--parquet-root",
        default=str(default_parquet_root),
        help="grid_date_master root directory. Default: <project_root>/data/grid_date_master",
    )
    parser.add_argument(
        "--duckdb-path",
        default=str(default_duckdb_path),
        help="Output DuckDB database file. Default: <project_root>/duckDB/pffdri.duckdb",
    )
    parser.add_argument("--table", default="grid_date_master", help="DuckDB table name")
    parser.add_argument("--max-part", type=int, default=721, help="Load part files up to this number")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--append", action="store_true", help="Append instead of CREATE OR REPLACE")
    args = parser.parse_args()

    parquet_root = Path(args.parquet_root)
    duckdb_path = Path(args.duckdb_path)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    files = collect_part_files(parquet_root, args.max_part)
    if not files:
        raise FileNotFoundError(f"No part*.parquet files found up to part{args.max_part}: {parquet_root}")

    print(f"[INFO] parquet root : {parquet_root}")
    print(f"[INFO] duckdb path  : {duckdb_path}")
    print(f"[INFO] table        : {args.table}")
    print(f"[INFO] max part     : {args.max_part}")
    print(f"[INFO] file count   : {len(files):,}")
    print(f"[INFO] first file   : {files[0]}")
    print(f"[INFO] last file    : {files[-1]}")

    con = duckdb.connect(str(duckdb_path))
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")

    file_array = sql_array(files)
    table_name = args.table.replace('"', '""')

    if args.append:
        sql = f"""
        INSERT INTO "{table_name}"
        SELECT *
        FROM read_parquet(
            {file_array},
            hive_partitioning=true,
            union_by_name=true,
            filename=true
        )
        """
    else:
        sql = f"""
        CREATE OR REPLACE TABLE "{table_name}" AS
        SELECT *
        FROM read_parquet(
            {file_array},
            hive_partitioning=true,
            union_by_name=true,
            filename=true
        )
        """

    print("[INFO] Loading parquet files into DuckDB...")
    con.execute(sql)

    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS row_count,
            COUNT(DISTINCT grid_id) AS grid_count,
            MIN(CAST(date AS DATE)) AS min_date,
            MAX(CAST(date AS DATE)) AS max_date,
            COUNT(DISTINCT month) AS month_count
        FROM "{table_name}"
        """
    ).df()
    print("[DONE] load complete")
    print(summary.to_string(index=False))

    print("\n[INFO] Month summary")
    month_summary = con.execute(
        f"""
        SELECT
            month,
            COUNT(*) AS row_count,
            COUNT(DISTINCT grid_id) AS grid_count,
            COUNT(DISTINCT CAST(date AS DATE)) AS date_count,
            MIN(CAST(date AS DATE)) AS min_date,
            MAX(CAST(date AS DATE)) AS max_date
        FROM "{table_name}"
        GROUP BY month
        ORDER BY month
        """
    ).df()
    print(month_summary.to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()

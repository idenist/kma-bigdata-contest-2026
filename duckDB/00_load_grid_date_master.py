from __future__ import annotations

import argparse
import re
from pathlib import Path

from pffdri_common import connect, default_duckdb_path, default_grid_date_root, qname, sql_path


PART_RE = re.compile(r"^part\.?(\d+)\.parquet$", re.IGNORECASE)


def collect_part_files(root: Path, max_part: int) -> list[Path]:
    files: list[tuple[str, int, Path]] = []
    for path in root.rglob("*.parquet"):
        match = PART_RE.match(path.name)
        if not match:
            continue
        part_no = int(match.group(1))
        if part_no <= max_part:
            month_key = next((p.name for p in path.parents if p.name.startswith("month=")), "")
            files.append((month_key, part_no, path))
    files.sort(key=lambda x: (x[0], x[1], str(x[2])))
    return [p for _, _, p in files]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load grid_date_master parquet files into DuckDB.")
    parser.add_argument("--parquet-root", default=str(default_grid_date_root(__file__)))
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--table", default="grid_date_master")
    parser.add_argument("--max-part", type=int, default=721)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    args = parser.parse_args()

    parquet_root = Path(args.parquet_root)
    db_path = Path(args.duckdb_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    files = collect_part_files(parquet_root, args.max_part)
    if not files:
        raise FileNotFoundError(f"No part*.parquet files found up to part{args.max_part}: {parquet_root}")

    file_array = "[" + ", ".join(f"'{sql_path(p)}'" for p in files) + "]"
    con = connect(db_path, args.threads, args.memory_limit)

    print(f"[INFO] files={len(files):,}")
    print(f"[INFO] first={files[0]}")
    print(f"[INFO] last ={files[-1]}")

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {qname(args.table)} AS
        SELECT *
        FROM read_parquet(
            {file_array},
            hive_partitioning=true,
            union_by_name=true,
            filename=true
        )
        """
    )

    print("[DONE] loaded")
    print(
        con.execute(
            f"""
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT grid_id) AS grid_count,
                MIN(CAST(date AS DATE)) AS min_date,
                MAX(CAST(date AS DATE)) AS max_date,
                COUNT(DISTINCT month) AS month_count
            FROM {qname(args.table)}
            """
        )
        .df()
        .to_string(index=False)
    )
    con.close()


if __name__ == "__main__":
    main()

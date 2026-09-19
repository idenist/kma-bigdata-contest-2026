from __future__ import annotations

import shutil
from pathlib import Path

import duckdb


def project_root(script_file: str | Path) -> Path:
    script_path = Path(script_file).resolve()
    if script_path.parent.name.lower() == "duckdb":
        return script_path.parent.parent
    return script_path.parent


def default_duckdb_path(script_file: str | Path) -> Path:
    return Path("duckDB") / "pffdri.duckdb"


def default_master_grid_path(script_file: str | Path) -> Path:
    return Path("data") / "master_grid.parquet"


def default_grid_date_root(script_file: str | Path) -> Path:
    return Path("data") / "grid_date_master"


def project_path(script_file: str | Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root(script_file) / candidate


def sql_path(path: str | Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/").replace("'", "''")


def qname(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_string_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def parquet_source(path: str | Path) -> str:
    parquet_path = Path(path)
    pattern = parquet_path if parquet_path.suffix.lower() == ".parquet" else parquet_path / "**" / "*.parquet"
    return f"read_parquet('{sql_path(pattern)}', union_by_name=true, hive_partitioning=true)"


def source_sql(table: str, parquet_path: str | Path | None) -> str:
    return parquet_source(parquet_path) if parquet_path else qname(table)


def copy_options(compression: str, compression_level: int | None, partition_by_month: bool, append: bool = False) -> str:
    options = ["FORMAT PARQUET", f"COMPRESSION {compression.upper()}"]
    if compression_level is not None and compression.upper() in {"ZSTD", "GZIP", "BROTLI"}:
        options.append(f"COMPRESSION_LEVEL {compression_level}")
    if partition_by_month:
        options.append("PARTITION_BY (month)")
    if append:
        options.append("APPEND")
    return ", ".join(options)


def prepare_export_path(path: Path, overwrite: bool, months: list[str] | None) -> None:
    if not overwrite:
        return
    if months:
        for month in months:
            month_path = path / f"month={month}"
            if month_path.exists():
                shutil.rmtree(month_path)
    elif path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def connect(db_path: str | Path, threads: int = 4, memory_limit: str = "8GB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute(f"PRAGMA threads={threads}")
    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    return con


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return table in {row[0] for row in con.execute("SHOW TABLES").fetchall()}


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {row[0] for row in con.execute(f"DESCRIBE {qname(table)}").fetchall()}


def parquet_columns(con: duckdb.DuckDBPyConnection, parquet_path: str | Path) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{sql_path(parquet_path)}', union_by_name=true)"
        ).fetchall()
    }


def require_table(con: duckdb.DuckDBPyConnection, table: str) -> None:
    if not table_exists(con, table):
        existing = sorted(row[0] for row in con.execute("SHOW TABLES").fetchall())
        raise KeyError(f"Required table not found: {table}. Existing tables: {existing}")


def require_columns(actual: set[str], required: list[str], source_name: str) -> None:
    missing = [col for col in required if col not in actual]
    if missing:
        raise KeyError(f"{source_name} missing required columns: {missing}")


def build_date_where(months: list[str] | None, start_date: str | None, end_date: str | None) -> str:
    clauses = []
    if months:
        clauses.append(f"strftime(CAST(date AS DATE), '%Y-%m') IN ({sql_string_list(months)})")
    if start_date:
        clauses.append(f"CAST(date AS DATE) >= DATE '{start_date}'")
    if end_date:
        clauses.append(f"CAST(date AS DATE) <= DATE '{end_date}'")
    return "" if not clauses else "WHERE " + " AND ".join(clauses)


def print_table_summary(con: duckdb.DuckDBPyConnection, table: str, date_col: str | None = None) -> None:
    cols = table_columns(con, table)
    parts = ["COUNT(*) AS row_count"]
    if "grid_id" in cols:
        parts.append("COUNT(DISTINCT grid_id) AS grid_count")
    if date_col and date_col in cols:
        parts.append(f"MIN(CAST({date_col} AS DATE)) AS min_date")
        parts.append(f"MAX(CAST({date_col} AS DATE)) AS max_date")
    sql = f"SELECT {', '.join(parts)} FROM {qname(table)}"
    print(con.execute(sql).df().to_string(index=False))

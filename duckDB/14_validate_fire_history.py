from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

try:
    from pyproj import Transformer
except ImportError as exc:
    raise ImportError("pyproj is required. Install it with: pip install pyproj") from exc


DEFAULT_FIRE_CSV = "data/산불발생이력.csv"
DEFAULT_MASTER_GRID = "data/master_grid.parquet"
DEFAULT_GRID_DATE_RISK = "output/risk/grid_date_risk/**/*.parquet"
DEFAULT_OUTPUT_DIR = "output/validation"
DEFAULT_REPORT_DIR = "output/report/risk"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def project_root_from_script() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name.lower() == "duckdb":
        return current.parent.parent
    return current.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def sql_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_csv_auto(path: Path, **kwargs) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    last_error: Exception | None = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Failed to read CSV with encodings {encodings}: {path}") from last_error


def get_parquet_columns(con: duckdb.DuckDBPyConnection, parquet_path: Path) -> list[str]:
    rows = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet('{sql_path(parquet_path)}', union_by_name=true)
        LIMIT 0
        """
    ).fetchall()
    return [r[0] for r in rows]


def get_risk_columns(con: duckdb.DuckDBPyConnection, risk_glob: Path) -> list[str]:
    rows = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet('{sql_path(risk_glob)}', hive_partitioning=true, union_by_name=true)
        LIMIT 0
        """
    ).fetchall()
    return [r[0] for r in rows]


def detect_col(columns: list[str], candidates: list[str], required: bool = True) -> str | None:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    if required:
        raise KeyError(f"Could not detect column. candidates={candidates}, available={columns}")
    return None


def detect_region_col(master_cols: list[str], requested: str) -> str | None:
    if requested.lower() in {"none", "null", ""}:
        return None
    if requested.lower() != "auto":
        if requested not in master_cols:
            raise KeyError(f"--region-col '{requested}' not found in master_grid columns: {master_cols}")
        return requested

    candidates = [
        "city_name",
        "sigungu_nm",
        "sgg_nm",
        "시군구명",
        "시군구",
        "adm_sigungu_nm",
        "region_sigungu",
        "sido_nm",
        "시도명",
    ]
    for cand in candidates:
        if cand in master_cols:
            return cand

    for col in master_cols:
        lc = col.lower()
        if any(k in lc for k in ["city", "sigungu", "sgg"]):
            return col
        if "시군구" in col:
            return col
    return None


def parse_months(value: str) -> list[int]:
    months = [int(x.strip()) for x in value.split(",") if x.strip()]
    if not months:
        raise ValueError("month list is empty")
    invalid = [m for m in months if m < 1 or m > 12]
    if invalid:
        raise ValueError(f"invalid months: {invalid}")
    return months


def file_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def df_to_markdown(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "(no rows)"
    view = df.head(max_rows).copy()
    str_df = view.astype(object).where(pd.notnull(view), "")
    headers = list(str_df.columns)
    rows = str_df.values.tolist()

    def fmt(x) -> str:
        if isinstance(x, float):
            return f"{x:.6g}"
        return str(x)

    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(fmt(x) for x in row) + " |")
    return "\n".join(lines)


def validate_inputs(fire_csv: Path, master_grid: Path, risk_glob: Path) -> None:
    if not fire_csv.exists():
        raise FileNotFoundError(f"fire history csv not found: {fire_csv}")
    if not master_grid.exists():
        raise FileNotFoundError(f"master_grid not found: {master_grid}")
    risk_root = str(risk_glob).split("**")[0].rstrip("/\\")
    if not Path(risk_root).exists():
        raise FileNotFoundError(f"grid-date risk root not found: {risk_root}")


def load_and_map_fire_history(
    fire_csv: Path,
    master_grid: Path,
    output_parquet: Path,
    output_unmatched_csv: Path,
    args: argparse.Namespace,
) -> dict:
    log("[1/6] Read fire history CSV")
    fire = read_csv_auto(fire_csv)
    original_count = len(fire)
    cols = list(fire.columns)

    date_col = args.date_col if args.date_col != "auto" else detect_col(
        cols, ["occu_date", "fire_date", "date", "발생일자", "발생일", "occu_de"]
    )
    year_col = args.year_col if args.year_col != "auto" else detect_col(
        cols, ["occu_year", "year", "발생연도"], required=False
    )
    month_col = args.month_col if args.month_col != "auto" else detect_col(
        cols, ["occu_mt", "month", "발생월"], required=False
    )
    lon_col = args.lon_col if args.lon_col != "auto" else detect_col(
        cols, ["longitude", "lon", "경도", "x_lon", "lng"]
    )
    lat_col = args.lat_col if args.lat_col != "auto" else detect_col(
        cols, ["latitude", "lat", "위도", "y_lat"]
    )
    address_col = args.address_col if args.address_col != "auto" else detect_col(
        cols, ["adres", "address", "주소", "rn_adres"], required=False
    )
    area_col = args.area_col if args.area_col != "auto" else detect_col(
        cols, ["ar", "area", "피해면적", "amount"], required=False
    )
    reason_col = args.reason_col if args.reason_col != "auto" else detect_col(
        cols, ["resn", "reason", "원인"], required=False
    )
    obj_id_col = args.obj_id_col if args.obj_id_col != "auto" else detect_col(
        cols, ["objt_id", "fire_id", "id"], required=False
    )
    province_col = detect_col(cols, ["ctprvn_cd", "province_code", "sido_cd", "시도코드"], required=False)

    fire["_occu_date"] = pd.to_datetime(fire[date_col], errors="coerce")
    if year_col:
        fire["_year"] = pd.to_numeric(fire[year_col], errors="coerce").astype("Int64")
    else:
        fire["_year"] = fire["_occu_date"].dt.year.astype("Int64")

    if month_col:
        fire["_month"] = pd.to_numeric(fire[month_col], errors="coerce").astype("Int64")
    else:
        fire["_month"] = fire["_occu_date"].dt.month.astype("Int64")

    months = parse_months(args.fire_season_months)

    start_date = pd.to_datetime(args.start_date)
    end_date = pd.to_datetime(args.end_date)

    filter_mask = (
        (fire["_occu_date"].notna())
        & (fire["_occu_date"] >= start_date)
        & (fire["_occu_date"] <= end_date)
        & (fire["_month"].isin(months))
    )

    province_codes = [x.strip() for x in str(args.province_codes).split(",") if x.strip()]
    if province_codes:
        if not province_col:
            raise KeyError(
                "--province-codes was provided, but province code column could not be detected."
            )
        province_values = pd.to_numeric(fire[province_col], errors="coerce").astype("Int64")
        province_code_ints = [int(x) for x in province_codes]
        filter_mask &= province_values.isin(province_code_ints)

    address_keywords = [x.strip() for x in str(args.address_keywords).split(",") if x.strip()]
    if address_keywords:
        if not address_col:
            raise KeyError(
                "--address-keywords was provided, but address column could not be detected."
            )
        address_text = fire[address_col].astype("string").fillna("")
        keyword_mask = False
        for keyword in address_keywords:
            keyword_mask = keyword_mask | address_text.str.contains(keyword, regex=False)
        filter_mask &= keyword_mask

    filtered = fire[filter_mask].copy()

    filtered = filtered.reset_index(drop=False).rename(columns={"index": "source_row_index"})
    filtered["fire_event_id"] = np.arange(1, len(filtered) + 1, dtype=np.int64)
    filtered["longitude"] = pd.to_numeric(filtered[lon_col], errors="coerce")
    filtered["latitude"] = pd.to_numeric(filtered[lat_col], errors="coerce")
    filtered["occu_date"] = filtered["_occu_date"].dt.date
    filtered["occu_year"] = filtered["_year"].astype("Int64")
    filtered["occu_month"] = filtered["_month"].astype("Int64")

    if obj_id_col:
        filtered["source_fire_id"] = filtered[obj_id_col]
    else:
        filtered["source_fire_id"] = pd.NA

    if address_col:
        filtered["fire_address"] = filtered[address_col].astype("string")
    else:
        filtered["fire_address"] = pd.NA

    if area_col:
        filtered["fire_area"] = pd.to_numeric(filtered[area_col], errors="coerce")
    else:
        filtered["fire_area"] = np.nan

    if reason_col:
        filtered["fire_reason"] = filtered[reason_col].astype("string")
    else:
        filtered["fire_reason"] = pd.NA

    invalid_coord = filtered[["longitude", "latitude"]].isna().any(axis=1)

    log("[1/6] Read master_grid grid lookup")
    con = duckdb.connect()
    try:
        master_cols = get_parquet_columns(con, master_grid)
    finally:
        con.close()

    required_master = {"grid_id", "grid_x", "grid_y"}
    missing_master = sorted(required_master.difference(master_cols))
    if missing_master:
        raise KeyError(f"master_grid missing required columns: {missing_master}")

    region_col = detect_region_col(master_cols, args.region_col)
    master_read_cols = ["grid_id", "grid_x", "grid_y"] + ([region_col] if region_col else [])
    master = pd.read_parquet(master_grid, columns=master_read_cols)
    master = master.dropna(subset=["grid_id", "grid_x", "grid_y"]).copy()
    master["grid_x"] = pd.to_numeric(master["grid_x"], errors="raise").astype("int64")
    master["grid_y"] = pd.to_numeric(master["grid_y"], errors="raise").astype("int64")
    master = master.drop_duplicates(["grid_x", "grid_y"], keep="first")

    if region_col:
        master = master.rename(columns={region_col: "grid_region"})
    else:
        master["grid_region"] = "UNKNOWN"

    log("[1/6] Transform fire lon/lat EPSG:4326 -> EPSG:5179")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x5179, y5179 = transformer.transform(
        filtered["longitude"].to_numpy(),
        filtered["latitude"].to_numpy(),
    )
    filtered["x5179"] = x5179
    filtered["y5179"] = y5179
    filtered.loc[invalid_coord, ["x5179", "y5179"]] = np.nan

    filtered["grid_x"] = np.floor(filtered["x5179"] / args.grid_size_m)
    filtered["grid_y"] = np.floor(filtered["y5179"] / args.grid_size_m)
    filtered.loc[invalid_coord, ["grid_x", "grid_y"]] = np.nan

    # pandas nullable integer keeps NaN safely before merge
    filtered["grid_x"] = filtered["grid_x"].astype("Int64")
    filtered["grid_y"] = filtered["grid_y"].astype("Int64")

    log("[1/6] Map fire events to master_grid")
    mapped = filtered.merge(master, on=["grid_x", "grid_y"], how="left", validate="many_to_one")

    mapped["fire_match_status"] = np.select(
        [
            mapped[["longitude", "latitude"]].isna().any(axis=1),
            mapped["grid_id"].isna(),
        ],
        [
            "invalid_coord",
            "outside_master_grid",
        ],
        default="matched_grid",
    )

    keep_cols = [
        "fire_event_id",
        "source_row_index",
        "source_fire_id",
        "occu_date",
        "occu_year",
        "occu_month",
        "longitude",
        "latitude",
        "x5179",
        "y5179",
        "grid_x",
        "grid_y",
        "grid_id",
        "grid_region",
        "fire_match_status",
        "fire_area",
        "fire_reason",
        "fire_address",
    ]
    mapped = mapped[keep_cols].copy()

    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.register("mapped_fire", mapped)
        con.execute(
            f"""
            COPY mapped_fire
            TO '{sql_path(output_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        con.close()

    unmatched = mapped[mapped["fire_match_status"] != "matched_grid"].copy()
    unmatched.to_csv(output_unmatched_csv, index=False, encoding="utf-8-sig")

    filtered_count = len(mapped)
    matched_count = int((mapped["fire_match_status"] == "matched_grid").sum())
    invalid_coord_count = int((mapped["fire_match_status"] == "invalid_coord").sum())
    outside_count = int((mapped["fire_match_status"] == "outside_master_grid").sum())

    log(
        f"[1/6] fire original={original_count:,}, filtered={filtered_count:,}, "
        f"matched={matched_count:,}, outside={outside_count:,}, invalid={invalid_coord_count:,}"
    )
    log(f"[1/6] saved mapped fire: {output_parquet} ({file_size_mb(output_parquet):,.2f} MB)")

    return {
        "original_fire_count": original_count,
        "filtered_fire_count": filtered_count,
        "matched_fire_count": matched_count,
        "outside_master_grid_count": outside_count,
        "invalid_coord_count": invalid_coord_count,
        "region_col": region_col or "NONE",
        "date_col": date_col,
        "lon_col": lon_col,
        "lat_col": lat_col,
        "province_col": province_col or "NONE",
        "province_codes_filter": args.province_codes or "NONE",
        "address_keywords_filter": args.address_keywords or "NONE",
    }


def build_neighbor_keys(
    con: duckdb.DuckDBPyConnection,
    fire_mapped_parquet: Path,
    master_grid: Path,
    radius_cells: int,
) -> dict:
    log("[2/6] Build fire-neighbor grid keys")

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fire_mapped AS
        SELECT *
        FROM read_parquet('{sql_path(fire_mapped_parquet)}', union_by_name=true)
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE master_xy AS
        SELECT
            grid_id,
            CAST(grid_x AS BIGINT) AS grid_x,
            CAST(grid_y AS BIGINT) AS grid_y
        FROM read_parquet('{sql_path(master_grid)}', union_by_name=true)
        WHERE grid_id IS NOT NULL
          AND grid_x IS NOT NULL
          AND grid_y IS NOT NULL
        GROUP BY grid_id, grid_x, grid_y
        """
    )

    offsets = [
        {"dx": dx, "dy": dy}
        for dx in range(-radius_cells, radius_cells + 1)
        for dy in range(-radius_cells, radius_cells + 1)
    ]
    offset_df = pd.DataFrame(offsets)
    con.register("offsets_df", offset_df)

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE fire_neighbor_keys AS
        SELECT
            f.fire_event_id,
            CAST(f.occu_date AS DATE) AS occu_date,
            m.grid_id AS neighbor_grid_id,
            o.dx,
            o.dy,
            CASE WHEN o.dx = 0 AND o.dy = 0 THEN 1 ELSE 0 END AS is_exact_grid
        FROM fire_mapped f
        CROSS JOIN offsets_df o
        LEFT JOIN master_xy m
          ON m.grid_x = CAST(f.grid_x AS BIGINT) + o.dx
         AND m.grid_y = CAST(f.grid_y AS BIGINT) + o.dy
        WHERE f.fire_match_status <> 'invalid_coord'
          AND m.grid_id IS NOT NULL
        """
    )

    row = con.execute(
        """
        SELECT
            COUNT(*) AS neighbor_key_count,
            COUNT(DISTINCT fire_event_id) AS fire_with_neighbor_count,
            COUNT(DISTINCT neighbor_grid_id) AS distinct_neighbor_grid_count,
            SUM(is_exact_grid) AS exact_key_count
        FROM fire_neighbor_keys
        """
    ).fetchdf().iloc[0].to_dict()

    log(
        f"[2/6] neighbor keys={int(row['neighbor_key_count']):,}, "
        f"fires_with_neighbor={int(row['fire_with_neighbor_count']):,}, "
        f"distinct_grids={int(row['distinct_neighbor_grid_count']):,}"
    )
    return row


def validate_fire_events(
    con: duckdb.DuckDBPyConnection,
    grid_date_risk: Path,
    output_events_parquet: Path,
    output_events_csv: Path,
    args: argparse.Namespace,
) -> dict:
    log("[3/6] Join fire events with grid-date risk")

    months = parse_months(args.fire_season_months)
    month_list_sql = ",".join(str(m) for m in months)

    risk_cols = get_risk_columns(con, grid_date_risk)
    required_risk = {"grid_id", "date", "final_grid_risk", "daily_risk_pct", "daily_high_risk_flag"}
    missing_risk = sorted(required_risk.difference(risk_cols))
    if missing_risk:
        raise KeyError(f"grid_date_risk missing required columns: {missing_risk}")

    # Partition pruning: risk parquet was written with year/month partitions.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW risk_view AS
        SELECT
            grid_id,
            CAST(date AS DATE) AS date,
            final_grid_risk,
            daily_risk_pct,
            daily_high_risk_flag,
            dwi,
            dwi_pct
        FROM read_parquet('{sql_path(grid_date_risk)}', hive_partitioning=true, union_by_name=true)
        WHERE CAST(date AS DATE) BETWEEN DATE '{args.start_date}' AND DATE '{args.end_date}'
          AND EXTRACT(month FROM CAST(date AS DATE)) IN ({month_list_sql})
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE fire_neighbor_risk AS
        SELECT
            k.fire_event_id,
            k.neighbor_grid_id,
            k.dx,
            k.dy,
            k.is_exact_grid,
            r.final_grid_risk,
            r.daily_risk_pct,
            r.daily_high_risk_flag,
            r.dwi,
            r.dwi_pct
        FROM fire_neighbor_keys k
        LEFT JOIN risk_view r
          ON r.grid_id = k.neighbor_grid_id
         AND r.date = k.occu_date
        """
    )

    log("[3/6] Aggregate exact/neighborhood validation metrics")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fire_exact_risk AS
        SELECT
            fire_event_id,
            MAX(final_grid_risk) AS exact_final_grid_risk,
            MAX(daily_risk_pct) AS exact_daily_risk_pct,
            MAX(daily_high_risk_flag) AS exact_daily_high_risk_flag,
            MAX(dwi) AS exact_dwi,
            MAX(dwi_pct) AS exact_dwi_pct
        FROM fire_neighbor_risk
        WHERE is_exact_grid = 1
        GROUP BY fire_event_id
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE fire_neighbor_agg AS
        SELECT
            fire_event_id,
            COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
            SUM(CASE WHEN final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_row_count,
            MAX(final_grid_risk) AS neighbor_max_final_grid_risk,
            AVG(final_grid_risk) AS neighbor_avg_final_grid_risk,
            MAX(daily_risk_pct) AS neighbor_max_daily_risk_pct,
            MAX(daily_high_risk_flag) AS neighbor_high_risk_any,
            MAX(dwi) AS neighbor_max_dwi,
            MAX(dwi_pct) AS neighbor_max_dwi_pct
        FROM fire_neighbor_risk
        GROUP BY fire_event_id
        """
    )

    log("[3/6] Write event-level validation result")
    output_events_parquet.parent.mkdir(parents=True, exist_ok=True)

    con.execute(
        f"""
        COPY (
            SELECT
                f.*,
                e.exact_final_grid_risk,
                e.exact_daily_risk_pct,
                e.exact_daily_high_risk_flag,
                e.exact_dwi,
                e.exact_dwi_pct,
                n.neighbor_grid_count,
                n.neighbor_risk_row_count,
                n.neighbor_max_final_grid_risk,
                n.neighbor_avg_final_grid_risk,
                n.neighbor_max_daily_risk_pct,
                n.neighbor_high_risk_any,
                n.neighbor_max_dwi,
                n.neighbor_max_dwi_pct,
                CASE
                    WHEN e.exact_daily_risk_pct >= 0.95 OR e.exact_daily_high_risk_flag = 1 THEN 1
                    ELSE 0
                END AS exact_top5_hit,
                CASE
                    WHEN e.exact_daily_risk_pct >= 0.90 THEN 1
                    ELSE 0
                END AS exact_top10_hit,
                CASE
                    WHEN e.exact_daily_risk_pct >= 0.80 THEN 1
                    ELSE 0
                END AS exact_top20_hit,
                CASE
                    WHEN n.neighbor_max_daily_risk_pct >= 0.95 OR n.neighbor_high_risk_any = 1 THEN 1
                    ELSE 0
                END AS neighbor_top5_hit,
                CASE
                    WHEN n.neighbor_max_daily_risk_pct >= 0.90 THEN 1
                    ELSE 0
                END AS neighbor_top10_hit,
                CASE
                    WHEN n.neighbor_max_daily_risk_pct >= 0.80 THEN 1
                    ELSE 0
                END AS neighbor_top20_hit
            FROM fire_mapped f
            LEFT JOIN fire_exact_risk e USING (fire_event_id)
            LEFT JOIN fire_neighbor_agg n USING (fire_event_id)
            ORDER BY f.fire_event_id
        )
        TO '{sql_path(output_events_parquet)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    con.execute(
        f"""
        COPY (
            SELECT *
            FROM read_parquet('{sql_path(output_events_parquet)}', union_by_name=true)
            ORDER BY fire_event_id
        )
        TO '{sql_path(output_events_csv)}'
        (HEADER, DELIMITER ',')
        """
    )

    row = con.execute(
        f"""
        SELECT
            COUNT(*) AS validation_event_count,
            SUM(CASE WHEN fire_match_status = 'matched_grid' THEN 1 ELSE 0 END) AS mapped_event_count,
            SUM(CASE WHEN fire_match_status <> 'invalid_coord' THEN 1 ELSE 0 END) AS valid_coord_event_count,
            SUM(CASE WHEN neighbor_grid_count IS NOT NULL AND neighbor_grid_count > 0 THEN 1 ELSE 0 END) AS neighbor_grid_mapped_event_count,
            SUM(CASE WHEN exact_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS exact_risk_available_count,
            SUM(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_available_count,
            AVG(exact_final_grid_risk) AS avg_exact_final_grid_risk,
            AVG(exact_daily_risk_pct) AS avg_exact_daily_risk_pct,
            AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top5_hit::DOUBLE END) AS exact_top5_hit_rate,
            AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top10_hit::DOUBLE END) AS exact_top10_hit_rate,
            AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top20_hit::DOUBLE END) AS exact_top20_hit_rate,
            AVG(neighbor_max_final_grid_risk) AS avg_neighbor_max_final_grid_risk,
            AVG(neighbor_max_daily_risk_pct) AS avg_neighbor_max_daily_risk_pct,
            AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top5_hit::DOUBLE END) AS neighbor_top5_hit_rate,
            AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top10_hit::DOUBLE END) AS neighbor_top10_hit_rate,
            AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top20_hit::DOUBLE END) AS neighbor_top20_hit_rate
        FROM read_parquet('{sql_path(output_events_parquet)}', union_by_name=true)
        """
    ).fetchdf().iloc[0].to_dict()

    log(f"[3/6] saved events: {output_events_csv} ({file_size_mb(output_events_csv):,.2f} MB)")
    return row


def write_group_summaries(
    con: duckdb.DuckDBPyConnection,
    events_parquet: Path,
    report_dir: Path,
) -> dict:
    log("[4/6] Write validation group summaries")

    report_dir.mkdir(parents=True, exist_ok=True)

    base_query = f"""
        FROM read_parquet('{sql_path(events_parquet)}', union_by_name=true)
        WHERE neighbor_max_final_grid_risk IS NOT NULL
    """

    metric_select = """
        COUNT(*) AS fire_count,
        SUM(CASE WHEN exact_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS exact_risk_available_count,
        AVG(exact_final_grid_risk) AS avg_exact_final_grid_risk,
        AVG(exact_daily_risk_pct) AS avg_exact_daily_risk_pct,
        AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top5_hit::DOUBLE END) AS exact_top5_hit_rate,
        AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top10_hit::DOUBLE END) AS exact_top10_hit_rate,
        AVG(CASE WHEN exact_final_grid_risk IS NOT NULL THEN exact_top20_hit::DOUBLE END) AS exact_top20_hit_rate,
        AVG(neighbor_max_final_grid_risk) AS avg_neighbor_max_final_grid_risk,
        AVG(neighbor_max_daily_risk_pct) AS avg_neighbor_max_daily_risk_pct,
        AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top5_hit::DOUBLE END) AS neighbor_top5_hit_rate,
        AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top10_hit::DOUBLE END) AS neighbor_top10_hit_rate,
        AVG(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN neighbor_top20_hit::DOUBLE END) AS neighbor_top20_hit_rate
    """

    outputs = {}

    queries = {
        "by_year": f"""
            SELECT
                occu_year,
                {metric_select}
            {base_query}
            GROUP BY occu_year
            ORDER BY occu_year
        """,
        "by_month": f"""
            SELECT
                occu_month,
                {metric_select}
            {base_query}
            GROUP BY occu_month
            ORDER BY occu_month
        """,
        "by_region": f"""
            SELECT
                grid_region,
                {metric_select}
            {base_query}
            GROUP BY grid_region
            ORDER BY fire_count DESC, neighbor_top5_hit_rate DESC
        """,
        "top_fire_events": f"""
            SELECT
                fire_event_id,
                occu_date,
                grid_region,
                longitude,
                latitude,
                fire_area,
                exact_final_grid_risk,
                exact_daily_risk_pct,
                exact_top5_hit,
                neighbor_max_final_grid_risk,
                neighbor_max_daily_risk_pct,
                neighbor_top5_hit,
                fire_address
            FROM read_parquet('{sql_path(events_parquet)}', union_by_name=true)
            WHERE neighbor_max_final_grid_risk IS NOT NULL
            ORDER BY neighbor_max_final_grid_risk DESC NULLS LAST
            LIMIT 100
        """,
    }

    for name, query in queries.items():
        out = report_dir / f"fire_history_validation_{name}.csv"
        con.execute(
            f"""
            COPY (
                {query}
            )
            TO '{sql_path(out)}'
            (HEADER, DELIMITER ',')
            """
        )
        outputs[name] = str(out)

    return outputs


def write_report(
    con: duckdb.DuckDBPyConnection,
    report_dir: Path,
    map_summary: dict,
    neighbor_summary: dict,
    validation_summary: dict,
    group_outputs: dict,
    events_parquet: Path,
    events_csv: Path,
    args: argparse.Namespace,
) -> None:
    log("[5/6] Write validation markdown report")

    report_dir.mkdir(parents=True, exist_ok=True)

    overall_csv = report_dir / "fire_history_validation_summary.csv"
    pd.DataFrame([{**map_summary, **neighbor_summary, **validation_summary}]).to_csv(
        overall_csv, index=False, encoding="utf-8-sig"
    )

    by_year = con.execute(
        f"SELECT * FROM read_csv_auto('{sql_path(group_outputs['by_year'])}')"
    ).fetchdf()
    by_region = con.execute(
        f"SELECT * FROM read_csv_auto('{sql_path(group_outputs['by_region'])}') LIMIT {args.top_n_regions}"
    ).fetchdf()

    def pct(value) -> str:
        if pd.isna(value):
            return "NA"
        return f"{float(value):.2%}"

    md = f"""# Fire History Validation 결과

## 목적

본 검증은 산불 발생 여부를 학습한 모델 평가가 아니라, 2020~2024년 2~5월 실제 산불 발생 이력이 산출된 위험도 상위권에 얼마나 위치하는지 확인하는 사후 검증이다.

## 입력

- fire history csv: `{args.fire_csv}`
- master grid: `{args.master_grid}`
- grid-date risk: `{args.grid_date_risk}`
- validation period: `{args.start_date}` ~ `{args.end_date}`
- fire-season months: `{args.fire_season_months}`
- province codes filter: `{args.province_codes or 'NONE'}`
- address keywords filter: `{args.address_keywords or 'NONE'}`
- neighborhood radius cells: `{args.radius_cells}`

## 출력

- event-level parquet: `{events_parquet}`
- event-level csv: `{events_csv}`
- overall summary: `{overall_csv}`
- by year: `{group_outputs['by_year']}`
- by month: `{group_outputs['by_month']}`
- by region: `{group_outputs['by_region']}`
- top fire events: `{group_outputs['top_fire_events']}`

## 매핑 요약

- original_fire_count: `{int(map_summary['original_fire_count']):,}`
- filtered_fire_count: `{int(map_summary['filtered_fire_count']):,}`
- matched_fire_count: `{int(map_summary['matched_fire_count']):,}`
- outside_master_grid_count: `{int(map_summary['outside_master_grid_count']):,}`
- invalid_coord_count: `{int(map_summary['invalid_coord_count']):,}`
- region_col: `{map_summary['region_col']}`

## 검증 요약

- validation_event_count: `{int(validation_summary['validation_event_count']):,}`
- mapped_event_count_exact_grid: `{int(validation_summary['mapped_event_count']):,}`
- valid_coord_event_count: `{int(validation_summary['valid_coord_event_count']):,}`
- neighbor_grid_mapped_event_count: `{int(validation_summary['neighbor_grid_mapped_event_count']):,}`
- exact_risk_available_count: `{int(validation_summary['exact_risk_available_count']):,}`
- neighbor_risk_available_count: `{int(validation_summary['neighbor_risk_available_count']):,}`

### Exact grid 기준

- avg_exact_final_grid_risk: `{validation_summary['avg_exact_final_grid_risk']}`
- avg_exact_daily_risk_pct: `{validation_summary['avg_exact_daily_risk_pct']}`
- exact_top5_hit_rate: `{pct(validation_summary['exact_top5_hit_rate'])}`
- exact_top10_hit_rate: `{pct(validation_summary['exact_top10_hit_rate'])}`
- exact_top20_hit_rate: `{pct(validation_summary['exact_top20_hit_rate'])}`

### Neighborhood 기준

- avg_neighbor_max_final_grid_risk: `{validation_summary['avg_neighbor_max_final_grid_risk']}`
- avg_neighbor_max_daily_risk_pct: `{validation_summary['avg_neighbor_max_daily_risk_pct']}`
- neighbor_top5_hit_rate: `{pct(validation_summary['neighbor_top5_hit_rate'])}`
- neighbor_top10_hit_rate: `{pct(validation_summary['neighbor_top10_hit_rate'])}`
- neighbor_top20_hit_rate: `{pct(validation_summary['neighbor_top20_hit_rate'])}`

## 연도별 요약

{df_to_markdown(by_year, max_rows=20)}

## 지역별 상위 요약

{df_to_markdown(by_region, max_rows=args.top_n_regions)}

## 해석 기준

- `exact_top5_hit_rate`: 실제 산불 발생 격자가 해당 날짜 위험도 상위 5%에 포함된 비율
- `neighbor_top5_hit_rate`: 실제 산불 발생 좌표 주변 {args.radius_cells}칸 이내에서 분석 대상 격자 중 상위 5% 위험 격자가 포착된 비율
- 무작위 기준으로는 상위 5%, 10%, 20% hit rate의 기대값이 각각 약 5%, 10%, 20%이다.
- 따라서 hit rate가 이 기준보다 높고 평균 `daily_risk_pct`가 0.5보다 높다면, 산출 위험도가 실제 산불 발생 위치와 일정 부분 정합성을 가진다고 해석할 수 있다.

## 주의

- 본 검증은 지도학습 모델의 정확도 평가가 아니라 위험도 스코어링 결과의 사후 타당성 확인이다.
- 산불 발생 이력은 학습에 사용하지 않았으며, 실제 발생 위치가 산출된 위험도 상위권에 얼마나 놓이는지 확인하는 용도로만 사용한다.
- exact grid 검증은 좌표 오차와 격자 경계 효과에 민감할 수 있으므로, neighborhood 기준 결과를 함께 해석해야 한다.
"""
    report_path = report_dir / "fire_history_validation_summary.md"
    report_path.write_text(md, encoding="utf-8")
    log(f"[5/6] report saved: {report_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate grid-date risk score using 2020~2024 spring fire history."
    )
    parser.add_argument("--fire-csv", default=DEFAULT_FIRE_CSV)
    parser.add_argument("--master-grid", default=DEFAULT_MASTER_GRID)
    parser.add_argument("--grid-date-risk", default=DEFAULT_GRID_DATE_RISK)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)

    parser.add_argument("--start-date", default="2020-02-01")
    parser.add_argument("--end-date", default="2024-05-31")
    parser.add_argument("--fire-season-months", default="2,3,4,5")
    parser.add_argument(
        "--province-codes",
        default="",
        help="Optional comma-separated ctprvn_cd filter. Example for Gangwon/Gangwon Special: 42,51",
    )
    parser.add_argument(
        "--address-keywords",
        default="",
        help="Optional comma-separated address keyword filter. Example: 강원,강원도,강원특별자치도",
    )
    parser.add_argument("--radius-cells", type=int, default=1, help="1 means 3x3 neighborhood around exact grid.")

    parser.add_argument("--date-col", default="auto")
    parser.add_argument("--year-col", default="auto")
    parser.add_argument("--month-col", default="auto")
    parser.add_argument("--lon-col", default="auto")
    parser.add_argument("--lat-col", default="auto")
    parser.add_argument("--address-col", default="auto")
    parser.add_argument("--area-col", default="auto")
    parser.add_argument("--reason-col", default="auto")
    parser.add_argument("--obj-id-col", default="auto")
    parser.add_argument("--region-col", default="auto")

    parser.add_argument("--grid-size-m", type=int, default=100)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="16GB")
    parser.add_argument("--top-n-regions", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reuse-fire-map", action="store_true")
    parser.add_argument("--enable-progress", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if args.radius_cells < 0:
        raise ValueError("--radius-cells must be >= 0")

    root = project_root_from_script()
    fire_csv = resolve_path(root, args.fire_csv)
    master_grid = resolve_path(root, args.master_grid)
    grid_date_risk = resolve_path(root, args.grid_date_risk)
    output_dir = resolve_path(root, args.output_dir)
    report_dir = resolve_path(root, args.report_dir)

    validate_inputs(fire_csv, master_grid, grid_date_risk)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    fire_mapped_parquet = output_dir / "fire_history_mapped.parquet"
    fire_unmatched_csv = output_dir / "fire_history_unmatched.csv"
    events_parquet = output_dir / "fire_history_validation_events.parquet"
    events_csv = output_dir / "fire_history_validation_events.csv"

    if args.overwrite:
        for p in [
            fire_mapped_parquet,
            fire_unmatched_csv,
            events_parquet,
            events_csv,
        ]:
            if p.exists():
                p.unlink()
        for p in report_dir.glob("fire_history_validation_*"):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)

    log("START fire history validation v3 - DWI-based risk columns")
    log(f"fire_csv={fire_csv}")
    log(f"master_grid={master_grid}")
    log(f"grid_date_risk={grid_date_risk}")
    log(f"output_dir={output_dir}")
    log(f"report_dir={report_dir}")

    if fire_mapped_parquet.exists() and args.reuse_fire_map:
        log(f"[1/6] Reuse existing fire map: {fire_mapped_parquet}")
        con_tmp = duckdb.connect()
        try:
            row = con_tmp.execute(
                f"""
                SELECT
                    COUNT(*) AS filtered_fire_count,
                    SUM(CASE WHEN fire_match_status='matched_grid' THEN 1 ELSE 0 END) AS matched_fire_count,
                    SUM(CASE WHEN fire_match_status='outside_master_grid' THEN 1 ELSE 0 END) AS outside_master_grid_count,
                    SUM(CASE WHEN fire_match_status='invalid_coord' THEN 1 ELSE 0 END) AS invalid_coord_count
                FROM read_parquet('{sql_path(fire_mapped_parquet)}', union_by_name=true)
                """
            ).fetchdf().iloc[0].to_dict()
            map_summary = {
                "original_fire_count": int(row["filtered_fire_count"]),
                "filtered_fire_count": int(row["filtered_fire_count"]),
                "matched_fire_count": int(row["matched_fire_count"]),
                "outside_master_grid_count": int(row["outside_master_grid_count"]),
                "invalid_coord_count": int(row["invalid_coord_count"]),
                "region_col": "reused",
                "date_col": "reused",
                "lon_col": "reused",
                "lat_col": "reused",
            }
        finally:
            con_tmp.close()
    else:
        map_summary = load_and_map_fire_history(
            fire_csv=fire_csv,
            master_grid=master_grid,
            output_parquet=fire_mapped_parquet,
            output_unmatched_csv=fire_unmatched_csv,
            args=args,
        )

    con = duckdb.connect()
    try:
        con.execute(f"PRAGMA threads={args.threads}")
        con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
        if args.enable_progress:
            con.execute("PRAGMA enable_progress_bar")
        else:
            con.execute("PRAGMA disable_progress_bar")

        neighbor_summary = build_neighbor_keys(
            con=con,
            fire_mapped_parquet=fire_mapped_parquet,
            master_grid=master_grid,
            radius_cells=args.radius_cells,
        )

        validation_summary = validate_fire_events(
            con=con,
            grid_date_risk=grid_date_risk,
            output_events_parquet=events_parquet,
            output_events_csv=events_csv,
            args=args,
        )

        group_outputs = write_group_summaries(
            con=con,
            events_parquet=events_parquet,
            report_dir=report_dir,
        )

        write_report(
            con=con,
            report_dir=report_dir,
            map_summary=map_summary,
            neighbor_summary=neighbor_summary,
            validation_summary=validation_summary,
            group_outputs=group_outputs,
            events_parquet=events_parquet,
            events_csv=events_csv,
            args=args,
        )

    finally:
        con.close()

    log("[6/6] DONE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

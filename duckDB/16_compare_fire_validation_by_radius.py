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
DEFAULT_OUTPUT_DIR = "output/validation/radius_compare"
DEFAULT_REPORT_DIR = "output/report/validation"


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
        "city_name", "sigungu_nm", "sgg_nm", "시군구명", "시군구",
        "adm_sigungu_nm", "region_sigungu", "sido_nm", "시도명",
    ]
    for cand in candidates:
        if cand in master_cols:
            return cand
    for col in master_cols:
        lc = col.lower()
        if any(k in lc for k in ["city", "sigungu", "sgg"]) or "시군구" in col:
            return col
    return None


def parse_months(value: str) -> list[int]:
    months = [int(x.strip()) for x in str(value).split(",") if x.strip()]
    if not months:
        raise ValueError("month list is empty")
    invalid = [m for m in months if m < 1 or m > 12]
    if invalid:
        raise ValueError(f"invalid months: {invalid}")
    return months


def parse_radii(value: str) -> list[int]:
    radii = sorted(set(int(x.strip()) for x in str(value).split(",") if x.strip()))
    if not radii:
        raise ValueError("--radii is empty")
    invalid = [r for r in radii if r < 0]
    if invalid:
        raise ValueError(f"invalid radii: {invalid}")
    return radii


def df_to_markdown(df: pd.DataFrame, max_rows: int = 100) -> str:
    if df.empty:
        return "(no rows)"
    view = df.head(max_rows).copy()
    view = view.astype(object).where(pd.notnull(view), "")

    def fmt(x) -> str:
        if isinstance(x, float):
            return f"{x:.6g}"
        return str(x)

    headers = list(view.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in view.values.tolist():
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
    output_dir: Path,
    args: argparse.Namespace,
) -> dict:
    log("[1/8] Read and filter fire history")
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

    mask = (
        fire["_occu_date"].notna()
        & (fire["_occu_date"] >= start_date)
        & (fire["_occu_date"] <= end_date)
        & (fire["_month"].isin(months))
    )

    province_codes = [x.strip() for x in str(args.province_codes).split(",") if x.strip()]
    if province_codes:
        if not province_col:
            raise KeyError("--province-codes was provided, but province code column could not be detected.")
        province_values = pd.to_numeric(fire[province_col], errors="coerce").astype("Int64")
        mask &= province_values.isin([int(x) for x in province_codes])

    address_keywords = [x.strip() for x in str(args.address_keywords).split(",") if x.strip()]
    if address_keywords:
        if not address_col:
            raise KeyError("--address-keywords was provided, but address column could not be detected.")
        address_text = fire[address_col].astype("string").fillna("")
        keyword_mask = False
        for keyword in address_keywords:
            keyword_mask = keyword_mask | address_text.str.contains(keyword, regex=False)
        mask &= keyword_mask

    filtered = fire[mask].copy().reset_index(drop=False).rename(columns={"index": "source_row_index"})
    filtered["fire_event_id"] = np.arange(1, len(filtered) + 1, dtype=np.int64)
    filtered["longitude"] = pd.to_numeric(filtered[lon_col], errors="coerce")
    filtered["latitude"] = pd.to_numeric(filtered[lat_col], errors="coerce")
    filtered["occu_date"] = filtered["_occu_date"].dt.date
    filtered["occu_year"] = filtered["_year"].astype("Int64")
    filtered["occu_month"] = filtered["_month"].astype("Int64")
    filtered["source_fire_id"] = filtered[obj_id_col] if obj_id_col else pd.NA
    filtered["fire_address"] = filtered[address_col].astype("string") if address_col else pd.NA

    invalid_coord = filtered[["longitude", "latitude"]].isna().any(axis=1)

    log("[1/8] Read master_grid")
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
    master_cols_to_read = ["grid_id", "grid_x", "grid_y"] + ([region_col] if region_col else [])
    master = pd.read_parquet(master_grid, columns=master_cols_to_read)
    master = master.dropna(subset=["grid_id", "grid_x", "grid_y"]).copy()
    master["grid_x"] = pd.to_numeric(master["grid_x"], errors="raise").astype("int64")
    master["grid_y"] = pd.to_numeric(master["grid_y"], errors="raise").astype("int64")
    master = master.drop_duplicates(["grid_x", "grid_y"], keep="first")
    if region_col:
        master = master.rename(columns={region_col: "grid_region"})
    else:
        master["grid_region"] = "UNKNOWN"

    log("[1/8] Transform fire lon/lat EPSG:4326 -> EPSG:5179")
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
    filtered["grid_x"] = filtered["grid_x"].astype("Int64")
    filtered["grid_y"] = filtered["grid_y"].astype("Int64")

    mapped = filtered.merge(master, on=["grid_x", "grid_y"], how="left", validate="many_to_one")
    mapped["fire_match_status"] = np.select(
        [
            mapped[["longitude", "latitude"]].isna().any(axis=1),
            mapped["grid_id"].isna(),
        ],
        ["invalid_coord", "outside_master_grid"],
        default="matched_grid",
    )

    keep = [
        "fire_event_id", "source_row_index", "source_fire_id", "occu_date", "occu_year", "occu_month",
        "longitude", "latitude", "x5179", "y5179", "grid_x", "grid_y", "grid_id",
        "grid_region", "fire_match_status", "fire_address",
    ]
    mapped = mapped[keep].copy()

    output_dir.mkdir(parents=True, exist_ok=True)
    mapped_parquet = output_dir / "fire_history_mapped_for_radius_compare.parquet"
    mapped_csv = output_dir / "fire_history_mapped_for_radius_compare.csv"
    con = duckdb.connect()
    try:
        con.register("mapped_fire", mapped)
        con.execute(
            f"""
            COPY mapped_fire
            TO '{sql_path(mapped_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        con.close()
    mapped.to_csv(mapped_csv, index=False, encoding="utf-8-sig")

    filtered_count = len(mapped)
    matched_count = int((mapped["fire_match_status"] == "matched_grid").sum())
    invalid_coord_count = int((mapped["fire_match_status"] == "invalid_coord").sum())
    outside_count = int((mapped["fire_match_status"] == "outside_master_grid").sum())

    log(
        f"[1/8] fire original={original_count:,}, filtered={filtered_count:,}, "
        f"matched={matched_count:,}, outside={outside_count:,}, invalid={invalid_coord_count:,}"
    )

    return {
        "mapped_df": mapped,
        "master_xy_df": master[["grid_id", "grid_x", "grid_y"]].copy(),
        "mapped_parquet": mapped_parquet,
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


def build_random_centers(
    mapped_df: pd.DataFrame,
    master_xy_df: pd.DataFrame,
    repeats: int,
    seed: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    events = mapped_df.loc[
        mapped_df["fire_match_status"] != "invalid_coord",
        ["fire_event_id", "occu_date", "occu_year", "occu_month"],
    ].copy()
    n_events = len(events)
    n_master = len(master_xy_df)
    if n_events == 0:
        raise ValueError("No valid fire events to build random centers.")
    if n_master == 0:
        raise ValueError("master_xy_df is empty.")
    if repeats <= 0:
        raise ValueError("--random-repeats must be > 0")

    event_rep = pd.DataFrame({
        "fire_event_id": np.repeat(events["fire_event_id"].to_numpy(), repeats),
        "occu_date": np.repeat(events["occu_date"].to_numpy(), repeats),
        "occu_year": np.repeat(events["occu_year"].to_numpy(), repeats),
        "occu_month": np.repeat(events["occu_month"].to_numpy(), repeats),
        "rep_id": np.tile(np.arange(1, repeats + 1, dtype=np.int64), n_events),
    })

    sample_idx = rng.integers(0, n_master, size=len(event_rep), endpoint=False)
    centers = master_xy_df.iloc[sample_idx].reset_index(drop=True)
    centers = centers.rename(
        columns={
            "grid_id": "random_center_grid_id",
            "grid_x": "center_grid_x",
            "grid_y": "center_grid_y",
        }
    )
    out = pd.concat([event_rep.reset_index(drop=True), centers.reset_index(drop=True)], axis=1)
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare exact/neighborhood fire-history validation metrics across multiple radii, "
            "including neighbor top-k grid ratios and random-neighborhood baselines."
        )
    )
    parser.add_argument("--fire-csv", default=DEFAULT_FIRE_CSV)
    parser.add_argument("--master-grid", default=DEFAULT_MASTER_GRID)
    parser.add_argument("--grid-date-risk", default=DEFAULT_GRID_DATE_RISK)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    parser.add_argument("--enable-progress", action="store_true")

    parser.add_argument("--start-date", default="2020-02-01")
    parser.add_argument("--end-date", default="2024-05-31")
    parser.add_argument("--fire-season-months", default="2,3,4,5")
    parser.add_argument("--address-keywords", default="")
    parser.add_argument("--province-codes", default="")
    parser.add_argument("--radii", default="0,1,3,5,10")
    parser.add_argument("--grid-size-m", type=float, default=100.0)

    parser.add_argument("--random-repeats", type=int, default=100)
    parser.add_argument("--random-seed", type=int, default=42)

    parser.add_argument("--date-col", default="auto")
    parser.add_argument("--year-col", default="auto")
    parser.add_argument("--month-col", default="auto")
    parser.add_argument("--lon-col", default="auto")
    parser.add_argument("--lat-col", default="auto")
    parser.add_argument("--address-col", default="auto")
    parser.add_argument("--obj-id-col", default="auto")
    parser.add_argument("--region-col", default="auto")

    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    radii = parse_radii(args.radii)
    max_radius = max(radii)

    root = project_root_from_script()
    fire_csv = resolve_path(root, args.fire_csv)
    master_grid = resolve_path(root, args.master_grid)
    grid_date_risk = resolve_path(root, args.grid_date_risk)
    output_dir = resolve_path(root, args.output_dir)
    report_dir = resolve_path(root, args.report_dir)

    validate_inputs(fire_csv, master_grid, grid_date_risk)

    if output_dir.exists() and args.overwrite:
        shutil.rmtree(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"Output dir already exists and is not empty: {output_dir}. Use --overwrite.")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    log("============================================================")
    log("16_compare_fire_validation_by_radius.py")
    log("Radius comparison + neighbor grid ratio + random baseline")
    log(f"grid_date_risk={grid_date_risk}")
    log(f"radii={radii}")
    log(f"random_repeats={args.random_repeats}, random_seed={args.random_seed}")
    log("============================================================")

    map_info = load_and_map_fire_history(fire_csv, master_grid, output_dir, args)
    mapped_parquet = map_info["mapped_parquet"]
    random_centers = build_random_centers(
        map_info["mapped_df"], map_info["master_xy_df"], args.random_repeats, args.random_seed
    )
    random_centers_parquet = output_dir / "random_centers.parquet"
    random_centers.to_parquet(random_centers_parquet, index=False)
    log(f"[2/8] random centers={len(random_centers):,} saved: {random_centers_parquet}")

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    if args.enable_progress:
        con.execute("PRAGMA enable_progress_bar")
    else:
        con.execute("PRAGMA disable_progress_bar")

    try:
        log("[3/8] Validate grid_date_risk columns")
        risk_cols = get_risk_columns(con, grid_date_risk)
        required_risk = {"grid_id", "date", "final_grid_risk", "daily_risk_pct"}
        missing_risk = sorted(required_risk.difference(risk_cols))
        if missing_risk:
            raise KeyError(f"grid_date_risk missing required columns: {missing_risk}")
        has_daily_high = "daily_high_risk_flag" in risk_cols

        months = parse_months(args.fire_season_months)
        month_list_sql = ",".join(str(m) for m in months)

        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE fire_mapped AS
            SELECT *
            FROM read_parquet('{sql_path(mapped_parquet)}', union_by_name=true)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE random_centers AS
            SELECT *
            FROM read_parquet('{sql_path(random_centers_parquet)}', union_by_name=true)
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

        radii_df = pd.DataFrame({"radius_cells": radii})
        offsets_df = pd.DataFrame([
            {
                "dx": dx,
                "dy": dy,
                "cheb_radius": max(abs(dx), abs(dy)),
            }
            for dx in range(-max_radius, max_radius + 1)
            for dy in range(-max_radius, max_radius + 1)
        ])
        con.register("radii_df", radii_df)
        con.register("offsets_df", offsets_df)
        con.execute("CREATE OR REPLACE TEMP TABLE radii AS SELECT * FROM radii_df")
        con.execute("CREATE OR REPLACE TEMP TABLE offsets AS SELECT * FROM offsets_df")

        log("[4/8] Build fire date table and risk_view")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE fire_dates AS
            SELECT DISTINCT CAST(occu_date AS DATE) AS date
            FROM fire_mapped
            WHERE occu_date IS NOT NULL
            """
        )

        top5_expr = (
            "CAST(r.daily_high_risk_flag AS INTEGER)"
            if has_daily_high
            else "CASE WHEN r.daily_risk_pct >= 0.95 THEN 1 ELSE 0 END"
        )

        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE risk_view AS
            SELECT
                r.grid_id,
                CAST(r.date AS DATE) AS date,
                r.final_grid_risk,
                r.daily_risk_pct,
                {top5_expr} AS top5_flag,
                CASE WHEN r.daily_risk_pct >= 0.90 THEN 1 ELSE 0 END AS top10_flag,
                CASE WHEN r.daily_risk_pct >= 0.80 THEN 1 ELSE 0 END AS top20_flag
            FROM read_parquet('{sql_path(grid_date_risk)}', hive_partitioning=true, union_by_name=true) r
            JOIN fire_dates fd
              ON fd.date = CAST(r.date AS DATE)
            WHERE CAST(r.date AS DATE) BETWEEN DATE '{args.start_date}' AND DATE '{args.end_date}'
              AND EXTRACT(month FROM CAST(r.date AS DATE)) IN ({month_list_sql})
              AND r.final_grid_risk IS NOT NULL
              AND r.daily_risk_pct IS NOT NULL
            """
        )

        risk_info = con.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT date) AS date_count,
                COUNT(DISTINCT grid_id) AS grid_count
            FROM risk_view
            """
        ).fetchdf().iloc[0].to_dict()
        log(
            f"[4/8] risk_view rows={int(risk_info['row_count']):,}, "
            f"dates={int(risk_info['date_count']):,}, grids={int(risk_info['grid_count']):,}"
        )

        log("[5/8] Actual fire-neighborhood validation by radius")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE event_radius_base AS
            SELECT
                f.fire_event_id,
                CAST(f.occu_date AS DATE) AS occu_date,
                f.occu_year,
                f.occu_month,
                f.grid_region,
                f.fire_match_status,
                r.radius_cells
            FROM fire_mapped f
            CROSS JOIN radii r
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_neighbor_keys AS
            SELECT
                f.fire_event_id,
                CAST(f.occu_date AS DATE) AS occu_date,
                r.radius_cells,
                m.grid_id AS neighbor_grid_id
            FROM fire_mapped f
            CROSS JOIN radii r
            JOIN offsets o
              ON o.cheb_radius <= r.radius_cells
            JOIN master_xy m
              ON m.grid_x = CAST(f.grid_x AS BIGINT) + o.dx
             AND m.grid_y = CAST(f.grid_y AS BIGINT) + o.dy
            WHERE f.fire_match_status <> 'invalid_coord'
              AND f.grid_x IS NOT NULL
              AND f.grid_y IS NOT NULL
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_neighbor_risk AS
            SELECT
                k.fire_event_id,
                k.occu_date,
                k.radius_cells,
                k.neighbor_grid_id,
                rv.final_grid_risk,
                rv.daily_risk_pct,
                rv.top5_flag,
                rv.top10_flag,
                rv.top20_flag
            FROM actual_neighbor_keys k
            LEFT JOIN risk_view rv
              ON rv.grid_id = k.neighbor_grid_id
             AND rv.date = k.occu_date
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_event_metrics_raw AS
            SELECT
                fire_event_id,
                occu_date,
                radius_cells,
                COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
                SUM(CASE WHEN final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_grid_count,
                MAX(final_grid_risk) AS neighbor_max_final_grid_risk,
                AVG(final_grid_risk) AS neighbor_avg_final_grid_risk,
                MAX(daily_risk_pct) AS neighbor_max_daily_risk_pct,
                SUM(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_grid_count,
                SUM(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_grid_count,
                SUM(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_grid_count,
                MAX(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_hit,
                MAX(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_hit,
                MAX(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_hit
            FROM actual_neighbor_risk
            GROUP BY fire_event_id, occu_date, radius_cells
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_event_metrics AS
            SELECT
                b.fire_event_id,
                b.occu_date,
                b.occu_year,
                b.occu_month,
                b.grid_region,
                b.fire_match_status,
                b.radius_cells,
                COALESCE(m.neighbor_grid_count, 0) AS neighbor_grid_count,
                COALESCE(m.neighbor_risk_grid_count, 0) AS neighbor_risk_grid_count,
                m.neighbor_max_final_grid_risk,
                m.neighbor_avg_final_grid_risk,
                m.neighbor_max_daily_risk_pct,
                COALESCE(m.neighbor_top5_grid_count, 0) AS neighbor_top5_grid_count,
                COALESCE(m.neighbor_top10_grid_count, 0) AS neighbor_top10_grid_count,
                COALESCE(m.neighbor_top20_grid_count, 0) AS neighbor_top20_grid_count,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top5_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top5_grid_ratio,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top10_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top10_grid_ratio,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top20_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top20_grid_ratio,
                m.neighbor_top5_hit,
                m.neighbor_top10_hit,
                m.neighbor_top20_hit
            FROM event_radius_base b
            LEFT JOIN actual_event_metrics_raw m
              ON m.fire_event_id = b.fire_event_id
             AND m.radius_cells = b.radius_cells
            """
        )

        actual_events_parquet = output_dir / "radius_actual_event_metrics.parquet"
        actual_events_csv = output_dir / "radius_actual_event_metrics.csv"
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM actual_event_metrics
                ORDER BY radius_cells, fire_event_id
            )
            TO '{sql_path(actual_events_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM actual_event_metrics
                ORDER BY radius_cells, fire_event_id
            )
            TO '{sql_path(actual_events_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        log("[6/8] Random-neighborhood baseline by radius")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_radius_base AS
            SELECT
                c.fire_event_id,
                CAST(c.occu_date AS DATE) AS occu_date,
                c.occu_year,
                c.occu_month,
                c.rep_id,
                c.random_center_grid_id,
                c.center_grid_x,
                c.center_grid_y,
                r.radius_cells
            FROM random_centers c
            CROSS JOIN radii r
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_neighbor_keys AS
            SELECT
                c.fire_event_id,
                CAST(c.occu_date AS DATE) AS occu_date,
                c.rep_id,
                r.radius_cells,
                m.grid_id AS neighbor_grid_id
            FROM random_centers c
            CROSS JOIN radii r
            JOIN offsets o
              ON o.cheb_radius <= r.radius_cells
            JOIN master_xy m
              ON m.grid_x = CAST(c.center_grid_x AS BIGINT) + o.dx
             AND m.grid_y = CAST(c.center_grid_y AS BIGINT) + o.dy
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_neighbor_risk AS
            SELECT
                k.fire_event_id,
                k.occu_date,
                k.rep_id,
                k.radius_cells,
                k.neighbor_grid_id,
                rv.final_grid_risk,
                rv.daily_risk_pct,
                rv.top5_flag,
                rv.top10_flag,
                rv.top20_flag
            FROM random_neighbor_keys k
            LEFT JOIN risk_view rv
              ON rv.grid_id = k.neighbor_grid_id
             AND rv.date = k.occu_date
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_event_metrics_raw AS
            SELECT
                fire_event_id,
                occu_date,
                rep_id,
                radius_cells,
                COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
                SUM(CASE WHEN final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_grid_count,
                MAX(final_grid_risk) AS neighbor_max_final_grid_risk,
                AVG(final_grid_risk) AS neighbor_avg_final_grid_risk,
                MAX(daily_risk_pct) AS neighbor_max_daily_risk_pct,
                SUM(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_grid_count,
                SUM(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_grid_count,
                SUM(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_grid_count,
                MAX(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_hit,
                MAX(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_hit,
                MAX(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_hit
            FROM random_neighbor_risk
            GROUP BY fire_event_id, occu_date, rep_id, radius_cells
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_event_metrics AS
            SELECT
                b.fire_event_id,
                b.occu_date,
                b.occu_year,
                b.occu_month,
                b.rep_id,
                b.random_center_grid_id,
                b.radius_cells,
                COALESCE(m.neighbor_grid_count, 0) AS neighbor_grid_count,
                COALESCE(m.neighbor_risk_grid_count, 0) AS neighbor_risk_grid_count,
                m.neighbor_max_final_grid_risk,
                m.neighbor_avg_final_grid_risk,
                m.neighbor_max_daily_risk_pct,
                COALESCE(m.neighbor_top5_grid_count, 0) AS neighbor_top5_grid_count,
                COALESCE(m.neighbor_top10_grid_count, 0) AS neighbor_top10_grid_count,
                COALESCE(m.neighbor_top20_grid_count, 0) AS neighbor_top20_grid_count,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top5_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top5_grid_ratio,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top10_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top10_grid_ratio,
                CASE
                    WHEN COALESCE(m.neighbor_risk_grid_count, 0) > 0
                    THEN COALESCE(m.neighbor_top20_grid_count, 0)::DOUBLE / m.neighbor_risk_grid_count
                    ELSE NULL
                END AS neighbor_top20_grid_ratio,
                m.neighbor_top5_hit,
                m.neighbor_top10_hit,
                m.neighbor_top20_hit
            FROM random_radius_base b
            LEFT JOIN random_event_metrics_raw m
              ON m.fire_event_id = b.fire_event_id
             AND m.rep_id = b.rep_id
             AND m.radius_cells = b.radius_cells
            """
        )

        random_events_parquet = output_dir / "radius_random_event_metrics.parquet"
        random_events_csv = output_dir / "radius_random_event_metrics.csv"
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM random_event_metrics
                ORDER BY radius_cells, rep_id, fire_event_id
            )
            TO '{sql_path(random_events_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM random_event_metrics
                ORDER BY radius_cells, rep_id, fire_event_id
            )
            TO '{sql_path(random_events_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        log("[7/8] Summarize actual and random metrics")
        actual_summary = con.execute(
            """
            SELECT
                radius_cells,
                COUNT(*) AS validation_event_count,
                SUM(CASE WHEN neighbor_risk_grid_count > 0 THEN 1 ELSE 0 END) AS neighbor_risk_available_count,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_grid_count END) AS avg_neighbor_grid_count_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_risk_grid_count END) AS avg_neighbor_risk_grid_count_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_max_final_grid_risk END) AS avg_neighbor_max_final_grid_risk,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_max_daily_risk_pct END) AS avg_neighbor_max_daily_risk_pct,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top5_hit AS DOUBLE) END) AS neighbor_top5_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top10_hit AS DOUBLE) END) AS neighbor_top10_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top20_hit AS DOUBLE) END) AS neighbor_top20_hit_rate_available,
                SUM(COALESCE(neighbor_top5_hit, 0))::DOUBLE / COUNT(*) AS neighbor_top5_hit_rate_all_events,
                SUM(COALESCE(neighbor_top10_hit, 0))::DOUBLE / COUNT(*) AS neighbor_top10_hit_rate_all_events,
                SUM(COALESCE(neighbor_top20_hit, 0))::DOUBLE / COUNT(*) AS neighbor_top20_hit_rate_all_events,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top5_grid_ratio END) AS avg_event_neighbor_top5_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top10_grid_ratio END) AS avg_event_neighbor_top10_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top20_grid_ratio END) AS avg_event_neighbor_top20_grid_ratio,
                SUM(neighbor_top5_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top5_grid_ratio,
                SUM(neighbor_top10_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top10_grid_ratio,
                SUM(neighbor_top20_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top20_grid_ratio
            FROM actual_event_metrics
            GROUP BY radius_cells
            ORDER BY radius_cells
            """
        ).fetchdf()

        random_by_rep = con.execute(
            """
            SELECT
                radius_cells,
                rep_id,
                COUNT(*) AS validation_event_count,
                SUM(CASE WHEN neighbor_risk_grid_count > 0 THEN 1 ELSE 0 END) AS neighbor_risk_available_count,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_grid_count END) AS avg_neighbor_grid_count_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_risk_grid_count END) AS avg_neighbor_risk_grid_count_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_max_daily_risk_pct END) AS avg_neighbor_max_daily_risk_pct,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top5_hit AS DOUBLE) END) AS neighbor_top5_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top10_hit AS DOUBLE) END) AS neighbor_top10_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top20_hit AS DOUBLE) END) AS neighbor_top20_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top5_grid_ratio END) AS avg_event_neighbor_top5_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top10_grid_ratio END) AS avg_event_neighbor_top10_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top20_grid_ratio END) AS avg_event_neighbor_top20_grid_ratio,
                SUM(neighbor_top5_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top5_grid_ratio,
                SUM(neighbor_top10_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top10_grid_ratio,
                SUM(neighbor_top20_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top20_grid_ratio
            FROM random_event_metrics
            GROUP BY radius_cells, rep_id
            ORDER BY radius_cells, rep_id
            """
        ).fetchdf()

        con.register("random_by_rep_df", random_by_rep)
        random_summary = con.execute(
            """
            SELECT
                radius_cells,
                COUNT(*) AS random_repeat_count,
                AVG(neighbor_risk_available_count) AS random_neighbor_risk_available_count_mean,
                AVG(avg_neighbor_risk_grid_count_available) AS random_avg_neighbor_risk_grid_count_mean,
                AVG(avg_neighbor_max_daily_risk_pct) AS random_avg_neighbor_max_daily_risk_pct_mean,
                STDDEV_SAMP(avg_neighbor_max_daily_risk_pct) AS random_avg_neighbor_max_daily_risk_pct_sd,
                AVG(neighbor_top5_hit_rate_available) AS random_neighbor_top5_hit_rate_mean,
                STDDEV_SAMP(neighbor_top5_hit_rate_available) AS random_neighbor_top5_hit_rate_sd,
                QUANTILE_CONT(neighbor_top5_hit_rate_available, 0.05) AS random_neighbor_top5_hit_rate_p05,
                QUANTILE_CONT(neighbor_top5_hit_rate_available, 0.95) AS random_neighbor_top5_hit_rate_p95,
                AVG(neighbor_top10_hit_rate_available) AS random_neighbor_top10_hit_rate_mean,
                STDDEV_SAMP(neighbor_top10_hit_rate_available) AS random_neighbor_top10_hit_rate_sd,
                QUANTILE_CONT(neighbor_top10_hit_rate_available, 0.05) AS random_neighbor_top10_hit_rate_p05,
                QUANTILE_CONT(neighbor_top10_hit_rate_available, 0.95) AS random_neighbor_top10_hit_rate_p95,
                AVG(neighbor_top20_hit_rate_available) AS random_neighbor_top20_hit_rate_mean,
                STDDEV_SAMP(neighbor_top20_hit_rate_available) AS random_neighbor_top20_hit_rate_sd,
                QUANTILE_CONT(neighbor_top20_hit_rate_available, 0.05) AS random_neighbor_top20_hit_rate_p05,
                QUANTILE_CONT(neighbor_top20_hit_rate_available, 0.95) AS random_neighbor_top20_hit_rate_p95,
                AVG(avg_event_neighbor_top5_grid_ratio) AS random_avg_event_neighbor_top5_grid_ratio_mean,
                STDDEV_SAMP(avg_event_neighbor_top5_grid_ratio) AS random_avg_event_neighbor_top5_grid_ratio_sd,
                AVG(avg_event_neighbor_top10_grid_ratio) AS random_avg_event_neighbor_top10_grid_ratio_mean,
                STDDEV_SAMP(avg_event_neighbor_top10_grid_ratio) AS random_avg_event_neighbor_top10_grid_ratio_sd,
                AVG(avg_event_neighbor_top20_grid_ratio) AS random_avg_event_neighbor_top20_grid_ratio_mean,
                STDDEV_SAMP(avg_event_neighbor_top20_grid_ratio) AS random_avg_event_neighbor_top20_grid_ratio_sd,
                AVG(pooled_neighbor_top5_grid_ratio) AS random_pooled_neighbor_top5_grid_ratio_mean,
                AVG(pooled_neighbor_top10_grid_ratio) AS random_pooled_neighbor_top10_grid_ratio_mean,
                AVG(pooled_neighbor_top20_grid_ratio) AS random_pooled_neighbor_top20_grid_ratio_mean
            FROM random_by_rep_df
            GROUP BY radius_cells
            ORDER BY radius_cells
            """
        ).fetchdf()

        comparison = actual_summary.merge(random_summary, on="radius_cells", how="left")
        for k in ["top5", "top10", "top20"]:
            comparison[f"neighbor_{k}_hit_rate_lift_vs_random"] = (
                comparison[f"neighbor_{k}_hit_rate_available"]
                / comparison[f"random_neighbor_{k}_hit_rate_mean"]
            )
            comparison[f"neighbor_{k}_hit_rate_diff_vs_random"] = (
                comparison[f"neighbor_{k}_hit_rate_available"]
                - comparison[f"random_neighbor_{k}_hit_rate_mean"]
            )
            comparison[f"avg_event_neighbor_{k}_grid_ratio_lift_vs_random"] = (
                comparison[f"avg_event_neighbor_{k}_grid_ratio"]
                / comparison[f"random_avg_event_neighbor_{k}_grid_ratio_mean"]
            )
            comparison[f"avg_event_neighbor_{k}_grid_ratio_diff_vs_random"] = (
                comparison[f"avg_event_neighbor_{k}_grid_ratio"]
                - comparison[f"random_avg_event_neighbor_{k}_grid_ratio_mean"]
            )

        actual_summary_csv = report_dir / "radius_validation_actual_summary.csv"
        random_summary_csv = report_dir / "radius_validation_random_summary.csv"
        random_by_rep_csv = report_dir / "radius_validation_random_by_rep.csv"
        comparison_csv = report_dir / "radius_validation_comparison.csv"
        summary_md = report_dir / "radius_validation_comparison_summary.md"

        actual_summary.to_csv(actual_summary_csv, index=False, encoding="utf-8-sig")
        random_summary.to_csv(random_summary_csv, index=False, encoding="utf-8-sig")
        random_by_rep.to_csv(random_by_rep_csv, index=False, encoding="utf-8-sig")
        comparison.to_csv(comparison_csv, index=False, encoding="utf-8-sig")

        display_cols = [
            "radius_cells",
            "validation_event_count",
            "neighbor_risk_available_count",
            "avg_neighbor_risk_grid_count_available",
            "neighbor_top5_hit_rate_available",
            "random_neighbor_top5_hit_rate_mean",
            "random_neighbor_top5_hit_rate_p05",
            "random_neighbor_top5_hit_rate_p95",
            "neighbor_top5_hit_rate_diff_vs_random",
            "neighbor_top5_hit_rate_lift_vs_random",
            "avg_event_neighbor_top5_grid_ratio",
            "random_avg_event_neighbor_top5_grid_ratio_mean",
            "avg_event_neighbor_top5_grid_ratio_diff_vs_random",
            "neighbor_top10_hit_rate_available",
            "random_neighbor_top10_hit_rate_mean",
            "neighbor_top10_hit_rate_diff_vs_random",
            "neighbor_top20_hit_rate_available",
            "random_neighbor_top20_hit_rate_mean",
            "neighbor_top20_hit_rate_diff_vs_random",
            "avg_neighbor_max_daily_risk_pct",
            "random_avg_neighbor_max_daily_risk_pct_mean",
        ]
        display_cols = [c for c in display_cols if c in comparison.columns]

        md = []
        md.append("# 반경별 산불 이력 검증 비교 결과")
        md.append("")
        md.append("## 목적")
        md.append("")
        md.append("본 분석은 neighborhood 검증의 반경 변화에 따른 hit rate를 비교하고, 주변 후보 격자 내 고위험 격자 비율 및 random-neighborhood baseline을 함께 산출하기 위한 것이다.")
        md.append("")
        md.append("## 핵심 해석 주의")
        md.append("")
        md.append("- Exact 기준은 산불 좌표가 속한 정확한 100m 격자 하나를 평가하므로 top5/top10/top20을 각각 5%/10%/20% 기준과 직접 비교할 수 있다.")
        md.append("- Neighborhood 기준은 주변 여러 격자 중 하나라도 고위험 격자가 있으면 hit로 계산되므로, top5 hit rate를 단순 5% 기준과 직접 비교하면 안 된다.")
        md.append("- 따라서 본 분석에서는 실제 산불 위치 주변 결과를 동일 날짜·동일 반경 조건의 random-neighborhood baseline과 비교한다.")
        md.append("- `avg_event_neighbor_top5_grid_ratio`는 각 산불 사건의 주변 유효 격자 중 top5 격자가 차지하는 비율을 사건 단위로 평균한 값이다.")
        md.append("")
        md.append("## 입력")
        md.append(f"- fire history csv: `{args.fire_csv}`")
        md.append(f"- master grid: `{args.master_grid}`")
        md.append(f"- grid-date risk: `{args.grid_date_risk}`")
        md.append(f"- validation period: `{args.start_date}` ~ `{args.end_date}`")
        md.append(f"- fire-season months: `{args.fire_season_months}`")
        md.append(f"- address keywords filter: `{args.address_keywords or 'NONE'}`")
        md.append(f"- radii: `{args.radii}`")
        md.append(f"- random repeats: `{args.random_repeats}`")
        md.append(f"- random seed: `{args.random_seed}`")
        md.append("")
        md.append("## 매핑 요약")
        md.append("")
        md.append(f"- original_fire_count: `{map_info['original_fire_count']:,}`")
        md.append(f"- filtered_fire_count: `{map_info['filtered_fire_count']:,}`")
        md.append(f"- exact matched_fire_count: `{map_info['matched_fire_count']:,}`")
        md.append(f"- outside_master_grid_count: `{map_info['outside_master_grid_count']:,}`")
        md.append(f"- invalid_coord_count: `{map_info['invalid_coord_count']:,}`")
        md.append("")
        md.append("## 반경별 실제값 vs Random-neighborhood baseline")
        md.append("")
        md.append(df_to_markdown(comparison[display_cols], max_rows=100))
        md.append("")
        md.append("## 주요 출력")
        md.append(f"- actual event metrics parquet: `{actual_events_parquet}`")
        md.append(f"- random event metrics parquet: `{random_events_parquet}`")
        md.append(f"- actual summary csv: `{actual_summary_csv}`")
        md.append(f"- random summary csv: `{random_summary_csv}`")
        md.append(f"- comparison csv: `{comparison_csv}`")
        md.append(f"- summary md: `{summary_md}`")

        summary_md.write_text("\n".join(md), encoding="utf-8")

        log("[8/8] Outputs")
        log(f"actual summary: {actual_summary_csv}")
        log(f"random summary: {random_summary_csv}")
        log(f"comparison: {comparison_csv}")
        log(f"summary md: {summary_md}")
        log("DONE")

    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

from __future__ import annotations

import argparse
import json
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
DEFAULT_OUTPUT_DIR = "output/validation/weight_radius_compare"
DEFAULT_REPORT_DIR = "output/report/validation"

MODEL_PRESETS = {
    "baseline":       (0.60, 0.25, 0.15, "현재 기준안"),
    "exposure_plus":  (0.50, 0.25, 0.25, "전력설비 노출도 비중 강화"),
    "static_plus":    (0.50, 0.35, 0.15, "정적 취약도 비중 강화"),
    "balanced":       (0.50, 0.30, 0.20, "균형형 참고안"),
    "weather_plus":   (0.70, 0.20, 0.10, "기상위험 비중 강화"),
    "weather_minus":  (0.50, 0.30, 0.20, "기상위험 비중 축소"),
    "static_minus":   (0.70, 0.15, 0.15, "정적 취약도 비중 축소"),
    "exposure_minus": (0.70, 0.25, 0.05, "전력설비 노출도 비중 축소"),
}


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


def parse_models(models: str, models_json: str | None) -> pd.DataFrame:
    if models_json:
        data = json.loads(models_json)
        if not isinstance(data, list):
            raise ValueError("--models-json must be a JSON list")
        df = pd.DataFrame(data)
        required = {"model_name", "w_dwi", "w_static", "w_exposure"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"--models-json missing columns: {sorted(missing)}")
        if "description" not in df.columns:
            df["description"] = ""
    else:
        names = [x.strip() for x in models.split(",") if x.strip()]
        if not names:
            raise ValueError("--models is empty")
        rows = []
        for name in names:
            if name not in MODEL_PRESETS:
                raise ValueError(f"Unknown model preset: {name}. Available: {sorted(MODEL_PRESETS)}")
            w_dwi, w_static, w_exposure, desc = MODEL_PRESETS[name]
            rows.append(
                {
                    "model_name": name,
                    "w_dwi": w_dwi,
                    "w_static": w_static,
                    "w_exposure": w_exposure,
                    "description": desc,
                }
            )
        df = pd.DataFrame(rows)

    df["model_name"] = df["model_name"].astype(str)
    for col in ["w_dwi", "w_static", "w_exposure"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    df["weight_sum"] = df["w_dwi"] + df["w_static"] + df["w_exposure"]
    bad = df[np.abs(df["weight_sum"] - 1.0) > 1e-8]
    if not bad.empty:
        raise ValueError(
            "All weights must sum to 1.0. Bad rows:\n"
            + bad[["model_name", "w_dwi", "w_static", "w_exposure", "weight_sum"]].to_string(index=False)
        )
    if df["model_name"].duplicated().any():
        raise ValueError("model_name values must be unique")
    return df.drop(columns=["weight_sum"])


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
    log("[1/9] Read and filter fire history")
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

    log("[1/9] Read master_grid")
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

    log("[1/9] Transform fire lon/lat EPSG:4326 -> EPSG:5179")
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
    mapped_parquet = output_dir / "fire_history_mapped_for_weight_radius.parquet"
    mapped_csv = output_dir / "fire_history_mapped_for_weight_radius.csv"
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
        f"[1/9] fire original={original_count:,}, filtered={filtered_count:,}, "
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
            "Compare multiple weight models by radius, including actual fire-neighborhood "
            "metrics and random-neighborhood baseline."
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

    parser.add_argument("--models", default="baseline,exposure_plus")
    parser.add_argument(
        "--models-json",
        default=None,
        help=(
            "Optional JSON list of custom models. "
            "Each item requires model_name,w_dwi,w_static,w_exposure and optional description."
        ),
    )

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
    model_df = parse_models(args.models, args.models_json)

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
    log("17_compare_weight_models_by_radius.py")
    log("Weight models + radius comparison + random baseline")
    log(f"grid_date_risk={grid_date_risk}")
    log(f"models={model_df['model_name'].tolist()}")
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
    log(f"[2/9] random centers={len(random_centers):,} saved: {random_centers_parquet}")

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    if args.enable_progress:
        con.execute("PRAGMA enable_progress_bar")
    else:
        con.execute("PRAGMA disable_progress_bar")

    try:
        con.register("model_df", model_df)
        con.execute("CREATE OR REPLACE TEMP TABLE weight_models AS SELECT * FROM model_df")

        log("[3/9] Validate grid_date_risk columns")
        risk_cols = get_risk_columns(con, grid_date_risk)
        required_risk = {"grid_id", "date", "dwi_pct", "static_vulnerability", "exposure_risk"}
        missing_risk = sorted(required_risk.difference(risk_cols))
        if missing_risk:
            raise KeyError(f"grid_date_risk missing required columns: {missing_risk}")

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
            {"dx": dx, "dy": dy, "cheb_radius": max(abs(dx), abs(dy))}
            for dx in range(-max_radius, max_radius + 1)
            for dy in range(-max_radius, max_radius + 1)
        ])
        con.register("radii_df", radii_df)
        con.register("offsets_df", offsets_df)
        con.execute("CREATE OR REPLACE TEMP TABLE radii AS SELECT * FROM radii_df")
        con.execute("CREATE OR REPLACE TEMP TABLE offsets AS SELECT * FROM offsets_df")

        log("[4/9] Create fire date table")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE fire_dates AS
            SELECT DISTINCT CAST(occu_date AS DATE) AS date
            FROM fire_mapped
            WHERE occu_date IS NOT NULL
            """
        )

        log("[5/9] Build candidate daily ranks for selected weight models")
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE candidate_daily_rank AS
            WITH risk_components AS (
                SELECT
                    r.grid_id,
                    CAST(r.date AS DATE) AS date,
                    r.dwi_pct,
                    r.static_vulnerability,
                    r.exposure_risk
                FROM read_parquet('{sql_path(grid_date_risk)}', hive_partitioning=true, union_by_name=true) r
                JOIN fire_dates fd
                  ON fd.date = CAST(r.date AS DATE)
                WHERE CAST(r.date AS DATE) BETWEEN DATE '{args.start_date}' AND DATE '{args.end_date}'
                  AND EXTRACT(month FROM CAST(r.date AS DATE)) IN ({month_list_sql})
                  AND r.dwi_pct IS NOT NULL
                  AND r.static_vulnerability IS NOT NULL
                  AND r.exposure_risk IS NOT NULL
            ),
            candidate AS (
                SELECT
                    m.model_name,
                    m.w_dwi,
                    m.w_static,
                    m.w_exposure,
                    m.description,
                    rc.grid_id,
                    rc.date,
                    (
                        m.w_dwi * rc.dwi_pct
                      + m.w_static * rc.static_vulnerability
                      + m.w_exposure * rc.exposure_risk
                    ) AS candidate_final_grid_risk
                FROM risk_components rc
                CROSS JOIN weight_models m
            ),
            ranked AS (
                SELECT
                    *,
                    PERCENT_RANK() OVER (
                        PARTITION BY model_name, date
                        ORDER BY candidate_final_grid_risk
                    ) AS candidate_daily_risk_pct,
                    CUME_DIST() OVER (
                        PARTITION BY model_name, date
                        ORDER BY candidate_final_grid_risk DESC
                    ) AS candidate_daily_top_cume
                FROM candidate
            )
            SELECT
                *,
                CASE WHEN candidate_daily_top_cume <= 0.05 THEN 1 ELSE 0 END AS top5_flag,
                CASE WHEN candidate_daily_risk_pct >= 0.90 THEN 1 ELSE 0 END AS top10_flag,
                CASE WHEN candidate_daily_risk_pct >= 0.80 THEN 1 ELSE 0 END AS top20_flag
            FROM ranked
            """
        )
        rank_info = con.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT model_name) AS model_count,
                COUNT(DISTINCT date) AS date_count,
                COUNT(DISTINCT grid_id) AS grid_count
            FROM candidate_daily_rank
            """
        ).fetchdf().iloc[0].to_dict()
        log(
            f"[5/9] candidate rank rows={int(rank_info['row_count']):,}, "
            f"models={int(rank_info['model_count'])}, dates={int(rank_info['date_count'])}, "
            f"grids={int(rank_info['grid_count']):,}"
        )

        log("[6/9] Actual fire-neighborhood validation by model/radius")
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
                r.radius_cells,
                m.model_name,
                m.w_dwi,
                m.w_static,
                m.w_exposure,
                m.description
            FROM fire_mapped f
            CROSS JOIN radii r
            CROSS JOIN weight_models m
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
                r.model_name,
                r.candidate_final_grid_risk,
                r.candidate_daily_risk_pct,
                r.top5_flag,
                r.top10_flag,
                r.top20_flag
            FROM actual_neighbor_keys k
            LEFT JOIN candidate_daily_rank r
              ON r.grid_id = k.neighbor_grid_id
             AND r.date = k.occu_date
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_event_metrics_raw AS
            SELECT
                model_name,
                fire_event_id,
                occu_date,
                radius_cells,
                COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
                SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_grid_count,
                MAX(candidate_final_grid_risk) AS neighbor_max_final_grid_risk,
                AVG(candidate_final_grid_risk) AS neighbor_avg_final_grid_risk,
                MAX(candidate_daily_risk_pct) AS neighbor_max_daily_risk_pct,
                SUM(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_grid_count,
                SUM(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_grid_count,
                SUM(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_grid_count,
                MAX(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_hit,
                MAX(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_hit,
                MAX(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_hit
            FROM actual_neighbor_risk
            GROUP BY model_name, fire_event_id, occu_date, radius_cells
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE actual_event_metrics AS
            SELECT
                b.model_name,
                b.w_dwi,
                b.w_static,
                b.w_exposure,
                b.description,
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
              ON m.model_name = b.model_name
             AND m.fire_event_id = b.fire_event_id
             AND m.radius_cells = b.radius_cells
            """
        )

        actual_events_parquet = output_dir / "weight_radius_actual_event_metrics.parquet"
        actual_events_csv = output_dir / "weight_radius_actual_event_metrics.csv"
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM actual_event_metrics
                ORDER BY model_name, radius_cells, fire_event_id
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
                ORDER BY model_name, radius_cells, fire_event_id
            )
            TO '{sql_path(actual_events_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        log("[7/9] Random-neighborhood baseline by model/radius")
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
                r.model_name,
                r.candidate_final_grid_risk,
                r.candidate_daily_risk_pct,
                r.top5_flag,
                r.top10_flag,
                r.top20_flag
            FROM random_neighbor_keys k
            LEFT JOIN candidate_daily_rank r
              ON r.grid_id = k.neighbor_grid_id
             AND r.date = k.occu_date
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE random_event_metrics AS
            SELECT
                model_name,
                fire_event_id,
                occu_date,
                rep_id,
                radius_cells,
                COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
                SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_grid_count,
                MAX(candidate_final_grid_risk) AS neighbor_max_final_grid_risk,
                AVG(candidate_final_grid_risk) AS neighbor_avg_final_grid_risk,
                MAX(candidate_daily_risk_pct) AS neighbor_max_daily_risk_pct,
                SUM(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_grid_count,
                SUM(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_grid_count,
                SUM(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_grid_count,
                MAX(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top5_hit,
                MAX(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top10_hit,
                MAX(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END) AS neighbor_top20_hit,
                CASE
                    WHEN SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) > 0
                    THEN SUM(CASE WHEN top5_flag = 1 THEN 1 ELSE 0 END)::DOUBLE
                         / SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END)
                    ELSE NULL
                END AS neighbor_top5_grid_ratio,
                CASE
                    WHEN SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) > 0
                    THEN SUM(CASE WHEN top10_flag = 1 THEN 1 ELSE 0 END)::DOUBLE
                         / SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END)
                    ELSE NULL
                END AS neighbor_top10_grid_ratio,
                CASE
                    WHEN SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) > 0
                    THEN SUM(CASE WHEN top20_flag = 1 THEN 1 ELSE 0 END)::DOUBLE
                         / SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END)
                    ELSE NULL
                END AS neighbor_top20_grid_ratio
            FROM random_neighbor_risk
            GROUP BY model_name, fire_event_id, occu_date, rep_id, radius_cells
            """
        )

        random_events_parquet = output_dir / "weight_radius_random_event_metrics.parquet"
        random_events_csv = output_dir / "weight_radius_random_event_metrics.csv"
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM random_event_metrics
                ORDER BY model_name, radius_cells, rep_id, fire_event_id
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
                ORDER BY model_name, radius_cells, rep_id, fire_event_id
            )
            TO '{sql_path(random_events_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        log("[8/9] Summarize actual and random metrics")
        actual_summary = con.execute(
            """
            SELECT
                model_name,
                MAX(w_dwi) AS w_dwi,
                MAX(w_static) AS w_static,
                MAX(w_exposure) AS w_exposure,
                MAX(description) AS description,
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
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top5_grid_ratio END) AS avg_event_neighbor_top5_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top10_grid_ratio END) AS avg_event_neighbor_top10_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top20_grid_ratio END) AS avg_event_neighbor_top20_grid_ratio,
                SUM(neighbor_top5_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top5_grid_ratio,
                SUM(neighbor_top10_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top10_grid_ratio,
                SUM(neighbor_top20_grid_count)::DOUBLE / NULLIF(SUM(neighbor_risk_grid_count), 0) AS pooled_neighbor_top20_grid_ratio
            FROM actual_event_metrics
            GROUP BY model_name, radius_cells
            ORDER BY model_name, radius_cells
            """
        ).fetchdf()

        random_by_rep = con.execute(
            """
            SELECT
                model_name,
                radius_cells,
                rep_id,
                COUNT(*) AS validation_event_count,
                SUM(CASE WHEN neighbor_risk_grid_count > 0 THEN 1 ELSE 0 END) AS neighbor_risk_available_count,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_max_daily_risk_pct END) AS avg_neighbor_max_daily_risk_pct,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top5_hit AS DOUBLE) END) AS neighbor_top5_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top10_hit AS DOUBLE) END) AS neighbor_top10_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN CAST(neighbor_top20_hit AS DOUBLE) END) AS neighbor_top20_hit_rate_available,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top5_grid_ratio END) AS avg_event_neighbor_top5_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top10_grid_ratio END) AS avg_event_neighbor_top10_grid_ratio,
                AVG(CASE WHEN neighbor_risk_grid_count > 0 THEN neighbor_top20_grid_ratio END) AS avg_event_neighbor_top20_grid_ratio
            FROM random_event_metrics
            GROUP BY model_name, radius_cells, rep_id
            ORDER BY model_name, radius_cells, rep_id
            """
        ).fetchdf()

        con.register("random_by_rep_df", random_by_rep)
        random_summary = con.execute(
            """
            SELECT
                model_name,
                radius_cells,
                COUNT(*) AS random_repeat_count,
                AVG(neighbor_top5_hit_rate_available) AS random_neighbor_top5_hit_rate_mean,
                STDDEV_SAMP(neighbor_top5_hit_rate_available) AS random_neighbor_top5_hit_rate_sd,
                QUANTILE_CONT(neighbor_top5_hit_rate_available, 0.05) AS random_neighbor_top5_hit_rate_p05,
                QUANTILE_CONT(neighbor_top5_hit_rate_available, 0.95) AS random_neighbor_top5_hit_rate_p95,
                AVG(neighbor_top10_hit_rate_available) AS random_neighbor_top10_hit_rate_mean,
                STDDEV_SAMP(neighbor_top10_hit_rate_available) AS random_neighbor_top10_hit_rate_sd,
                AVG(neighbor_top20_hit_rate_available) AS random_neighbor_top20_hit_rate_mean,
                STDDEV_SAMP(neighbor_top20_hit_rate_available) AS random_neighbor_top20_hit_rate_sd,
                AVG(avg_event_neighbor_top5_grid_ratio) AS random_avg_event_neighbor_top5_grid_ratio_mean,
                STDDEV_SAMP(avg_event_neighbor_top5_grid_ratio) AS random_avg_event_neighbor_top5_grid_ratio_sd,
                AVG(avg_event_neighbor_top10_grid_ratio) AS random_avg_event_neighbor_top10_grid_ratio_mean,
                AVG(avg_event_neighbor_top20_grid_ratio) AS random_avg_event_neighbor_top20_grid_ratio_mean,
                AVG(avg_neighbor_max_daily_risk_pct) AS random_avg_neighbor_max_daily_risk_pct_mean,
                STDDEV_SAMP(avg_neighbor_max_daily_risk_pct) AS random_avg_neighbor_max_daily_risk_pct_sd
            FROM random_by_rep_df
            GROUP BY model_name, radius_cells
            ORDER BY model_name, radius_cells
            """
        ).fetchdf()

        comparison = actual_summary.merge(random_summary, on=["model_name", "radius_cells"], how="left")
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

        actual_summary_csv = report_dir / "weight_radius_actual_summary.csv"
        random_summary_csv = report_dir / "weight_radius_random_summary.csv"
        random_by_rep_csv = report_dir / "weight_radius_random_by_rep.csv"
        comparison_csv = report_dir / "weight_radius_comparison.csv"
        summary_md = report_dir / "weight_radius_comparison_summary.md"

        actual_summary.to_csv(actual_summary_csv, index=False, encoding="utf-8-sig")
        random_summary.to_csv(random_summary_csv, index=False, encoding="utf-8-sig")
        random_by_rep.to_csv(random_by_rep_csv, index=False, encoding="utf-8-sig")
        comparison.to_csv(comparison_csv, index=False, encoding="utf-8-sig")

        display_cols = [
            "model_name",
            "w_dwi", "w_static", "w_exposure",
            "radius_cells",
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
        md.append("# 가중치별 반경 검증 비교 결과")
        md.append("")
        md.append("## 목적")
        md.append("")
        md.append("본 분석은 후보 가중치별로 DWI 기반 위험도를 재계산한 뒤, 반경별 neighborhood 검증과 random-neighborhood baseline을 함께 비교하기 위한 것이다.")
        md.append("")
        md.append("## 기준 산식")
        md.append("")
        md.append("```text")
        md.append("candidate_final_grid_risk = w_dwi * dwi_pct + w_static * static_vulnerability + w_exposure * exposure_risk")
        md.append("```")
        md.append("")
        md.append("## 후보 가중치")
        md.append("")
        md.append(df_to_markdown(model_df, max_rows=50))
        md.append("")
        md.append("## 핵심 해석 주의")
        md.append("")
        md.append("- Neighborhood hit rate는 반경이 커질수록 자연스럽게 증가하므로, 단순 5% 기준과 직접 비교하지 않는다.")
        md.append("- 동일 날짜·동일 반경 조건에서 무작위 중심 격자를 샘플링한 random-neighborhood baseline과 비교한다.")
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
        md.append("## 가중치별 반경 검증 결과")
        md.append("")
        md.append(df_to_markdown(comparison[display_cols], max_rows=200))
        md.append("")
        md.append("## 주요 출력")
        md.append(f"- actual event metrics parquet: `{actual_events_parquet}`")
        md.append(f"- random event metrics parquet: `{random_events_parquet}`")
        md.append(f"- actual summary csv: `{actual_summary_csv}`")
        md.append(f"- random summary csv: `{random_summary_csv}`")
        md.append(f"- comparison csv: `{comparison_csv}`")
        md.append(f"- summary md: `{summary_md}`")

        summary_md.write_text("\n".join(md), encoding="utf-8")

        log("[9/9] Outputs")
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

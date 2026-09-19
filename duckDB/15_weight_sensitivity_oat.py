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
DEFAULT_OUTPUT_DIR = "output/sensitivity/weight_oat"
DEFAULT_REPORT_DIR = "output/report/sensitivity"

DEFAULT_MODELS = [
    # model_name, dwi_weight, static_weight, exposure_weight, description
    ("baseline",       0.60, 0.25, 0.15, "현재 기준안"),
    ("weather_plus",   0.70, 0.20, 0.10, "기상위험 비중 강화"),
    ("weather_minus",  0.50, 0.30, 0.20, "기상위험 비중 축소"),
    ("static_plus",    0.50, 0.35, 0.15, "정적 취약도 비중 강화"),
    ("static_minus",   0.70, 0.15, 0.15, "정적 취약도 비중 축소"),
    ("exposure_plus",  0.50, 0.25, 0.25, "전력설비 노출도 비중 강화"),
    ("exposure_minus", 0.70, 0.25, 0.05, "전력설비 노출도 비중 축소"),
    ("balanced",       0.50, 0.30, 0.20, "균형형 참고안"),
]


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


def df_to_markdown(df: pd.DataFrame, max_rows: int = 50) -> str:
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


def parse_models(models_json: str | None) -> pd.DataFrame:
    if not models_json:
        rows = DEFAULT_MODELS
        df = pd.DataFrame(
            rows,
            columns=["model_name", "w_dwi", "w_static", "w_exposure", "description"],
        )
    else:
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
        dup = df.loc[df["model_name"].duplicated(), "model_name"].tolist()
        raise ValueError(f"Duplicated model_name values: {dup}")
    return df.drop(columns=["weight_sum"])


def load_and_map_fire_history(
    fire_csv: Path,
    master_grid: Path,
    output_dir: Path,
    args: argparse.Namespace,
) -> dict:
    log("[1/7] Read and filter fire history")
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

    log("[1/7] Read master_grid")
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

    log("[1/7] Transform fire lon/lat EPSG:4326 -> EPSG:5179")
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
    mapped_parquet = output_dir / "fire_history_mapped_for_sensitivity.parquet"
    mapped_csv = output_dir / "fire_history_mapped_for_sensitivity.csv"
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
        f"[1/7] fire original={original_count:,}, filtered={filtered_count:,}, "
        f"matched={matched_count:,}, outside={outside_count:,}, invalid={invalid_coord_count:,}"
    )

    return {
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-at-a-time weight sensitivity analysis for DWI-based grid risk score. "
            "This script reuses dwi_pct/static_vulnerability/exposure_risk in grid_date_risk "
            "and recalculates fire-history validation metrics for a small set of candidate weights."
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
    parser.add_argument("--radius-cells", type=int, default=5)
    parser.add_argument("--grid-size-m", type=float, default=100.0)
    parser.add_argument("--high-risk-pct", type=float, default=0.05)

    parser.add_argument("--date-col", default="auto")
    parser.add_argument("--year-col", default="auto")
    parser.add_argument("--month-col", default="auto")
    parser.add_argument("--lon-col", default="auto")
    parser.add_argument("--lat-col", default="auto")
    parser.add_argument("--address-col", default="auto")
    parser.add_argument("--obj-id-col", default="auto")
    parser.add_argument("--region-col", default="auto")

    parser.add_argument(
        "--models-json",
        default=None,
        help=(
            "Optional JSON list of custom models. "
            "Each item requires model_name,w_dwi,w_static,w_exposure and optional description."
        ),
    )
    parser.add_argument(
        "--fire-dates-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If true, rank candidate risks only on dates where filtered fire events occurred. "
            "This is much faster and is appropriate for fire-history validation sensitivity analysis."
        ),
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.radius_cells < 0:
        raise ValueError("--radius-cells must be >= 0")
    if not (0 < args.high_risk_pct < 1):
        raise ValueError("--high-risk-pct must be between 0 and 1")

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

    model_df = parse_models(args.models_json)

    log("============================================================")
    log("15_weight_sensitivity_oat.py")
    log("One-at-a-time weight sensitivity analysis")
    log(f"grid_date_risk={grid_date_risk}")
    log(f"radius_cells={args.radius_cells}")
    log(f"models={len(model_df)}")
    log("============================================================")

    map_info = load_and_map_fire_history(fire_csv, master_grid, output_dir, args)
    mapped_parquet = map_info["mapped_parquet"]

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    if args.enable_progress:
        con.execute("PRAGMA enable_progress_bar")
    else:
        con.execute("PRAGMA disable_progress_bar")

    try:
        con.register("models_df", model_df)
        con.execute("CREATE OR REPLACE TEMP TABLE sensitivity_models AS SELECT * FROM models_df")

        log("[2/7] Validate grid_date_risk columns")
        risk_cols = get_risk_columns(con, grid_date_risk)
        required_risk = {"grid_id", "date", "dwi_pct", "static_vulnerability", "exposure_risk"}
        missing_risk = sorted(required_risk.difference(risk_cols))
        if missing_risk:
            raise KeyError(f"grid_date_risk missing required columns: {missing_risk}")

        months = parse_months(args.fire_season_months)
        month_list_sql = ",".join(str(m) for m in months)

        log("[3/7] Load fire events and build neighbor keys")
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE fire_mapped AS
            SELECT *
            FROM read_parquet('{sql_path(mapped_parquet)}', union_by_name=true)
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
            for dx in range(-args.radius_cells, args.radius_cells + 1)
            for dy in range(-args.radius_cells, args.radius_cells + 1)
        ]
        con.register("offsets_df", pd.DataFrame(offsets))

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE fire_neighbor_keys AS
            SELECT
                f.fire_event_id,
                CAST(f.occu_date AS DATE) AS occu_date,
                f.occu_year,
                f.occu_month,
                f.grid_region,
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

        neighbor_info = con.execute(
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
            f"[3/7] neighbor keys={int(neighbor_info['neighbor_key_count']):,}, "
            f"fires_with_neighbor={int(neighbor_info['fire_with_neighbor_count']):,}, "
            f"distinct_grids={int(neighbor_info['distinct_neighbor_grid_count']):,}"
        )

        log("[4/7] Create fire date table")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE fire_dates AS
            SELECT DISTINCT occu_date AS date
            FROM fire_neighbor_keys
            """
        )
        fire_date_count = con.execute("SELECT COUNT(*) FROM fire_dates").fetchone()[0]
        log(f"[4/7] fire dates={fire_date_count:,}")

        date_join = "JOIN fire_dates fd ON fd.date = CAST(r.date AS DATE)" if args.fire_dates_only else ""

        log("[5/7] Build candidate daily ranks")
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
                {date_join}
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
                CROSS JOIN sensitivity_models m
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
                CASE WHEN candidate_daily_top_cume <= {args.high_risk_pct} THEN 1 ELSE 0 END AS candidate_top5_flag,
                CASE WHEN candidate_daily_risk_pct >= 0.90 THEN 1 ELSE 0 END AS candidate_top10_flag,
                CASE WHEN candidate_daily_risk_pct >= 0.80 THEN 1 ELSE 0 END AS candidate_top20_flag
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
            f"[5/7] rank rows={int(rank_info['row_count']):,}, "
            f"models={int(rank_info['model_count']):,}, "
            f"dates={int(rank_info['date_count']):,}, grids={int(rank_info['grid_count']):,}"
        )

        log("[6/7] Join fire-neighbor keys with candidate ranks")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE fire_neighbor_candidate AS
            SELECT
                k.fire_event_id,
                k.occu_date,
                k.occu_year,
                k.occu_month,
                k.grid_region,
                k.neighbor_grid_id,
                k.dx,
                k.dy,
                k.is_exact_grid,
                r.model_name,
                r.w_dwi,
                r.w_static,
                r.w_exposure,
                r.description,
                r.candidate_final_grid_risk,
                r.candidate_daily_risk_pct,
                r.candidate_top5_flag,
                r.candidate_top10_flag,
                r.candidate_top20_flag
            FROM fire_neighbor_keys k
            LEFT JOIN candidate_daily_rank r
              ON r.grid_id = k.neighbor_grid_id
             AND r.date = k.occu_date
            """
        )

        log("[6/7] Aggregate event-level exact/neighborhood metrics")
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE exact_by_model AS
            SELECT
                model_name,
                fire_event_id,
                MAX(w_dwi) AS w_dwi,
                MAX(w_static) AS w_static,
                MAX(w_exposure) AS w_exposure,
                MAX(description) AS description,
                MAX(candidate_final_grid_risk) AS exact_final_grid_risk,
                MAX(candidate_daily_risk_pct) AS exact_daily_risk_pct,
                MAX(candidate_top5_flag) AS exact_top5_hit,
                MAX(candidate_top10_flag) AS exact_top10_hit,
                MAX(candidate_top20_flag) AS exact_top20_hit
            FROM fire_neighbor_candidate
            WHERE is_exact_grid = 1
              AND model_name IS NOT NULL
            GROUP BY model_name, fire_event_id
            """
        )

        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE neighbor_by_model AS
            SELECT
                model_name,
                fire_event_id,
                MAX(w_dwi) AS w_dwi,
                MAX(w_static) AS w_static,
                MAX(w_exposure) AS w_exposure,
                MAX(description) AS description,
                COUNT(DISTINCT neighbor_grid_id) AS neighbor_grid_count,
                SUM(CASE WHEN candidate_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_row_count,
                MAX(candidate_final_grid_risk) AS neighbor_max_final_grid_risk,
                AVG(candidate_final_grid_risk) AS neighbor_avg_final_grid_risk,
                MAX(candidate_daily_risk_pct) AS neighbor_max_daily_risk_pct,
                MAX(candidate_top5_flag) AS neighbor_top5_hit,
                MAX(candidate_top10_flag) AS neighbor_top10_hit,
                MAX(candidate_top20_flag) AS neighbor_top20_hit
            FROM fire_neighbor_candidate
            WHERE model_name IS NOT NULL
            GROUP BY model_name, fire_event_id
            """
        )

        log("[6/7] Write event-level sensitivity result")
        events_parquet = output_dir / "weight_oat_sensitivity_events.parquet"
        events_csv = output_dir / "weight_oat_sensitivity_events.csv"
        con.execute(
            f"""
            COPY (
                SELECT
                    m.model_name,
                    m.w_dwi,
                    m.w_static,
                    m.w_exposure,
                    m.description,
                    f.fire_event_id,
                    f.occu_date,
                    f.occu_year,
                    f.occu_month,
                    f.grid_region,
                    f.fire_match_status,
                    e.exact_final_grid_risk,
                    e.exact_daily_risk_pct,
                    e.exact_top5_hit,
                    e.exact_top10_hit,
                    e.exact_top20_hit,
                    n.neighbor_grid_count,
                    n.neighbor_risk_row_count,
                    n.neighbor_max_final_grid_risk,
                    n.neighbor_avg_final_grid_risk,
                    n.neighbor_max_daily_risk_pct,
                    n.neighbor_top5_hit,
                    n.neighbor_top10_hit,
                    n.neighbor_top20_hit
                FROM sensitivity_models m
                CROSS JOIN fire_mapped f
                LEFT JOIN exact_by_model e
                  ON e.model_name = m.model_name
                 AND e.fire_event_id = f.fire_event_id
                LEFT JOIN neighbor_by_model n
                  ON n.model_name = m.model_name
                 AND n.fire_event_id = f.fire_event_id
                ORDER BY m.model_name, f.fire_event_id
            )
            TO '{sql_path(events_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        con.execute(
            f"""
            COPY (
                SELECT *
                FROM read_parquet('{sql_path(events_parquet)}', union_by_name=true)
                ORDER BY model_name, fire_event_id
            )
            TO '{sql_path(events_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        log("[7/7] Build summary tables")
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE sensitivity_events AS
            SELECT *
            FROM read_parquet('{sql_path(events_parquet)}', union_by_name=true)
            """
        )

        summary_df = con.execute(
            """
            SELECT
                model_name,
                MAX(w_dwi) AS w_dwi,
                MAX(w_static) AS w_static,
                MAX(w_exposure) AS w_exposure,
                MAX(description) AS description,
                COUNT(*) AS validation_event_count,
                SUM(CASE WHEN exact_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS exact_risk_available_count,
                SUM(CASE WHEN neighbor_max_final_grid_risk IS NOT NULL THEN 1 ELSE 0 END) AS neighbor_risk_available_count,
                AVG(exact_final_grid_risk) AS avg_exact_final_grid_risk,
                AVG(exact_daily_risk_pct) AS avg_exact_daily_risk_pct,
                AVG(CAST(exact_top5_hit AS DOUBLE)) AS exact_top5_hit_rate,
                AVG(CAST(exact_top10_hit AS DOUBLE)) AS exact_top10_hit_rate,
                AVG(CAST(exact_top20_hit AS DOUBLE)) AS exact_top20_hit_rate,
                AVG(neighbor_max_final_grid_risk) AS avg_neighbor_max_final_grid_risk,
                AVG(neighbor_max_daily_risk_pct) AS avg_neighbor_max_daily_risk_pct,
                AVG(CAST(neighbor_top5_hit AS DOUBLE)) AS neighbor_top5_hit_rate,
                AVG(CAST(neighbor_top10_hit AS DOUBLE)) AS neighbor_top10_hit_rate,
                AVG(CAST(neighbor_top20_hit AS DOUBLE)) AS neighbor_top20_hit_rate
            FROM sensitivity_events
            GROUP BY model_name
            ORDER BY
                CASE WHEN model_name = 'baseline' THEN 0 ELSE 1 END,
                neighbor_top5_hit_rate DESC,
                neighbor_top10_hit_rate DESC
            """
        ).fetchdf()

        by_year_df = con.execute(
            """
            SELECT
                model_name,
                occu_year,
                COUNT(*) AS fire_count,
                AVG(CAST(exact_top5_hit AS DOUBLE)) AS exact_top5_hit_rate,
                AVG(CAST(exact_top10_hit AS DOUBLE)) AS exact_top10_hit_rate,
                AVG(CAST(exact_top20_hit AS DOUBLE)) AS exact_top20_hit_rate,
                AVG(neighbor_max_daily_risk_pct) AS avg_neighbor_max_daily_risk_pct,
                AVG(CAST(neighbor_top5_hit AS DOUBLE)) AS neighbor_top5_hit_rate,
                AVG(CAST(neighbor_top10_hit AS DOUBLE)) AS neighbor_top10_hit_rate,
                AVG(CAST(neighbor_top20_hit AS DOUBLE)) AS neighbor_top20_hit_rate
            FROM sensitivity_events
            GROUP BY model_name, occu_year
            ORDER BY model_name, occu_year
            """
        ).fetchdf()

        # Baseline deltas for quick interpretation.
        baseline = summary_df[summary_df["model_name"] == "baseline"]
        if not baseline.empty:
            b = baseline.iloc[0]
            for col in [
                "exact_top5_hit_rate",
                "neighbor_top5_hit_rate",
                "neighbor_top10_hit_rate",
                "neighbor_top20_hit_rate",
                "avg_neighbor_max_daily_risk_pct",
            ]:
                summary_df[f"delta_{col}_vs_baseline"] = summary_df[col] - b[col]

        summary_csv = report_dir / "weight_oat_sensitivity_summary.csv"
        by_year_csv = report_dir / "weight_oat_sensitivity_by_year.csv"
        summary_md = report_dir / "weight_oat_sensitivity_summary.md"

        summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
        by_year_df.to_csv(by_year_csv, index=False, encoding="utf-8-sig")

        display_cols = [
            "model_name", "w_dwi", "w_static", "w_exposure", "description",
            "validation_event_count", "exact_risk_available_count", "neighbor_risk_available_count",
            "exact_top5_hit_rate", "exact_top10_hit_rate", "exact_top20_hit_rate",
            "avg_neighbor_max_daily_risk_pct",
            "neighbor_top5_hit_rate", "neighbor_top10_hit_rate", "neighbor_top20_hit_rate",
        ]
        if "delta_neighbor_top5_hit_rate_vs_baseline" in summary_df.columns:
            display_cols += [
                "delta_neighbor_top5_hit_rate_vs_baseline",
                "delta_neighbor_top10_hit_rate_vs_baseline",
                "delta_neighbor_top20_hit_rate_vs_baseline",
            ]

        md = []
        md.append("# 가중치 One-at-a-time 민감도 분석 결과")
        md.append("")
        md.append("## 목적")
        md.append("")
        md.append("본 분석은 DWI 기반 최종 위험도 산식의 가중치가 특정 조합에만 과도하게 의존하는지 확인하기 위한 민감도 분석이다.")
        md.append("원본 feature를 다시 계산하지 않고, 이미 생성된 `grid_date_risk`의 `dwi_pct`, `static_vulnerability`, `exposure_risk`를 재조합하여 후보 가중치별 사후 검증 지표를 비교하였다.")
        md.append("")
        md.append("## 기준 산식")
        md.append("")
        md.append("```text")
        md.append("final_grid_risk = w_dwi * dwi_pct + w_static * static_vulnerability + w_exposure * exposure_risk")
        md.append("```")
        md.append("")
        md.append("## 입력")
        md.append(f"- fire history csv: `{args.fire_csv}`")
        md.append(f"- master grid: `{args.master_grid}`")
        md.append(f"- grid-date risk: `{args.grid_date_risk}`")
        md.append(f"- validation period: `{args.start_date}` ~ `{args.end_date}`")
        md.append(f"- fire-season months: `{args.fire_season_months}`")
        md.append(f"- address keywords filter: `{args.address_keywords or 'NONE'}`")
        md.append(f"- neighborhood radius cells: `{args.radius_cells}`")
        md.append(f"- fire dates only: `{args.fire_dates_only}`")
        md.append("")
        md.append("## 매핑 요약")
        md.append("")
        md.append(f"- original_fire_count: `{map_info['original_fire_count']:,}`")
        md.append(f"- filtered_fire_count: `{map_info['filtered_fire_count']:,}`")
        md.append(f"- exact matched_fire_count: `{map_info['matched_fire_count']:,}`")
        md.append(f"- outside_master_grid_count: `{map_info['outside_master_grid_count']:,}`")
        md.append(f"- invalid_coord_count: `{map_info['invalid_coord_count']:,}`")
        md.append(f"- neighbor_grid_mapped_event_count: `{int(neighbor_info['fire_with_neighbor_count']):,}`")
        md.append("")
        md.append("## 후보 가중치별 검증 요약")
        md.append("")
        md.append(df_to_markdown(summary_df[display_cols], max_rows=100))
        md.append("")
        md.append("## 해석 기준")
        md.append("")
        md.append("- `exact_top5_hit_rate`: 산불 좌표가 속한 정확한 100m 격자가 해당 날짜 위험도 상위 5%에 포함된 비율")
        md.append("- `neighbor_top5_hit_rate`: 산불 좌표 주변 ±N격자 내에서 해당 날짜 위험도 상위 5% 격자가 포착된 비율")
        md.append("- 무작위 기준으로 top5, top10, top20 hit rate의 기대값은 각각 약 5%, 10%, 20%이다.")
        md.append("- 본 분석은 최적 가중치를 탐색하기 위한 grid search가 아니라, 기준 가중치 주변의 안정성을 확인하는 축소형 민감도 분석이다.")
        md.append("")
        md.append("## 출력")
        md.append(f"- event-level parquet: `{events_parquet}`")
        md.append(f"- event-level csv: `{events_csv}`")
        md.append(f"- summary csv: `{summary_csv}`")
        md.append(f"- by-year csv: `{by_year_csv}`")

        summary_md.write_text("\n".join(md), encoding="utf-8")

        log(f"[7/7] wrote summary: {summary_md}")
        log("DONE")

    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

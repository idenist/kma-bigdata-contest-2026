from __future__ import annotations

import argparse
import math
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
    raise ImportError(
        "pyproj is required. Install it with: pip install pyproj"
    ) from exc


DEFAULT_POLE_CSV = "data/test_hanjeon.csv"
DEFAULT_MASTER_GRID = "data/master_grid.parquet"
DEFAULT_GRID_DATE_RISK = "output/risk/grid_date_risk/**/*.parquet"
DEFAULT_OUTPUT_DIR = "output/risk"
DEFAULT_REPORT_DIR = "output/report/risk"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def project_root_from_script() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name.lower() == "duckdb":
        return current.parent.parent
    return current.parent


def resolve_path(root: Path, path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p


def sql_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def file_size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    if path.is_dir():
        total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return total / (1024 * 1024)
    return 0.0


def read_parquet_columns(path: Path) -> list[str]:
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{sql_path(path)}', union_by_name=true) LIMIT 0"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def validate_inputs(pole_csv: Path, master_grid: Path, risk_glob: Path) -> None:
    if not pole_csv.exists():
        raise FileNotFoundError(f"Pole CSV not found: {pole_csv}")
    if not master_grid.exists():
        raise FileNotFoundError(f"Master grid parquet not found: {master_grid}")

    risk_root = str(risk_glob).split("**")[0].rstrip("/\\")
    if not Path(risk_root).exists():
        raise FileNotFoundError(f"Grid-date risk root not found: {risk_root}")


def map_poles_to_grid(
    pole_csv: Path,
    master_grid: Path,
    output_parquet: Path,
    grid_size_m: int,
    pole_id_col: str,
    lon_col: str,
    lat_col: str,
) -> dict:
    log("[1/5] Read pole CSV")
    pole = pd.read_csv(pole_csv)

    required_pole = {pole_id_col, lon_col, lat_col}
    missing_pole = sorted(required_pole.difference(pole.columns))
    if missing_pole:
        raise KeyError(f"Pole CSV missing required columns: {missing_pole}")

    pole = pole[[pole_id_col, lon_col, lat_col]].copy()
    pole[pole_id_col] = pd.to_numeric(pole[pole_id_col], errors="raise").astype("int64")
    pole[lon_col] = pd.to_numeric(pole[lon_col], errors="coerce")
    pole[lat_col] = pd.to_numeric(pole[lat_col], errors="coerce")

    invalid_coord = int(pole[[lon_col, lat_col]].isna().any(axis=1).sum())
    if invalid_coord:
        log(f"[WARN] invalid lon/lat rows: {invalid_coord:,}")

    log("[1/5] Read master_grid grid_id/grid_x/grid_y")
    master_cols = read_parquet_columns(master_grid)
    required_master = {"grid_id", "grid_x", "grid_y"}
    missing_master = sorted(required_master.difference(master_cols))
    if missing_master:
        raise KeyError(
            f"master_grid must contain grid_id, grid_x, grid_y. Missing: {missing_master}"
        )

    master = pd.read_parquet(master_grid, columns=["grid_id", "grid_x", "grid_y"])
    master = master.dropna(subset=["grid_id", "grid_x", "grid_y"]).copy()
    master["grid_x"] = pd.to_numeric(master["grid_x"], errors="raise").astype("int64")
    master["grid_y"] = pd.to_numeric(master["grid_y"], errors="raise").astype("int64")
    master = master.drop_duplicates(["grid_x", "grid_y"], keep="first")
    master = master[["grid_x", "grid_y", "grid_id"]].copy()

    log("[1/5] Transform lon/lat EPSG:4326 -> EPSG:5179")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x5179, y5179 = transformer.transform(
        pole[lon_col].to_numpy(),
        pole[lat_col].to_numpy(),
    )
    pole["x5179"] = x5179
    pole["y5179"] = y5179

    # 기존 06_fire_target.py와 동일한 방식:
    # EPSG:5179 m 좌표를 100m로 나누어 floor한 값을 master_grid의 grid_x/grid_y와 매핑한다.
    log("[1/5] Build 100m grid_x/grid_y for poles")
    pole["grid_x"] = np.floor(pole["x5179"] / grid_size_m).astype("int64")
    pole["grid_y"] = np.floor(pole["y5179"] / grid_size_m).astype("int64")

    log("[1/5] Join poles with master_grid")
    mapped = pole.merge(master, on=["grid_x", "grid_y"], how="left", validate="many_to_one")
    mapped["match_status"] = np.where(mapped["grid_id"].isna(), "unmatched_grid", "matched")

    row_count = len(mapped)
    matched_count = int((mapped["match_status"] == "matched").sum())
    unmatched_count = row_count - matched_count
    match_rate = matched_count / row_count if row_count else 0.0

    log(
        f"[1/5] pole rows={row_count:,}, matched={matched_count:,}, "
        f"unmatched={unmatched_count:,}, match_rate={match_rate:.4%}"
    )

    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.register("mapped_poles", mapped)
        con.execute(
            f"""
            COPY mapped_poles
            TO '{sql_path(output_parquet)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        con.close()

    log(f"[1/5] pole_grid_map saved: {output_parquet} ({file_size_mb(output_parquet):,.1f} MB)")

    return {
        "pole_count": row_count,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "match_rate": match_rate,
        "invalid_coord_count": invalid_coord,
        "master_grid_xy_count": len(master),
    }


def build_grid_period_risk(
    con: duckdb.DuckDBPyConnection,
    risk_glob: Path,
    output_parquet: Path,
    top_n_days: int,
) -> dict:
    log("[2/5] Aggregate grid-date risk -> grid-period risk")
    risk_glob_sql = sql_path(risk_glob)
    output_sql = sql_path(output_parquet)

    # grid_id별 전체 기간 위험도 집계.
    # mean_top5_daily_risk는 각 grid_id의 final_grid_risk 상위 N일 평균.
    con.execute(
        f"""
        COPY (
            WITH ranked AS (
                SELECT
                    grid_id,
                    final_grid_risk,
                    daily_high_risk_flag,
                    ROW_NUMBER() OVER (
                        PARTITION BY grid_id
                        ORDER BY final_grid_risk DESC
                    ) AS rn
                FROM read_parquet('{risk_glob_sql}', hive_partitioning=true, union_by_name=true)
                WHERE final_grid_risk IS NOT NULL
            ),
            agg AS (
                SELECT
                    grid_id,
                    COUNT(*) AS observed_days,
                    MAX(final_grid_risk) AS max_daily_risk,
                    AVG(CASE WHEN rn <= {top_n_days} THEN final_grid_risk END) AS mean_top{top_n_days}_daily_risk,
                    AVG(CAST(daily_high_risk_flag AS DOUBLE)) AS high_risk_day_ratio
                FROM ranked
                GROUP BY grid_id
            )
            SELECT
                grid_id,
                observed_days,
                max_daily_risk,
                mean_top{top_n_days}_daily_risk AS mean_top5_daily_risk,
                high_risk_day_ratio,
                (
                    0.50 * COALESCE(max_daily_risk, 0.0)
                  + 0.30 * COALESCE(mean_top{top_n_days}_daily_risk, 0.0)
                  + 0.20 * COALESCE(high_risk_day_ratio, 0.0)
                ) AS grid_period_risk
            FROM agg
        )
        TO '{output_sql}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS grid_count,
            MIN(grid_period_risk) AS min_grid_period_risk,
            AVG(grid_period_risk) AS avg_grid_period_risk,
            MAX(grid_period_risk) AS max_grid_period_risk,
            MIN(observed_days) AS min_observed_days,
            MAX(observed_days) AS max_observed_days
        FROM read_parquet('{output_sql}', union_by_name=true)
        """
    ).fetchdf()

    row = summary.iloc[0].to_dict()
    log(
        "[2/5] grid_period_risk saved: "
        f"{output_parquet} ({file_size_mb(output_parquet):,.1f} MB), "
        f"grid_count={int(row['grid_count']):,}"
    )
    return row


def build_pole_score_outputs(
    con: duckdb.DuckDBPyConnection,
    pole_grid_map: Path,
    grid_period_risk: Path,
    output_dir: Path,
    decision_top_pct: float,
    pole_id_col: str,
) -> dict:
    log("[3/5] Join pole grid map with grid-period risk")
    pole_sql = sql_path(pole_grid_map)
    grid_sql = sql_path(grid_period_risk)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pole_scored AS
        SELECT
            p.pole_id,
            p.lon,
            p.lat,
            p.x5179,
            p.y5179,
            p.grid_x,
            p.grid_y,
            p.grid_id,
            p.match_status,
            g.observed_days,
            g.max_daily_risk,
            g.mean_top5_daily_risk,
            g.high_risk_day_ratio,
            g.grid_period_risk,
            CASE
                WHEN g.grid_id IS NULL THEN NULL
                ELSE (
                    0.50 * COALESCE(g.max_daily_risk, 0.0)
                  + 0.30 * COALESCE(g.mean_top5_daily_risk, 0.0)
                  + 0.20 * COALESCE(g.high_risk_day_ratio, 0.0)
                )
            END AS pole_risk_score
        FROM read_parquet('{pole_sql}', union_by_name=true) p
        LEFT JOIN read_parquet('{grid_sql}', union_by_name=true) g
        USING (grid_id)
        """
    )

    log("[3/5] Rank matched poles and assign decision")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE matched_decision AS
        SELECT
            pole_id,
            pole_risk_rank,
            pole_risk_pct,
            CASE
                WHEN pole_risk_rank <= CEIL(matched_count * {decision_top_pct}) THEN 1
                ELSE 0
            END AS decision
        FROM (
            SELECT
                pole_id,
                ROW_NUMBER() OVER (
                    ORDER BY pole_risk_score DESC, pole_id ASC
                ) AS pole_risk_rank,
                PERCENT_RANK() OVER (
                    ORDER BY pole_risk_score
                ) AS pole_risk_pct,
                COUNT(*) OVER () AS matched_count
            FROM pole_scored
            WHERE pole_risk_score IS NOT NULL
        )
        """
    )

    log("[4/5] Write full pole risk parquet")
    full_parquet = output_dir / "pole_risk_score.parquet"
    con.execute(
        f"""
        COPY (
            SELECT
                s.*,
                m.pole_risk_rank,
                m.pole_risk_pct,
                COALESCE(m.decision, 0)::INTEGER AS decision
            FROM pole_scored s
            LEFT JOIN matched_decision m USING (pole_id)
            ORDER BY pole_id
        )
        TO '{sql_path(full_parquet)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    log("[4/5] Write full pole risk CSV")
    full_csv = output_dir / "pole_risk_score.csv"
    con.execute(
        f"""
        COPY (
            SELECT *
            FROM read_parquet('{sql_path(full_parquet)}', union_by_name=true)
            ORDER BY pole_id
        )
        TO '{sql_path(full_csv)}'
        (HEADER, DELIMITER ',')
        """
    )

    log("[4/5] Write submission-like CSV")
    decision_csv = output_dir / "test_hanjeon_with_decision.csv"
    con.execute(
        f"""
        COPY (
            SELECT
                pole_id,
                lon,
                lat,
                decision
            FROM read_parquet('{sql_path(full_parquet)}', union_by_name=true)
            ORDER BY pole_id
        )
        TO '{sql_path(decision_csv)}'
        (HEADER, DELIMITER ',')
        """
    )

    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS pole_count,
            SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched_pole_count,
            SUM(CASE WHEN match_status <> 'matched' THEN 1 ELSE 0 END) AS unmatched_pole_count,
            SUM(decision) AS decision_1_count,
            AVG(decision) AS decision_1_rate,
            MIN(pole_risk_score) AS min_pole_risk_score,
            AVG(pole_risk_score) AS avg_pole_risk_score,
            MAX(pole_risk_score) AS max_pole_risk_score
        FROM read_parquet('{sql_path(full_parquet)}', union_by_name=true)
        """
    ).fetchdf()

    row = summary.iloc[0].to_dict()
    log(
        "[4/5] outputs saved: "
        f"decision_csv={decision_csv} ({file_size_mb(decision_csv):,.1f} MB), "
        f"full_csv={full_csv} ({file_size_mb(full_csv):,.1f} MB)"
    )
    return {
        **row,
        "full_parquet": str(full_parquet),
        "full_csv": str(full_csv),
        "decision_csv": str(decision_csv),
        "full_parquet_mb": file_size_mb(full_parquet),
        "full_csv_mb": file_size_mb(full_csv),
        "decision_csv_mb": file_size_mb(decision_csv),
    }


def write_report(
    report_dir: Path,
    pole_mapping_summary: dict,
    grid_period_summary: dict,
    pole_output_summary: dict,
    args: argparse.Namespace,
) -> None:
    log("[5/5] Write summary report")
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_csv = report_dir / "pole_risk_score_summary.csv"
    pd.DataFrame(
        [
            {
                **{f"mapping_{k}": v for k, v in pole_mapping_summary.items()},
                **{f"grid_{k}": v for k, v in grid_period_summary.items()},
                **{f"pole_{k}": v for k, v in pole_output_summary.items()},
            }
        ]
    ).to_csv(summary_csv, index=False, encoding="utf-8-sig")

    md = f"""# Pole Risk Score 생성 결과

## 입력

- pole csv: `{args.pole_csv}`
- master grid: `{args.master_grid}`
- grid-date risk: `{args.grid_date_risk}`

## 출력

- pole grid map: `{args.output_dir}/pole_grid_map.parquet`
- grid period risk: `{args.output_dir}/grid_period_risk.parquet`
- full pole risk parquet: `{args.output_dir}/pole_risk_score.parquet`
- full pole risk csv: `{args.output_dir}/pole_risk_score.csv`
- decision csv: `{args.output_dir}/test_hanjeon_with_decision.csv`

## 전신주-grid 매핑 요약

- pole_count: `{int(pole_mapping_summary["pole_count"]):,}`
- matched_count: `{int(pole_mapping_summary["matched_count"]):,}`
- unmatched_count: `{int(pole_mapping_summary["unmatched_count"]):,}`
- match_rate: `{pole_mapping_summary["match_rate"]:.4%}`
- invalid_coord_count: `{int(pole_mapping_summary["invalid_coord_count"]):,}`
- master_grid_xy_count: `{int(pole_mapping_summary["master_grid_xy_count"]):,}`

## grid-period risk 요약

- grid_count: `{int(grid_period_summary["grid_count"]):,}`
- min_grid_period_risk: `{grid_period_summary["min_grid_period_risk"]}`
- avg_grid_period_risk: `{grid_period_summary["avg_grid_period_risk"]}`
- max_grid_period_risk: `{grid_period_summary["max_grid_period_risk"]}`
- min_observed_days: `{int(grid_period_summary["min_observed_days"]):,}`
- max_observed_days: `{int(grid_period_summary["max_observed_days"]):,}`

## pole risk score 요약

- pole_count: `{int(pole_output_summary["pole_count"]):,}`
- matched_pole_count: `{int(pole_output_summary["matched_pole_count"]):,}`
- unmatched_pole_count: `{int(pole_output_summary["unmatched_pole_count"]):,}`
- decision_1_count: `{int(pole_output_summary["decision_1_count"]):,}`
- decision_1_rate: `{pole_output_summary["decision_1_rate"]:.4%}`
- min_pole_risk_score: `{pole_output_summary["min_pole_risk_score"]}`
- avg_pole_risk_score: `{pole_output_summary["avg_pole_risk_score"]}`
- max_pole_risk_score: `{pole_output_summary["max_pole_risk_score"]}`

## 산식

```text
pole_risk_score
= 0.50 * max_daily_risk
+ 0.30 * mean_top5_daily_risk
+ 0.20 * high_risk_day_ratio
```

- `max_daily_risk`: 분석 기간 중 해당 전신주가 속한 grid의 최대 일별 위험도
- `mean_top5_daily_risk`: 분석 기간 중 해당 grid의 위험도 상위 {args.top_n_days}일 평균
- `high_risk_day_ratio`: 해당 grid가 날짜별 상위 5% 고위험 격자에 포함된 날짜 비율
- `decision`: `pole_risk_score` 기준 상위 {args.decision_top_pct * 100:.1f}% 전신주에 1 부여

## 해석상 주의

- `decision=1`은 산불 발생 확률이 아니라 전력설비 점검 우선순위 상위 후보군을 의미한다.
- master_grid와 매핑되지 않은 전신주는 위험도 산정이 불가능하므로 `decision=0`으로 처리하고 `match_status=unmatched_grid`로 남긴다.
"""
    (report_dir / "pole_risk_score_summary.md").write_text(md, encoding="utf-8")
    log(f"[5/5] report saved: {report_dir / 'pole_risk_score_summary.md'}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build pole-level risk score and decision from grid-date risk."
    )
    parser.add_argument("--pole-csv", default=DEFAULT_POLE_CSV)
    parser.add_argument("--master-grid", default=DEFAULT_MASTER_GRID)
    parser.add_argument("--grid-date-risk", default=DEFAULT_GRID_DATE_RISK)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)

    parser.add_argument("--pole-id-col", default="pole_id")
    parser.add_argument("--lon-col", default="lon")
    parser.add_argument("--lat-col", default="lat")

    parser.add_argument("--grid-size-m", type=int, default=100)
    parser.add_argument("--top-n-days", type=int, default=5)
    parser.add_argument("--decision-top-pct", type=float, default=0.05)

    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reuse-pole-grid-map", action="store_true")
    parser.add_argument("--reuse-grid-period-risk", action="store_true")
    parser.add_argument("--enable-progress", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if not (0 < args.decision_top_pct < 1):
        raise ValueError("--decision-top-pct must be between 0 and 1.")
    if args.top_n_days < 1:
        raise ValueError("--top-n-days must be >= 1")

    root = project_root_from_script()
    pole_csv = resolve_path(root, args.pole_csv)
    master_grid = resolve_path(root, args.master_grid)
    grid_date_risk = resolve_path(root, args.grid_date_risk)
    output_dir = resolve_path(root, args.output_dir)
    report_dir = resolve_path(root, args.report_dir)

    validate_inputs(pole_csv, master_grid, grid_date_risk)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    pole_grid_map = output_dir / "pole_grid_map.parquet"
    grid_period_risk = output_dir / "grid_period_risk.parquet"

    removable_outputs = [
        pole_grid_map,
        grid_period_risk,
        output_dir / "pole_risk_score.parquet",
        output_dir / "pole_risk_score.csv",
        output_dir / "test_hanjeon_with_decision.csv",
    ]

    if args.overwrite:
        for p in removable_outputs:
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()

    log("START pole risk scoring")
    log(f"pole_csv={pole_csv}")
    log(f"master_grid={master_grid}")
    log(f"grid_date_risk={grid_date_risk}")
    log(f"output_dir={output_dir}")

    if pole_grid_map.exists() and args.reuse_pole_grid_map:
        log(f"[1/5] Reuse existing pole_grid_map: {pole_grid_map}")
        con_tmp = duckdb.connect()
        try:
            row = con_tmp.execute(
                f"""
                SELECT
                    COUNT(*) AS pole_count,
                    SUM(CASE WHEN match_status='matched' THEN 1 ELSE 0 END) AS matched_count,
                    SUM(CASE WHEN match_status<>'matched' THEN 1 ELSE 0 END) AS unmatched_count,
                    SUM(CASE WHEN lon IS NULL OR lat IS NULL THEN 1 ELSE 0 END) AS invalid_coord_count,
                    COUNT(DISTINCT grid_id) AS master_grid_xy_count
                FROM read_parquet('{sql_path(pole_grid_map)}', union_by_name=true)
                """
            ).fetchdf().iloc[0].to_dict()
            pole_mapping_summary = {
                "pole_count": int(row["pole_count"]),
                "matched_count": int(row["matched_count"]),
                "unmatched_count": int(row["unmatched_count"]),
                "match_rate": int(row["matched_count"]) / int(row["pole_count"]),
                "invalid_coord_count": int(row["invalid_coord_count"]),
                "master_grid_xy_count": int(row["master_grid_xy_count"]),
            }
        finally:
            con_tmp.close()
    else:
        pole_mapping_summary = map_poles_to_grid(
            pole_csv=pole_csv,
            master_grid=master_grid,
            output_parquet=pole_grid_map,
            grid_size_m=args.grid_size_m,
            pole_id_col=args.pole_id_col,
            lon_col=args.lon_col,
            lat_col=args.lat_col,
        )

    con = duckdb.connect()
    try:
        con.execute(f"PRAGMA threads={args.threads}")
        con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
        if args.enable_progress:
            con.execute("PRAGMA enable_progress_bar")
        else:
            con.execute("PRAGMA disable_progress_bar")

        if grid_period_risk.exists() and args.reuse_grid_period_risk:
            log(f"[2/5] Reuse existing grid_period_risk: {grid_period_risk}")
            grid_period_summary = con.execute(
                f"""
                SELECT
                    COUNT(*) AS grid_count,
                    MIN(grid_period_risk) AS min_grid_period_risk,
                    AVG(grid_period_risk) AS avg_grid_period_risk,
                    MAX(grid_period_risk) AS max_grid_period_risk,
                    MIN(observed_days) AS min_observed_days,
                    MAX(observed_days) AS max_observed_days
                FROM read_parquet('{sql_path(grid_period_risk)}', union_by_name=true)
                """
            ).fetchdf().iloc[0].to_dict()
        else:
            grid_period_summary = build_grid_period_risk(
                con=con,
                risk_glob=grid_date_risk,
                output_parquet=grid_period_risk,
                top_n_days=args.top_n_days,
            )

        pole_output_summary = build_pole_score_outputs(
            con=con,
            pole_grid_map=pole_grid_map,
            grid_period_risk=grid_period_risk,
            output_dir=output_dir,
            decision_top_pct=args.decision_top_pct,
            pole_id_col=args.pole_id_col,
        )
    finally:
        con.close()

    write_report(
        report_dir=report_dir,
        pole_mapping_summary=pole_mapping_summary,
        grid_period_summary=grid_period_summary,
        pole_output_summary=pole_output_summary,
        args=args,
    )

    log("DONE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

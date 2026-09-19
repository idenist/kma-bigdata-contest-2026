from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd


DEFAULT_MASTER_GRID = "data/master_grid.parquet"
DEFAULT_POLE_RISK = "output/risk/pole_risk_score.parquet"
DEFAULT_OUTPUT_DIR = "output/report/risk"
DEFAULT_OPTIONAL_POLE_REGION = "output/risk/pole_risk_with_region.parquet"


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


def get_parquet_columns(con: duckdb.DuckDBPyConnection, parquet_path: Path) -> list[str]:
    rows = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet('{sql_path(parquet_path)}', union_by_name=true)
        LIMIT 0
        """
    ).fetchall()
    return [r[0] for r in rows]


def detect_region_columns(master_cols: list[str]) -> list[str]:
    """
    Try to detect administrative region columns from master_grid.
    The project files may use different naming conventions, so this function is intentionally broad.
    """
    lower_to_original = {c.lower(): c for c in master_cols}

    ordered_candidate_groups = [
        ["sido_nm", "sigungu_nm", "emd_nm"],
        ["sido_nm", "sgg_nm", "emd_nm"],
        ["ctprvn_nm", "sgg_nm", "emd_nm"],
        ["ctpv_nm", "sgg_nm", "emd_nm"],
        ["sd_nm", "sgg_nm", "emd_nm"],
        ["sido", "sigungu", "emd"],
        ["sido", "sgg", "emd"],
        ["sido_name", "sigungu_name", "emd_name"],
        ["province", "city", "town"],
        ["province_name", "city_name", "town_name"],
        ["시도명", "시군구명", "읍면동명"],
        ["시도", "시군구", "읍면동"],
        ["adm_sido", "adm_sigungu", "adm_emd"],
        ["adm_sido_nm", "adm_sigungu_nm", "adm_emd_nm"],
        ["region_sido", "region_sigungu", "region_emd"],
    ]

    for group in ordered_candidate_groups:
        if all(c.lower() in lower_to_original for c in group):
            return [lower_to_original[c.lower()] for c in group]

    # Fallback: choose likely columns by keyword, preserving a useful administrative order.
    keywords_with_rank = [
        ("sido", 10),
        ("ctprvn", 10),
        ("ctpv", 10),
        ("province", 10),
        ("시도", 10),
        ("sd_", 10),
        ("sigungu", 20),
        ("sgg", 20),
        ("city", 20),
        ("시군구", 20),
        ("emd", 30),
        ("town", 30),
        ("읍면동", 30),
        ("adm", 40),
        ("region", 50),
    ]

    candidates: list[tuple[int, str]] = []
    for col in master_cols:
        lc = col.lower()
        if col == "grid_id":
            continue
        for keyword, rank in keywords_with_rank:
            if keyword in lc or keyword in col:
                candidates.append((rank, col))
                break

    # de-duplicate, max 3 columns
    seen = set()
    ordered = []
    for _, col in sorted(candidates, key=lambda x: (x[0], x[1])):
        if col not in seen:
            seen.add(col)
            ordered.append(col)

    return ordered[:3]


def parse_region_cols(value: str, master_cols: list[str]) -> list[str]:
    if value.lower() == "auto":
        detected = detect_region_columns(master_cols)
        if not detected:
            raise KeyError(
                "Could not auto-detect region columns from master_grid.\n"
                "Use --region-cols col1,col2,col3 manually.\n"
                f"Available master_grid columns:\n{master_cols}"
            )
        return detected

    if value.lower() in {"none", "all"}:
        return []

    requested = [x.strip() for x in value.split(",") if x.strip()]
    missing = [c for c in requested if c not in master_cols]
    if missing:
        raise KeyError(
            f"--region-cols includes missing columns: {missing}\n"
            f"Available master_grid columns:\n{master_cols}"
        )
    return requested


def validate_required_columns(
    pole_cols: list[str],
    master_cols: list[str],
    region_cols: list[str],
) -> None:
    required_pole = {
        "pole_id",
        "lon",
        "lat",
        "grid_id",
        "match_status",
        "max_daily_risk",
        "mean_top5_daily_risk",
        "high_risk_day_ratio",
        "pole_risk_score",
        "decision",
    }
    missing_pole = sorted(required_pole.difference(pole_cols))
    if missing_pole:
        raise KeyError(
            f"pole_risk parquet missing required columns: {missing_pole}"
        )

    if "grid_id" not in master_cols:
        raise KeyError("master_grid must contain grid_id.")

    missing_region = [c for c in region_cols if c not in master_cols]
    if missing_region:
        raise KeyError(f"master_grid missing region columns: {missing_region}")


def create_pole_region_table(
    con: duckdb.DuckDBPyConnection,
    pole_risk_path: Path,
    master_grid_path: Path,
    region_cols: list[str],
) -> None:
    log("[1/4] Build master_region table")

    if region_cols:
        region_aggs = ",\n                ".join(
            [
                f"ANY_VALUE(CAST({q(c)} AS VARCHAR)) AS {q(c)}"
                for c in region_cols
            ]
        )
        region_select = ",\n                " + region_aggs
    else:
        region_select = ""

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE master_region AS
        SELECT
            grid_id
            {region_select}
        FROM read_parquet('{sql_path(master_grid_path)}', union_by_name=true)
        GROUP BY grid_id
        """
    )

    log("[1/4] Join pole risk with region columns")

    if region_cols:
        region_join_select = ",\n                ".join(
            [
                f"COALESCE(CAST(m.{q(c)} AS VARCHAR), 'UNKNOWN') AS {q(c)}"
                for c in region_cols
            ]
        )
        region_join_sql = ",\n                " + region_join_select
    else:
        region_join_sql = ",\n                'ALL' AS region_all"

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pole_region AS
        SELECT
            p.*
            {region_join_sql}
        FROM read_parquet('{sql_path(pole_risk_path)}', union_by_name=true) p
        LEFT JOIN master_region m
        USING (grid_id)
        """
    )


def write_optional_pole_region(
    con: duckdb.DuckDBPyConnection,
    output_path: Path,
    overwrite: bool,
) -> None:
    if output_path.exists():
        if overwrite:
            output_path.unlink()
        else:
            log(f"[SKIP] optional pole-region parquet already exists: {output_path}")
            return

    log("[2/4] Write optional pole_risk_with_region parquet")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
            SELECT *
            FROM pole_region
            ORDER BY pole_id
        )
        TO '{sql_path(output_path)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    log(f"[2/4] saved: {output_path}")


def build_summary_query(region_cols: list[str]) -> str:
    if region_cols:
        region_select = ",\n            ".join([q(c) for c in region_cols])
        group_by = ", ".join([q(c) for c in region_cols])
    else:
        region_select = "region_all"
        group_by = "region_all"

    return f"""
        SELECT
            {region_select},
            COUNT(*) AS pole_count,
            COUNT(DISTINCT grid_id) AS grid_count,
            SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched_pole_count,
            SUM(CASE WHEN match_status <> 'matched' THEN 1 ELSE 0 END) AS unmatched_pole_count,
            SUM(decision) AS decision_1_count,
            AVG(CAST(decision AS DOUBLE)) AS decision_1_rate,
            MIN(pole_risk_score) AS min_pole_risk_score,
            AVG(pole_risk_score) AS avg_pole_risk_score,
            QUANTILE_CONT(pole_risk_score, 0.50) AS median_pole_risk_score,
            QUANTILE_CONT(pole_risk_score, 0.90) AS p90_pole_risk_score,
            QUANTILE_CONT(pole_risk_score, 0.95) AS p95_pole_risk_score,
            MAX(pole_risk_score) AS max_pole_risk_score,
            AVG(max_daily_risk) AS avg_max_daily_risk,
            AVG(mean_top5_daily_risk) AS avg_mean_top5_daily_risk,
            AVG(high_risk_day_ratio) AS avg_high_risk_day_ratio
        FROM pole_region
        GROUP BY {group_by}
        ORDER BY decision_1_count DESC, avg_pole_risk_score DESC
    """


def write_region_summaries(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
    region_cols: list[str],
    top_n_regions: int,
) -> dict:
    log("[3/4] Write region summary CSV files")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_paths = {}

    if region_cols:
        levels = [(i, region_cols[:i]) for i in range(1, len(region_cols) + 1)]
    else:
        levels = [(1, [])]

    for i, cols in levels:
        level_name = "_".join(cols) if cols else "all"
        safe_level_name = (
            level_name.replace("/", "_")
            .replace("\\", "_")
            .replace(" ", "_")
            .replace(":", "_")
        )

        summary_csv = output_dir / f"region_risk_summary_{safe_level_name}.csv"
        top_csv = output_dir / f"region_risk_top{top_n_regions}_{safe_level_name}.csv"

        query = build_summary_query(cols)

        log(f"[3/4] summary level={level_name}")
        con.execute(
            f"""
            COPY (
                {query}
            )
            TO '{sql_path(summary_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        con.execute(
            f"""
            COPY (
                SELECT *
                FROM ({query})
                LIMIT {top_n_regions}
            )
            TO '{sql_path(top_csv)}'
            (HEADER, DELIMITER ',')
            """
        )

        summary_paths[level_name] = {
            "summary_csv": str(summary_csv),
            "top_csv": str(top_csv),
            "group_cols": cols,
        }

    return summary_paths


def fetch_overall_summary(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS pole_count,
            COUNT(DISTINCT grid_id) AS grid_count,
            SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END) AS matched_pole_count,
            SUM(CASE WHEN match_status <> 'matched' THEN 1 ELSE 0 END) AS unmatched_pole_count,
            SUM(decision) AS decision_1_count,
            AVG(CAST(decision AS DOUBLE)) AS decision_1_rate,
            MIN(pole_risk_score) AS min_pole_risk_score,
            AVG(pole_risk_score) AS avg_pole_risk_score,
            MAX(pole_risk_score) AS max_pole_risk_score
        FROM pole_region
        """
    ).fetchdf().iloc[0].to_dict()
    return row


def fetch_top_regions_for_md(
    con: duckdb.DuckDBPyConnection,
    region_cols: list[str],
    limit: int,
) -> pd.DataFrame:
    cols = region_cols if region_cols else []
    query = build_summary_query(cols)
    return con.execute(
        f"""
        SELECT *
        FROM ({query})
        LIMIT {limit}
        """
    ).fetchdf()


def write_report(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path,
    summary_paths: dict,
    region_cols: list[str],
    args: argparse.Namespace,
) -> None:
    log("[4/4] Write markdown report")

    overall = fetch_overall_summary(con)
    detailed_cols = region_cols if region_cols else []
    top_regions = fetch_top_regions_for_md(con, detailed_cols, args.top_n_regions)

    report_path = output_dir / "region_risk_summary.md"
    summary_csv = output_dir / "region_risk_overall_summary.csv"

    pd.DataFrame([overall]).to_csv(summary_csv, index=False, encoding="utf-8-sig")

    top_markdown = top_regions.to_markdown(index=False) if not top_regions.empty else "(no rows)"

    outputs_md = "\n".join(
        [
            f"- `{level}`: `{paths['summary_csv']}` / `{paths['top_csv']}`"
            for level, paths in summary_paths.items()
        ]
    )

    region_cols_text = ", ".join(region_cols) if region_cols else "ALL"

    md = f"""# Region Risk Summary 생성 결과

## 입력

- master grid: `{args.master_grid}`
- pole risk: `{args.pole_risk}`

## 지역 컬럼

- region columns: `{region_cols_text}`

## 출력

- overall summary: `{summary_csv}`
{outputs_md}

## 전체 요약

- pole_count: `{int(overall["pole_count"]):,}`
- grid_count: `{int(overall["grid_count"]):,}`
- matched_pole_count: `{int(overall["matched_pole_count"]):,}`
- unmatched_pole_count: `{int(overall["unmatched_pole_count"]):,}`
- decision_1_count: `{int(overall["decision_1_count"]):,}`
- decision_1_rate: `{overall["decision_1_rate"]:.4%}`
- min_pole_risk_score: `{overall["min_pole_risk_score"]}`
- avg_pole_risk_score: `{overall["avg_pole_risk_score"]}`
- max_pole_risk_score: `{overall["max_pole_risk_score"]}`

## 상위 지역 요약

아래 표는 가장 상세한 지역 수준 기준으로 `decision_1_count`가 많은 지역을 우선 정렬한 결과이다.

{top_markdown}

## 해석 기준

- `pole_count`: 해당 지역에 속한 전신주 수
- `decision_1_count`: 점검 우선순위 상위 5%로 분류된 전신주 수
- `decision_1_rate`: 해당 지역 전신주 중 decision=1 비율
- `avg_pole_risk_score`: 해당 지역 전신주의 평균 위험도 점수
- `p95_pole_risk_score`: 해당 지역 전신주 위험도 상위 5% 경계값
- `avg_high_risk_day_ratio`: 해당 지역 전신주가 속한 격자가 날짜별 상위 5% 고위험 격자에 포함된 평균 비율

## 주의

- `decision_1_count`는 전신주 수가 많은 지역에서 커질 수 있으므로, 지역 간 상대 위험도를 비교할 때는 `decision_1_rate`, `avg_pole_risk_score`, `p95_pole_risk_score`를 함께 확인해야 한다.
- 본 요약은 행정구역별 위험도 분포를 해석하기 위한 자료이며, 최종 의사결정 단위는 전신주이다.
"""
    report_path.write_text(md, encoding="utf-8")
    log(f"[4/4] report saved: {report_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize pole risk score by administrative region."
    )
    parser.add_argument("--master-grid", default=DEFAULT_MASTER_GRID)
    parser.add_argument("--pole-risk", default=DEFAULT_POLE_RISK)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    parser.add_argument(
        "--region-cols",
        default="auto",
        help=(
            "Comma-separated region columns in master_grid. "
            "Use 'auto' for auto-detection or 'none' for overall-only summary."
        ),
    )

    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="16GB")
    parser.add_argument("--top-n-regions", type=int, default=30)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--write-pole-with-region",
        action="store_true",
        help="Also write output/risk/pole_risk_with_region.parquet.",
    )
    parser.add_argument(
        "--pole-with-region-output",
        default=DEFAULT_OPTIONAL_POLE_REGION,
    )
    parser.add_argument("--enable-progress", action="store_true")
    parser.add_argument(
        "--list-master-columns",
        action="store_true",
        help="Print master_grid columns and exit.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    root = project_root_from_script()

    master_grid = resolve_path(root, args.master_grid)
    pole_risk = resolve_path(root, args.pole_risk)
    output_dir = resolve_path(root, args.output_dir)
    pole_with_region_output = resolve_path(root, args.pole_with_region_output)

    if not master_grid.exists():
        raise FileNotFoundError(f"master_grid not found: {master_grid}")
    if not pole_risk.exists():
        raise FileNotFoundError(f"pole risk parquet not found: {pole_risk}")

    if output_dir.exists() and args.overwrite:
        # only remove region summary files, not the whole risk report directory
        for p in output_dir.glob("region_risk_*"):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
    output_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute(f"PRAGMA threads={args.threads}")
        con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
        if args.enable_progress:
            con.execute("PRAGMA enable_progress_bar")
        else:
            con.execute("PRAGMA disable_progress_bar")

        master_cols = get_parquet_columns(con, master_grid)
        pole_cols = get_parquet_columns(con, pole_risk)

        if args.list_master_columns:
            print("\n".join(master_cols))
            return

        region_cols = parse_region_cols(args.region_cols, master_cols)
        validate_required_columns(pole_cols, master_cols, region_cols)

        log("START region risk summary")
        log(f"master_grid={master_grid}")
        log(f"pole_risk={pole_risk}")
        log(f"output_dir={output_dir}")
        log(f"region_cols={region_cols if region_cols else ['ALL']}")

        create_pole_region_table(
            con=con,
            pole_risk_path=pole_risk,
            master_grid_path=master_grid,
            region_cols=region_cols,
        )

        if args.write_pole_with_region:
            write_optional_pole_region(
                con=con,
                output_path=pole_with_region_output,
                overwrite=args.overwrite,
            )
        else:
            log("[2/4] Skip optional pole_risk_with_region parquet")

        summary_paths = write_region_summaries(
            con=con,
            output_dir=output_dir,
            region_cols=region_cols,
            top_n_regions=args.top_n_regions,
        )

        write_report(
            con=con,
            output_dir=output_dir,
            summary_paths=summary_paths,
            region_cols=region_cols,
            args=args,
        )

        log("DONE")
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

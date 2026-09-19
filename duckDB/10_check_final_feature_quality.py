from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import duckdb


DEFAULT_FINAL_PARQUET = "output/final/final_feature_daily/**/*.parquet"
DEFAULT_OUTPUT_DIR = "output/report/qc_exact"

IMPORTANT_COLUMNS = [
    "grid_id",
    "date",
    "month",
    "dwi",
    "dwi_n",
    "ffdri",
    "pffdri",
    "fmi",
    "fmi_n",
    "tmi_p",
    "slope",
    "slope_pct",
    "road_prox",
    "river_far",
    "pei",
    "pole_count",
]

NUMERIC_CHECK_COLUMNS = [
    "dwi",
    "dwi_n",
    "ffdri",
    "pffdri",
    "fmi",
    "fmi_n",
    "tmi_base_n",
    "tmi_p",
    "slope",
    "slope_pct",
    "road_prox",
    "river_far",
    "pei",
    "pole_count",
    "ta_mean",
    "hm_mean",
    "wind_ws_mean",
    "effective_humidity",
]


def project_root_from_script() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name.lower() == "duckdb":
        return current.parent.parent
    return current.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def sql_path(path_or_glob: Path | str) -> str:
    return str(path_or_glob).replace("\\", "/").replace("'", "''")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class StepTimer:
    def __init__(self, title: str):
        self.title = title
        self.start = None

    def __enter__(self):
        self.start = time.perf_counter()
        log(f"START {self.title}")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.start
        if exc_type is None:
            log(f"DONE  {self.title} ({elapsed:,.1f}s)")
        else:
            log(f"FAIL  {self.title} ({elapsed:,.1f}s)")
        return False


def fetch_one_dict(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else {}


def write_rows_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def existing_columns(con: duckdb.DuckDBPyConnection, final_glob: str) -> list[str]:
    rows = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet('{final_glob}', hive_partitioning=true, union_by_name=true)
        LIMIT 0
        """
    ).fetchall()
    return [row[0] for row in rows]


def safe_int(value) -> int | None:
    if value is None:
        return None
    return int(value)


def pct(numer, denom) -> float | None:
    if numer is None or denom in (None, 0):
        return None
    return float(numer) / float(denom)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Exact/progress QC for output/final/final_feature_daily. "
            "Use this when final report numbers must be exact."
        )
    )
    parser.add_argument("--final-parquet", default=DEFAULT_FINAL_PARQUET)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    parser.add_argument(
        "--enable-progress",
        action="store_true",
        help="Enable DuckDB progress bar for long-running queries.",
    )
    parser.add_argument(
        "--skip-monthly",
        action="store_true",
        help="Skip monthly exact summary.",
    )
    parser.add_argument(
        "--exact-duplicate-check",
        action="store_true",
        help="Run exact grid_id+date duplicate check. This can be slow.",
    )
    parser.add_argument(
        "--write-sample",
        action="store_true",
        help="Write first 1000 rows of important columns.",
    )
    args = parser.parse_args()

    root = project_root_from_script()
    final_path = resolve_path(root, args.final_parquet)
    output_dir = resolve_path(root, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_root = str(final_path).split("**")[0].rstrip("/\\")
    if not Path(final_root).exists():
        raise FileNotFoundError(f"Final feature parquet root not found: {final_root}")

    final_glob = sql_path(final_path)

    log("============================================================")
    log("10_check_final_feature_quality_v2.py")
    log("This version reports exact grid/date counts with step logs.")
    log(f"final_parquet={final_path}")
    log(f"output_dir={output_dir}")
    log("============================================================")

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    if args.enable_progress:
        con.execute("PRAGMA enable_progress_bar")
    else:
        con.execute("PRAGMA disable_progress_bar")

    try:
        with StepTimer("[1/9] Create final_view"):
            con.execute(
                f"""
                CREATE OR REPLACE VIEW final_view AS
                SELECT *
                FROM read_parquet('{final_glob}', hive_partitioning=true, union_by_name=true)
                """
            )

        with StepTimer("[2/9] Read schema"):
            columns = existing_columns(con, final_glob)
            column_set = set(columns)
            (output_dir / "final_feature_columns.txt").write_text(
                "\n".join(columns), encoding="utf-8"
            )

        missing_important = [c for c in IMPORTANT_COLUMNS if c not in column_set]
        existing_important = [c for c in IMPORTANT_COLUMNS if c in column_set]
        existing_numeric = [c for c in NUMERIC_CHECK_COLUMNS if c in column_set]

        with StepTimer("[3/9] Exact row/date/grid overview"):
            overview_exprs = [
                "COUNT(*) AS row_count",
                "COUNT(DISTINCT grid_id) AS exact_grid_count" if "grid_id" in column_set else "NULL AS exact_grid_count",
                "COUNT(DISTINCT date) AS exact_date_count" if "date" in column_set else "NULL AS exact_date_count",
                "MIN(date) AS min_date" if "date" in column_set else "NULL AS min_date",
                "MAX(date) AS max_date" if "date" in column_set else "NULL AS max_date",
                "SUM(CASE WHEN grid_id IS NULL THEN 1 ELSE 0 END) AS null_grid_id" if "grid_id" in column_set else "NULL AS null_grid_id",
                "SUM(CASE WHEN date IS NULL THEN 1 ELSE 0 END) AS null_date" if "date" in column_set else "NULL AS null_date",
            ]
            overview = fetch_one_dict(
                con,
                "SELECT\n  " + ",\n  ".join(overview_exprs) + "\nFROM final_view",
            )

        row_count = safe_int(overview.get("row_count"))
        exact_grid_count = safe_int(overview.get("exact_grid_count"))
        exact_date_count = safe_int(overview.get("exact_date_count"))
        expected_panel_rows = (
            exact_grid_count * exact_date_count
            if exact_grid_count is not None and exact_date_count is not None
            else None
        )
        panel_row_diff = (
            row_count - expected_panel_rows
            if row_count is not None and expected_panel_rows is not None
            else None
        )

        with StepTimer("[4/9] Null and numeric range scan"):
            null_exprs = []
            for col in existing_important:
                q = quote_ident(col)
                null_exprs.append(f"SUM(CASE WHEN {q} IS NULL THEN 1 ELSE 0 END) AS null__{col}")

            range_exprs = []
            for col in existing_numeric:
                q = quote_ident(col)
                range_exprs.extend([
                    f"MIN({q}) AS min__{col}",
                    f"MAX({q}) AS max__{col}",
                    f"AVG({q}) AS mean__{col}",
                ])

            scan_exprs = null_exprs + range_exprs
            scan_summary = (
                fetch_one_dict(con, "SELECT\n  " + ",\n  ".join(scan_exprs) + "\nFROM final_view")
                if scan_exprs
                else {}
            )

        pffdri_null_count = safe_int(scan_summary.get("null__pffdri")) if "pffdri" in column_set else None

        with StepTimer("[5/9] P-FFDRI valid panel check"):
            if {"grid_id", "date", "pffdri"}.issubset(column_set):
                valid_summary = fetch_one_dict(
                    con,
                    """
                    SELECT
                        COUNT(*) AS pffdri_valid_row_count,
                        COUNT(DISTINCT grid_id) AS pffdri_valid_grid_count,
                        COUNT(DISTINCT date) AS pffdri_valid_date_count
                    FROM final_view
                    WHERE pffdri IS NOT NULL
                    """
                )
                null_grid_summary = fetch_one_dict(
                    con,
                    """
                    SELECT
                        COUNT(DISTINCT grid_id) AS pffdri_null_grid_count,
                        COUNT(DISTINCT date) AS pffdri_null_date_count
                    FROM final_view
                    WHERE pffdri IS NULL
                    """
                )
            else:
                valid_summary = {
                    "pffdri_valid_row_count": None,
                    "pffdri_valid_grid_count": None,
                    "pffdri_valid_date_count": None,
                }
                null_grid_summary = {
                    "pffdri_null_grid_count": None,
                    "pffdri_null_date_count": None,
                }

        valid_row_count = safe_int(valid_summary.get("pffdri_valid_row_count"))
        valid_grid_count = safe_int(valid_summary.get("pffdri_valid_grid_count"))
        valid_date_count = safe_int(valid_summary.get("pffdri_valid_date_count"))
        valid_expected_rows = (
            valid_grid_count * valid_date_count
            if valid_grid_count is not None and valid_date_count is not None
            else None
        )
        valid_panel_row_diff = (
            valid_row_count - valid_expected_rows
            if valid_row_count is not None and valid_expected_rows is not None
            else None
        )

        # Write overview CSV
        overview_rows = [
            ["row_count", row_count],
            ["exact_grid_count", exact_grid_count],
            ["exact_date_count", exact_date_count],
            ["expected_panel_rows", expected_panel_rows],
            ["panel_row_diff", panel_row_diff],
            ["min_date", overview.get("min_date")],
            ["max_date", overview.get("max_date")],
            ["null_grid_id", overview.get("null_grid_id")],
            ["null_date", overview.get("null_date")],
            ["pffdri_null_count", pffdri_null_count],
            ["pffdri_null_grid_count", null_grid_summary.get("pffdri_null_grid_count")],
            ["pffdri_null_date_count", null_grid_summary.get("pffdri_null_date_count")],
            ["pffdri_valid_row_count", valid_row_count],
            ["pffdri_valid_grid_count", valid_grid_count],
            ["pffdri_valid_date_count", valid_date_count],
            ["pffdri_valid_expected_rows", valid_expected_rows],
            ["pffdri_valid_panel_row_diff", valid_panel_row_diff],
        ]
        write_rows_csv(output_dir / "final_feature_overview_exact.csv", ["metric", "value"], overview_rows)

        null_rows = []
        for col in existing_important:
            null_count = safe_int(scan_summary.get(f"null__{col}"))
            null_rows.append([col, null_count, row_count, pct(null_count, row_count)])
        write_rows_csv(
            output_dir / "final_feature_nulls_exact.csv",
            ["column_name", "null_count", "row_count", "null_rate"],
            null_rows,
        )

        range_rows = []
        for col in existing_numeric:
            range_rows.append([
                col,
                scan_summary.get(f"min__{col}"),
                scan_summary.get(f"max__{col}"),
                scan_summary.get(f"mean__{col}"),
            ])
        write_rows_csv(
            output_dir / "final_feature_ranges_exact.csv",
            ["column_name", "min_value", "max_value", "mean_value"],
            range_rows,
        )

        if not args.skip_monthly:
            with StepTimer("[6/9] Monthly exact summary"):
                month_col_expr = "CAST(year AS VARCHAR) || '-' || LPAD(CAST(month AS VARCHAR), 2, '0')" if {"year", "month"}.issubset(column_set) else "STRFTIME(CAST(date AS DATE), '%Y-%m')"
                pffdri_expr = (
                    "MIN(pffdri) AS min_pffdri, AVG(pffdri) AS avg_pffdri, MAX(pffdri) AS max_pffdri, SUM(CASE WHEN pffdri IS NULL THEN 1 ELSE 0 END) AS null_pffdri"
                    if "pffdri" in column_set
                    else "NULL AS min_pffdri, NULL AS avg_pffdri, NULL AS max_pffdri, NULL AS null_pffdri"
                )
                con.execute(
                    f"""
                    COPY (
                        SELECT
                            {month_col_expr} AS year_month,
                            COUNT(*) AS row_count,
                            COUNT(DISTINCT grid_id) AS grid_count,
                            COUNT(DISTINCT date) AS date_count,
                            MIN(date) AS min_date,
                            MAX(date) AS max_date,
                            {pffdri_expr}
                        FROM final_view
                        GROUP BY 1
                        ORDER BY 1
                    )
                    TO '{sql_path(output_dir / "final_feature_monthly_exact.csv")}'
                    (HEADER, DELIMITER ',')
                    """
                )
        else:
            log("SKIP [6/9] Monthly exact summary (--skip-monthly)")

        duplicate_summary = None
        if args.exact_duplicate_check:
            with StepTimer("[7/9] Exact duplicate grid_id+date check"):
                duplicate_summary = fetch_one_dict(
                    con,
                    """
                    WITH dup AS (
                        SELECT grid_id, date, COUNT(*) AS cnt
                        FROM final_view
                        GROUP BY grid_id, date
                        HAVING COUNT(*) > 1
                    )
                    SELECT
                        COUNT(*) AS duplicate_key_count,
                        COALESCE(SUM(cnt), 0) AS duplicated_rows_including_original,
                        COALESCE(SUM(cnt - 1), 0) AS extra_duplicate_rows,
                        COALESCE(MAX(cnt), 0) AS max_duplicate_count
                    FROM dup
                    """
                )
                write_rows_csv(
                    output_dir / "final_feature_duplicate_summary.csv",
                    list(duplicate_summary.keys()),
                    [list(duplicate_summary.values())],
                )
        else:
            log("SKIP [7/9] Exact duplicate grid_id+date check (use --exact-duplicate-check)")

        if args.write_sample and existing_important:
            with StepTimer("[8/9] Write sample 1000"):
                sample_cols = [quote_ident(c) for c in existing_important]
                con.execute(
                    f"""
                    COPY (
                        SELECT {", ".join(sample_cols)}
                        FROM final_view
                        LIMIT 1000
                    )
                    TO '{sql_path(output_dir / "final_feature_sample_1000.csv")}'
                    (HEADER, DELIMITER ',')
                    """
                )
        else:
            log("SKIP [8/9] Write sample 1000 (use --write-sample)")

        with StepTimer("[9/9] Write markdown report"):
            fire_label_exists = "fire_label" in column_set
            pffdri_min = scan_summary.get("min__pffdri")
            pffdri_max = scan_summary.get("max__pffdri")
            pffdri_range_ok = (
                pffdri_min is not None
                and pffdri_max is not None
                and pffdri_min >= 0
                and pffdri_max <= 100
            )

            passes = []
            warnings = []

            if not missing_important:
                passes.append("위험도 산정 후보 중요 컬럼이 모두 존재합니다.")
            else:
                warnings.append("중요 컬럼 누락: " + ", ".join(missing_important))

            if overview.get("null_grid_id") == 0 and overview.get("null_date") == 0:
                passes.append("grid_id/date 결측이 없습니다.")
            else:
                warnings.append(
                    f"grid_id/date 결측 존재: grid_id={overview.get('null_grid_id')}, date={overview.get('null_date')}"
                )

            if panel_row_diff == 0:
                passes.append(
                    f"원본 feature가 exact_grid_count × exact_date_count = row_count 구조입니다. "
                    f"({exact_grid_count:,} × {exact_date_count:,} = {row_count:,})"
                )
            else:
                warnings.append(
                    f"원본 feature panel row 차이 확인 필요: row_count - grid_count*date_count = {panel_row_diff}"
                )

            if "pffdri" in column_set:
                if pffdri_null_count == 0:
                    passes.append("pffdri 결측이 없습니다.")
                else:
                    warnings.append(
                        f"pffdri 결측 존재: {pffdri_null_count:,} rows, "
                        f"{null_grid_summary.get('pffdri_null_grid_count')} grids"
                    )

                if valid_panel_row_diff == 0:
                    passes.append(
                        f"pffdri 유효 데이터가 균형 패널 구조입니다. "
                        f"({valid_grid_count:,} × {valid_date_count:,} = {valid_row_count:,})"
                    )
                else:
                    warnings.append(
                        f"pffdri 유효 데이터 panel row 차이 확인 필요: {valid_panel_row_diff}"
                    )

                if pffdri_range_ok:
                    passes.append("pffdri min/max가 0~100 범위 안에 있습니다.")
                else:
                    warnings.append(f"pffdri 범위 확인 필요: min={pffdri_min}, max={pffdri_max}")

            if fire_label_exists:
                warnings.append("final feature에 fire_label이 존재합니다. 이번 방향에서는 학습 타깃으로 사용하지 마세요.")
            else:
                passes.append("fire_label이 포함되지 않은 final feature입니다.")

            if duplicate_summary is None:
                warnings.append("정확한 중복 key 검사는 생략했습니다. 필요 시 --exact-duplicate-check로 별도 실행하세요.")
            elif duplicate_summary.get("duplicate_key_count") == 0:
                passes.append("grid_id+date 중복 key가 없습니다.")
            else:
                warnings.append(
                    f"grid_id+date 중복 key 존재: duplicate_key_count={duplicate_summary.get('duplicate_key_count')}, "
                    f"extra_duplicate_rows={duplicate_summary.get('extra_duplicate_rows')}"
                )

            md_lines = [
                "# 1단계 기존 전처리 산출물 품질 확인 결과 - EXACT v2",
                "",
                "## 입력 경로",
                f"- final feature: `{args.final_parquet}`",
                f"- output dir: `{args.output_dir}`",
                "",
                "## 전체 요약",
                f"- row_count: `{row_count}`",
                f"- exact_grid_count: `{exact_grid_count}`",
                f"- exact_date_count: `{exact_date_count}`",
                f"- expected_panel_rows: `{expected_panel_rows}`",
                f"- panel_row_diff: `{panel_row_diff}`",
                f"- date range: `{overview.get('min_date')}` ~ `{overview.get('max_date')}`",
                "",
                "## P-FFDRI 유효 데이터 요약",
                f"- pffdri_null_count: `{pffdri_null_count}`",
                f"- pffdri_null_grid_count: `{null_grid_summary.get('pffdri_null_grid_count')}`",
                f"- pffdri_null_date_count: `{null_grid_summary.get('pffdri_null_date_count')}`",
                f"- pffdri_valid_row_count: `{valid_row_count}`",
                f"- pffdri_valid_grid_count: `{valid_grid_count}`",
                f"- pffdri_valid_date_count: `{valid_date_count}`",
                f"- pffdri_valid_expected_rows: `{valid_expected_rows}`",
                f"- pffdri_valid_panel_row_diff: `{valid_panel_row_diff}`",
                "",
                "## 해석",
                "- 이전 FAST 리포트의 `approx_grid_count`, `approx_date_count`는 `approx_count_distinct` 기반 근사값이므로 최종 보고서 수치로 사용하지 않습니다.",
                "- 본 v2 리포트의 `exact_grid_count`, `exact_date_count`를 최종 보고서 수치로 사용합니다.",
                "- `pffdri` 결측은 위험도 산정 단계에서 제외됩니다.",
                "",
                "## PASS",
                *(f"- {msg}" for msg in passes),
                "",
                "## WARNING / 확인 필요",
                *(f"- {msg}" for msg in warnings),
                "",
                "## 생성 파일",
                "- `final_feature_overview_exact.csv`",
                "- `final_feature_columns.txt`",
                "- `final_feature_nulls_exact.csv`",
                "- `final_feature_ranges_exact.csv`",
                "- `final_feature_monthly_exact.csv`는 `--skip-monthly` 미사용 시 생성",
                "- `final_feature_duplicate_summary.csv`는 `--exact-duplicate-check` 사용 시 생성",
                "- `final_feature_sample_1000.csv`는 `--write-sample` 사용 시 생성",
                "",
                "## 다음 단계",
                "- 이 리포트에서 정확한 grid/date/row 구조를 확인한 뒤 `0623_결과보고서_v2.md`의 QC 수치를 정정합니다.",
            ]

            report_path = output_dir / "final_feature_quality_summary_exact.md"
            report_path.write_text("\n".join(md_lines), encoding="utf-8")

        log("============================================================")
        log("DONE exact QC completed")
        log(f"REPORT: {output_dir / 'final_feature_quality_summary_exact.md'}")
        log(f"OUTPUT_DIR: {output_dir}")
        log("============================================================")

    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import duckdb


DEFAULT_FINAL_PARQUET = "output/final/final_feature_daily/**/*.parquet"
DEFAULT_OUTPUT_DIR = "output/risk/grid_date_risk"
DEFAULT_REPORT_DIR = "output/report/risk"

# DWI 기반 분리형 산식에서는 pffdri를 최종 위험도 계산에 사용하지 않는다.
# pffdri는 존재할 경우 참고/비교용 컬럼으로만 출력할 수 있다.
REQUIRED_COLUMNS = ["grid_id", "date", "dwi", "fmi_n", "tmi_p", "pei"]

OPTIONAL_COLUMNS = [
    "pffdri", "dwi_n", "ffdri", "fmi", "slope", "slope_n", "road_prox",
    "river_far", "pole_count", "ta_mean", "hm_mean", "wind_ws_mean",
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


def as_sql_path(path_or_glob: Path | str) -> str:
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


def get_columns(con: duckdb.DuckDBPyConnection, final_glob: str) -> list[str]:
    rows = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet('{final_glob}', hive_partitioning=true, union_by_name=true)
        LIMIT 0
        """
    ).fetchall()
    return [row[0] for row in rows]


def fetch_one_dict(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else {}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build DWI-based grid-date fire risk score from final_feature_daily. "
            "This v4 formula excludes P-FFDRI from the final score to avoid double-counting "
            "static/exposure factors already embedded in P-FFDRI."
        )
    )
    parser.add_argument("--final-parquet", default=DEFAULT_FINAL_PARQUET)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="23GB")
    parser.add_argument("--start-date", default=None, help="Optional inclusive start date, e.g. 2024-01-01")
    parser.add_argument("--end-date", default=None, help="Optional inclusive end date, e.g. 2024-12-31")
    parser.add_argument(
        "--fire-season-months",
        default="2,3,4,5",
        help="Comma-separated fire-season months. Default is 2,3,4,5.",
    )
    parser.add_argument(
        "--no-month-filter",
        action="store_true",
        help="Do not filter to fire-season months. Use only if the input data is already guaranteed to be restricted.",
    )
    parser.add_argument(
        "--validation-end-year",
        type=int,
        default=2024,
        help="Last year with fire history available for post-hoc validation. Default: 2024.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Delete output dir before writing.")
    parser.add_argument("--skip-summary", action="store_true", help="Skip summary report after writing parquet.")
    parser.add_argument("--enable-progress", action="store_true", help="Enable DuckDB progress bar.")
    parser.add_argument("--weather-weight", type=float, default=0.60)
    parser.add_argument("--static-weight", type=float, default=0.25)
    parser.add_argument("--exposure-weight", type=float, default=0.15)
    parser.add_argument("--high-risk-pct", type=float, default=0.05)
    parser.add_argument("--static-fmi-weight", type=float, default=0.40)
    parser.add_argument("--static-tmi-weight", type=float, default=0.35)
    parser.add_argument("--static-slope-weight", type=float, default=0.10)
    parser.add_argument("--static-road-weight", type=float, default=0.075)
    parser.add_argument("--static-river-weight", type=float, default=0.075)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    root = project_root_from_script()
    final_path = resolve_path(root, args.final_parquet)
    output_dir = resolve_path(root, args.output_dir)
    report_dir = resolve_path(root, args.report_dir)

    final_root = str(final_path).split("**")[0].rstrip("/\\")
    if not Path(final_root).exists():
        raise FileNotFoundError(f"Final feature parquet root not found: {final_root}")

    if output_dir.exists() and args.overwrite:
        log(f"Remove existing output dir: {output_dir}")
        shutil.rmtree(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(
            f"Output dir already exists and is not empty: {output_dir}\n"
            "Use --overwrite or choose another --output-dir."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    weight_sum = args.weather_weight + args.static_weight + args.exposure_weight
    if abs(weight_sum - 1.0) > 1e-8:
        raise ValueError(f"weather/static/exposure weights must sum to 1.0. Current sum={weight_sum}")

    static_weight_sum = (
        args.static_fmi_weight + args.static_tmi_weight + args.static_slope_weight
        + args.static_road_weight + args.static_river_weight
    )
    if abs(static_weight_sum - 1.0) > 1e-8:
        raise ValueError(f"static component weights must sum to 1.0. Current sum={static_weight_sum}")

    if not (0 < args.high_risk_pct < 1):
        raise ValueError("--high-risk-pct must be between 0 and 1.")

    final_glob = as_sql_path(final_path)

    log("============================================================")
    log("11_build_grid_risk_score_v4.py")
    log("Formula version: DWI-based separated risk score")
    log("P-FFDRI is excluded from final_grid_risk calculation.")
    log(f"final_parquet={final_path}")
    log(f"output_dir={output_dir}")
    log(f"report_dir={report_dir}")
    log("============================================================")

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={args.threads}")
    con.execute(f"PRAGMA memory_limit='{args.memory_limit}'")
    if args.enable_progress:
        con.execute("PRAGMA enable_progress_bar")
    else:
        con.execute("PRAGMA disable_progress_bar")

    try:
        with StepTimer("[1/8] Read schema"):
            columns = get_columns(con, final_glob)
            colset = set(columns)
            missing = [c for c in REQUIRED_COLUMNS if c not in colset]
            if missing:
                raise KeyError("Required columns are missing from final_feature_daily: " + ", ".join(missing))

        has_slope_n = "slope_n" in colset
        has_slope = "slope" in colset
        has_road_prox = "road_prox" in colset
        has_river_far = "river_far" in colset
        has_pole_count = "pole_count" in colset
        has_pffdri = "pffdri" in colset

        filters = []
        if args.start_date and args.end_date:
            filters.append(f"CAST(date AS DATE) BETWEEN DATE '{args.start_date}' AND DATE '{args.end_date}'")
        elif args.start_date:
            filters.append(f"CAST(date AS DATE) >= DATE '{args.start_date}'")
        elif args.end_date:
            filters.append(f"CAST(date AS DATE) <= DATE '{args.end_date}'")

        fire_season_months = [
            int(x.strip()) for x in str(args.fire_season_months).split(",") if x.strip()
        ]
        if not args.no_month_filter:
            if not fire_season_months:
                raise ValueError("--fire-season-months is empty.")
            invalid_months = [m for m in fire_season_months if m < 1 or m > 12]
            if invalid_months:
                raise ValueError(f"Invalid fire-season months: {invalid_months}")
            filters.append(
                "EXTRACT(month FROM CAST(date AS DATE)) IN ("
                + ",".join(str(m) for m in fire_season_months)
                + ")"
            )

        date_filter = ("WHERE " + " AND ".join(filters)) if filters else ""

        with StepTimer("[2/8] Create final_view"):
            con.execute(
                f"""
                CREATE OR REPLACE VIEW final_view AS
                SELECT *
                FROM read_parquet('{final_glob}', hive_partitioning=true, union_by_name=true)
                {date_filter}
                """
            )

        with StepTimer("[3/8] Build grid-level static/exposure base"):
            slope_select = (
                "AVG(slope_n) AS slope_n"
                if has_slope_n
                else ("AVG(slope) AS slope" if has_slope else "NULL::DOUBLE AS slope")
            )
            road_select = "AVG(road_prox) AS road_prox" if has_road_prox else "NULL::DOUBLE AS road_prox"
            river_select = "AVG(river_far) AS river_far" if has_river_far else "NULL::DOUBLE AS river_far"
            pole_select = "AVG(pole_count) AS pole_count" if has_pole_count else "NULL::DOUBLE AS pole_count"

            con.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE grid_static_base AS
                SELECT
                    grid_id,
                    AVG(fmi_n) AS fmi_n,
                    AVG(tmi_p) AS tmi_p,
                    AVG(pei) AS pei,
                    {slope_select},
                    {road_select},
                    {river_select},
                    {pole_select}
                FROM final_view
                GROUP BY grid_id
                """
            )

        with StepTimer("[4/8] Build grid-level percentile components"):
            if has_slope_n:
                slope_component = "COALESCE(LEAST(GREATEST(slope_n, 0.0), 1.0), 0.0)"
                slope_value_select = "slope_n"
                slope_pct_expr = "COALESCE(LEAST(GREATEST(slope_n, 0.0), 1.0), 0.0) AS slope_pct"
                slope_note = "`slope_n` 원본 컬럼을 0~1 범위로 clip하여 slope_pct로 사용"
            elif has_slope:
                slope_component = "COALESCE(slope_pct, 0.0)"
                slope_value_select = "slope"
                slope_pct_expr = "CASE WHEN slope IS NULL THEN NULL ELSE PERCENT_RANK() OVER (ORDER BY slope) END AS slope_pct"
                slope_note = "`slope` 원본 컬럼을 전체 grid 기준 percentile rank로 변환하여 slope_pct 파생"
            else:
                slope_component = "0.0"
                slope_value_select = "NULL::DOUBLE AS slope"
                slope_pct_expr = "0.0 AS slope_pct"
                slope_note = "slope 관련 컬럼이 없어 slope 항을 0으로 처리"

            con.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE grid_component AS
                WITH slope_pct_table AS (
                    SELECT
                        grid_id, fmi_n, tmi_p, pei, {slope_value_select}, road_prox, river_far, pole_count,
                        {slope_pct_expr}
                    FROM grid_static_base
                ),
                static_raw_table AS (
                    SELECT
                        grid_id, fmi_n, tmi_p, pei, {slope_value_select}, slope_pct,
                        road_prox, river_far, pole_count,
                        (
                            {args.static_fmi_weight} * COALESCE(LEAST(GREATEST(fmi_n, 0.0), 1.0), 0.0)
                          + {args.static_tmi_weight} * COALESCE(LEAST(GREATEST(tmi_p, 0.0), 1.0), 0.0)
                          + {args.static_slope_weight} * {slope_component}
                          + {args.static_road_weight} * COALESCE(LEAST(GREATEST(road_prox, 0.0), 1.0), 0.0)
                          + {args.static_river_weight} * COALESCE(LEAST(GREATEST(river_far, 0.0), 1.0), 0.0)
                        ) AS static_raw
                    FROM slope_pct_table
                )
                SELECT
                    grid_id, fmi_n, tmi_p, pei, {slope_value_select}, slope_pct,
                    road_prox, river_far, pole_count, static_raw,
                    CASE WHEN static_raw IS NULL THEN NULL ELSE PERCENT_RANK() OVER (ORDER BY static_raw) END AS static_vulnerability,
                    CASE WHEN pei IS NULL THEN NULL ELSE PERCENT_RANK() OVER (ORDER BY pei) END AS exposure_risk
                FROM static_raw_table
                """
            )

        optional_selects = []
        for name in OPTIONAL_COLUMNS:
            if name in colset and name not in {"dwi"}:
                optional_selects.append(f"b.{quote_ident(name)} AS {quote_ident(name)}")
        optional_sql = ""
        if optional_selects:
            optional_sql = ",\n                    " + ",\n                    ".join(optional_selects)

        optional_output_cols = ""
        for name in OPTIONAL_COLUMNS:
            if name in colset and name not in {"dwi"}:
                optional_output_cols += f",\n                {quote_ident(name)}"

        with StepTimer("[5/8] Write DWI-based grid-date risk parquet"):
            output_sql_path = as_sql_path(output_dir)
            con.execute(
                f"""
                COPY (
                    WITH base AS (
                        SELECT
                            grid_id,
                            CAST(date AS DATE) AS date,
                            EXTRACT(year FROM CAST(date AS DATE))::INTEGER AS year,
                            EXTRACT(month FROM CAST(date AS DATE))::INTEGER AS month,
                            dwi
                            {optional_sql}
                        FROM final_view b
                        WHERE dwi IS NOT NULL
                    ),
                    weather_pct AS (
                        SELECT
                            *,
                            PERCENT_RANK() OVER (PARTITION BY date ORDER BY dwi) AS dwi_pct
                        FROM base
                    ),
                    scored AS (
                        SELECT
                            w.*,
                            g.static_raw,
                            g.static_vulnerability,
                            g.exposure_risk,
                            (
                                {args.weather_weight} * COALESCE(w.dwi_pct, 0.0)
                              + {args.static_weight} * COALESCE(g.static_vulnerability, 0.0)
                              + {args.exposure_weight} * COALESCE(g.exposure_risk, 0.0)
                            ) AS final_grid_risk
                        FROM weather_pct w
                        LEFT JOIN grid_component g USING (grid_id)
                    ),
                    ranked AS (
                        SELECT
                            *,
                            ROW_NUMBER() OVER (PARTITION BY date ORDER BY final_grid_risk DESC) AS daily_risk_rank,
                            PERCENT_RANK() OVER (PARTITION BY date ORDER BY final_grid_risk) AS daily_risk_pct,
                            CUME_DIST() OVER (PARTITION BY date ORDER BY final_grid_risk DESC) AS daily_top_cume
                        FROM scored
                    )
                    SELECT
                        grid_id,
                        date,
                        month,
                        dwi,
                        dwi_pct,
                        static_raw,
                        static_vulnerability,
                        exposure_risk,
                        final_grid_risk,
                        daily_risk_rank,
                        daily_risk_pct,
                        CASE WHEN daily_top_cume <= {args.high_risk_pct} THEN 1 ELSE 0 END AS daily_high_risk_flag,
                        year
                        {optional_output_cols}
                    FROM ranked
                )
                TO '{output_sql_path}'
                (FORMAT PARQUET, PARTITION_BY (year, month), COMPRESSION ZSTD)
                """
            )

        with StepTimer("[6/8] Write formula document"):
            formula_md = f"""# Grid-Date Risk Score 산식 - DWI 기반 분리형 v4

## 목적

본 산식은 산불 발생 여부를 직접 맞히는 지도학습 모델이 아니라, 봄철 산불위험기(2~5월)에 전력설비 주변 격자 중 산불 위험 환경에 상대적으로 많이 노출된 후보지를 우선순위화하기 위한 위험도 스코어링 모델이다.

## v4 변경 사항

기존 산식은 `pffdri_pct + static_vulnerability + exposure_risk` 구조였으나, P-FFDRI 내부에 산림·지형·전력설비 노출 성격이 일부 포함될 수 있어 정적 취약도와 노출도를 다시 더할 경우 중복 반영 지적을 받을 수 있었다.

따라서 v4에서는 최종 위험도 계산에서 `pffdri`를 제외하고, 기상위험을 나타내는 `dwi`를 기준 위험도로 사용한다.

```text
기존: final_grid_risk = 0.60 * pffdri_pct + 0.25 * static_vulnerability + 0.15 * exposure_risk
수정: final_grid_risk = 0.60 * dwi_pct    + 0.25 * static_vulnerability + 0.15 * exposure_risk
```

`pffdri`는 최종 위험도 산식에는 사용하지 않으며, 존재할 경우 비교·참고용 컬럼으로만 남긴다.

## 산식

### 1. 기상 위험도

```text
dwi_pct = 같은 날짜 안에서 dwi의 percentile rank
```

- 날짜별 전체 기상위험 수준이 다르므로, 원점수보다 같은 날짜 안의 상대적 위치를 사용한다.
- `dwi`가 NULL인 행은 위험도 산정에서 제외한다.

### 2. 정적 취약도

```text
static_raw
= {args.static_fmi_weight:.3f} * fmi_n
+ {args.static_tmi_weight:.3f} * tmi_p
+ {args.static_slope_weight:.3f} * slope_pct
+ {args.static_road_weight:.3f} * road_prox
+ {args.static_river_weight:.3f} * river_far

static_vulnerability = static_raw의 전체 grid 기준 percentile rank
```

- 산림 연료 위험, 지형 위험, 경사, 도로 접근성, 수계 이격도를 위치별 취약성으로 결합한다.
- 정적 변수는 날짜별로 반복되므로 grid_id 단위로 집계한 뒤 percentile을 계산한다.
- slope 처리: {slope_note}

### 3. 전력설비 노출도

```text
exposure_risk = pei의 전체 grid 기준 percentile rank
```

- PEI는 전력설비 주변 노출도 지표로 사용한다.

### 4. 최종 격자-일자 위험도

```text
final_grid_risk
= {args.weather_weight:.2f} * dwi_pct
+ {args.static_weight:.2f} * static_vulnerability
+ {args.exposure_weight:.2f} * exposure_risk
```

### 5. 일자별 고위험 플래그

```text
daily_high_risk_flag = final_grid_risk가 해당 날짜 상위 {args.high_risk_pct * 100:.1f}% 이내이면 1
```

## 산식 해석

- 기상 위험도 {args.weather_weight:.2f}: 일별 산불위험 변동을 설명하는 핵심 축이다.
- 정적 취약도 {args.static_weight:.2f}: 같은 기상 조건에서도 산림, 지형, 경사, 접근성에 따라 취약도가 달라지는 점을 반영한다.
- 전력설비 노출도 {args.exposure_weight:.2f}: 본 과제의 목적이 전력설비 주변 관리 우선순위 산정이므로 설비 노출도를 별도 항으로 반영한다.

## 산식 설계상 주의

- 이 점수는 산불 발생 확률이 아니라 상대적 위험노출도이다.
- 산불 발생 이력은 학습 타깃이 아니라 사후 검증 자료로만 사용한다.
- 본 v4 산식은 P-FFDRI와 static/exposure의 중복 반영 가능성을 줄이기 위한 분리형 baseline이다.
- 최종 채택 여부는 12~14번 재실행 후 전신주 decision 분포와 산불 이력 검증 결과를 비교하여 판단한다.
"""
            (report_dir / "risk_score_formula.md").write_text(formula_md, encoding="utf-8")

        if not args.skip_summary:
            with StepTimer("[7/8] Write exact output summary"):
                risk_glob = as_sql_path(output_dir / "**/*.parquet")
                summary = con.execute(
                    f"""
                    SELECT
                        COUNT(*) AS row_count,
                        COUNT(DISTINCT grid_id) AS exact_grid_count,
                        COUNT(DISTINCT date) AS exact_date_count,
                        MIN(date) AS min_date,
                        MAX(date) AS max_date,
                        MIN(final_grid_risk) AS min_final_grid_risk,
                        AVG(final_grid_risk) AS avg_final_grid_risk,
                        MAX(final_grid_risk) AS max_final_grid_risk,
                        SUM(daily_high_risk_flag) AS high_risk_row_count,
                        AVG(daily_high_risk_flag) AS high_risk_row_rate,
                        MIN(dwi) AS min_dwi,
                        AVG(dwi) AS avg_dwi,
                        MAX(dwi) AS max_dwi
                    FROM read_parquet('{risk_glob}', hive_partitioning=true, union_by_name=true)
                    """
                ).fetchdf()

                summary.to_csv(report_dir / "grid_date_risk_summary.csv", index=False, encoding="utf-8-sig")
                row = summary.iloc[0].to_dict()

                md_lines = [
                    "# Grid-Date Risk Score 생성 결과 - DWI 기반 분리형 v4",
                    "",
                    "## 입력",
                    f"- final feature: `{args.final_parquet}`",
                    f"- date filter: `{args.start_date or 'None'}` ~ `{args.end_date or 'None'}`",
                    f"- fire season months: `{'ALL' if args.no_month_filter else args.fire_season_months}`",
                    f"- fire-history validation years: `2020~{args.validation_end_year}`",
                    "",
                    "## 산식",
                    "```text",
                    f"final_grid_risk = {args.weather_weight:.2f} * dwi_pct + {args.static_weight:.2f} * static_vulnerability + {args.exposure_weight:.2f} * exposure_risk",
                    "```",
                    "",
                    "## 출력",
                    f"- grid-date risk parquet: `{args.output_dir}`",
                    f"- formula document: `{args.report_dir}/risk_score_formula.md`",
                    f"- summary csv: `{args.report_dir}/grid_date_risk_summary.csv`",
                    "",
                    "## 요약",
                ]
                for k, v in row.items():
                    md_lines.append(f"- {k}: `{v}`")

                md_lines += [
                    "",
                    "## 해석",
                    "- 본 결과는 P-FFDRI를 최종 산식에서 제외한 DWI 기반 분리형 위험도 산정 결과이다.",
                    "- `exact_grid_count × exact_date_count`가 `row_count`와 일치하는지 확인한다.",
                    "- `high_risk_row_rate`가 설정한 상위 위험 비율과 일치하는지 확인한다.",
                    "",
                    "## 다음 단계",
                    "1. `12_build_pole_risk_score.py --overwrite`를 실행하여 전신주별 위험도와 decision을 새로 산출한다.",
                    "2. `13_summarize_risk_by_region.py --overwrite`를 실행하여 지역별 위험도 요약을 새로 산출한다.",
                    "3. `14_validate_fire_history_v2.py --overwrite --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5`를 실행하여 산불 이력 검증을 새로 수행한다.",
                    "4. 기존 P-FFDRI 기반 결과와 DWI 기반 결과의 검증 지표를 비교한다.",
                ]
                (report_dir / "grid_date_risk_summary.md").write_text("\n".join(md_lines), encoding="utf-8")

        with StepTimer("[8/8] Final log"):
            log("DONE")
            log(f"OUTPUT: {output_dir}")
            log(f"FORMULA: {report_dir / 'risk_score_formula.md'}")
            if not args.skip_summary:
                log(f"SUMMARY: {report_dir / 'grid_date_risk_summary.md'}")

    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

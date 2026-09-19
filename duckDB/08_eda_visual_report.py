from __future__ import annotations

import argparse
import html
from pathlib import Path

import duckdb
import pandas as pd

from pffdri_common import project_path, sql_path


DEFAULT_EDA_DIR = Path("output") / "eda"
DEFAULT_REPORT_DIR = Path("output") / "report" / "eda"
GRID_LABELS = ("500m", "1km", "2km")
TARGET_COLUMNS = {
    "fire_label",
    "fire_count",
    "fire_objt_ids",
    "fire_addresses",
    "fire_amount_sum",
    "target_grid_id",
    "target_grid_size_m",
    "target_grid_x",
    "target_grid_y",
    "event_lon_mean",
    "event_lat_mean",
    "event_x5179_mean",
    "event_y5179_mean",
}
ID_COLUMNS = {"grid_id", "date", "month"}
PRIMARY_METRIC_COLUMNS = ("pffdri", "ffdri", "dwi", "dwi_n", "fmi", "fmi_n", "tmi_p", "ywi", "pei")
NUMERIC_TYPES = ("INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "TINYINT")


def parse_grid_labels(value: str) -> list[str]:
    labels = []
    for item in value.split(","):
        label = item.strip().lower().replace("1000m", "1km").replace("2000m", "2km")
        if not label:
            continue
        if label not in GRID_LABELS:
            raise ValueError(f"unsupported grid label: {item}")
        labels.append(label)
    if not labels:
        raise ValueError("at least one grid label is required")
    return labels


def ensure_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_eda_sample(con: duckdb.DuckDBPyConnection, path: Path, sample_rows: int | None) -> pd.DataFrame:
    limit_sql = "" if sample_rows is None or sample_rows <= 0 else f"USING SAMPLE {sample_rows} ROWS"
    return con.execute(
        f"""
        SELECT *
        FROM read_parquet('{sql_path(path)}', union_by_name=true)
        {limit_sql}
        """
    ).df()


def feature_columns(con: duckdb.DuckDBPyConnection, path: Path) -> tuple[list[str], list[str]]:
    describe = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{sql_path(path)}', union_by_name=true)"
    ).df()
    numeric_features = []
    categorical_features = []
    for row in describe.itertuples(index=False):
        column = row.column_name
        column_type = row.column_type.upper()
        if column in TARGET_COLUMNS or column in {"date"}:
            continue
        if column_type.startswith(NUMERIC_TYPES):
            numeric_features.append(column)
        elif column in {"grid_id", "month"}:
            categorical_features.append(column)
    return numeric_features, categorical_features


def quantile_summary(con: duckdb.DuckDBPyConnection, path: Path, score_columns: list[str]) -> pd.DataFrame:
    parts = []
    for column in score_columns:
        parts.append(
            f"""
            SELECT
                '{column}' AS variable,
                fire_label,
                COUNT(*) AS row_count,
                AVG({column}) AS mean,
                STDDEV({column}) AS std,
                MIN({column}) AS min,
                QUANTILE_CONT({column}, 0.25) AS q25,
                QUANTILE_CONT({column}, 0.50) AS median,
                QUANTILE_CONT({column}, 0.75) AS q75,
                MAX({column}) AS max
            FROM read_parquet('{sql_path(path)}', union_by_name=true)
            GROUP BY fire_label
            """
        )
    return con.execute("\nUNION ALL\n".join(parts)).df()


def auc_like_summary(con: duckdb.DuckDBPyConnection, path: Path, score_columns: list[str]) -> pd.DataFrame:
    rows = []
    for column in score_columns:
        result = con.execute(
            f"""
            WITH scored AS (
                SELECT
                    fire_label,
                    {column} AS score,
                    CUME_DIST() OVER (ORDER BY {column} DESC) AS risk_pct
                FROM read_parquet('{sql_path(path)}', union_by_name=true)
                WHERE {column} IS NOT NULL
            )
            SELECT
                '{column}' AS variable,
                COUNT(*) AS row_count,
                SUM(CASE WHEN fire_label = 1 THEN 1 ELSE 0 END) AS positive_rows,
                AVG(CASE WHEN fire_label = 1 THEN score END) AS positive_mean,
                AVG(CASE WHEN fire_label = 0 THEN score END) AS negative_mean,
                SUM(CASE WHEN fire_label = 1 AND risk_pct <= 0.10 THEN 1.0 ELSE 0.0 END)
                    / NULLIF(SUM(CASE WHEN fire_label = 1 THEN 1.0 ELSE 0.0 END), 0.0) AS positive_top10_share,
                SUM(CASE WHEN fire_label = 1 AND risk_pct <= 0.20 THEN 1.0 ELSE 0.0 END)
                    / NULLIF(SUM(CASE WHEN fire_label = 1 THEN 1.0 ELSE 0.0 END), 0.0) AS positive_top20_share
            FROM scored
            """
        ).fetchone()
        rows.append(result)
    return pd.DataFrame(
        rows,
        columns=[
            "variable",
            "row_count",
            "positive_rows",
            "positive_mean",
            "negative_mean",
            "positive_top10_share",
            "positive_top20_share",
        ],
    )


def pearson_with_label(con: duckdb.DuckDBPyConnection, path: Path, numeric_columns: list[str]) -> pd.DataFrame:
    rows = []
    for column in numeric_columns:
        value = con.execute(
            f"""
            SELECT
                '{column}' AS variable,
                CORR({column}, fire_label) AS pearson_corr_fire_label
            FROM read_parquet('{sql_path(path)}', union_by_name=true)
            WHERE {column} IS NOT NULL AND fire_label IS NOT NULL
            """
        ).fetchone()
        rows.append(value)
    return pd.DataFrame(rows, columns=["variable", "pearson_corr_fire_label"]).sort_values(
        "pearson_corr_fire_label", key=lambda series: series.abs(), ascending=False
    )


def pearson_matrix(con: duckdb.DuckDBPyConnection, path: Path, numeric_columns: list[str], max_columns: int) -> pd.DataFrame:
    selected = numeric_columns[:max_columns]
    rows = []
    for left in selected:
        row = {"variable": left}
        for right in selected:
            corr = con.execute(
                f"""
                SELECT CORR({left}, {right})
                FROM read_parquet('{sql_path(path)}', union_by_name=true)
                WHERE {left} IS NOT NULL AND {right} IS NOT NULL
                """
            ).fetchone()[0]
            row[right] = corr
        rows.append(row)
    return pd.DataFrame(rows)


def categorical_summary(con: duckdb.DuckDBPyConnection, path: Path, column: str, limit: int) -> pd.DataFrame:
    return con.execute(
        f"""
        SELECT
            {column},
            COUNT(*) AS row_count,
            SUM(CASE WHEN fire_label = 1 THEN 1 ELSE 0 END) AS positive_rows,
            AVG(CAST(fire_label AS DOUBLE)) AS positive_rate,
            AVG(pffdri) AS avg_pffdri,
            AVG(ffdri) AS avg_ffdri
        FROM read_parquet('{sql_path(path)}', union_by_name=true)
        GROUP BY {column}
        ORDER BY positive_rows DESC, positive_rate DESC, row_count DESC
        LIMIT {limit}
        """
    ).df()


def bar_chart_svg(title: str, labels: list[str], values: list[float], width: int = 860, height: int = 420) -> str:
    margin_left = 90
    margin_bottom = 80
    margin_top = 55
    chart_width = width - margin_left - 30
    chart_height = height - margin_top - margin_bottom
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0)
    bar_gap = 18
    bar_width = max(12, int((chart_width - bar_gap * (len(values) + 1)) / max(len(values), 1)))
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="30" text-anchor="middle" font-family="Arial" font-size="20" font-weight="700">{html.escape(title)}</text>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + chart_height}" stroke="#222"/>',
        f'<line x1="{margin_left}" y1="{margin_top + chart_height}" x2="{margin_left + chart_width}" y2="{margin_top + chart_height}" stroke="#222"/>',
    ]
    for index, (label, value) in enumerate(zip(labels, values)):
        x = margin_left + bar_gap + index * (bar_width + bar_gap)
        bar_height = 0 if max_value == 0 else (value / max_value) * chart_height
        y = margin_top + chart_height - bar_height
        elements.append(f'<rect x="{x}" y="{y:.2f}" width="{bar_width}" height="{bar_height:.2f}" fill="#4C78A8"/>')
        elements.append(f'<text x="{x + bar_width / 2}" y="{margin_top + chart_height + 22}" text-anchor="middle" font-family="Arial" font-size="12">{html.escape(label)}</text>')
        elements.append(f'<text x="{x + bar_width / 2}" y="{max(48, y - 8):.2f}" text-anchor="middle" font-family="Arial" font-size="12">{value:,.2f}</text>')
    elements.append("</svg>")
    return "\n".join(elements)


def heatmap_svg(title: str, matrix: pd.DataFrame, width: int = 980) -> str:
    if matrix.empty:
        return bar_chart_svg(title, [], [])
    labels = matrix["variable"].astype(str).tolist()
    value_columns = [column for column in matrix.columns if column != "variable"]
    cell = max(26, min(54, int((width - 220) / max(len(value_columns), 1))))
    height = 120 + cell * len(labels)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="30" text-anchor="middle" font-family="Arial" font-size="20" font-weight="700">{html.escape(title)}</text>',
    ]
    x0 = 150
    y0 = 75
    for col_index, column in enumerate(value_columns):
        x = x0 + col_index * cell
        elements.append(f'<text x="{x + cell / 2}" y="65" text-anchor="middle" font-family="Arial" font-size="10" transform="rotate(-45 {x + cell / 2},65)">{html.escape(column)}</text>')
    for row_index, row in enumerate(matrix.itertuples(index=False)):
        y = y0 + row_index * cell
        variable = getattr(row, "variable")
        elements.append(f'<text x="{x0 - 8}" y="{y + cell * 0.65}" text-anchor="end" font-family="Arial" font-size="11">{html.escape(str(variable))}</text>')
        for col_index, column in enumerate(value_columns):
            value = matrix.iloc[row_index][column]
            value = 0.0 if pd.isna(value) else max(-1.0, min(1.0, float(value)))
            if value >= 0:
                intensity = int(255 - abs(value) * 165)
                color = f"rgb({intensity},{intensity},255)"
            else:
                intensity = int(255 - abs(value) * 165)
                color = f"rgb(255,{intensity},{intensity})"
            x = x0 + col_index * cell
            elements.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{color}" stroke="#eee"/>')
            if abs(value) >= 0.5:
                elements.append(f'<text x="{x + cell / 2}" y="{y + cell * 0.62}" text-anchor="middle" font-family="Arial" font-size="9">{value:.2f}</text>')
    elements.append("</svg>")
    return "\n".join(elements)


def grouped_bar_chart_svg(title: str, df: pd.DataFrame, category_col: str, value_col: str, group_col: str) -> str:
    labels = [str(value) for value in df[category_col].drop_duplicates().tolist()]
    groups = [str(value) for value in sorted(df[group_col].drop_duplicates().tolist(), reverse=True)]
    values = []
    flat_labels = []
    for label in labels:
        for group in groups:
            matched = df[(df[category_col].astype(str) == label) & (df[group_col].astype(str) == group)]
            value = float(matched[value_col].iloc[0]) if not matched.empty else 0.0
            values.append(value)
            flat_labels.append(f"{label}\\n{group}")
    return bar_chart_svg(title, flat_labels, values, width=max(860, 130 * len(flat_labels)))


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows_"
    columns = [str(column) for column in df.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in df.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.6g}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_markdown_report(
    report_path: Path,
    label: str,
    label_counts: pd.DataFrame,
    score_summary: pd.DataFrame,
    lift_summary: pd.DataFrame,
    pearson_label: pd.DataFrame,
    month_summary: pd.DataFrame,
    grid_summary: pd.DataFrame,
) -> None:
    top_variables = lift_summary.sort_values("positive_top10_share", ascending=False).head(5)
    top_corr = pearson_label.head(10)
    lines = [
        f"# EDA Visual Report - {label}",
        "",
        "## Label Counts",
        "",
        markdown_table(label_counts),
        "",
        "## Top Variables by Positive Top 10% Capture",
        "",
        markdown_table(top_variables),
        "",
        "## Pearson Correlation with Fire Label",
        "",
        markdown_table(top_corr),
        "",
        "## Monthly/Time Summary",
        "",
        markdown_table(month_summary),
        "",
        "## Top Grid Summary",
        "",
        markdown_table(grid_summary),
        "",
        "## Score Summary",
        "",
        markdown_table(score_summary),
        "",
        "## Generated Charts",
        "",
        f"- `label_counts_{label}.svg`",
        f"- `positive_mean_scores_{label}.svg`",
        f"- `top10_capture_{label}.svg`",
        f"- `pearson_fire_label_{label}.svg`",
        f"- `feature_corr_heatmap_{label}.svg`",
        f"- `monthly_counts_{label}.svg`",
    ]
    write_text(report_path, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create EDA tables and dependency-free SVG charts from EDA parquet samples.")
    parser.add_argument("--eda-dir", default=str(DEFAULT_EDA_DIR))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--grid-sizes", default=",".join(GRID_LABELS))
    parser.add_argument("--sample-rows", type=int, default=0, help="Optional row sample for local quick checks. 0 means all rows.")
    parser.add_argument("--corr-max-columns", type=int, default=24, help="Maximum numeric feature columns for pairwise correlation heatmap.")
    parser.add_argument("--top-category-limit", type=int, default=30)
    args = parser.parse_args()

    eda_dir = project_path(__file__, args.eda_dir)
    report_dir = project_path(__file__, args.report_dir)
    grid_labels = parse_grid_labels(args.grid_sizes)
    con = duckdb.connect()

    report_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] eda_dir={eda_dir}")
    print(f"[INFO] report_dir={report_dir}")

    for index, label in enumerate(grid_labels, 1):
        print(f"[PROGRESS] EDA report {index}/{len(grid_labels)} {label}")
        sample_path = eda_dir / f"eda_sample_{label}.parquet"
        monthly_path = eda_dir / f"eda_monthly_counts_{label}.csv"
        ensure_file(sample_path)
        ensure_file(monthly_path)

        numeric_features, categorical_features = feature_columns(con, sample_path)
        metric_columns = [column for column in PRIMARY_METRIC_COLUMNS if column in numeric_features]
        if not numeric_features:
            raise ValueError(f"No score columns found in {sample_path}")

        label_counts = con.execute(
            f"""
            SELECT fire_label, COUNT(*) AS row_count, COUNT(DISTINCT date) AS date_count, COUNT(DISTINCT grid_id) AS grid_count
            FROM read_parquet('{sql_path(sample_path)}', union_by_name=true)
            GROUP BY fire_label
            ORDER BY fire_label DESC
            """
        ).df()
        score_summary = quantile_summary(con, sample_path, numeric_features)
        lift_summary = auc_like_summary(con, sample_path, numeric_features)
        pearson_label = pearson_with_label(con, sample_path, numeric_features)
        corr_ranked_features = pearson_label["variable"].head(args.corr_max_columns).tolist()
        corr_matrix = pearson_matrix(con, sample_path, corr_ranked_features, args.corr_max_columns)
        month_summary = categorical_summary(con, sample_path, "month", args.top_category_limit) if "month" in categorical_features else pd.DataFrame()
        grid_summary = categorical_summary(con, sample_path, "grid_id", args.top_category_limit) if "grid_id" in categorical_features else pd.DataFrame()
        monthly_counts = pd.read_csv(monthly_path)

        label_counts.to_csv(report_dir / f"label_counts_{label}.csv", index=False, encoding="utf-8-sig")
        score_summary.to_csv(report_dir / f"feature_summary_{label}.csv", index=False, encoding="utf-8-sig")
        lift_summary.to_csv(report_dir / f"lift_summary_{label}.csv", index=False, encoding="utf-8-sig")
        pearson_label.to_csv(report_dir / f"pearson_fire_label_{label}.csv", index=False, encoding="utf-8-sig")
        corr_matrix.to_csv(report_dir / f"feature_corr_matrix_{label}.csv", index=False, encoding="utf-8-sig")
        month_summary.to_csv(report_dir / f"month_summary_{label}.csv", index=False, encoding="utf-8-sig")
        grid_summary.to_csv(report_dir / f"grid_summary_{label}.csv", index=False, encoding="utf-8-sig")

        write_text(
            report_dir / f"label_counts_{label}.svg",
            bar_chart_svg(
                f"{label} Label Counts",
                [f"label={int(row.fire_label)}" for row in label_counts.itertuples()],
                [float(row.row_count) for row in label_counts.itertuples()],
            ),
        )
        positive_means = score_summary[
            (score_summary["fire_label"] == 1) & (score_summary["variable"].isin(metric_columns))
        ].sort_values("mean", ascending=False)
        write_text(
            report_dir / f"positive_mean_scores_{label}.svg",
            bar_chart_svg(
                f"{label} Positive Mean Scores",
                positive_means["variable"].astype(str).tolist(),
                positive_means["mean"].fillna(0).astype(float).tolist(),
            ),
        )
        top10 = lift_summary.sort_values("positive_top10_share", ascending=False)
        write_text(
            report_dir / f"top10_capture_{label}.svg",
            bar_chart_svg(
                f"{label} Positive Capture in Top 10% Risk",
                top10["variable"].astype(str).tolist(),
                (top10["positive_top10_share"].fillna(0) * 100).astype(float).tolist(),
            ),
        )
        corr_chart = pearson_label.head(20).copy()
        write_text(
            report_dir / f"pearson_fire_label_{label}.svg",
            bar_chart_svg(
                f"{label} Pearson Corr with Fire Label",
                corr_chart["variable"].astype(str).tolist(),
                corr_chart["pearson_corr_fire_label"].fillna(0).astype(float).tolist(),
                width=max(860, 95 * len(corr_chart)),
            ),
        )
        write_text(
            report_dir / f"feature_corr_heatmap_{label}.svg",
            heatmap_svg(f"{label} Feature Correlation Heatmap", corr_matrix),
        )
        write_text(
            report_dir / f"monthly_counts_{label}.svg",
            grouped_bar_chart_svg(f"{label} Monthly Label Counts", monthly_counts, "month", "row_count", "fire_label"),
        )
        write_markdown_report(
            report_dir / f"eda_report_{label}.md",
            label,
            label_counts,
            score_summary,
            lift_summary,
            pearson_label,
            month_summary,
            grid_summary,
        )

    con.close()
    print("[DONE] EDA visual report")


if __name__ == "__main__":
    main()

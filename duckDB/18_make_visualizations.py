from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_FIG_DIR = "output/figures"
DEFAULT_REGION_CSV = "output/report/risk/region_risk_summary_city_name.csv"
DEFAULT_POLE_SCORE_CSV = "output/risk/pole_risk_score.csv"
DEFAULT_POLE_GRID_MAP = "output/risk/pole_grid_map.parquet"
DEFAULT_RADIUS_COMPARISON = "output/report/validation/radius_validation_comparison.csv"
DEFAULT_WEIGHT_RADIUS_COMPARISON = "output/report/validation/weight_radius_comparison.csv"


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


def read_csv_auto(path: Path) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    return pd.read_csv(path)


def find_col(df: pd.DataFrame, candidates: list[str]) -> str:
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    raise KeyError(f"columns not found. candidates={candidates}, available={list(df.columns)}")


def setup_plot() -> None:
    """
    Pick one actually installed Korean-capable font.
    This avoids repeated "findfont: Font family ... not found" warnings.
    """
    import logging
    import matplotlib.font_manager as fm

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

    candidates = [
        "Malgun Gothic",      # Windows Korean
        "NanumGothic",        # common Linux Korean
        "Noto Sans CJK KR",
        "Noto Sans KR",
        "AppleGothic",        # macOS Korean
        "DejaVu Sans",        # final fallback
    ]
    available = {font.name for font in fm.fontManager.ttflist}
    chosen = next((font for font in candidates if font in available), "DejaVu Sans")

    plt.rcParams["font.family"] = chosen
    plt.rcParams["axes.unicode_minus"] = False
    print(f"[FONT] matplotlib font = {chosen}")


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=220, bbox_inches="tight")
    plt.close()
    print(f"[SAVE] {path}")


def plot_region(region_csv: Path, fig_dir: Path, top_n: int) -> None:
    if not region_csv.exists():
        print(f"[SKIP] region csv not found: {region_csv}")
        return

    df = read_csv_auto(region_csv)
    region_col = find_col(df, ["city_name", "region", "grid_region", "시군구"])
    count_col = find_col(df, ["decision_1_count", "high_risk_pole_count"])
    rate_col = find_col(df, ["decision_1_rate", "high_risk_rate"])
    pole_col = find_col(df, ["pole_count", "total_pole_count"])

    df[count_col] = pd.to_numeric(df[count_col], errors="coerce")
    df[rate_col] = pd.to_numeric(df[rate_col], errors="coerce")
    df[pole_col] = pd.to_numeric(df[pole_col], errors="coerce")

    top = df.dropna(subset=[count_col]).sort_values(count_col, ascending=False).head(top_n)
    top = top.sort_values(count_col)
    plt.figure(figsize=(9, 6))
    plt.barh(top[region_col], top[count_col])
    plt.title(f"시군구별 고위험 전신주 수 TOP {top_n}")
    plt.xlabel("고위험 전신주 수(decision=1)")
    plt.ylabel("시군구")
    plt.grid(True, axis="x", alpha=0.3)
    savefig(fig_dir / "fig_region_decision_count_top15.png")

    rate_df = df[df[pole_col] >= 100].dropna(subset=[rate_col]).copy()
    rate_df = rate_df.sort_values(rate_col, ascending=False).head(top_n).sort_values(rate_col)
    display_rate = rate_df[rate_col]
    if display_rate.max() <= 1.0:
        display_rate = display_rate * 100
    plt.figure(figsize=(9, 6))
    plt.barh(rate_df[region_col], display_rate)
    plt.title(f"시군구별 고위험 전신주 비율 TOP {top_n}")
    plt.xlabel("고위험 전신주 비율(%)")
    plt.ylabel("시군구")
    plt.grid(True, axis="x", alpha=0.3)
    savefig(fig_dir / "fig_region_decision_rate_top15.png")


def plot_pole_maps(pole_score_csv: Path, pole_grid_map: Path, fig_dir: Path, sample_n: int) -> None:
    if not pole_score_csv.exists():
        print(f"[SKIP] pole score csv not found: {pole_score_csv}")
        return

    con = duckdb.connect()
    try:
        # Prefer EPSG:5179 coordinates from pole_grid_map if available.
        if pole_grid_map.exists():
            cols = [r[0] for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{sql_path(pole_grid_map)}') LIMIT 0"
            ).fetchall()]
            if {"pole_id", "x5179", "y5179"}.issubset(set(cols)):
                base_q = f"""
                SELECT
                    p.pole_id,
                    CAST(p.pole_risk_score AS DOUBLE) AS pole_risk_score,
                    CAST(p.decision AS INTEGER) AS decision,
                    m.x5179 AS x_plot,
                    m.y5179 AS y_plot
                FROM read_csv_auto('{sql_path(pole_score_csv)}', HEADER=true) p
                JOIN read_parquet('{sql_path(pole_grid_map)}') m
                  ON p.pole_id = m.pole_id
                WHERE p.pole_risk_score IS NOT NULL
                  AND m.x5179 IS NOT NULL
                  AND m.y5179 IS NOT NULL
                """
            elif {"pole_id", "grid_x", "grid_y"}.issubset(set(cols)):
                base_q = f"""
                SELECT
                    p.pole_id,
                    CAST(p.pole_risk_score AS DOUBLE) AS pole_risk_score,
                    CAST(p.decision AS INTEGER) AS decision,
                    m.grid_x AS x_plot,
                    m.grid_y AS y_plot
                FROM read_csv_auto('{sql_path(pole_score_csv)}', HEADER=true) p
                JOIN read_parquet('{sql_path(pole_grid_map)}') m
                  ON p.pole_id = m.pole_id
                WHERE p.pole_risk_score IS NOT NULL
                  AND m.grid_x IS NOT NULL
                  AND m.grid_y IS NOT NULL
                """
            else:
                base_q = f"""
                SELECT
                    pole_id,
                    CAST(pole_risk_score AS DOUBLE) AS pole_risk_score,
                    CAST(decision AS INTEGER) AS decision,
                    lon AS x_plot,
                    lat AS y_plot
                FROM read_csv_auto('{sql_path(pole_score_csv)}', HEADER=true)
                WHERE pole_risk_score IS NOT NULL AND lon IS NOT NULL AND lat IS NOT NULL
                """
        else:
            base_q = f"""
            SELECT
                pole_id,
                CAST(pole_risk_score AS DOUBLE) AS pole_risk_score,
                CAST(decision AS INTEGER) AS decision,
                lon AS x_plot,
                lat AS y_plot
            FROM read_csv_auto('{sql_path(pole_score_csv)}', HEADER=true)
            WHERE pole_risk_score IS NOT NULL AND lon IS NOT NULL AND lat IS NOT NULL
            """

        total = con.execute(f"SELECT COUNT(*) FROM ({base_q})").fetchone()[0]
        sample_percent = min(100.0, max(0.1, sample_n / max(total, 1) * 100))
        sample_df = con.execute(
            f"""
            SELECT *
            FROM ({base_q})
            USING SAMPLE {sample_percent} PERCENT (bernoulli)
            """
        ).fetchdf()
        if len(sample_df) > sample_n:
            sample_df = sample_df.sample(sample_n, random_state=42)

        sample_df["pole_risk_score"] = pd.to_numeric(sample_df["pole_risk_score"], errors="coerce")
        vmin = float(sample_df["pole_risk_score"].quantile(0.02))
        vmax = float(sample_df["pole_risk_score"].quantile(0.98))
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
            vmin = float(sample_df["pole_risk_score"].min())
            vmax = float(sample_df["pole_risk_score"].max())

        plt.figure(figsize=(7, 8))
        sc = plt.scatter(
            sample_df["x_plot"],
            sample_df["y_plot"],
            c=sample_df["pole_risk_score"],
            s=1,
            alpha=0.62,
            cmap="coolwarm",
            vmin=vmin,
            vmax=vmax,
        )
        plt.colorbar(sc, label=f"pole_risk_score low→high, clipped p2-p98 ({vmin:.3f}~{vmax:.3f})")
        plt.title(f"전신주 위험도 공간 분포 샘플(sample n={len(sample_df):,})")
        plt.xlabel("X")
        plt.ylabel("Y")
        plt.axis("equal")
        savefig(fig_dir / "fig_pole_risk_map_sample.png")

        decision_counts = con.execute(
            f"""
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN CAST(decision AS INTEGER) = 1 THEN 1 ELSE 0 END) AS decision_1_count
            FROM ({base_q})
            """
        ).fetchdf().iloc[0]
        total_count = int(decision_counts["total_count"])
        decision_1_count = int(decision_counts["decision_1_count"])
        decision_1_rate = decision_1_count / total_count * 100 if total_count else 0.0

        # Sample from all poles so gray decision=0 background and red decision=1 points
        # are visible in the same figure.
        plot_df = sample_df.copy()
        plot_df["decision"] = pd.to_numeric(plot_df["decision"], errors="coerce").fillna(0).astype(int)
        d0 = plot_df[plot_df["decision"] != 1]
        d1 = plot_df[plot_df["decision"] == 1]

        plt.figure(figsize=(7, 8))
        if not d0.empty:
            plt.scatter(d0["x_plot"], d0["y_plot"], s=1, alpha=0.18, c="lightgray", label="decision=0")
        if not d1.empty:
            plt.scatter(d1["x_plot"], d1["y_plot"], s=2, alpha=0.85, c="red", label="decision=1")
        plt.title(
            f"전신주 decision 공간 분포(sample n={len(plot_df):,}, "
            f"decision=1 total {decision_1_count:,}/{total_count:,}, {decision_1_rate:.2f}%)"
        )
        plt.xlabel("X")
        plt.ylabel("Y")
        plt.legend(markerscale=5, loc="best")
        plt.axis("equal")
        savefig(fig_dir / "fig_decision1_pole_map.png")
    finally:
        con.close()


def plot_radius(radius_csv: Path, fig_dir: Path) -> None:
    if not radius_csv.exists():
        print(f"[SKIP] radius comparison csv not found: {radius_csv}")
        return
    df = read_csv_auto(radius_csv)
    df["radius_cells"] = pd.to_numeric(df["radius_cells"], errors="coerce")
    df = df.sort_values("radius_cells")

    x = df["radius_cells"]
    actual = pd.to_numeric(df["neighbor_top5_hit_rate_available"], errors="coerce") * 100
    random = pd.to_numeric(df["random_neighbor_top5_hit_rate_mean"], errors="coerce") * 100
    diff = pd.to_numeric(df["neighbor_top5_hit_rate_diff_vs_random"], errors="coerce") * 100

    plt.figure(figsize=(8, 5))
    plt.plot(x, actual, marker="o", label="Actual")
    plt.plot(x, random, marker="o", label="Random baseline")
    plt.title("반경별 Actual vs Random Top5 Hit Rate")
    plt.xlabel("반경(cells)")
    plt.ylabel("Top5 hit rate(%)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    savefig(fig_dir / "fig_radius_actual_vs_random_top5.png")

    plt.figure(figsize=(8, 5))
    plt.bar(x.astype(str), diff)
    plt.title("반경별 Actual - Random Top5 차이")
    plt.xlabel("반경(cells)")
    plt.ylabel("Actual - Random(%p)")
    plt.grid(True, axis="y", alpha=0.3)
    savefig(fig_dir / "fig_radius_top5_diff_vs_random.png")

    if "avg_event_neighbor_top5_grid_ratio" in df.columns:
        actual_ratio = pd.to_numeric(df["avg_event_neighbor_top5_grid_ratio"], errors="coerce") * 100
        random_ratio = pd.to_numeric(df["random_avg_event_neighbor_top5_grid_ratio_mean"], errors="coerce") * 100
        plt.figure(figsize=(8, 5))
        plt.plot(x, actual_ratio, marker="o", label="Actual")
        plt.plot(x, random_ratio, marker="o", label="Random baseline")
        plt.title("반경별 주변 후보 격자 내 Top5 격자 비율")
        plt.xlabel("반경(cells)")
        plt.ylabel("Top5 grid ratio(%)")
        plt.legend()
        plt.grid(True, alpha=0.3)
        savefig(fig_dir / "fig_radius_top5_grid_ratio.png")


def plot_weight_radius(weight_csv: Path, fig_dir: Path, radius: int) -> None:
    if not weight_csv.exists():
        print(f"[SKIP] weight-radius comparison csv not found: {weight_csv}")
        return
    df = read_csv_auto(weight_csv)
    df["radius_cells"] = pd.to_numeric(df["radius_cells"], errors="coerce")
    df = df[df["radius_cells"] == radius].copy()
    if df.empty:
        print(f"[SKIP] no rows for radius={radius} in {weight_csv}")
        return

    df["actual_top5"] = pd.to_numeric(df["neighbor_top5_hit_rate_available"], errors="coerce") * 100
    df["random_top5"] = pd.to_numeric(df["random_neighbor_top5_hit_rate_mean"], errors="coerce") * 100
    df["diff_top5"] = pd.to_numeric(df["neighbor_top5_hit_rate_diff_vs_random"], errors="coerce") * 100

    order = df.sort_values("diff_top5", ascending=True)
    plt.figure(figsize=(9, 5))
    plt.barh(order["model_name"], order["diff_top5"])
    plt.title(f"가중치 후보별 Actual - Random Top5 차이 ±{radius}격자")
    plt.xlabel("Actual - Random top5 hit rate(%p)")
    plt.ylabel("가중치 후보")
    plt.grid(True, axis="x", alpha=0.3)
    savefig(fig_dir / "fig_weight_radius_top5_diff.png")

    order2 = df.sort_values("actual_top5", ascending=True)
    y = np.arange(len(order2))
    h = 0.38
    plt.figure(figsize=(9, 5))
    plt.barh(y - h / 2, order2["actual_top5"], height=h, label="Actual")
    plt.barh(y + h / 2, order2["random_top5"], height=h, label="Random")
    plt.yticks(y, order2["model_name"])
    plt.title(f"가중치 후보별 Actual vs Random Top5 ±{radius}격자")
    plt.xlabel("Top5 hit rate(%)")
    plt.ylabel("가중치 후보")
    plt.legend()
    plt.grid(True, axis="x", alpha=0.3)
    savefig(fig_dir / "fig_weight_radius_actual_vs_random_top5.png")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create report-ready PNG visualizations.")
    parser.add_argument("--fig-dir", default=DEFAULT_FIG_DIR)
    parser.add_argument("--region-csv", default=DEFAULT_REGION_CSV)
    parser.add_argument("--pole-score-csv", default=DEFAULT_POLE_SCORE_CSV)
    parser.add_argument("--pole-grid-map", default=DEFAULT_POLE_GRID_MAP)
    parser.add_argument("--radius-comparison", default=DEFAULT_RADIUS_COMPARISON)
    parser.add_argument("--weight-radius-comparison", default=DEFAULT_WEIGHT_RADIUS_COMPARISON)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--map-sample-n", type=int, default=120000)
    parser.add_argument("--weight-radius", type=int, default=5)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    root = project_root_from_script()
    fig_dir = resolve_path(root, args.fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    setup_plot()

    plot_region(resolve_path(root, args.region_csv), fig_dir, args.top_n)
    plot_pole_maps(resolve_path(root, args.pole_score_csv), resolve_path(root, args.pole_grid_map), fig_dir, args.map_sample_n)
    plot_radius(resolve_path(root, args.radius_comparison), fig_dir)
    plot_weight_radius(resolve_path(root, args.weight_radius_comparison), fig_dir, args.weight_radius)

    print(f"[DONE] figures saved under: {fig_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", file=sys.stderr)
        sys.exit(130)

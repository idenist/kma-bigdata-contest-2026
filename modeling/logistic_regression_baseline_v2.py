"""
Logistic Regression baseline for P-FFDRI fire-risk modeling. (v2: robust NaN handling)

Recommended usage from project root:

python modeling/logistic_regression_baseline.py \
  --input output/eda/eda_sample_500m.parquet \
  --outdir output/modeling/logistic_500m \
  --split year \
  --test-year 2024

For quick random split:
python modeling/logistic_regression_baseline.py \
  --input output/eda/eda_sample_500m.parquet \
  --outdir output/modeling/logistic_500m_random \
  --split random
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


DEFAULT_FEATURES = [
    # Weather
    "ta_mean", "ta_max", "hm_mean", "hm_min",
    "wind_ws_mean", "wind_ws_max",
    "rn_day_mean", "rn_day_max",
    # Accumulated dryness / rain effect
    "effective_humidity", "rne",
    # Official / index features
    "dwi", "dwi_n", "fmi", "fmi_n", "ffdri", "day_weight",
    # Terrain / YWI
    "elevation", "slope", "aspect_sin", "aspect_cos",
    "tmi_base_n", "tmi_p", "ywi", "Da", "Ws", "Dr",
    # Power / access / extended index
    "pole_count", "pole_n", "road_prox", "river_far", "pei", "pffdri",
]

LEAKAGE_COLUMNS = {
    "fire_label",
    "fire_count",
    "fire_objt_ids",
    "fire_addresses",
    "fire_amount_sum",
    "event_lon_mean",
    "event_lat_mean",
    "event_x_mean",
    "event_y_mean",
    "target_grid_id",
    "target_grid_size_m",
    "target_grid_x",
    "target_grid_y",
}

ID_OR_META_COLUMNS = {
    "grid_id",
    "date",
    "city_name",
    "month",
}

BASELINE_SCORE_COLUMNS = ["dwi", "ffdri", "pffdri"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Logistic Regression baseline for fire_label.")
    parser.add_argument("--input", required=True, help="Input parquet/csv path, e.g. output/eda/eda_sample_500m.parquet")
    parser.add_argument("--outdir", default="output/modeling/logistic_500m", help="Output directory")
    parser.add_argument("--target", default="fire_label", help="Target column name")
    parser.add_argument("--split", choices=["year", "random"], default="year", help="Validation split strategy")
    parser.add_argument("--test-year", type=int, default=2024, help="Test year for year split")
    parser.add_argument("--max-year", type=int, default=2024, help="Drop rows after this year, set 0 to disable")
    parser.add_argument("--test-size", type=float, default=0.2, help="Random split test size")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--threshold", type=float, default=0.5, help="Default binary threshold")
    parser.add_argument("--features", nargs="*", default=None, help="Optional explicit feature list")
    return parser.parse_args()


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input format: {path.suffix}. Use .parquet or .csv")


def add_date_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "date" in df.columns:
        date = pd.to_datetime(df["date"], errors="coerce")
        df["year"] = date.dt.year
        df["month_num"] = date.dt.month
        df["dayofyear"] = date.dt.dayofyear
    elif "month" in df.columns:
        month = pd.to_datetime(df["month"].astype(str) + "-01", errors="coerce")
        df["year"] = month.dt.year
        df["month_num"] = month.dt.month
    return df


def coerce_candidate_numeric_columns(df: pd.DataFrame, candidate_columns: Iterable[str]) -> pd.DataFrame:
    """Convert object/string columns that are actually numeric into numeric dtype."""
    df = df.copy()
    converted = []
    for col in candidate_columns:
        if col not in df.columns:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        # Convert when at least one non-null numeric value exists.
        if numeric.notna().sum() > 0:
            df[col] = numeric
            converted.append(col)
    if converted:
        print(f"[INFO] Coerced numeric-like columns: {converted}")
    return df


def select_features(df: pd.DataFrame, explicit_features: list[str] | None, target: str) -> list[str]:
    if explicit_features:
        features = [c for c in explicit_features if c in df.columns]
    else:
        features = [c for c in DEFAULT_FEATURES if c in df.columns]
        for c in ["year", "month_num", "dayofyear"]:
            if c in df.columns:
                features.append(c)

    blocked = LEAKAGE_COLUMNS | ID_OR_META_COLUMNS | {target}
    features = [c for c in features if c not in blocked]

    # Logistic Regression requires numeric input.
    numeric_features = [c for c in features if pd.api.types.is_numeric_dtype(df[c])]
    dropped = sorted(set(features) - set(numeric_features))
    if dropped:
        print(f"[INFO] Dropped non-numeric features: {dropped}")
    return numeric_features


def split_data(df: pd.DataFrame, features: list[str], target: str, args: argparse.Namespace):
    df = df.dropna(subset=[target]).copy()
    df[target] = df[target].astype(int)

    if args.max_year and "year" in df.columns:
        before = len(df)
        df = df[df["year"].notna() & (df["year"] <= args.max_year)].copy()
        print(f"[INFO] max_year<={args.max_year}: {before:,} -> {len(df):,} rows")

    if args.split == "year":
        if "year" not in df.columns:
            raise ValueError("Year split requested, but year column could not be derived from date/month.")
        train_df = df[df["year"] < args.test_year].copy()
        test_df = df[df["year"] == args.test_year].copy()
        if train_df.empty or test_df.empty:
            raise ValueError(f"Empty train/test split. Check test_year={args.test_year} and data years={sorted(df['year'].dropna().unique())}")
    else:
        train_df, test_df = train_test_split(
            df,
            test_size=args.test_size,
            stratify=df[target],
            random_state=args.random_state,
        )

    X_train, y_train = train_df[features], train_df[target]
    X_test, y_test = test_df[features], test_df[target]
    return train_df, test_df, X_train, X_test, y_train, y_test


def finite_y_score(y_true: Iterable[int], score: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    """Return y/score rows where score is finite. This prevents sklearn metric errors on NaN/inf."""
    y = np.asarray(y_true).astype(int)
    s = np.asarray(score, dtype=float)
    mask = np.isfinite(s)
    return y[mask], s[mask]


def recall_at_top_fraction(y_true: Iterable[int], score: Iterable[float], fraction: float) -> float:
    y, s = finite_y_score(y_true, score)
    if len(y) == 0 or y.sum() == 0:
        return float("nan")
    k = max(1, int(np.ceil(len(y) * fraction)))
    order = np.argsort(-s, kind="mergesort")[:k]
    return float(y[order].sum() / y.sum())


def safe_roc_auc(y_true, score) -> float:
    y, s = finite_y_score(y_true, score)
    if len(y) == 0 or len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, s))


def safe_pr_auc(y_true, score) -> float:
    y, s = finite_y_score(y_true, score)
    if len(y) == 0 or y.sum() == 0:
        return float("nan")
    return float(average_precision_score(y, s))


def evaluate_scores(y_true, score, name: str, threshold: float = 0.5) -> dict:
    y, s = finite_y_score(y_true, score)
    dropped_rows = int(len(np.asarray(score)) - len(s))
    if len(y) == 0:
        return {
            "model": name,
            "valid_rows": 0,
            "dropped_nan_score_rows": dropped_rows,
            "roc_auc": float("nan"),
            "pr_auc": float("nan"),
            "recall_at_top10pct": float("nan"),
            "recall_at_top20pct": float("nan"),
            "precision_at_threshold": float("nan"),
            "recall_at_threshold": float("nan"),
        }
    pred = (s >= threshold).astype(int)
    if dropped_rows > 0:
        print(f"[WARN] {name}: dropped {dropped_rows:,} rows with NaN/inf score for metric calculation")
    return {
        "model": name,
        "valid_rows": int(len(y)),
        "dropped_nan_score_rows": dropped_rows,
        "roc_auc": safe_roc_auc(y, s),
        "pr_auc": safe_pr_auc(y, s),
        "recall_at_top10pct": recall_at_top_fraction(y, s, 0.10),
        "recall_at_top20pct": recall_at_top_fraction(y, s, 0.20),
        "precision_at_threshold": float(precision_score(y, pred, zero_division=0)),
        "recall_at_threshold": float(recall_score(y, pred, zero_division=0)),
    }


def choose_best_f1_threshold(y_true, score) -> tuple[float, float]:
    precision, recall, thresholds = precision_recall_curve(y_true, score)
    if len(thresholds) == 0:
        return 0.5, float("nan")
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    idx = int(np.nanargmax(f1))
    return float(thresholds[idx]), float(f1[idx])


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[LOAD] {input_path}")
    df = read_table(input_path)
    df = add_date_features(df)
    df = coerce_candidate_numeric_columns(df, [*DEFAULT_FEATURES, *BASELINE_SCORE_COLUMNS, "year", "month_num", "dayofyear"])

    if args.target not in df.columns:
        raise KeyError(f"Target column not found: {args.target}")

    features = select_features(df, args.features, args.target)
    if not features:
        raise ValueError("No usable numeric features selected.")

    print(f"[INFO] rows={len(df):,}, cols={len(df.columns):,}, features={len(features):,}")
    print(f"[INFO] selected features: {features}")

    train_df, test_df, X_train, X_test, y_train, y_test = split_data(df, features, args.target, args)
    print(f"[SPLIT] train={len(train_df):,}, test={len(test_df):,}")
    print(f"[SPLIT] train positives={int(y_train.sum()):,}, test positives={int(y_test.sum()):,}")

    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("model", LogisticRegression(
            class_weight="balanced",
            max_iter=2000,
            random_state=args.random_state,
            solver="lbfgs",
        )),
    ])

    print("[TRAIN] Logistic Regression")
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]

    best_threshold, best_f1 = choose_best_f1_threshold(y_test, proba)
    metrics = [evaluate_scores(y_test, proba, "Logistic Regression", threshold=args.threshold)]
    metrics.append(evaluate_scores(y_test, proba, f"Logistic Regression threshold={best_threshold:.4f}", threshold=best_threshold))

    for col in BASELINE_SCORE_COLUMNS:
        if col in test_df.columns:
            score = pd.to_numeric(test_df[col], errors="coerce").to_numpy(dtype=float)
            finite_score = score[np.isfinite(score)]
            if len(finite_score) == 0:
                print(f"[WARN] {col}: all scores are NaN/inf; skipping single-score baseline")
                continue
            metrics.append(evaluate_scores(y_test, score, f"{col.upper()} single score", threshold=float(np.nanmedian(finite_score))))

    metrics_df = pd.DataFrame(metrics)
    metrics_df["best_f1_threshold"] = best_threshold
    metrics_df["best_f1"] = best_f1
    metrics_df.to_csv(outdir / "metrics.csv", index=False, encoding="utf-8-sig")

    pred_default = (proba >= args.threshold).astype(int)
    report = classification_report(y_test, pred_default, digits=4, zero_division=0)
    cm = confusion_matrix(y_test, pred_default)

    coef = model.named_steps["model"].coef_.ravel()
    coef_df = pd.DataFrame({
        "feature": features,
        "coef": coef,
        "abs_coef": np.abs(coef),
    }).sort_values("abs_coef", ascending=False)
    coef_df.to_csv(outdir / "coefficients.csv", index=False, encoding="utf-8-sig")

    pred_df = test_df[[c for c in ["date", "grid_id", "year", "month_num", args.target] if c in test_df.columns]].copy()
    pred_df["logistic_score"] = proba
    pred_df["logistic_pred_threshold_0_5"] = pred_default
    pred_df.to_csv(outdir / "test_predictions.csv", index=False, encoding="utf-8-sig")

    summary = {
        "input": str(input_path),
        "outdir": str(outdir),
        "split": args.split,
        "test_year": args.test_year if args.split == "year" else None,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_positive": int(y_train.sum()),
        "test_positive": int(y_test.sum()),
        "features": features,
        "default_threshold": args.threshold,
        "best_f1_threshold": best_threshold,
        "best_f1": best_f1,
        "confusion_matrix_threshold_0_5": cm.tolist(),
        "classification_report_threshold_0_5": report,
    }
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "classification_report.txt").write_text(report, encoding="utf-8")

    print("\n[RESULT] metrics")
    print(metrics_df.to_string(index=False))
    print("\n[RESULT] top coefficients")
    print(coef_df.head(20).to_string(index=False))
    print(f"\n[SAVED] {outdir}")


if __name__ == "__main__":
    main()

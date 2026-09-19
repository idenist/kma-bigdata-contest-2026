#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
단일 위험지수(DWI/FFDRI/P-FFDRI) Full Test Top-K 평가 스크립트.

목적
- ML 모델 없이 dwi, ffdri, pffdri 자체가 2024년 전체 격자 모집단에서
  실제 산불 발생 격자를 상위 위험 후보군으로 올리는지 확인한다.
- 기존 ML 평가의 full_test_topk_metrics.csv와 같은 방식으로
  일자별 Recall@Top-K, Precision@Top-K, Lift@Top-K를 산출한다.

CMD 실행 예시
python scripts/modeling/evaluate_single_index_topk.py --grid-date-master output/final/final_feature_daily --sample-cache-dir outputs/sample_cache_1km_hard_neg100 --out-dir outputs/single_index_baseline_1km --score-cols dwi ffdri pffdri --topk 100 500 1000 5000

labels.parquet가 없을 때 직접 라벨을 생성하려면 다음 인자를 함께 제공한다.
--master-grid data/master_grid.parquet --fire-history data/산불발생이력.csv --radius-m 1000
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from tqdm.auto import tqdm
except Exception:  # noqa: BLE001
    def tqdm(iterable=None, **kwargs):  # type: ignore[override]
        return iterable if iterable is not None else range(0)


@dataclass
class Config:
    grid_date_master: str
    out_dir: str
    score_cols: Tuple[str, ...] = ("dwi", "ffdri", "pffdri")
    topk: Tuple[int, ...] = (100, 500, 1000, 5000)
    test_years: Tuple[int, ...] = (2024,)
    months: Tuple[int, ...] = (2, 3, 4, 5)
    sample_cache_dir: Optional[str] = None
    label_cache: Optional[str] = None
    master_grid: Optional[str] = None
    fire_history: Optional[str] = None
    radius_m: int = 1000
    save_monthly_scores: bool = False


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_parquet_auto(path: str | Path, *, filters=None, columns=None) -> pd.DataFrame:
    path = str(path)
    last_err = None
    for engine in ("pyarrow", "fastparquet"):
        try:
            return pd.read_parquet(path, filters=filters, columns=columns, engine=engine)
        except Exception as e:  # noqa: BLE001
            last_err = e
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"parquet 로드 실패: {path}\n마지막 오류: {last_err}\n기본 엔진 오류: {e}") from e


def load_monthly_grid(path: str | Path, year: int, month: int) -> pd.DataFrame:
    ym = f"{year}-{month:02d}"
    try:
        df = read_parquet_auto(path, filters=[("month", "==", ym)])
    except Exception:
        warnings.warn(
            f"month={ym} filter 로드 실패. 전체 parquet를 읽은 뒤 date로 필터링합니다. 데이터가 크면 느릴 수 있습니다.",
            RuntimeWarning,
        )
        df = read_parquet_auto(path)

    if "date" not in df.columns:
        raise ValueError("grid_date_master에 date 컬럼이 필요합니다.")
    if "grid_id" not in df.columns:
        raise ValueError("grid_date_master에 grid_id 컬럼이 필요합니다.")

    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"].dt.year == year) & (df["date"].dt.month == month)].copy()
    df["date"] = df["date"].dt.normalize()
    if "month" not in df.columns:
        df["month"] = df["date"].dt.strftime("%Y-%m")
    return df


# ---------------------------------------------------------------------
# label 생성 유틸: labels.parquet가 없을 때만 사용
# ---------------------------------------------------------------------

def load_master_grid(path: str | Path) -> pd.DataFrame:
    master = read_parquet_auto(path)
    required = {"grid_id", "grid_x", "grid_y"}
    missing = sorted(required - set(master.columns))
    if missing:
        raise ValueError(f"master_grid에 필수 컬럼이 없습니다: {missing}")
    master = master.drop_duplicates("grid_id").copy()
    master["grid_x"] = master["grid_x"].astype(int)
    master["grid_y"] = master["grid_y"].astype(int)
    return master


def load_fire_history(path: str | Path, years: Sequence[int], months: Sequence[int]) -> pd.DataFrame:
    fire = pd.read_csv(path)
    if "occu_date" not in fire.columns:
        raise ValueError("산불발생이력.csv에 occu_date 컬럼이 필요합니다.")
    fire["date"] = pd.to_datetime(fire["occu_date"], errors="coerce")
    fire = fire.dropna(subset=["date"]).copy()
    fire = fire[fire["date"].dt.year.isin(list(years)) & fire["date"].dt.month.isin(list(months))].copy()

    gangwon_by_code = fire.get("ctprvn_cd", pd.Series(index=fire.index, dtype="float")).isin([42, 51])
    gangwon_by_addr = fire.get("adres", pd.Series("", index=fire.index)).astype(str).str.contains("강원", na=False)
    fire = fire[gangwon_by_code | gangwon_by_addr].copy()

    if not {"longitude", "latitude"}.issubset(fire.columns):
        raise ValueError("산불발생이력.csv에 longitude, latitude 컬럼이 필요합니다.")
    fire = fire.dropna(subset=["longitude", "latitude"]).copy()
    return fire


def transform_lonlat_to_epsg5179(lon: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    try:
        from pyproj import Transformer
    except ImportError as e:
        raise ImportError("라벨 생성을 위해 pyproj가 필요합니다. 설치: pip install pyproj") from e
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x, y = transformer.transform(lon, lat)
    return np.asarray(x), np.asarray(y)


def make_fire_grid_labels(master: pd.DataFrame, fire: pd.DataFrame, radius_m: int) -> pd.DataFrame:
    if fire.empty:
        raise ValueError("라벨 생성 대상 산불 이력이 없습니다. 기간/지역 필터를 확인하세요.")

    fire = fire.copy()
    x5179, y5179 = transform_lonlat_to_epsg5179(
        fire["longitude"].astype(float).to_numpy(),
        fire["latitude"].astype(float).to_numpy(),
    )
    fire["event_x5179"] = x5179
    fire["event_y5179"] = y5179
    fire["event_grid_x"] = np.floor(fire["event_x5179"] / 100).astype(int)
    fire["event_grid_y"] = np.floor(fire["event_y5179"] / 100).astype(int)

    grid_lookup = {
        (int(gx), int(gy)): gid
        for gid, gx, gy in master[["grid_id", "grid_x", "grid_y"]].itertuples(index=False, name=None)
    }
    cell_radius = int(math.ceil(radius_m / 100))
    label_rows = []

    for row in tqdm(list(fire.itertuples(index=False)), desc="label mapping", unit="fire"):
        gx0 = int(getattr(row, "event_grid_x"))
        gy0 = int(getattr(row, "event_grid_y"))
        ex = float(getattr(row, "event_x5179"))
        ey = float(getattr(row, "event_y5179"))
        date = pd.Timestamp(getattr(row, "date")).normalize()

        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                gx = gx0 + dx
                gy = gy0 + dy
                gid = grid_lookup.get((gx, gy))
                if gid is None:
                    continue
                cx = gx * 100 + 50
                cy = gy * 100 + 50
                if math.hypot(cx - ex, cy - ey) <= radius_m:
                    label_rows.append({"date": date, "grid_id": gid, "fire_label": 1})

    labels = pd.DataFrame(label_rows)
    if labels.empty:
        raise ValueError("라벨 매핑 결과가 비었습니다. master_grid 좌표계 또는 산불 좌표 변환을 확인하세요.")
    labels = labels.groupby(["date", "grid_id"], as_index=False).agg(fire_label=("fire_label", "max"))
    return labels


def load_or_build_labels(cfg: Config, out_dir: Path) -> pd.DataFrame:
    candidates: List[Path] = []
    if cfg.label_cache:
        candidates.append(Path(cfg.label_cache))
    if cfg.sample_cache_dir:
        candidates.append(Path(cfg.sample_cache_dir) / "labels.parquet")

    for p in candidates:
        if p.exists():
            labels = read_parquet_auto(p)
            labels["date"] = pd.to_datetime(labels["date"]).dt.normalize()
            if "fire_label" not in labels.columns:
                labels["fire_label"] = 1
            labels = labels[["date", "grid_id", "fire_label"]].drop_duplicates(["date", "grid_id"])
            log(f"labels loaded: {p}")
            return labels

    if not cfg.master_grid or not cfg.fire_history:
        raise ValueError(
            "labels.parquet를 찾지 못했습니다. --sample-cache-dir 또는 --label-cache를 지정하거나, "
            "--master-grid와 --fire-history를 함께 지정해 라벨을 생성하세요."
        )

    master = load_master_grid(cfg.master_grid)
    fire = load_fire_history(cfg.fire_history, years=cfg.test_years, months=cfg.months)
    labels = make_fire_grid_labels(master, fire, cfg.radius_m)
    label_path = out_dir / f"labels_radius_{cfg.radius_m}m.parquet"
    labels.to_parquet(label_path, index=False)
    log(f"labels saved: {label_path}")
    return labels


# ---------------------------------------------------------------------
# 평가 로직
# ---------------------------------------------------------------------

def _score_aliases(score_col: str) -> List[str]:
    # 컬럼명이 조금 다를 때를 대비한 최소 fallback
    alias_map = {
        "dwi": ["dwi", "DWI", "dwi_n"],
        "ffdri": ["ffdri", "FFDRI", "ff_dri"],
        "pffdri": ["pffdri", "P-FFDRI", "p_ffdri", "pffdri_n"],
    }
    return alias_map.get(score_col, [score_col])


def resolve_score_col(df: pd.DataFrame, requested: str) -> Optional[str]:
    for c in _score_aliases(requested):
        if c in df.columns:
            return c
    return None


def evaluate_daily_topk_for_score(
    df: pd.DataFrame,
    requested_score_col: str,
    actual_score_col: str,
    ks: Sequence[int],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """한 score column에 대한 날짜별 Top-K와 positive percentile 정보를 반환."""
    rows: List[Dict[str, object]] = []
    pct_rows: List[Dict[str, object]] = []

    use = df[["date", "grid_id", "fire_label", actual_score_col]].copy()
    use[actual_score_col] = pd.to_numeric(use[actual_score_col], errors="coerce")
    use = use.dropna(subset=[actual_score_col])
    if use.empty:
        return rows, pct_rows

    for date, g in use.groupby("date", sort=True):
        pos_total = int(g["fire_label"].sum())
        n = len(g)
        if pos_total == 0 or n == 0:
            continue

        base_rate = pos_total / n

        # high score가 high risk라고 보고 percentile 계산. 1에 가까울수록 상위 위험권.
        pct = g[actual_score_col].rank(method="average", pct=True, ascending=True)
        pos_pct = pct[g["fire_label"].to_numpy(dtype=bool)]
        if len(pos_pct) > 0:
            pct_rows.append(
                {
                    "score_col": requested_score_col,
                    "actual_score_col": actual_score_col,
                    "date": date,
                    "n": int(n),
                    "pos_total": int(pos_total),
                    "positive_percentile_mean": float(pos_pct.mean()),
                    "positive_percentile_median": float(pos_pct.median()),
                    "positive_percentile_min": float(pos_pct.min()),
                    "positive_percentile_max": float(pos_pct.max()),
                }
            )

        for k in ks:
            kk = min(int(k), n)
            top = g.nlargest(kk, actual_score_col)
            top_pos = int(top["fire_label"].sum())
            precision_at_k = top_pos / kk if kk > 0 else 0.0
            recall_at_k = top_pos / pos_total if pos_total > 0 else np.nan
            lift_at_k = precision_at_k / base_rate if base_rate > 0 else np.nan
            rows.append(
                {
                    "score_col": requested_score_col,
                    "actual_score_col": actual_score_col,
                    "date": date,
                    "k": int(k),
                    "n": int(n),
                    "pos_total": int(pos_total),
                    "top_pos": int(top_pos),
                    "recall_at_k": float(recall_at_k),
                    "precision_at_k": float(precision_at_k),
                    "lift_at_k": float(lift_at_k),
                    "score_min": float(g[actual_score_col].min()),
                    "score_mean": float(g[actual_score_col].mean()),
                    "score_max": float(g[actual_score_col].max()),
                    "topk_score_min": float(top[actual_score_col].min()) if len(top) else np.nan,
                }
            )
    return rows, pct_rows


def summarize_topk(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(
            columns=[
                "score_col", "actual_score_col", "k", "days", "total_pos", "total_top_pos",
                "mean_recall_at_k", "weighted_recall_at_k", "mean_precision_at_k", "mean_lift_at_k",
            ]
        )

    rows = []
    for (score_col, actual_score_col, k), d in daily.groupby(["score_col", "actual_score_col", "k"], sort=True):
        rows.append(
            {
                "score_col": score_col,
                "actual_score_col": actual_score_col,
                "k": int(k),
                "days": int(len(d)),
                "total_pos": int(d["pos_total"].sum()),
                "total_top_pos": int(d["top_pos"].sum()),
                "mean_recall_at_k": float(d["recall_at_k"].mean()),
                "weighted_recall_at_k": float(d["top_pos"].sum() / d["pos_total"].sum()) if d["pos_total"].sum() > 0 else np.nan,
                "mean_precision_at_k": float(d["precision_at_k"].mean()),
                "mean_lift_at_k": float(d["lift_at_k"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["score_col", "k"])


def evaluate_single_indices(cfg: Config, labels: pd.DataFrame, out_dir: Path) -> None:
    daily_rows: List[Dict[str, object]] = []
    pct_rows: List[Dict[str, object]] = []
    score_dir = ensure_dir(out_dir / "monthly_scores") if cfg.save_monthly_scores else None

    ym_pairs = [(year, month) for year in cfg.test_years for month in cfg.months]
    for year, month in tqdm(ym_pairs, desc="full-test months", unit="month"):
        log(f"[single-index] evaluate {year}-{month:02d}")
        month_df = load_monthly_grid(cfg.grid_date_master, year, month)
        if month_df.empty:
            continue

        month_df["date"] = pd.to_datetime(month_df["date"]).dt.normalize()
        lab = labels[labels["date"].isin(month_df["date"].unique())]
        month_df = month_df.merge(lab[["date", "grid_id", "fire_label"]], on=["date", "grid_id"], how="left")
        month_df["fire_label"] = month_df["fire_label"].fillna(0).astype(np.int8)

        if score_dir is not None:
            keep_cols = ["date", "grid_id", "fire_label"]
            for s in cfg.score_cols:
                actual = resolve_score_col(month_df, s)
                if actual is not None and actual not in keep_cols:
                    keep_cols.append(actual)
            month_df[keep_cols].to_parquet(score_dir / f"single_index_scores_{year}_{month:02d}.parquet", index=False)

        for requested in cfg.score_cols:
            actual = resolve_score_col(month_df, requested)
            if actual is None:
                warnings.warn(f"요청 score_col={requested}에 해당하는 컬럼을 찾지 못해 건너뜁니다.", RuntimeWarning)
                continue
            rows, pcts = evaluate_daily_topk_for_score(month_df, requested, actual, cfg.topk)
            daily_rows.extend(rows)
            pct_rows.extend(pcts)

        del month_df

    daily = pd.DataFrame(daily_rows)
    pct = pd.DataFrame(pct_rows)
    summary = summarize_topk(daily)

    daily.to_csv(out_dir / "single_index_daily_topk_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out_dir / "single_index_topk_metrics.csv", index=False, encoding="utf-8-sig")
    pct.to_csv(out_dir / "single_index_positive_percentile_by_day.csv", index=False, encoding="utf-8-sig")

    log("\nSingle-index Top-K summary")
    if summary.empty:
        log("평가 결과가 비었습니다. score 컬럼명 또는 test 기간의 positive label을 확인하세요.")
    else:
        log(str(summary))
    log(f"\n완료: {out_dir}")


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Evaluate single risk-index Top-K baselines")
    parser.add_argument("--grid-date-master", required=True, help="output/final/final_feature_daily")
    parser.add_argument("--out-dir", default="outputs/single_index_baseline_1km")
    parser.add_argument("--score-cols", nargs="+", default=["dwi", "ffdri", "pffdri"])
    parser.add_argument("--topk", nargs="+", type=int, default=[100, 500, 1000, 5000])
    parser.add_argument("--test-years", nargs="+", type=int, default=[2024])
    parser.add_argument("--months", nargs="+", type=int, default=[2, 3, 4, 5])
    parser.add_argument("--sample-cache-dir", default=None, help="labels.parquet가 있는 샘플 캐시 폴더")
    parser.add_argument("--label-cache", default=None, help="labels parquet 직접 경로")
    parser.add_argument("--master-grid", default=None, help="labels가 없을 때 라벨 생성을 위한 master_grid")
    parser.add_argument("--fire-history", default=None, help="labels가 없을 때 라벨 생성을 위한 산불발생이력.csv")
    parser.add_argument("--radius-m", type=int, default=1000)
    parser.add_argument("--save-monthly-scores", action="store_true", help="월별 전체 점수 parquet 저장. 용량 커질 수 있음")
    args = parser.parse_args()
    return Config(
        grid_date_master=args.grid_date_master,
        out_dir=args.out_dir,
        score_cols=tuple(args.score_cols),
        topk=tuple(args.topk),
        test_years=tuple(args.test_years),
        months=tuple(args.months),
        sample_cache_dir=args.sample_cache_dir,
        label_cache=args.label_cache,
        master_grid=args.master_grid,
        fire_history=args.fire_history,
        radius_m=args.radius_m,
        save_monthly_scores=args.save_monthly_scores,
    )


def main() -> None:
    cfg = parse_args()
    out_dir = ensure_dir(cfg.out_dir)
    with open(out_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2, default=list)

    labels = load_or_build_labels(cfg, out_dir)
    log(f"labels rows={len(labels):,}, positive grid-date={labels[['date', 'grid_id']].drop_duplicates().shape[0]:,}")
    evaluate_single_indices(cfg, labels, out_dir)


if __name__ == "__main__":
    main()

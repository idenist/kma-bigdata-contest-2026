#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
강원도 100m 격자 산불위험 Logistic Regression baseline.

전략 반영 사항
- 시간 기준 분할: Train 2020~2022 / Validation 2023 / Test 2024
- positive 전체 + negative sampling
- feature set: index_only / weather_only / compact_full / no_index
- 평가: PR-AUC, ROC-AUC, threshold 기반 F2/Recall/Precision, 일자별 Recall@Top-K, Lift@Top-K
- 선택 기능: 산불 발생 이력 좌표를 100m grid_id에 반경 라벨로 매핑

권장 실행 예시
python train_logistic_regression.py \
  --master-grid data/master_grid.parquet \
  --grid-date-master data/grid_date_master \
  --fire-history data/raw/산불발생이력.csv \
  --out-dir outputs/logistic_1km_compact \
  --radius-m 1000 \
  --feature-set compact_full \
  --neg-ratio 50 \
  --eval-full-test
"""

from __future__ import annotations

import argparse
import json
import math
import os
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    fbeta_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


# -----------------------------
# 1. 설정
# -----------------------------

@dataclass
class Config:
    master_grid: str
    grid_date_master: str
    fire_history: str
    out_dir: str = "outputs/logistic_regression"
    label_cache: Optional[str] = None
    radius_m: int = 1000
    neg_ratio: int = 50
    feature_set: str = "compact_full"
    random_state: int = 42
    train_years: Tuple[int, ...] = (2020, 2021, 2022)
    valid_years: Tuple[int, ...] = (2023,)
    test_years: Tuple[int, ...] = (2024,)
    months: Tuple[int, ...] = (2, 3, 4, 5)
    topk: Tuple[int, ...] = (100, 500, 1000, 5000)
    class_weight: str = "balanced"
    max_iter: int = 2000
    eval_full_test: bool = False
    save_full_test_scores: bool = False


EXCLUDE_COLUMNS = {
    # target / leakage
    "fire_label", "fire_count", "fire_objt_ids", "fire_addresses", "fire_amount_sum",
    "event_lon_mean", "event_lat_mean", "event_x5179_mean", "event_y5179_mean",
    "target_grid_id", "target_grid_size_m", "target_grid_x", "target_grid_y",
    # identifiers / raw temporal columns
    "grid_id", "date", "month",
}

FEATURE_SETS: Dict[str, List[str]] = {
    "index_only": [
        "dwi", "ffdri", "pffdri",
    ],
    "weather_only": [
        "ta_mean", "ta_max", "hm_mean", "hm_min",
        "wind_ws_mean", "wind_ws_max",
        "rn_day_mean", "rn_day_max",
        "effective_humidity", "rne", "dwi", "dwi_n", "day_weight",
        "month_num", "day_num", "dayofyear",
    ],
    "compact_full": [
        # weather / derived weather index
        "ta_mean", "ta_max", "hm_mean", "hm_min",
        "wind_ws_mean", "wind_ws_max",
        "rn_day_mean", "rn_day_max",
        "effective_humidity", "rne", "dwi", "dwi_n", "day_weight",
        # forest / terrain
        "fmi_n", "tmi_p", "ywi", "Da", "Ws", "Dr",
        "slope", "elevation", "aspect_sin", "aspect_cos",
        # electricity / accessibility
        "pei", "pole_n", "pole_count", "road_prox", "river_far",
        "nearest_road_dist", "nearest_river_dist",
        # composite index
        "ffdri", "pffdri",
        # time / region
        "month_num", "day_num", "dayofyear", "city_name",
    ],
    "no_index": [
        "ta_mean", "ta_max", "hm_mean", "hm_min",
        "wind_ws_mean", "wind_ws_max",
        "rn_day_mean", "rn_day_max",
        "effective_humidity", "rne", "day_weight",
        "fmi_n", "tmi_p", "ywi", "Da", "Ws", "Dr",
        "slope", "elevation", "aspect_sin", "aspect_cos",
        "pei", "pole_n", "pole_count", "road_prox", "river_far",
        "nearest_road_dist", "nearest_river_dist",
        "month_num", "day_num", "dayofyear", "city_name",
    ],
}


# -----------------------------
# 2. I/O 유틸
# -----------------------------

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_parquet_auto(path: str | Path, *, filters=None, columns=None) -> pd.DataFrame:
    """pyarrow/fastparquet 어느 쪽이 설치되어 있어도 읽을 수 있게 처리."""
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
    """grid_date_master를 월 단위로 로드. month partition이 없으면 전체 로드 후 필터링."""
    ym = f"{year}-{month:02d}"
    try:
        df = read_parquet_auto(path, filters=[("month", "==", ym)])
    except Exception:
        warnings.warn(
            f"month={ym} filter 로드 실패. 전체 parquet를 읽은 뒤 date로 필터링합니다. "
            "데이터가 크면 느릴 수 있습니다.",
            RuntimeWarning,
        )
        df = read_parquet_auto(path)

    if "date" not in df.columns:
        raise ValueError("grid_date_master에 date 컬럼이 필요합니다.")

    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"].dt.year == year) & (df["date"].dt.month == month)].copy()
    if "month" not in df.columns:
        df["month"] = df["date"].dt.strftime("%Y-%m")
    return df


# -----------------------------
# 3. 라벨 생성: 산불 발생 좌표 -> 100m grid_id 반경 라벨
# -----------------------------

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

    # 강원도만 사용. 2023년 이후 강원특별자치도 코드 변화 가능성을 고려해 주소 문자열도 함께 사용.
    gangwon_by_code = fire.get("ctprvn_cd", pd.Series(index=fire.index, dtype="float")).isin([42, 51])
    gangwon_by_addr = fire.get("adres", pd.Series("", index=fire.index)).astype(str).str.contains("강원", na=False)
    fire = fire[gangwon_by_code | gangwon_by_addr].copy()

    coord_cols = {"longitude", "latitude"}
    if not coord_cols.issubset(fire.columns):
        raise ValueError("산불발생이력.csv에 longitude, latitude 컬럼이 필요합니다.")
    fire = fire.dropna(subset=["longitude", "latitude"]).copy()
    return fire


def transform_lonlat_to_epsg5179(lon: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    try:
        from pyproj import Transformer
    except ImportError as e:
        raise ImportError(
            "라벨 생성을 위해 pyproj가 필요합니다. 설치: pip install pyproj"
        ) from e
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x, y = transformer.transform(lon, lat)
    return np.asarray(x), np.asarray(y)


def make_fire_grid_labels(
    master: pd.DataFrame,
    fire: pd.DataFrame,
    radius_m: int,
) -> pd.DataFrame:
    """각 산불 발생점 주변 radius_m 내 100m 격자에 positive label 부여."""
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

    # 빠른 grid_id 조회용 dict
    grid_lookup = {
        (int(gx), int(gy)): gid
        for gid, gx, gy in master[["grid_id", "grid_x", "grid_y"]].itertuples(index=False, name=None)
    }

    cell_radius = int(math.ceil(radius_m / 100))
    label_rows = []

    for row in fire.itertuples(index=False):
        gx0 = int(getattr(row, "event_grid_x"))
        gy0 = int(getattr(row, "event_grid_y"))
        ex = float(getattr(row, "event_x5179"))
        ey = float(getattr(row, "event_y5179"))
        date = pd.Timestamp(getattr(row, "date")).normalize()
        objt_id = getattr(row, "objt_id", None)
        amount = getattr(row, "amount", np.nan)
        adres = getattr(row, "adres", "")

        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                gx = gx0 + dx
                gy = gy0 + dy
                gid = grid_lookup.get((gx, gy))
                if gid is None:
                    continue
                cx = gx * 100 + 50
                cy = gy * 100 + 50
                dist = math.hypot(cx - ex, cy - ey)
                if dist <= radius_m:
                    label_rows.append(
                        {
                            "date": date,
                            "grid_id": gid,
                            "fire_label": 1,
                            "fire_count": 1,
                            "fire_objt_ids": str(objt_id),
                            "fire_addresses": str(adres),
                            "fire_amount_sum": pd.to_numeric(amount, errors="coerce"),
                            "event_lon_mean": getattr(row, "longitude"),
                            "event_lat_mean": getattr(row, "latitude"),
                            "event_x5179_mean": ex,
                            "event_y5179_mean": ey,
                            "target_grid_size_m": radius_m,
                        }
                    )

    labels = pd.DataFrame(label_rows)
    if labels.empty:
        raise ValueError(
            "라벨 매핑 결과가 비었습니다. master_grid 좌표계/grid_x, grid_y 또는 산불 좌표 변환을 확인하세요."
        )

    # 같은 날짜-격자에 여러 산불이 잡힐 수 있으므로 집계
    labels = (
        labels.groupby(["date", "grid_id"], as_index=False)
        .agg(
            fire_label=("fire_label", "max"),
            fire_count=("fire_count", "sum"),
            fire_objt_ids=("fire_objt_ids", lambda s: "|".join(sorted(set(map(str, s))))),
            fire_addresses=("fire_addresses", lambda s: "|".join(sorted(set(map(str, s))))),
            fire_amount_sum=("fire_amount_sum", "sum"),
            event_lon_mean=("event_lon_mean", "mean"),
            event_lat_mean=("event_lat_mean", "mean"),
            event_x5179_mean=("event_x5179_mean", "mean"),
            event_y5179_mean=("event_y5179_mean", "mean"),
            target_grid_size_m=("target_grid_size_m", "first"),
        )
    )
    return labels


# -----------------------------
# 4. Feature engineering
# -----------------------------

def add_feature_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """프로젝트 문서에 나온 추천 변수명이 없을 때 가능한 범위에서 alias 생성."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["month_num"] = df["date"].dt.month.astype(np.int16)
    df["day_num"] = df["date"].dt.day.astype(np.int16)
    df["dayofyear"] = df["date"].dt.dayofyear.astype(np.int16)

    if "pole_n" not in df.columns and "pole_count" in df.columns:
        df["pole_n"] = np.log1p(pd.to_numeric(df["pole_count"], errors="coerce"))

    if "road_prox" not in df.columns and "nearest_road_dist" in df.columns:
        road = pd.to_numeric(df["nearest_road_dist"], errors="coerce")
        df["road_prox"] = 1.0 / (1.0 + road.clip(lower=0))

    if "river_far" not in df.columns and "nearest_river_dist" in df.columns:
        df["river_far"] = pd.to_numeric(df["nearest_river_dist"], errors="coerce")

    # FMI: forest_type_code 1=침엽, 2=활엽, 3=혼효 기준
    if "fmi_n" not in df.columns and "forest_type_code" in df.columns:
        mapping = {1: 10.0, 2: 2.0, 3: 3.0}
        df["fmi_n"] = pd.to_numeric(df["forest_type_code"], errors="coerce").map(mapping).fillna(0.0)

    # TMI 근사: aspect_sin/cos와 elevation이 있으면 국가산불위험지수 표 기준으로 간단 산출
    if "tmi_p" not in df.columns and {"aspect_sin", "aspect_cos", "elevation"}.issubset(df.columns):
        angle = (np.degrees(np.arctan2(df["aspect_sin"], df["aspect_cos"])) + 360) % 360
        aspect_score = np.select(
            [
                (angle >= 67.5) & (angle < 112.5),  # E
                ((angle >= 337.5) | (angle < 22.5)) | ((angle >= 247.5) & (angle < 292.5)),  # N/W
                ((angle >= 112.5) & (angle < 202.5)),  # SE/S
                ((angle >= 22.5) & (angle < 67.5)) | ((angle >= 292.5) & (angle < 337.5)),  # NE/NW
                ((angle >= 202.5) & (angle < 247.5)),  # SW
            ],
            [1.5, 2.5, 4.0, 4.5, 5.0],
            default=np.nan,
        )
        elev = pd.to_numeric(df["elevation"], errors="coerce")
        elev_score = np.select(
            [elev >= 876, (elev >= 628) & (elev < 876), (elev >= 380) & (elev < 628), (elev >= 132) & (elev < 380), elev < 132],
            [1.0, 2.0, 3.0, 4.0, 5.0],
            default=np.nan,
        )
        df["tmi_p"] = aspect_score + elev_score

    if "dwi_n" not in df.columns and "dwi" in df.columns:
        dwi = pd.to_numeric(df["dwi"], errors="coerce")
        # DWI가 1~10이면 0~1로, 이미 0~1이면 그대로에 가깝게 유지
        df["dwi_n"] = np.where(dwi.max(skipna=True) > 1.5, dwi / 10.0, dwi)

    return df


def select_existing_features(df: pd.DataFrame, feature_set: str) -> Tuple[List[str], List[str], List[str]]:
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"feature_set은 {sorted(FEATURE_SETS)} 중 하나여야 합니다.")

    candidates = [c for c in FEATURE_SETS[feature_set] if c in df.columns and c not in EXCLUDE_COLUMNS]
    if not candidates:
        raise ValueError(
            f"선택된 feature_set={feature_set}에서 사용 가능한 feature가 없습니다. "
            f"현재 컬럼 예시: {list(df.columns)[:30]}"
        )

    num_cols = [c for c in candidates if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in candidates if c not in num_cols]
    return candidates, num_cols, cat_cols


# -----------------------------
# 5. 샘플 데이터 구축
# -----------------------------

def attach_labels_and_sample(
    month_df: pd.DataFrame,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
    neg_ratio: int,
    random_state: int,
) -> pd.DataFrame:
    """월별 grid-date 데이터에 static feature와 label을 붙이고 negative sample 추출."""
    if month_df.empty:
        return month_df

    month_df = month_df.merge(master_static, on="grid_id", how="left", suffixes=("", "_mg"))
    month_df["date"] = pd.to_datetime(month_df["date"]).dt.normalize()

    lab = labels[labels["date"].isin(month_df["date"].unique())]
    df = month_df.merge(lab, on=["date", "grid_id"], how="left")
    df["fire_label"] = df["fire_label"].fillna(0).astype(np.int8)

    pos = df[df["fire_label"] == 1]
    neg = df[df["fire_label"] == 0]

    if pos.empty:
        # 해당 월에 산불 positive가 없으면 학습/검증 샘플에 굳이 넣지 않음
        return pos.copy()

    n_neg = min(len(neg), len(pos) * int(neg_ratio))
    neg_sample = neg.sample(n=n_neg, random_state=random_state) if n_neg > 0 else neg.head(0)
    sampled = pd.concat([pos, neg_sample], ignore_index=True)
    sampled = add_feature_aliases(sampled)
    return sampled


def build_split_dataset(
    years: Sequence[int],
    cfg: Config,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
    split_name: str,
) -> pd.DataFrame:
    parts = []
    for year in years:
        for month in cfg.months:
            print(f"[{split_name}] load/sample {year}-{month:02d}")
            month_df = load_monthly_grid(cfg.grid_date_master, year, month)
            if month_df.empty:
                continue
            sampled = attach_labels_and_sample(
                month_df=month_df,
                master_static=master_static,
                labels=labels,
                neg_ratio=cfg.neg_ratio,
                random_state=cfg.random_state + year * 100 + month,
            )
            if not sampled.empty:
                parts.append(sampled)
            del month_df, sampled

    if not parts:
        raise ValueError(f"{split_name} 데이터가 비었습니다. 기간/월/라벨 매핑을 확인하세요.")
    df = pd.concat(parts, ignore_index=True)
    df = add_feature_aliases(df)
    print(f"[{split_name}] rows={len(df):,}, positives={int(df['fire_label'].sum()):,}")
    return df


# -----------------------------
# 6. 모델/평가
# -----------------------------

def make_one_hot_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=True)
    except TypeError:  # sklearn 구버전
        return OneHotEncoder(handle_unknown="ignore", sparse=True)


def build_model(num_cols: List[str], cat_cols: List[str], cfg: Config) -> Pipeline:
    numeric_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", make_one_hot_encoder()),
        ]
    )

    transformers = []
    if num_cols:
        transformers.append(("num", numeric_pipe, num_cols))
    if cat_cols:
        transformers.append(("cat", categorical_pipe, cat_cols))

    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
    clf = LogisticRegression(
        penalty="l2",
        solver="saga",
        C=1.0,
        class_weight=cfg.class_weight,
        max_iter=cfg.max_iter,
        n_jobs=-1,
        random_state=cfg.random_state,
    )
    return Pipeline(steps=[("preprocess", preprocessor), ("model", clf)])


def safe_auc(metric_func, y_true, score) -> float:
    y_arr = np.asarray(y_true)
    if len(np.unique(y_arr)) < 2:
        return float("nan")
    return float(metric_func(y_true, score))


def choose_threshold_by_fbeta(y_true, score, beta: float = 2.0) -> float:
    if len(np.unique(y_true)) < 2:
        return 0.5
    precision, recall, thresholds = precision_recall_curve(y_true, score)
    if len(thresholds) == 0:
        return 0.5
    beta2 = beta ** 2
    fbeta = (1 + beta2) * precision[:-1] * recall[:-1] / (beta2 * precision[:-1] + recall[:-1] + 1e-15)
    best_idx = int(np.nanargmax(fbeta))
    return float(thresholds[best_idx])


def binary_metrics(y_true, score, threshold: float) -> Dict[str, float]:
    pred = (np.asarray(score) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "roc_auc": safe_auc(roc_auc_score, y_true, score),
        "pr_auc": safe_auc(average_precision_score, y_true, score),
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f2": float(fbeta_score(y_true, pred, beta=2, zero_division=0)),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
        "positive_rate_pred": float(pred.mean()),
        "positive_rate_true": float(np.mean(y_true)),
    }


def daily_recall_lift_at_k(
    df: pd.DataFrame,
    y_col: str = "fire_label",
    score_col: str = "pred_prob",
    date_col: str = "date",
    ks: Sequence[int] = (100, 500, 1000),
) -> pd.DataFrame:
    rows = []
    tmp = df[[date_col, y_col, score_col]].copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col]).dt.normalize()

    for k in ks:
        day_rows = []
        for date, g in tmp.groupby(date_col, sort=True):
            pos_total = int(g[y_col].sum())
            if pos_total == 0:
                continue
            kk = min(int(k), len(g))
            top = g.nlargest(kk, score_col)
            top_pos = int(top[y_col].sum())
            base_rate = pos_total / len(g)
            precision_at_k = top_pos / kk if kk > 0 else 0.0
            recall_at_k = top_pos / pos_total if pos_total > 0 else np.nan
            lift_at_k = precision_at_k / base_rate if base_rate > 0 else np.nan
            day_rows.append(
                {
                    "date": date,
                    "k": k,
                    "n": len(g),
                    "pos_total": pos_total,
                    "top_pos": top_pos,
                    "recall_at_k": recall_at_k,
                    "precision_at_k": precision_at_k,
                    "lift_at_k": lift_at_k,
                }
            )
        if not day_rows:
            rows.append({"k": k, "days": 0, "mean_recall_at_k": np.nan, "weighted_recall_at_k": np.nan, "mean_lift_at_k": np.nan})
            continue
        d = pd.DataFrame(day_rows)
        rows.append(
            {
                "k": k,
                "days": int(len(d)),
                "mean_recall_at_k": float(d["recall_at_k"].mean()),
                "weighted_recall_at_k": float(d["top_pos"].sum() / d["pos_total"].sum()),
                "mean_precision_at_k": float(d["precision_at_k"].mean()),
                "mean_lift_at_k": float(d["lift_at_k"].mean()),
            }
        )
    return pd.DataFrame(rows)


def predict_proba(model: Pipeline, df: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    return model.predict_proba(df[list(features)])[:, 1]


def save_coefficients(model: Pipeline, out_path: str | Path) -> pd.DataFrame:
    pre = model.named_steps["preprocess"]
    clf = model.named_steps["model"]
    try:
        names = pre.get_feature_names_out()
    except Exception:
        names = np.array([f"feature_{i}" for i in range(clf.coef_.shape[1])])
    coef = pd.DataFrame({"feature": names, "coef": clf.coef_.ravel()})
    coef["abs_coef"] = coef["coef"].abs()
    coef = coef.sort_values("abs_coef", ascending=False)
    coef.to_csv(out_path, index=False, encoding="utf-8-sig")
    return coef


# -----------------------------
# 7. 전체 Test 2024 스트리밍 Top-K 평가
# -----------------------------

def evaluate_full_test_streaming(
    model: Pipeline,
    features: Sequence[str],
    cfg: Config,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
    out_dir: Path,
) -> pd.DataFrame:
    """샘플이 아니라 2024 전체 grid-date 모집단 기준으로 일자별 Top-K 평가."""
    records = []
    score_dir = ensure_dir(out_dir / "full_test_scores") if cfg.save_full_test_scores else None

    for year in cfg.test_years:
        for month in cfg.months:
            print(f"[full-test] score {year}-{month:02d}")
            month_df = load_monthly_grid(cfg.grid_date_master, year, month)
            if month_df.empty:
                continue
            month_df = month_df.merge(master_static, on="grid_id", how="left", suffixes=("", "_mg"))
            month_df["date"] = pd.to_datetime(month_df["date"]).dt.normalize()
            lab = labels[labels["date"].isin(month_df["date"].unique())]
            month_df = month_df.merge(lab[["date", "grid_id", "fire_label"]], on=["date", "grid_id"], how="left")
            month_df["fire_label"] = month_df["fire_label"].fillna(0).astype(np.int8)
            month_df = add_feature_aliases(month_df)
            month_df["pred_prob"] = predict_proba(model, month_df, features)

            # 월 단위 일자별 결과를 먼저 계산하고 records에 저장
            for date, g in month_df.groupby("date", sort=True):
                pos_total = int(g["fire_label"].sum())
                n = len(g)
                if pos_total == 0:
                    continue
                base_rate = pos_total / n
                for k in cfg.topk:
                    kk = min(k, n)
                    top = g.nlargest(kk, "pred_prob")
                    top_pos = int(top["fire_label"].sum())
                    precision_at_k = top_pos / kk
                    recall_at_k = top_pos / pos_total
                    lift_at_k = precision_at_k / base_rate if base_rate > 0 else np.nan
                    records.append(
                        {
                            "date": date,
                            "k": k,
                            "n": n,
                            "pos_total": pos_total,
                            "top_pos": top_pos,
                            "recall_at_k": recall_at_k,
                            "precision_at_k": precision_at_k,
                            "lift_at_k": lift_at_k,
                        }
                    )

            if score_dir is not None:
                keep = month_df[["date", "grid_id", "fire_label", "pred_prob"]].copy()
                keep.to_parquet(score_dir / f"scores_{year}_{month:02d}.parquet", index=False)

            del month_df

    daily = pd.DataFrame(records)
    if daily.empty:
        summary = pd.DataFrame(columns=["k", "days", "mean_recall_at_k", "weighted_recall_at_k", "mean_precision_at_k", "mean_lift_at_k"])
    else:
        summary = (
            daily.groupby("k")
            .apply(
                lambda d: pd.Series(
                    {
                        "days": int(len(d)),
                        "mean_recall_at_k": float(d["recall_at_k"].mean()),
                        "weighted_recall_at_k": float(d["top_pos"].sum() / d["pos_total"].sum()),
                        "mean_precision_at_k": float(d["precision_at_k"].mean()),
                        "mean_lift_at_k": float(d["lift_at_k"].mean()),
                    }
                )
            )
            .reset_index()
        )
        daily.to_csv(out_dir / "full_test_daily_topk_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out_dir / "full_test_topk_metrics.csv", index=False, encoding="utf-8-sig")
    return summary


# -----------------------------
# 8. main
# -----------------------------

def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Train Logistic Regression wildfire risk baseline")
    parser.add_argument("--master-grid", required=True, help="data/master_grid.parquet")
    parser.add_argument("--grid-date-master", required=True, help="data/grid_date_master")
    parser.add_argument("--fire-history", required=True, help="data/(공통데이터)산불발생이력데이터_forest_fire_all_4326.csv")
    parser.add_argument("--out-dir", default="outputs/logistic_regression")
    parser.add_argument("--label-cache", default=None, help="이미 생성한 labels parquet 경로. 없으면 새로 생성")
    parser.add_argument("--radius-m", type=int, default=1000, help="positive target 반경. 예: 500, 1000, 2000")
    parser.add_argument("--neg-ratio", type=int, default=50, help="negative 수 = positive 수 × neg_ratio")
    parser.add_argument("--feature-set", choices=sorted(FEATURE_SETS), default="compact_full")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--topk", nargs="+", type=int, default=[100, 500, 1000, 5000])
    parser.add_argument("--eval-full-test", action="store_true", help="2024 전체 모집단 기준 Top-K 평가 수행")
    parser.add_argument("--save-full-test-scores", action="store_true", help="전체 Test 점수 parquet 저장. 용량 커질 수 있음")
    args = parser.parse_args()
    return Config(
        master_grid=args.master_grid,
        grid_date_master=args.grid_date_master,
        fire_history=args.fire_history,
        out_dir=args.out_dir,
        label_cache=args.label_cache,
        radius_m=args.radius_m,
        neg_ratio=args.neg_ratio,
        feature_set=args.feature_set,
        random_state=args.random_state,
        topk=tuple(args.topk),
        eval_full_test=args.eval_full_test,
        save_full_test_scores=args.save_full_test_scores,
    )


def main() -> None:
    cfg = parse_args()
    out_dir = ensure_dir(cfg.out_dir)
    with open(out_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2, default=list)

    print("[1/6] load master_grid")
    master = load_master_grid(cfg.master_grid)

    # 모델에 붙일 static feature. grid_id/grid_x/grid_y 포함해서 merge 후 필요한 것만 사용.
    master_static = master.copy()

    print("[2/6] build/load labels")
    if cfg.label_cache and Path(cfg.label_cache).exists():
        labels = read_parquet_auto(cfg.label_cache)
        labels["date"] = pd.to_datetime(labels["date"]).dt.normalize()
    else:
        all_years = tuple(sorted(set(cfg.train_years + cfg.valid_years + cfg.test_years)))
        fire = load_fire_history(cfg.fire_history, years=all_years, months=cfg.months)
        labels = make_fire_grid_labels(master=master, fire=fire, radius_m=cfg.radius_m)
        label_path = out_dir / f"labels_radius_{cfg.radius_m}m.parquet"
        labels.to_parquet(label_path, index=False)
        print(f"labels saved: {label_path}")

    print(f"labels rows={len(labels):,}, positive grid-date={labels[['date','grid_id']].drop_duplicates().shape[0]:,}")

    print("[3/6] build sampled train/valid/test")
    train_df = build_split_dataset(cfg.train_years, cfg, master_static, labels, "train")
    valid_df = build_split_dataset(cfg.valid_years, cfg, master_static, labels, "valid")
    test_df = build_split_dataset(cfg.test_years, cfg, master_static, labels, "test_sample")

    features, num_cols, cat_cols = select_existing_features(train_df, cfg.feature_set)
    # valid/test에 없는 alias가 있으면 생성되지만, 컬럼 자체가 없는 경우는 결측 컬럼으로 추가
    for df in (valid_df, test_df):
        for c in features:
            if c not in df.columns:
                df[c] = np.nan

    print(f"feature_set={cfg.feature_set}")
    print(f"features({len(features)}): {features}")
    print(f"numeric={len(num_cols)}, categorical={len(cat_cols)}")
    pd.Series(features, name="feature").to_csv(out_dir / "used_features.csv", index=False, encoding="utf-8-sig")

    print("[4/6] train logistic regression")
    model = build_model(num_cols=num_cols, cat_cols=cat_cols, cfg=cfg)
    model.fit(train_df[features], train_df["fire_label"].astype(int))
    joblib.dump(model, out_dir / "logistic_regression.joblib")
    save_coefficients(model, out_dir / "logistic_coefficients.csv")

    print("[5/6] evaluate sampled validation/test")
    valid_df["pred_prob"] = predict_proba(model, valid_df, features)
    test_df["pred_prob"] = predict_proba(model, test_df, features)

    threshold = choose_threshold_by_fbeta(valid_df["fire_label"].astype(int), valid_df["pred_prob"], beta=2.0)
    valid_metrics = binary_metrics(valid_df["fire_label"].astype(int), valid_df["pred_prob"], threshold)
    test_metrics = binary_metrics(test_df["fire_label"].astype(int), test_df["pred_prob"], threshold)

    valid_topk = daily_recall_lift_at_k(valid_df, ks=cfg.topk)
    test_topk = daily_recall_lift_at_k(test_df, ks=cfg.topk)

    pd.DataFrame([valid_metrics]).to_csv(out_dir / "valid_threshold_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([test_metrics]).to_csv(out_dir / "test_sample_threshold_metrics.csv", index=False, encoding="utf-8-sig")
    valid_topk.to_csv(out_dir / "valid_topk_metrics.csv", index=False, encoding="utf-8-sig")
    test_topk.to_csv(out_dir / "test_sample_topk_metrics.csv", index=False, encoding="utf-8-sig")

    # 샘플 데이터 점수 저장. 추후 오류 분석용.
    valid_df[["date", "grid_id", "fire_label", "pred_prob"]].to_parquet(out_dir / "valid_scores_sample.parquet", index=False)
    test_df[["date", "grid_id", "fire_label", "pred_prob"]].to_parquet(out_dir / "test_scores_sample.parquet", index=False)

    print("\nValidation threshold metrics")
    print(json.dumps(valid_metrics, ensure_ascii=False, indent=2))
    print("\nTest sample threshold metrics")
    print(json.dumps(test_metrics, ensure_ascii=False, indent=2))
    print("\nValidation Top-K")
    print(valid_topk)
    print("\nTest sample Top-K")
    print(test_topk)

    if cfg.eval_full_test:
        print("[6/6] evaluate full 2024 population Top-K")
        full_summary = evaluate_full_test_streaming(
            model=model,
            features=features,
            cfg=cfg,
            master_static=master_static,
            labels=labels,
            out_dir=out_dir,
        )
        print("\nFull Test Top-K")
        print(full_summary)
    else:
        print("[6/6] skip full test. 전체 모집단 Top-K가 필요하면 --eval-full-test 옵션을 켜세요.")

    print(f"\n완료: {out_dir}")


if __name__ == "__main__":
    main()

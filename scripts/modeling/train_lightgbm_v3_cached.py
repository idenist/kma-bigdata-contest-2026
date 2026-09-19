#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
강원도 100m 격자 산불위험 LightGBM compact/full baseline.

전략 반영 사항
- 시간 기준 분할: Train 2020~2022 / Validation 2023 / Test 2024
- positive 전체 + random/hard negative sampling
- feature set: index_only / weather_only / compact_full / no_index
- hard negative: 산불 발생일의 고위험(P-FFDRI/FFDRI/DWI 상위) negative를 우선 포함
- 평가: PR-AUC, ROC-AUC, threshold 기반 F2/Recall/Precision, 일자별 Recall@Top-K, Lift@Top-K
- 2024 전체 모집단 Top-K 평가 옵션 지원
- 진행 로그/tqdm/소요 시간 출력

권장 실행 예시(cmd 한 줄)
python scripts/modeling/train_lightgbm.py --master-grid data/master_grid.parquet --grid-date-master output/final/final_feature_daily --fire-history data/산불발생이력.csv --out-dir outputs/lightgbm_1km_compact_pffdri_fulltest --radius-m 1000 --feature-set compact_full --neg-ratio 100 --sample-strategy hard --hard-negative-frac 0.7 --class-weight none --topk 100 500 1000 5000 --eval-full-test
"""

from __future__ import annotations

import argparse
import json
import math
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
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
from sklearn.preprocessing import OneHotEncoder

try:
    from tqdm.auto import tqdm
except Exception:  # noqa: BLE001
    def tqdm(iterable=None, **kwargs):  # type: ignore[override]
        return iterable if iterable is not None else range(0)

try:
    import lightgbm as lgb
    from lightgbm import LGBMClassifier
except ImportError as e:  # pragma: no cover
    lgb = None
    LGBMClassifier = None
    LIGHTGBM_IMPORT_ERROR = e
else:
    LIGHTGBM_IMPORT_ERROR = None


# -----------------------------
# 1. 설정
# -----------------------------

@dataclass
class Config:
    master_grid: str
    grid_date_master: str
    fire_history: str
    out_dir: str = "outputs/lightgbm"
    label_cache: Optional[str] = None
    sample_cache_dir: Optional[str] = None
    force_resample: bool = False
    radius_m: int = 1000
    neg_ratio: int = 50
    sample_strategy: str = "random"  # random / hard
    hard_negative_frac: float = 0.7
    hard_score_col: str = "pffdri"
    hard_pool_multiplier: int = 5
    feature_set: str = "compact_full"
    random_state: int = 42
    train_years: Tuple[int, ...] = (2020, 2021, 2022)
    valid_years: Tuple[int, ...] = (2023,)
    test_years: Tuple[int, ...] = (2024,)
    months: Tuple[int, ...] = (2, 3, 4, 5)
    topk: Tuple[int, ...] = (100, 500, 1000, 5000)
    eval_full_test: bool = False
    save_full_test_scores: bool = False
    strict_features: bool = False
    # LightGBM params
    n_estimators: int = 3000
    learning_rate: float = 0.03
    num_leaves: int = 63
    max_depth: int = -1
    min_child_samples: int = 80
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 1.0
    class_weight: str = "balanced"  # balanced / none
    early_stopping_rounds: int = 100
    log_period: int = 50


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


def log(msg: str) -> None:
    print(msg, flush=True)


class Timer:
    def __init__(self, label: str):
        self.label = label
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        log(self.label)
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.t0
        log(f"{self.label} done. elapsed={elapsed:.1f}s")


# -----------------------------
# 2. I/O 유틸
# -----------------------------

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
# 3. 라벨 생성
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
        raise ValueError("라벨 매핑 결과가 비었습니다. master_grid 좌표계 또는 산불 좌표 변환을 확인하세요.")

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

    if "fmi_n" not in df.columns and "forest_type_code" in df.columns:
        mapping = {1: 10.0, 2: 2.0, 3: 3.0}
        df["fmi_n"] = pd.to_numeric(df["forest_type_code"], errors="coerce").map(mapping).fillna(0.0)

    if "tmi_p" not in df.columns and {"aspect_sin", "aspect_cos", "elevation"}.issubset(df.columns):
        angle = (np.degrees(np.arctan2(df["aspect_sin"], df["aspect_cos"])) + 360) % 360
        aspect_score = np.select(
            [
                (angle >= 67.5) & (angle < 112.5),
                ((angle >= 337.5) | (angle < 22.5)) | ((angle >= 247.5) & (angle < 292.5)),
                ((angle >= 112.5) & (angle < 202.5)),
                ((angle >= 22.5) & (angle < 67.5)) | ((angle >= 292.5) & (angle < 337.5)),
                ((angle >= 202.5) & (angle < 247.5)),
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
        df["dwi_n"] = np.where(dwi.max(skipna=True) > 1.5, dwi / 10.0, dwi)

    return df


def select_existing_features(df: pd.DataFrame, feature_set: str, *, strict: bool, out_dir: Path) -> Tuple[List[str], List[str], List[str]]:
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"feature_set은 {sorted(FEATURE_SETS)} 중 하나여야 합니다.")

    requested = [c for c in FEATURE_SETS[feature_set] if c not in EXCLUDE_COLUMNS]
    missing = [c for c in requested if c not in df.columns]
    if missing:
        pd.Series(missing, name="missing_feature").to_csv(out_dir / "missing_features.csv", index=False, encoding="utf-8-sig")
        msg = f"[WARNING] feature_set={feature_set} 요청 feature 중 누락: {missing}"
        if strict:
            raise ValueError(msg + "\n--strict-features가 켜져 있어 중단합니다.")
        warnings.warn(msg, RuntimeWarning)

    candidates = [c for c in requested if c in df.columns]
    if not candidates:
        raise ValueError(f"선택된 feature_set={feature_set}에서 사용 가능한 feature가 없습니다.")

    num_cols = [c for c in candidates if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in candidates if c not in num_cols]
    return candidates, num_cols, cat_cols


# -----------------------------
# 5. 샘플 데이터 구축
# -----------------------------

def choose_hard_score_column(df: pd.DataFrame, preferred: str) -> Optional[str]:
    """Hard negative 선별에 사용할 위험도 컬럼을 고른다.

    preferred가 없으면 pffdri -> ffdri -> dwi -> dwi_n 순서로 fallback한다.
    """
    candidates = [preferred, "pffdri", "ffdri", "dwi", "dwi_n"]
    for col in candidates:
        if col and col in df.columns:
            return col
    return None


def sample_negative_random(neg: pd.DataFrame, n_neg: int, random_state: int) -> pd.DataFrame:
    if n_neg <= 0 or neg.empty:
        return neg.head(0).copy()
    n_neg = min(n_neg, len(neg))
    return neg.sample(n=n_neg, random_state=random_state).copy()


def sample_negative_hard(
    neg: pd.DataFrame,
    pos_dates: pd.Series,
    n_neg: int,
    random_state: int,
    *,
    hard_negative_frac: float,
    hard_score_col: str,
    hard_pool_multiplier: int,
) -> pd.DataFrame:
    """고위험 hard negative + random negative를 섞어 추출한다.

    hard negative는 기본적으로 산불 발생일과 같은 날짜의 negative 중
    pffdri/ffdri/dwi 등 위험도 점수가 높은 격자를 우선 선택한다.
    이렇게 해야 전체 모집단 Top-K에서 모델이 마주치는 '위험해 보이지만 산불은 안 난 격자'를
    학습 단계에도 포함할 수 있다.
    """
    if n_neg <= 0 or neg.empty:
        return neg.head(0).copy()

    n_neg = min(n_neg, len(neg))
    hard_negative_frac = float(np.clip(hard_negative_frac, 0.0, 1.0))
    n_hard = int(round(n_neg * hard_negative_frac))
    n_random = n_neg - n_hard

    same_day_neg = neg[neg["date"].isin(pd.to_datetime(pos_dates).dt.normalize().unique())]
    hard_base = same_day_neg if not same_day_neg.empty else neg

    score_col = choose_hard_score_column(hard_base, hard_score_col)
    hard_sample = hard_base.head(0).copy()

    if n_hard > 0 and score_col is not None:
        score = pd.to_numeric(hard_base[score_col], errors="coerce")
        hard_base_scored = hard_base.loc[score.notna()].copy()
        if not hard_base_scored.empty:
            # 무조건 최상위 n개만 쓰면 매번 같은 극단 격자만 들어갈 수 있으므로,
            # top pool을 넓게 잡고 그 안에서 랜덤 샘플링한다.
            pool_n = min(len(hard_base_scored), max(n_hard, n_hard * max(1, int(hard_pool_multiplier))))
            pool_idx = pd.to_numeric(hard_base_scored[score_col], errors="coerce").nlargest(pool_n).index
            hard_pool = hard_base_scored.loc[pool_idx]
            hard_sample = hard_pool.sample(n=min(n_hard, len(hard_pool)), random_state=random_state).copy()
            hard_sample["negative_sample_type"] = f"hard:{score_col}"

    # hard score 컬럼이 없거나 hard 후보가 부족하면 같은 날짜 random으로 보충
    if len(hard_sample) < n_hard:
        remaining_for_hard = hard_base.drop(index=hard_sample.index, errors="ignore")
        add_n = min(n_hard - len(hard_sample), len(remaining_for_hard))
        if add_n > 0:
            add_hard = remaining_for_hard.sample(n=add_n, random_state=random_state + 11).copy()
            add_hard["negative_sample_type"] = "hard:same_day_random"
            hard_sample = pd.concat([hard_sample, add_hard], axis=0)

    remaining = neg.drop(index=hard_sample.index, errors="ignore")
    random_sample = sample_negative_random(remaining, n_random, random_state + 23)
    if not random_sample.empty:
        random_sample["negative_sample_type"] = "random"

    sampled_neg = pd.concat([hard_sample, random_sample], axis=0)

    # 총량이 부족하면 전체 remaining에서 추가 보충
    if len(sampled_neg) < n_neg:
        remaining2 = neg.drop(index=sampled_neg.index, errors="ignore")
        add_n = min(n_neg - len(sampled_neg), len(remaining2))
        if add_n > 0:
            add_random = remaining2.sample(n=add_n, random_state=random_state + 37).copy()
            add_random["negative_sample_type"] = "random_fill"
            sampled_neg = pd.concat([sampled_neg, add_random], axis=0)

    return sampled_neg


def attach_labels_and_sample(
    month_df: pd.DataFrame,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
    cfg: Config,
    random_state: int,
) -> pd.DataFrame:
    if month_df.empty:
        return month_df

    month_df = month_df.merge(master_static, on="grid_id", how="left", suffixes=("", "_mg"))
    month_df["date"] = pd.to_datetime(month_df["date"]).dt.normalize()

    lab = labels[labels["date"].isin(month_df["date"].unique())]
    df = month_df.merge(lab, on=["date", "grid_id"], how="left")
    df["fire_label"] = df["fire_label"].fillna(0).astype(np.int8)

    pos = df[df["fire_label"] == 1].copy()
    neg = df[df["fire_label"] == 0].copy()

    if pos.empty:
        return pos.copy()

    n_neg = min(len(neg), len(pos) * int(cfg.neg_ratio))

    if str(cfg.sample_strategy).lower() == "hard":
        neg_sample = sample_negative_hard(
            neg=neg,
            pos_dates=pos["date"],
            n_neg=n_neg,
            random_state=random_state,
            hard_negative_frac=cfg.hard_negative_frac,
            hard_score_col=cfg.hard_score_col,
            hard_pool_multiplier=cfg.hard_pool_multiplier,
        )
    else:
        neg_sample = sample_negative_random(neg, n_neg=n_neg, random_state=random_state)
        if not neg_sample.empty:
            neg_sample["negative_sample_type"] = "random"

    pos["negative_sample_type"] = "positive"
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
    ym_pairs = [(year, month) for year in years for month in cfg.months]
    for year, month in tqdm(ym_pairs, desc=f"{split_name} months", unit="month"):
        log(f"[{split_name}] load/sample {year}-{month:02d}")
        month_df = load_monthly_grid(cfg.grid_date_master, year, month)
        if month_df.empty:
            continue
        sampled = attach_labels_and_sample(
            month_df=month_df,
            master_static=master_static,
            labels=labels,
            cfg=cfg,
            random_state=cfg.random_state + year * 100 + month,
        )
        if not sampled.empty:
            parts.append(sampled)
        del month_df, sampled

    if not parts:
        raise ValueError(f"{split_name} 데이터가 비었습니다. 기간/월/라벨 매핑을 확인하세요.")
    df = pd.concat(parts, ignore_index=True)
    df = add_feature_aliases(df)
    log(f"[{split_name}] rows={len(df):,}, positives={int(df['fire_label'].sum()):,}")
    if "negative_sample_type" in df.columns:
        log(f"[{split_name}] sample types: {df['negative_sample_type'].value_counts(dropna=False).to_dict()}")
    return df


def sample_cache_metadata(cfg: Config) -> Dict[str, object]:
    """샘플 데이터 재사용 안전성을 확인하기 위한 메타데이터.

    feature_set과 모델 하이퍼파라미터는 일부러 제외한다.
    같은 train/valid/test 샘플을 여러 feature_set과 여러 모델이 공유할 수 있게 하기 위함이다.
    """
    meta: Dict[str, object] = {
        "master_grid": str(Path(cfg.master_grid)),
        "grid_date_master": str(Path(cfg.grid_date_master)),
        "fire_history": str(Path(cfg.fire_history)),
        "radius_m": int(cfg.radius_m),
        "neg_ratio": int(cfg.neg_ratio),
        "sample_strategy": str(cfg.sample_strategy),
        "hard_negative_frac": float(cfg.hard_negative_frac),
        "hard_score_col": str(cfg.hard_score_col),
        "hard_pool_multiplier": int(cfg.hard_pool_multiplier),
        "random_state": int(cfg.random_state),
        "train_years": list(cfg.train_years),
        "valid_years": list(cfg.valid_years),
        "test_years": list(cfg.test_years),
        "months": list(cfg.months),
    }
    return meta


def sample_cache_paths(sample_cache_dir: str) -> Dict[str, Path]:
    base = ensure_dir(sample_cache_dir)
    return {
        "base": base,
        "meta": base / "sample_cache_config.json",
        "labels": base / "labels.parquet",
        "train": base / "train_sample.parquet",
        "valid": base / "valid_sample.parquet",
        "test": base / "test_sample.parquet",
    }


def _metadata_equal(a: Dict[str, object], b: Dict[str, object]) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)


def load_or_build_labels(cfg: Config, master: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    cache_label_path: Optional[Path] = None
    if cfg.sample_cache_dir:
        cache_label_path = sample_cache_paths(cfg.sample_cache_dir)["labels"]

    if cfg.label_cache and Path(cfg.label_cache).exists():
        labels = read_parquet_auto(cfg.label_cache)
        labels["date"] = pd.to_datetime(labels["date"]).dt.normalize()
        log(f"labels loaded from --label-cache: {cfg.label_cache}")
        return labels

    if cache_label_path is not None and cache_label_path.exists() and not cfg.force_resample:
        labels = read_parquet_auto(cache_label_path)
        labels["date"] = pd.to_datetime(labels["date"]).dt.normalize()
        log(f"labels loaded from sample cache: {cache_label_path}")
        return labels

    all_years = tuple(sorted(set(cfg.train_years + cfg.valid_years + cfg.test_years)))
    fire = load_fire_history(cfg.fire_history, years=all_years, months=cfg.months)
    labels = make_fire_grid_labels(master=master, fire=fire, radius_m=cfg.radius_m)

    label_path = out_dir / f"labels_radius_{cfg.radius_m}m.parquet"
    labels.to_parquet(label_path, index=False)
    log(f"labels saved: {label_path}")
    if cache_label_path is not None:
        labels.to_parquet(cache_label_path, index=False)
        log(f"labels saved to sample cache: {cache_label_path}")
    return labels


def load_or_build_sampled_datasets(
    cfg: Config,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not cfg.sample_cache_dir:
        return (
            build_split_dataset(cfg.train_years, cfg, master_static, labels, "train"),
            build_split_dataset(cfg.valid_years, cfg, master_static, labels, "valid"),
            build_split_dataset(cfg.test_years, cfg, master_static, labels, "test_sample"),
        )

    paths = sample_cache_paths(cfg.sample_cache_dir)
    current_meta = sample_cache_metadata(cfg)
    required_files = [paths["train"], paths["valid"], paths["test"], paths["meta"]]

    if all(p.exists() for p in required_files) and not cfg.force_resample:
        with open(paths["meta"], "r", encoding="utf-8") as f:
            saved_meta = json.load(f)
        if not _metadata_equal(current_meta, saved_meta):
            raise ValueError(
                "sample cache metadata가 현재 설정과 다릅니다.\n"
                f"cache_dir={paths['base']}\n"
                "같은 캐시를 쓰려면 radius/neg_ratio/sample_strategy/year/month/input 경로가 같아야 합니다.\n"
                "새 캐시 경로를 쓰거나 --force-resample을 붙여 다시 생성하세요."
            )
        log(f"sample cache hit: {paths['base']}")
        train_df = read_parquet_auto(paths["train"])
        valid_df = read_parquet_auto(paths["valid"])
        test_df = read_parquet_auto(paths["test"])
        for name, df in [("train", train_df), ("valid", valid_df), ("test_sample", test_df)]:
            df["date"] = pd.to_datetime(df["date"]).dt.normalize()
            log(f"[{name}] loaded from cache rows={len(df):,}, positives={int(df['fire_label'].sum()):,}")
        return train_df, valid_df, test_df

    log(f"sample cache miss/build: {paths['base']}")
    train_df = build_split_dataset(cfg.train_years, cfg, master_static, labels, "train")
    valid_df = build_split_dataset(cfg.valid_years, cfg, master_static, labels, "valid")
    test_df = build_split_dataset(cfg.test_years, cfg, master_static, labels, "test_sample")

    train_df.to_parquet(paths["train"], index=False)
    valid_df.to_parquet(paths["valid"], index=False)
    test_df.to_parquet(paths["test"], index=False)
    with open(paths["meta"], "w", encoding="utf-8") as f:
        json.dump(current_meta, f, ensure_ascii=False, indent=2, default=list)
    log(f"sample cache saved: {paths['base']}")
    return train_df, valid_df, test_df


# -----------------------------
# 6. 모델/평가
# -----------------------------

def make_one_hot_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=True)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=True)


def build_preprocessor(num_cols: List[str], cat_cols: List[str]) -> ColumnTransformer:
    numeric_pipe = Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))])
    categorical_pipe = Pipeline(steps=[("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", make_one_hot_encoder())])

    transformers = []
    if num_cols:
        transformers.append(("num", numeric_pipe, num_cols))
    if cat_cols:
        transformers.append(("cat", categorical_pipe, cat_cols))
    return ColumnTransformer(transformers=transformers, remainder="drop")


def build_lgbm_model(cfg: Config):
    if LGBMClassifier is None:
        raise ImportError(
            "lightgbm이 설치되어 있지 않습니다. 설치 후 다시 실행하세요: pip install lightgbm"
        ) from LIGHTGBM_IMPORT_ERROR

    class_weight = None if str(cfg.class_weight).lower() in {"none", "null", "false"} else cfg.class_weight
    return LGBMClassifier(
        objective="binary",
        boosting_type="gbdt",
        n_estimators=cfg.n_estimators,
        learning_rate=cfg.learning_rate,
        num_leaves=cfg.num_leaves,
        max_depth=cfg.max_depth,
        min_child_samples=cfg.min_child_samples,
        subsample=cfg.subsample,
        subsample_freq=1,
        colsample_bytree=cfg.colsample_bytree,
        reg_lambda=cfg.reg_lambda,
        class_weight=class_weight,
        random_state=cfg.random_state,
        n_jobs=-1,
        importance_type="gain",
        verbosity=-1,
    )


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
            rows.append({"k": k, "days": 0, "mean_recall_at_k": np.nan, "weighted_recall_at_k": np.nan, "mean_precision_at_k": np.nan, "mean_lift_at_k": np.nan})
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


def ensure_feature_columns(df: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
    for c in features:
        if c not in df.columns:
            df[c] = np.nan
    return df


def predict_proba(preprocessor: ColumnTransformer, model, df: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    df = ensure_feature_columns(df.copy(), features)
    x = preprocessor.transform(df[list(features)])
    return model.predict_proba(x)[:, 1]


def save_feature_importance(preprocessor: ColumnTransformer, model, out_path: str | Path) -> pd.DataFrame:
    try:
        names = preprocessor.get_feature_names_out()
    except Exception:
        names = np.array([f"feature_{i}" for i in range(len(model.feature_importances_))])
    imp = pd.DataFrame({"feature": names, "importance_gain": model.feature_importances_})
    imp = imp.sort_values("importance_gain", ascending=False)
    imp.to_csv(out_path, index=False, encoding="utf-8-sig")
    return imp


# -----------------------------
# 7. 전체 Test 2024 스트리밍 Top-K 평가
# -----------------------------

def evaluate_full_test_streaming(
    preprocessor: ColumnTransformer,
    model,
    features: Sequence[str],
    cfg: Config,
    master_static: pd.DataFrame,
    labels: pd.DataFrame,
    out_dir: Path,
) -> pd.DataFrame:
    records = []
    score_dir = ensure_dir(out_dir / "full_test_scores") if cfg.save_full_test_scores else None
    ym_pairs = [(year, month) for year in cfg.test_years for month in cfg.months]

    for year, month in tqdm(ym_pairs, desc="full-test months", unit="month"):
        log(f"[full-test] score {year}-{month:02d}")
        month_df = load_monthly_grid(cfg.grid_date_master, year, month)
        if month_df.empty:
            continue
        month_df = month_df.merge(master_static, on="grid_id", how="left", suffixes=("", "_mg"))
        month_df["date"] = pd.to_datetime(month_df["date"]).dt.normalize()
        lab = labels[labels["date"].isin(month_df["date"].unique())]
        month_df = month_df.merge(lab[["date", "grid_id", "fire_label"]], on=["date", "grid_id"], how="left")
        month_df["fire_label"] = month_df["fire_label"].fillna(0).astype(np.int8)
        month_df = add_feature_aliases(month_df)
        month_df = ensure_feature_columns(month_df, features)
        month_df["pred_prob"] = predict_proba(preprocessor, model, month_df, features)

        for date, g in tqdm(list(month_df.groupby("date", sort=True)), desc=f"{year}-{month:02d} days", unit="day", leave=False):
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
        rows = []
        for k, d in daily.groupby("k"):
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
        summary = pd.DataFrame(rows)
        daily.to_csv(out_dir / "full_test_daily_topk_detail.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out_dir / "full_test_topk_metrics.csv", index=False, encoding="utf-8-sig")
    return summary


# -----------------------------
# 8. main
# -----------------------------

def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Train LightGBM wildfire risk model")
    parser.add_argument("--master-grid", required=True, help="data/master_grid.parquet")
    parser.add_argument("--grid-date-master", required=True, help="output/final/final_feature_daily 권장")
    parser.add_argument("--fire-history", required=True, help="data/산불발생이력.csv")
    parser.add_argument("--out-dir", default="outputs/lightgbm")
    parser.add_argument("--label-cache", default=None, help="이미 생성한 labels parquet 경로. 없으면 새로 생성")
    parser.add_argument("--sample-cache-dir", default=None, help="샘플링된 train/valid/test parquet 캐시 폴더. feature_set/모델별 재사용 가능")
    parser.add_argument("--force-resample", action="store_true", help="sample cache가 있어도 다시 샘플링하여 덮어씀")
    parser.add_argument("--radius-m", type=int, default=1000)
    parser.add_argument("--neg-ratio", type=int, default=50)
    parser.add_argument("--sample-strategy", choices=["random", "hard"], default="random", help="negative sampling 방식")
    parser.add_argument("--hard-negative-frac", type=float, default=0.7, help="hard 전략에서 negative 중 고위험 후보 비율")
    parser.add_argument("--hard-score-col", default="pffdri", help="hard negative 정렬 기준 컬럼. 없으면 pffdri/ffdri/dwi/dwi_n 순서 fallback")
    parser.add_argument("--hard-pool-multiplier", type=int, default=5, help="hard 후보 pool 크기 = n_hard * multiplier")
    parser.add_argument("--feature-set", choices=sorted(FEATURE_SETS), default="compact_full")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--topk", nargs="+", type=int, default=[100, 500, 1000, 5000])
    parser.add_argument("--eval-full-test", action="store_true")
    parser.add_argument("--save-full-test-scores", action="store_true")
    parser.add_argument("--strict-features", action="store_true", help="feature_set 요청 컬럼이 누락되면 중단")

    parser.add_argument("--n-estimators", type=int, default=3000)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--num-leaves", type=int, default=63)
    parser.add_argument("--max-depth", type=int, default=-1)
    parser.add_argument("--min-child-samples", type=int, default=80)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--reg-lambda", type=float, default=1.0)
    parser.add_argument("--class-weight", choices=["balanced", "none"], default="balanced")
    parser.add_argument("--early-stopping-rounds", type=int, default=100)
    parser.add_argument("--log-period", type=int, default=50)

    args = parser.parse_args()
    return Config(
        master_grid=args.master_grid,
        grid_date_master=args.grid_date_master,
        fire_history=args.fire_history,
        out_dir=args.out_dir,
        label_cache=args.label_cache,
        sample_cache_dir=args.sample_cache_dir,
        force_resample=args.force_resample,
        radius_m=args.radius_m,
        neg_ratio=args.neg_ratio,
        sample_strategy=args.sample_strategy,
        hard_negative_frac=args.hard_negative_frac,
        hard_score_col=args.hard_score_col,
        hard_pool_multiplier=args.hard_pool_multiplier,
        feature_set=args.feature_set,
        random_state=args.random_state,
        topk=tuple(args.topk),
        eval_full_test=args.eval_full_test,
        save_full_test_scores=args.save_full_test_scores,
        strict_features=args.strict_features,
        n_estimators=args.n_estimators,
        learning_rate=args.learning_rate,
        num_leaves=args.num_leaves,
        max_depth=args.max_depth,
        min_child_samples=args.min_child_samples,
        subsample=args.subsample,
        colsample_bytree=args.colsample_bytree,
        reg_lambda=args.reg_lambda,
        class_weight=args.class_weight,
        early_stopping_rounds=args.early_stopping_rounds,
        log_period=args.log_period,
    )


def main() -> None:
    if LGBMClassifier is None:
        raise ImportError("lightgbm이 설치되어 있지 않습니다. 설치: pip install lightgbm") from LIGHTGBM_IMPORT_ERROR

    cfg = parse_args()
    out_dir = ensure_dir(cfg.out_dir)
    with open(out_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2, default=list)

    with Timer("[1/6] load master_grid"):
        master = load_master_grid(cfg.master_grid)
        master_static = master.copy()

    with Timer("[2/6] build/load labels"):
        labels = load_or_build_labels(cfg, master=master, out_dir=out_dir)
        log(f"labels rows={len(labels):,}, positive grid-date={labels[['date','grid_id']].drop_duplicates().shape[0]:,}")

    with Timer("[3/6] load/build sampled train/valid/test"):
        train_df, valid_df, test_df = load_or_build_sampled_datasets(cfg, master_static, labels)

        features, num_cols, cat_cols = select_existing_features(train_df, cfg.feature_set, strict=cfg.strict_features, out_dir=out_dir)
        for df in (valid_df, test_df):
            ensure_feature_columns(df, features)

        log(f"feature_set={cfg.feature_set}")
        log(f"features({len(features)}): {features}")
        log(f"numeric={len(num_cols)}, categorical={len(cat_cols)}")
        pd.Series(features, name="feature").to_csv(out_dir / "used_features.csv", index=False, encoding="utf-8-sig")

    with Timer("[4/6] preprocess + train LightGBM"):
        y_train = train_df["fire_label"].astype(int).to_numpy()
        y_valid = valid_df["fire_label"].astype(int).to_numpy()
        preprocessor = build_preprocessor(num_cols, cat_cols)

        log("[4/6] fit preprocessor...")
        x_train = preprocessor.fit_transform(train_df[list(features)])
        x_valid = preprocessor.transform(valid_df[list(features)])
        log(f"[4/6] transformed shapes: train={x_train.shape}, valid={x_valid.shape}")

        model = build_lgbm_model(cfg)
        callbacks = []
        if cfg.early_stopping_rounds and cfg.early_stopping_rounds > 0:
            callbacks.append(lgb.early_stopping(cfg.early_stopping_rounds, verbose=True))
        if cfg.log_period and cfg.log_period > 0:
            callbacks.append(lgb.log_evaluation(period=cfg.log_period))

        log("[4/6] fitting LightGBM...")
        model.fit(
            x_train,
            y_train,
            eval_set=[(x_valid, y_valid)],
            eval_metric=["auc", "average_precision"],
            callbacks=callbacks,
        )
        log(f"[4/6] best_iteration_={getattr(model, 'best_iteration_', None)}")
        joblib.dump(
            {
                "preprocessor": preprocessor,
                "model": model,
                "features": features,
                "num_cols": num_cols,
                "cat_cols": cat_cols,
                "config": asdict(cfg),
            },
            out_dir / "lightgbm_model.joblib",
        )
        save_feature_importance(preprocessor, model, out_dir / "lightgbm_feature_importance.csv")

    with Timer("[5/6] evaluate sampled validation/test"):
        valid_df["pred_prob"] = model.predict_proba(x_valid)[:, 1]
        x_test = preprocessor.transform(test_df[list(features)])
        test_df["pred_prob"] = model.predict_proba(x_test)[:, 1]

        threshold = choose_threshold_by_fbeta(valid_df["fire_label"].astype(int), valid_df["pred_prob"], beta=2.0)
        valid_metrics = binary_metrics(valid_df["fire_label"].astype(int), valid_df["pred_prob"], threshold)
        test_metrics = binary_metrics(test_df["fire_label"].astype(int), test_df["pred_prob"], threshold)

        valid_topk = daily_recall_lift_at_k(valid_df, ks=cfg.topk)
        test_topk = daily_recall_lift_at_k(test_df, ks=cfg.topk)

        pd.DataFrame([valid_metrics]).to_csv(out_dir / "valid_threshold_metrics.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([test_metrics]).to_csv(out_dir / "test_sample_threshold_metrics.csv", index=False, encoding="utf-8-sig")
        valid_topk.to_csv(out_dir / "valid_topk_metrics.csv", index=False, encoding="utf-8-sig")
        test_topk.to_csv(out_dir / "test_sample_topk_metrics.csv", index=False, encoding="utf-8-sig")
        valid_df[["date", "grid_id", "fire_label", "pred_prob"]].to_parquet(out_dir / "valid_scores_sample.parquet", index=False)
        test_df[["date", "grid_id", "fire_label", "pred_prob"]].to_parquet(out_dir / "test_scores_sample.parquet", index=False)

        log("\nValidation threshold metrics")
        log(json.dumps(valid_metrics, ensure_ascii=False, indent=2))
        log("\nTest sample threshold metrics")
        log(json.dumps(test_metrics, ensure_ascii=False, indent=2))
        log("\nValidation Top-K")
        log(str(valid_topk))
        log("\nTest sample Top-K")
        log(str(test_topk))

    if cfg.eval_full_test:
        with Timer("[6/6] evaluate full 2024 population Top-K"):
            full_summary = evaluate_full_test_streaming(
                preprocessor=preprocessor,
                model=model,
                features=features,
                cfg=cfg,
                master_static=master_static,
                labels=labels,
                out_dir=out_dir,
            )
            log("\nFull Test Top-K")
            log(str(full_summary))
    else:
        log("[6/6] skip full test. 전체 모집단 Top-K가 필요하면 --eval-full-test 옵션을 켜세요.")

    log(f"\n완료: {out_dir}")


if __name__ == "__main__":
    main()

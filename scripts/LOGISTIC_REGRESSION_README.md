# Logistic Regression 모델링 코드 사용 가이드

## 목적

`train_logistic_regression.py`는 산불위험 모델링 전략에 맞춘 선형 baseline 코드입니다.

- Train: 2020~2022
- Validation: 2023
- Test: 2024
- Positive: 산불 발생점 반경 내 100m 격자 전체
- Negative: positive 수의 N배 샘플링
- 모델: `StandardScaler + LogisticRegression(class_weight='balanced')`
- 평가: PR-AUC, ROC-AUC, F2-score, Recall, Precision, 일자별 Recall@Top-K, Lift@Top-K

## 권장 위치

프로젝트 루트 기준으로 다음 위치에 두는 것을 권장합니다.

```text
scripts/modeling/train_logistic_regression.py
```

## 실행 예시

```bash
python scripts/modeling/train_logistic_regression.py \
  --master-grid processed/master_grid.parquet \
  --grid-date-master processed/grid_date_master \
  --fire-history data/raw/산불발생이력.csv \
  --out-dir outputs/logistic_1km_compact \
  --radius-m 1000 \
  --feature-set compact_full \
  --neg-ratio 50 \
  --topk 100 500 1000 5000
```

2024년 전체 모집단 기준 Top-K 평가까지 수행하려면 다음 옵션을 추가합니다.

```bash
--eval-full-test
```

전체 점수 parquet까지 저장하려면 다음 옵션도 추가할 수 있지만, 용량이 커질 수 있습니다.

```bash
--save-full-test-scores
```

## target 반경 민감도 분석

500m, 1km, 2km target을 각각 돌려 비교합니다.

```bash
python scripts/modeling/train_logistic_regression.py \
  --master-grid processed/master_grid.parquet \
  --grid-date-master processed/grid_date_master \
  --fire-history data/raw/산불발생이력.csv \
  --out-dir outputs/logistic_500m_compact \
  --radius-m 500 \
  --feature-set compact_full \
  --neg-ratio 50

python scripts/modeling/train_logistic_regression.py \
  --master-grid processed/master_grid.parquet \
  --grid-date-master processed/grid_date_master \
  --fire-history data/raw/산불발생이력.csv \
  --out-dir outputs/logistic_1000m_compact \
  --radius-m 1000 \
  --feature-set compact_full \
  --neg-ratio 50

python scripts/modeling/train_logistic_regression.py \
  --master-grid processed/master_grid.parquet \
  --grid-date-master processed/grid_date_master \
  --fire-history data/raw/산불발생이력.csv \
  --out-dir outputs/logistic_2000m_compact \
  --radius-m 2000 \
  --feature-set compact_full \
  --neg-ratio 50
```

## feature_set 옵션

| 옵션 | 용도 |
|---|---|
| `index_only` | `dwi`, `ffdri`, `pffdri`만 사용 |
| `weather_only` | 기상 변수와 기상 파생지수 중심 |
| `compact_full` | 기상 + 임상 + 지형 + 전신주/접근성 + 종합지수 |
| `no_index` | `ffdri`, `pffdri` 제외 후 원천/파생 변수만 사용 |

실제 데이터에 없는 컬럼은 자동 제외됩니다. 예를 들어 아직 `pffdri`가 없다면 `compact_full`에서도 해당 컬럼은 빠집니다.

## 주요 산출물

| 파일 | 설명 |
|---|---|
| `config.json` | 실행 설정 |
| `labels_radius_1000m.parquet` | 산불 발생 반경 라벨 |
| `used_features.csv` | 실제 학습에 사용된 feature 목록 |
| `logistic_regression.joblib` | 학습된 모델 |
| `logistic_coefficients.csv` | 표준화 후 로지스틱 회귀 계수 |
| `valid_threshold_metrics.csv` | Validation threshold 평가 |
| `test_sample_threshold_metrics.csv` | Test sample threshold 평가 |
| `valid_topk_metrics.csv` | Validation Top-K 평가 |
| `test_sample_topk_metrics.csv` | Test sample Top-K 평가 |
| `full_test_topk_metrics.csv` | `--eval-full-test` 사용 시 2024 전체 모집단 Top-K 평가 |

## 주의 사항

1. `grid_date_master` 전체 로드는 메모리 부담이 크므로, 코드는 월별 필터 로드를 우선 시도합니다.
2. 산불 발생 이력 좌표를 EPSG:5179로 변환하기 위해 `pyproj`가 필요합니다.
3. 로지스틱 회귀의 확률값은 negative sampling의 영향을 받을 수 있으므로, 최종 보고서에서는 절대 확률보다 ranking score로 해석하는 편이 안전합니다.
4. 모델 간 공정 비교용 benchmark에서는 `--neg-ratio`, `--radius-m`, `--feature-set`, 기간 분할을 통일해야 합니다.

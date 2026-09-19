# 모델링 샘플 캐시 사용법

## 목적

`train/valid/test` 샘플링은 월별 `final_feature_daily`를 반복 로드하고, 산불 라벨을 붙인 뒤 negative sampling까지 수행하므로 시간이 오래 걸립니다.

캐시 버전 스크립트는 한 번 만든 샘플을 parquet로 저장해두고, 이후 feature set이나 모델을 바꿔도 같은 샘플을 재사용합니다.

## 생성되는 캐시 파일

예를 들어 `outputs/sample_cache_1km_hard_neg100`를 지정하면 다음 파일이 생성됩니다.

```text
outputs/sample_cache_1km_hard_neg100/
  labels.parquet
  train_sample.parquet
  valid_sample.parquet
  test_sample.parquet
  sample_cache_config.json
```

## 캐시 재사용 조건

다음 설정이 같으면 재사용 가능합니다.

```text
master_grid 경로
grid_date_master 경로
fire_history 경로
radius_m
neg_ratio
sample_strategy
hard_negative_frac
hard_score_col
hard_pool_multiplier
random_state
train/valid/test years
months
```

반대로 다음 설정은 바꿔도 같은 캐시를 재사용할 수 있습니다.

```text
feature_set
모델 종류
LightGBM 하이퍼파라미터
Logistic Regression max_iter
out_dir
Top-K 값
```

즉, `compact_full`, `index_only`, `weather_only`, `no_index`를 돌릴 때 같은 샘플 캐시를 재사용할 수 있습니다.

## LightGBM hard negative 캐시 실행 예시

처음 실행할 때는 캐시가 없으므로 샘플을 생성합니다.

```cmd
python scripts/modeling/train_lightgbm_v3_cached.py --master-grid data/master_grid.parquet --grid-date-master output/final/final_feature_daily --fire-history data/산불발생이력.csv --out-dir outputs/lightgbm_v3_1km_compact_hard_pffdri_fulltest --sample-cache-dir outputs/sample_cache_1km_hard_neg100 --radius-m 1000 --feature-set compact_full --neg-ratio 100 --sample-strategy hard --hard-negative-frac 0.7 --hard-score-col pffdri --class-weight none --topk 100 500 1000 5000 --eval-full-test
```

다음 feature set은 같은 캐시를 재사용합니다.

```cmd
python scripts/modeling/train_lightgbm_v3_cached.py --master-grid data/master_grid.parquet --grid-date-master output/final/final_feature_daily --fire-history data/산불발생이력.csv --out-dir outputs/lightgbm_v3_1km_index_hard_pffdri_fulltest --sample-cache-dir outputs/sample_cache_1km_hard_neg100 --radius-m 1000 --feature-set index_only --neg-ratio 100 --sample-strategy hard --hard-negative-frac 0.7 --hard-score-col pffdri --class-weight none --topk 100 500 1000 5000 --eval-full-test
```

## Logistic Regression hard negative 캐시 실행 예시

LightGBM과 같은 캐시를 재사용할 수 있습니다.

```cmd
python scripts/modeling/train_logistic_regression_v2_cached.py --master-grid data/master_grid.parquet --grid-date-master output/final/final_feature_daily --fire-history data/산불발생이력.csv --out-dir outputs/logistic_v2_1km_compact_hard_pffdri_fulltest --sample-cache-dir outputs/sample_cache_1km_hard_neg100 --radius-m 1000 --feature-set compact_full --neg-ratio 100 --sample-strategy hard --hard-negative-frac 0.7 --hard-score-col pffdri --topk 100 500 1000 5000 --eval-full-test
```

## 다시 샘플링하고 싶을 때

동일한 캐시 폴더를 덮어쓰려면 `--force-resample`을 붙입니다.

```cmd
python scripts/modeling/train_lightgbm_v3_cached.py --master-grid data/master_grid.parquet --grid-date-master output/final/final_feature_daily --fire-history data/산불발생이력.csv --out-dir outputs/lightgbm_resample_test --sample-cache-dir outputs/sample_cache_1km_hard_neg100 --force-resample --radius-m 1000 --feature-set compact_full --neg-ratio 100 --sample-strategy hard --hard-negative-frac 0.7 --hard-score-col pffdri --class-weight none --topk 100 500 1000 5000 --eval-full-test
```

## 주의점

`neg_ratio`, `sample_strategy`, `radius_m`을 바꿀 때는 같은 캐시를 쓰면 안 됩니다. 새 캐시 폴더를 만드는 것이 안전합니다.

예시:

```text
outputs/sample_cache_1km_hard_neg100
outputs/sample_cache_1km_random_neg50
outputs/sample_cache_2km_hard_neg100
```

# 코드 실행 및 산출물 정리

본 문서는 산불 전력설비 위험도 분석 프로젝트의 실행 코드와 산출물을 정리한 문서이다.  
모든 코드는 `scripts/` 폴더 안에 있다고 가정한다.

```text
project_root/
  scripts/
    run_all_features.py
    pffdri_common.py
    00_load_grid_date_master.py
    ...
    18_make_visualizations.py
  data/
    master_grid.parquet
    grid_date_master/
    test_hanjeon.csv
    산불발생이력.csv
  output/
```

모든 입력·출력 경로는 **프로젝트 루트 기준 상대경로**이다.  
실행 명령어는 PowerShell 기준으로 작성하였다.

---

## 0. 실행 전 준비

### 0.1 Python 패키지 설치

```powershell
pip install -r requirements.txt
```

### 0.2 중요 주의사항

`00~09`와 `run_all_features.py`는 `pffdri_common.py`를 import한다. 따라서 `scripts/` 폴더 안에 아래 파일이 함께 있어야 한다.

```text
scripts/pffdri_common.py
```

기존 코드가 `duckDB/` 폴더명을 기준으로 프로젝트 루트를 계산하도록 되어 있다면, `scripts/`로 폴더명을 바꿀 때 루트 경로 계산 함수도 함께 확인해야 한다. 코드 내부에서 프로젝트 루트를 `현재 스크립트 폴더의 상위 폴더`로 잡도록 되어 있어야 `data/`, `output/` 경로가 정상적으로 해석된다.

---

## 1. 전체 실행 흐름

```text
[원천 데이터]
data/master_grid.parquet
data/grid_date_master/**/*.parquet
data/test_hanjeon.csv
data/산불발생이력.csv

        ↓

[00~09] 최종 feature 생성
output/stage/*
output/final/final_feature_daily

        ↓

[10] 최종 feature 품질 확인
output/report/qc_exact

        ↓

[11] grid-date 위험도 산정
output/risk/grid_date_risk

        ↓

[12] 전신주별 위험도 및 decision 산정
output/risk/pole_risk_score.csv
output/risk/test_hanjeon_with_decision.csv

        ↓

[13] 시군구별 위험도 요약
output/report/risk/region_risk_summary.md

        ↓

[14] 산불 이력 기반 사후 검증
output/report/risk/fire_history_validation_summary.md

        ↓

[15] 가중치 민감도 분석
output/report/sensitivity/weight_oat_sensitivity_summary.md

        ↓

[16] 반경별 random-neighborhood 검증
output/report/validation/radius_validation_comparison_summary.md

        ↓

[17] 가중치별 반경 검증
output/report/validation/weight_radius_comparison_summary.md

        ↓

[18] 보고서·발표용 시각화
output/figures/*.png
```

---

## 2. 권장 전체 실행 명령어

### 2.1 최종 feature 생성

`run_all_features.py`를 사용하는 경우 다음 명령으로 `01~05`, `06`, `09` 단계를 일괄 실행한다.

```powershell
python scripts/run_all_features.py --mode parquet --without-target --overwrite-parquet
```

월별 진행률을 보면서 실행하는 기본 방식이다. 특정 월만 실행하려면 다음과 같이 실행한다.

```powershell
python scripts/run_all_features.py --mode parquet --months 2025-03 --without-target --overwrite-parquet
```

### 2.2 위험도 산정·검증·시각화

```powershell
python scripts/10_check_final_feature_quality.py --enable-progress

python scripts/11_build_grid_risk_score.py --overwrite --enable-progress

python scripts/12_build_pole_risk_score.py --overwrite --enable-progress

python scripts/13_summarize_risk_by_region.py --overwrite --enable-progress --write-pole-with-region

python scripts/14_validate_fire_history.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5

python scripts/15_weight_sensitivity_oat.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5

python scripts/16_compare_fire_validation_by_radius.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radii 0,1,3,5,10 --random-repeats 100

python scripts/17_compare_weight_models_by_radius.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radii 0,1,3,5,10 --random-repeats 100 --models baseline,exposure_plus,static_plus,weather_minus,static_minus,weather_plus,exposure_minus

python scripts/18_make_visualizations.py
```

---

# 3. 코드별 입력·출력·기능·실행 명령어

## 3.1 `run_all_features.py`

### 기능

`01~05`, `06`, `09` 단계를 순서대로 실행하는 통합 실행 스크립트이다.  
Parquet 모드에서는 중간 결과와 최종 feature를 `output/stage`, `output/final`에 저장한다.

### 입력

```text
data/grid_date_master/**/*.parquet
data/master_grid.parquet
```

선택적으로 산불 타깃 생성 시 다음 파일을 사용한다.

```text
data/(공통데이터)산불발생이력데이터_forest_fire_all_4326.csv
```

현재 프로젝트 파일명을 사용할 경우 `06_fire_target.py`를 별도로 실행하면서 `--fire-history data/산불발생이력.csv`를 지정하는 편이 안전하다.

### 출력

```text
output/stage/feat_weather_daily/
output/stage/feat_forest_static.parquet
output/stage/feat_terrain_ywi_daily/
output/stage/feat_power_access_static.parquet
output/stage/feat_pffdri_daily/
output/target/target_fire_daily_500m.parquet
output/target/target_fire_daily_1km.parquet
output/target/target_fire_daily_2km.parquet
output/final/final_feature_daily/
```

### 실행 명령어

```powershell
python scripts/run_all_features.py --mode parquet --without-target --overwrite-parquet
```

특정 월만 실행:

```powershell
python scripts/run_all_features.py --mode parquet --months 2025-03 --without-target --overwrite-parquet
```

---

## 3.2 `00_load_grid_date_master.py`

### 기능

`data/grid_date_master`에 저장된 월별 Parquet 원천 데이터를 DuckDB 테이블로 적재한다.  
DuckDB 테이블 방식으로 실행할 때 사용하는 단계이다. Parquet 모드만 사용할 경우 필수 실행 단계는 아니다.

### 입력

```text
data/grid_date_master/**/*.parquet
```

### 출력

```text
scripts/pffdri.duckdb
  └─ table: grid_date_master
```

또는 `--duckdb-path`로 지정한 DuckDB 파일 안의 `grid_date_master` 테이블이다.

### 실행 명령어

```powershell
python scripts/00_load_grid_date_master.py
```

옵션 지정:

```powershell
python scripts/00_load_grid_date_master.py --parquet-root data/grid_date_master --table grid_date_master --threads 4 --memory-limit 23GB
```

---

## 3.3 `01_weather_features.py`

### 기능

일별 기상 원천 데이터에서 유효습도, 강수 보정, DWI, 정규화 기상 피처 등 일별 기상 위험 피처를 생성한다.

### 입력

DuckDB 테이블 방식:

```text
table: grid_date_master
```

Parquet 방식:

```text
data/grid_date_master/**/*.parquet
```

### 출력

DuckDB 테이블 방식:

```text
table: feat_weather_daily
```

Parquet 방식:

```text
output/stage/feat_weather_daily/month=YYYY-MM/*.parquet
```

### 실행 명령어

```powershell
python scripts/01_weather_features.py --source-parquet "data/grid_date_master/**/*.parquet" --parquet-only --export-parquet-dir output/stage/feat_weather_daily --overwrite-parquet
```

---

## 3.4 `02_forest_features.py`

### 기능

`master_grid`의 산림 관련 정적 정보를 이용하여 산림연료위험도 계열 피처를 생성한다.

### 입력

```text
data/master_grid.parquet
```

### 출력

DuckDB 테이블 방식:

```text
table: feat_forest_static
```

Parquet 방식:

```text
output/stage/feat_forest_static.parquet
```

### 실행 명령어

```powershell
python scripts/02_forest_features.py --parquet-only --export-parquet output/stage/feat_forest_static.parquet --overwrite-parquet
```

---

## 3.5 `03_terrain_ywi_features.py`

### 기능

지형 정보와 일별 기상 피처를 결합하여 지형 위험도, 양간지풍 가중치, YWI, TMI 계열 피처를 생성한다.

### 입력

```text
data/master_grid.parquet
output/stage/feat_weather_daily/**/*.parquet
```

### 출력

DuckDB 테이블 방식:

```text
table: feat_terrain_ywi_daily
```

Parquet 방식:

```text
output/stage/feat_terrain_ywi_daily/month=YYYY-MM/*.parquet
```

### 실행 명령어

```powershell
python scripts/03_terrain_ywi_features.py --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" --parquet-only --export-parquet-dir output/stage/feat_terrain_ywi_daily --overwrite-parquet
```

---

## 3.6 `04_power_access_pei_features.py`

### 기능

전신주 수, 도로 접근성, 하천 거리 등 전력설비 및 접근성 관련 정적 피처를 생성한다.

### 입력

```text
data/master_grid.parquet
```

### 출력

DuckDB 테이블 방식:

```text
table: feat_power_access_static
```

Parquet 방식:

```text
output/stage/feat_power_access_static.parquet
```

### 실행 명령어

```powershell
python scripts/04_power_access_pei_features.py --parquet-only --export-parquet output/stage/feat_power_access_static.parquet --overwrite-parquet
```

---

## 3.7 `05_index_features.py`

### 기능

기상·산림·지형·전력설비 피처를 결합하여 기존 지수 계열 피처인 FFDRI, P-FFDRI 등을 생성한다.

주의: 최종 전신주 점검 우선순위 산정 단계에서는 11번 코드에서 DWI 기반 재산식화를 수행한다. 따라서 05번의 P-FFDRI는 최종 `pole_risk_score` 산정에 직접 사용되는 최종 점수는 아니다.

### 입력

```text
output/stage/feat_weather_daily/**/*.parquet
output/stage/feat_forest_static.parquet
output/stage/feat_terrain_ywi_daily/**/*.parquet
output/stage/feat_power_access_static.parquet
```

### 출력

DuckDB 테이블 방식:

```text
table: feat_pffdri_daily
```

Parquet 방식:

```text
output/stage/feat_pffdri_daily/month=YYYY-MM/*.parquet
```

### 실행 명령어

```powershell
python scripts/05_index_features.py --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" --forest-parquet output/stage/feat_forest_static.parquet --terrain-parquet "output/stage/feat_terrain_ywi_daily/**/*.parquet" --power-parquet output/stage/feat_power_access_static.parquet --parquet-only --export-parquet-dir output/stage/feat_pffdri_daily --overwrite-parquet
```

---

## 3.8 `06_fire_target.py`

### 기능

산불 발생 이력 CSV의 위·경도 좌표를 EPSG:5179 좌표계로 변환한 뒤, 500m, 1km, 2km 단위 산불 발생 타깃 파일을 생성한다.

### 입력

기본값:

```text
data/(공통데이터)산불발생이력데이터_forest_fire_all_4326.csv
data/master_grid.parquet
```

현재 프로젝트 파일명 사용 시:

```text
data/산불발생이력.csv
data/master_grid.parquet
```

### 출력

```text
output/target/target_fire_daily_500m.parquet
output/target/target_fire_daily_1km.parquet
output/target/target_fire_daily_2km.parquet
```

### 실행 명령어

현재 프로젝트 파일명 기준:

```powershell
python scripts/06_fire_target.py --fire-history data/산불발생이력.csv
```

기본 파일명 기준:

```powershell
python scripts/06_fire_target.py
```

---

## 3.9 `07_prepare_eda_dataset.py`

### 기능

최종 feature와 산불 타깃 파일을 결합해 EDA용 샘플 데이터셋을 만든다. 산불 발생 양성 건은 전체 포함하고, 음성은 샘플링한다.

### 입력

```text
output/final/final_feature_daily/**/*.parquet
output/target/target_fire_daily_500m.parquet
output/target/target_fire_daily_1km.parquet
output/target/target_fire_daily_2km.parquet
```

### 출력

```text
output/eda/eda_positive_500m.parquet
output/eda/eda_sample_500m.parquet
output/eda/eda_label_counts_500m.csv
output/eda/eda_monthly_counts_500m.csv

output/eda/eda_positive_1km.parquet
output/eda/eda_sample_1km.parquet
output/eda/eda_label_counts_1km.csv
output/eda/eda_monthly_counts_1km.csv

output/eda/eda_positive_2km.parquet
output/eda/eda_sample_2km.parquet
output/eda/eda_label_counts_2km.csv
output/eda/eda_monthly_counts_2km.csv
```

### 실행 명령어

```powershell
python scripts/07_prepare_eda_dataset.py --overwrite
```

---

## 3.10 `08_eda_visual_report.py`

### 기능

EDA 샘플 데이터를 이용하여 feature summary, label summary, Pearson correlation, 월별/격자별 요약, SVG 시각화 리포트를 생성한다.

### 입력

```text
output/eda/eda_sample_500m.parquet
output/eda/eda_sample_1km.parquet
output/eda/eda_sample_2km.parquet
output/eda/eda_label_counts_*.csv
output/eda/eda_monthly_counts_*.csv
```

### 출력

```text
output/report/eda/eda_report_500m.md
output/report/eda/feature_summary_500m.csv
output/report/eda/pearson_fire_label_500m.csv
output/report/eda/feature_corr_matrix_500m.csv
output/report/eda/grid_summary_500m.csv
output/report/eda/month_summary_500m.csv
output/report/eda/*.svg
```

500m, 1km, 2km 단위별로 같은 구조의 파일이 생성된다.

### 실행 명령어

```powershell
python scripts/08_eda_visual_report.py
```

---

## 3.11 `09_build_final_dataset.py`

### 기능

01~05 단계에서 생성한 기상·산림·지형·전력설비·기존 지수 피처를 `grid_id + date` 기준으로 병합하여 최종 일별 격자 feature dataset을 생성한다.

### 입력

```text
output/stage/feat_weather_daily/**/*.parquet
output/stage/feat_forest_static.parquet
output/stage/feat_terrain_ywi_daily/**/*.parquet
output/stage/feat_power_access_static.parquet
output/stage/feat_pffdri_daily/**/*.parquet
```

선택 입력:

```text
output/target/target_fire_daily_*.parquet
```

최종 위험도 산정용 feature를 만들 때는 산불 타깃 컬럼을 제외하기 위해 `--without-target`을 사용한다.

### 출력

```text
output/final/final_feature_daily/month=YYYY-MM/*.parquet
```

### 실행 명령어

```powershell
python scripts/09_build_final_dataset.py --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" --forest-parquet output/stage/feat_forest_static.parquet --terrain-parquet "output/stage/feat_terrain_ywi_daily/**/*.parquet" --power-parquet output/stage/feat_power_access_static.parquet --index-parquet "output/stage/feat_pffdri_daily/**/*.parquet" --without-target --parquet-only --export-parquet-dir output/final/final_feature_daily --overwrite-parquet
```

---

## 3.12 `10_check_final_feature_quality.py`

### 기능

`output/final/final_feature_daily`의 row 수, grid 수, date 수, 결측률, 날짜 범위, 월별 row 수, 중복 여부 등을 점검한다.

### 입력

```text
output/final/final_feature_daily/**/*.parquet
```

### 출력

```text
output/report/qc_exact/final_feature_quality_summary_exact.md
output/report/qc_exact/final_feature_overview_exact.csv
output/report/qc_exact/final_feature_nulls_exact.csv
output/report/qc_exact/final_feature_ranges_exact.csv
output/report/qc_exact/final_feature_monthly_exact.csv
output/report/qc_exact/final_feature_duplicate_summary.csv
output/report/qc_exact/final_feature_sample_1000.csv
output/report/qc_exact/final_feature_columns.txt
```

중복 점검과 샘플 저장은 옵션에 따라 생성된다.

### 실행 명령어

```powershell
python scripts/10_check_final_feature_quality.py --enable-progress
```

정확한 중복 점검과 샘플 저장까지 수행:

```powershell
python scripts/10_check_final_feature_quality.py --enable-progress --exact-duplicate-check --write-sample
```

---

## 3.13 `11_build_grid_risk_score.py`

### 기능

최종 feature를 입력으로 받아 `grid_id + date` 단위의 DWI 기반 최종 위험도 점수 `final_grid_risk`를 계산한다.

최종 산식:

```text
final_grid_risk
= 0.60 * dwi_pct
+ 0.25 * static_vulnerability
+ 0.15 * exposure_risk
```

정적 취약도:

```text
static_raw
= 0.400 * fmi_n
+ 0.350 * tmi_p
+ 0.100 * slope_pct
+ 0.075 * road_prox
+ 0.075 * river_far

static_vulnerability = percentile rank of static_raw over all grids
```

전력설비 노출도:

```text
exposure_risk = percentile rank of pei over all grids
```

### 입력

```text
output/final/final_feature_daily/**/*.parquet
```

### 출력

```text
output/risk/grid_date_risk/month=YYYY-MM/*.parquet
output/report/risk/risk_score_formula.md
output/report/risk/grid_date_risk_summary.csv
output/report/risk/grid_date_risk_summary.md
```

### 실행 명령어

```powershell
python scripts/11_build_grid_risk_score.py --overwrite --enable-progress
```

가중치 명시 실행:

```powershell
python scripts/11_build_grid_risk_score.py --overwrite --enable-progress --weather-weight 0.60 --static-weight 0.25 --exposure-weight 0.15
```

---

## 3.14 `12_build_pole_risk_score.py`

### 기능

전신주 좌표를 100m 격자에 매핑하고, 각 전신주가 속한 격자의 기간 위험도 요약값을 이용해 전신주별 최종 위험도 `pole_risk_score`와 점검 우선순위 `decision`을 산정한다.

전신주 위험도 산식:

```text
pole_risk_score
= 0.50 * max_daily_risk
+ 0.30 * mean_top5_daily_risk
+ 0.20 * high_risk_day_ratio
```

`decision=1`은 `pole_risk_score` 기준 상위 5% 전신주를 의미한다.

### 입력

```text
data/test_hanjeon.csv
data/master_grid.parquet
output/risk/grid_date_risk/**/*.parquet
```

### 출력

```text
output/risk/pole_grid_map.parquet
output/risk/grid_period_risk.parquet
output/risk/pole_risk_score.parquet
output/risk/pole_risk_score.csv
output/risk/test_hanjeon_with_decision.csv
output/report/risk/pole_risk_score_summary.csv
output/report/risk/pole_risk_score_summary.md
```

### 실행 명령어

```powershell
python scripts/12_build_pole_risk_score.py --overwrite --enable-progress
```

중간 산출물 재사용:

```powershell
python scripts/12_build_pole_risk_score.py --reuse-pole-grid-map --reuse-grid-period-risk --enable-progress
```

---

## 3.15 `13_summarize_risk_by_region.py`

### 기능

전신주별 위험도 결과를 행정구역별로 집계하여 지역별 위험 전신주 수, 평균 위험도, 상위 5% 전신주 비율 등을 요약한다.

### 입력

```text
data/master_grid.parquet
output/risk/pole_risk_score.parquet
```

### 출력

```text
output/report/risk/region_risk_summary.md
output/report/risk/region_risk_overall_summary.csv
output/report/risk/region_risk_summary_city_name.csv
output/report/risk/region_risk_top30_city_name.csv
output/risk/pole_risk_with_region.parquet
```

`pole_risk_with_region.parquet`는 `--write-pole-with-region` 옵션 사용 시 생성된다.

### 실행 명령어

```powershell
python scripts/13_summarize_risk_by_region.py --overwrite --enable-progress --write-pole-with-region
```

---

## 3.16 `14_validate_fire_history.py`

### 기능

2020~2024년 봄철 산불 이력을 최종 grid-date 위험도와 매칭하여 사후 검증을 수행한다. 검증은 exact grid 기준과 neighborhood 기준을 모두 산출한다.

- exact 기준: 산불 발생 좌표가 속한 정확한 100m 격자가 해당 날짜 상위 위험군에 포함되는지 확인
- neighborhood 기준: 산불 발생 좌표 주변 ±N격자 내에 상위 위험군 격자가 존재하는지 확인

### 입력

```text
data/산불발생이력.csv
data/master_grid.parquet
output/risk/grid_date_risk/**/*.parquet
```

### 출력

```text
output/validation/fire_history_mapped.parquet
output/validation/fire_history_unmatched.csv
output/validation/fire_history_validation_events.parquet
output/validation/fire_history_validation_events.csv

output/report/risk/fire_history_validation_summary.csv
output/report/risk/fire_history_validation_summary.md
output/report/risk/fire_history_validation_by_year.csv
output/report/risk/fire_history_validation_by_month.csv
output/report/risk/fire_history_validation_by_region.csv
output/report/risk/fire_history_validation_top_fire_events.csv
```

### 실행 명령어

```powershell
python scripts/14_validate_fire_history.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5
```

---

## 3.17 `15_weight_sensitivity_oat.py`

### 기능

최종 위험도 산식의 가중치가 특정 조합에 과도하게 의존하는지 확인하기 위해 One-at-a-time 방식의 가중치 민감도 분석을 수행한다.

후보 산식:

```text
candidate_final_grid_risk
= w_dwi * dwi_pct
+ w_static * static_vulnerability
+ w_exposure * exposure_risk
```

### 입력

```text
data/산불발생이력.csv
data/master_grid.parquet
output/risk/grid_date_risk/**/*.parquet
```

### 출력

```text
output/sensitivity/weight_oat/fire_history_mapped_for_sensitivity.parquet
output/sensitivity/weight_oat/fire_history_mapped_for_sensitivity.csv
output/sensitivity/weight_oat/weight_oat_sensitivity_events.parquet
output/sensitivity/weight_oat/weight_oat_sensitivity_events.csv

output/report/sensitivity/weight_oat_sensitivity_summary.md
output/report/sensitivity/weight_oat_sensitivity_summary.csv
output/report/sensitivity/weight_oat_sensitivity_by_year.csv
```

### 실행 명령어

```powershell
python scripts/15_weight_sensitivity_oat.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5
```

---

## 3.18 `16_compare_fire_validation_by_radius.py`

### 기능

Neighborhood 검증의 반경 효과를 확인하고, 단순 5%, 10%, 20% 기준 대신 동일 날짜·동일 반경 조건의 random-neighborhood baseline을 산출한다.

### 입력

```text
data/산불발생이력.csv
data/master_grid.parquet
output/risk/grid_date_risk/**/*.parquet
```

### 출력

```text
output/validation/radius_compare/fire_history_mapped_for_radius_compare.parquet
output/validation/radius_compare/fire_history_mapped_for_radius_compare.csv
output/validation/radius_compare/random_centers.parquet
output/validation/radius_compare/radius_actual_event_metrics.parquet
output/validation/radius_compare/radius_actual_event_metrics.csv
output/validation/radius_compare/radius_random_event_metrics.parquet
output/validation/radius_compare/radius_random_event_metrics.csv

output/report/validation/radius_validation_actual_summary.csv
output/report/validation/radius_validation_random_summary.csv
output/report/validation/radius_validation_random_by_rep.csv
output/report/validation/radius_validation_comparison.csv
output/report/validation/radius_validation_comparison_summary.md
```

### 실행 명령어

```powershell
python scripts/16_compare_fire_validation_by_radius.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radii 0,1,3,5,10 --random-repeats 100
```

---

## 3.19 `17_compare_weight_models_by_radius.py`

### 기능

가중치 민감도 분석과 반경별 random-neighborhood 검증을 결합하여, 후보 가중치별 실제-random 구분력을 비교한다.

### 입력

```text
data/산불발생이력.csv
data/master_grid.parquet
output/risk/grid_date_risk/**/*.parquet
```

### 출력

```text
output/validation/weight_radius_compare/fire_history_mapped_for_weight_radius.parquet
output/validation/weight_radius_compare/fire_history_mapped_for_weight_radius.csv
output/validation/weight_radius_compare/random_centers.parquet
output/validation/weight_radius_compare/weight_radius_actual_event_metrics.parquet
output/validation/weight_radius_compare/weight_radius_actual_event_metrics.csv
output/validation/weight_radius_compare/weight_radius_random_event_metrics.parquet
output/validation/weight_radius_compare/weight_radius_random_event_metrics.csv

output/report/validation/weight_radius_actual_summary.csv
output/report/validation/weight_radius_random_summary.csv
output/report/validation/weight_radius_random_by_rep.csv
output/report/validation/weight_radius_comparison.csv
output/report/validation/weight_radius_comparison_summary.md
```

### 실행 명령어

```powershell
python scripts/17_compare_weight_models_by_radius.py --overwrite --enable-progress --address-keywords 강원,강원도,강원특별자치도 --radii 0,1,3,5,10 --random-repeats 100 --models baseline,exposure_plus,static_plus,weather_minus,static_minus,weather_plus,exposure_minus
```

---

## 3.20 `18_make_visualizations.py`

### 기능

위험도 산정과 검증 결과를 보고서·발표에 바로 사용할 수 있는 PNG 그림으로 생성한다.

### 입력

```text
output/report/risk/region_risk_summary_city_name.csv
output/risk/pole_risk_score.csv
output/risk/pole_grid_map.parquet
output/report/validation/radius_validation_comparison.csv
output/report/validation/weight_radius_comparison.csv
```

### 출력

```text
output/figures/fig_region_decision_count_top15.png
output/figures/fig_region_decision_rate_top15.png
output/figures/fig_pole_risk_map_sample.png
output/figures/fig_decision1_pole_map.png
output/figures/fig_radius_actual_vs_random_top5.png
output/figures/fig_radius_top5_diff_vs_random.png
output/figures/fig_radius_top5_grid_ratio.png
output/figures/fig_weight_radius_actual_vs_random_top5.png
output/figures/fig_weight_radius_top5_diff.png
```

### 실행 명령어

```powershell
python scripts/18_make_visualizations.py
```

옵션 지정:

```powershell
python scripts/18_make_visualizations.py --top-n 15 --map-sample-n 120000 --weight-radius 5
```

---

# 4. 최종 산출물 확인 가이드

| 확인 목적 | 파일 |
|---|---|
| 최종 전신주 decision | `output/risk/test_hanjeon_with_decision.csv` |
| 전신주 위험도 상세 | `output/risk/pole_risk_score.csv` |
| 전신주 위험도 요약 | `output/report/risk/pole_risk_score_summary.md` |
| 지역별 위험도 요약 | `output/report/risk/region_risk_summary.md` |
| 산불 이력 검증 요약 | `output/report/risk/fire_history_validation_summary.md` |
| grid-date 위험도 산식 | `output/report/risk/risk_score_formula.md` |
| grid-date 위험도 요약 | `output/report/risk/grid_date_risk_summary.md` |
| 최종 feature 품질 점검 | `output/report/qc_exact/final_feature_quality_summary_exact.md` |
| 가중치 민감도 분석 | `output/report/sensitivity/weight_oat_sensitivity_summary.md` |
| 반경별 random-neighborhood 검증 | `output/report/validation/radius_validation_comparison_summary.md` |
| 가중치별 반경 검증 | `output/report/validation/weight_radius_comparison_summary.md` |
| 시각화 결과 | `output/figures/*.png` |

---

# 5. 제출용 폴더 정리 예시

```text
project_root/
  scripts/
    pffdri_common.py
    run_all_features.py
    00_load_grid_date_master.py
    01_weather_features.py
    02_forest_features.py
    03_terrain_ywi_features.py
    04_power_access_pei_features.py
    05_index_features.py
    06_fire_target.py
    07_prepare_eda_dataset.py
    08_eda_visual_report.py
    09_build_final_dataset.py
    10_check_final_feature_quality.py
    11_build_grid_risk_score.py
    12_build_pole_risk_score.py
    13_summarize_risk_by_region.py
    14_validate_fire_history.py
    15_weight_sensitivity_oat.py
    16_compare_fire_validation_by_radius.py
    17_compare_weight_models_by_radius.py
    18_make_visualizations.py
  requirements.txt
  README_CODE_WORKFLOW.md
```

이전 버전 코드가 남아 있으면 제출용 폴더에서는 혼동될 수 있다. 제출 전에는 최종 사용본만 `scripts/`에 남기고, 과거 버전은 `scripts/archive/`로 이동하는 것을 권장한다.

---

# 6. 해석상 주의사항

1. `final_grid_risk`와 `pole_risk_score`는 산불 발생 확률이 아니라 상대적 위험노출도 및 점검 우선순위 점수이다.
2. 산불 이력은 학습 타깃으로 사용하지 않고 사후 검증 자료로만 사용한다.
3. Neighborhood hit rate는 주변 여러 격자 중 하나라도 고위험 격자가 있으면 hit로 계산되므로 단순 5%, 10%, 20% 기준과 직접 비교하지 않는다.
4. 반경별 검증에서는 동일 날짜·동일 반경의 random-neighborhood baseline과 비교해야 한다.
5. 가중치 민감도 분석에서 단순 hit rate가 높은 후보가 있더라도 random baseline 대비 구분력과 도메인 해석 가능성을 함께 고려해야 한다.

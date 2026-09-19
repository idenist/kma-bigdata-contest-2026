# 산불 전력설비 위험도 분석 코드 작업물 정리

본 문서는 `duckDB/00~09` 피처 생성 파이프라인과 `duckDB/10~15` 위험도 산정·검증 코드를 기준으로, 각 코드의 입력 파일, 출력 파일, 수행 기능, 실행 명령어를 정리한 문서이다.

모든 경로는 **프로젝트 루트 기준 상대경로**로 작성하였다. 예를 들어 `data/master_grid.parquet`는 `<project_root>/data/master_grid.parquet`를 의미한다.

---

## 1. 전체 작업 흐름

```text
[원천 데이터]
data/master_grid.parquet
data/grid_date_master/**/*.parquet
data/test_hanjeon.csv
data/산불발생이력.csv

        ↓

[00~09] 일별 격자 feature 생성
output/stage/*
output/final/final_feature_daily

        ↓

[10] 최종 feature 품질 점검
output/report/qc_exact

        ↓

[11] DWI 기반 grid-date 위험도 산정
output/risk/grid_date_risk

        ↓

[12] 전신주별 위험도 산정
output/risk/pole_risk_score.parquet
output/risk/test_hanjeon_with_decision.csv

        ↓

[13] 지역별 위험도 요약
output/report/risk/region_risk_summary.md

        ↓

[14] 산불 이력 기반 사후 검증
output/validation
output/report/risk/fire_history_validation_summary.md

        ↓

[15] 가중치 민감도 분석
output/sensitivity/weight_oat
output/report/sensitivity/weight_oat_sensitivity_summary.md
```

---

## 2. 최종 사용본 기준 코드 목록

분석 과정 중 여러 버전이 생성되었으므로, 제출 또는 재현용으로는 아래 버전을 기준으로 관리한다.

| 구분 | 최종 사용본 | 비고 |
|---|---|---|
| 00~09 | `00_load_grid_date_master.py` ~ `09_build_final_dataset.py` | 피처 생성 파이프라인 |
| 10 | `10_check_final_feature_quality_v2.py` | 최종 feature 품질 점검 사용본 |
| 11 | `11_build_grid_risk_score_v4.py` | DWI 기반 최종 grid-date 위험도 산식 사용본 |
| 12 | `12_build_pole_risk_score.py` | 전신주별 위험도 산정 |
| 13 | `13_summarize_risk_by_region.py` | 지역별 위험도 요약 |
| 14 | `14_validate_fire_history_v3.py` | 산불 이력 사후 검증 사용본 |
| 15 | `15_weight_sensitivity_oat_v2.py` | 가중치 One-at-a-time 민감도 분석 사용본 |
| 15b | `15b_weight_sensitivity_make_report_from_events.py` | 15번 실행 중 CSV 저장 단계 오류 발생 시 복구용 |

권장 관리 방식은 최종 사용본을 아래처럼 이름 정리하는 것이다.

```text
10_check_final_feature_quality_v2.py  → 10_check_final_feature_quality.py
11_build_grid_risk_score_v4.py        → 11_build_grid_risk_score.py
14_validate_fire_history_v3.py        → 14_validate_fire_history.py
15_weight_sensitivity_oat_v2.py       → 15_weight_sensitivity_oat.py
```

---

## 3. 실행 전 준비

### 3.1 폴더 구조

```text
project_root/
  duckDB/
    00_load_grid_date_master.py
    ...
    15_weight_sensitivity_oat.py
    pffdri_common.py
  data/
    master_grid.parquet
    grid_date_master/
      month=2020-02/
        part.0.parquet
        ...
    test_hanjeon.csv
    산불발생이력.csv
  output/
```

### 3.2 설치

```powershell
pip install -r requirements.txt
```

### 3.3 공통 주의사항

- `00~09` 코드는 `pffdri_common.py`를 같은 `duckDB/` 폴더 안에서 import한다.
- 최종 분석 단위는 `grid_id + date`이다.
- `output/final/final_feature_daily`는 월별 partition 폴더로 저장되지만, 내부 row는 일별 격자 데이터이다.
- 최종 위험도 산정 단계인 `11_build_grid_risk_score.py`는 최종 점수 계산에 P-FFDRI를 직접 사용하지 않는다. 최종 산식은 `dwi_pct`, `static_vulnerability`, `exposure_risk`를 결합한다.
- `fire_label` 계열 컬럼은 모델 feature가 아니라 검증/EDA용 타깃 컬럼이므로 최종 위험도 산식에는 사용하지 않는다.

---

# 4. 00~09 피처 생성 파이프라인

## 00. `00_load_grid_date_master.py`

### 기능

`data/grid_date_master`에 저장된 월별 Parquet 원천 데이터를 DuckDB 테이블 `grid_date_master`로 적재한다. DuckDB 테이블 방식으로 실행할 때 사용하는 단계이다.

### 입력

```text
data/grid_date_master/**/*.parquet
```

### 출력

```text
duckDB/pffdri.duckdb
  └─ table: grid_date_master
```

### 실행 명령어

```powershell
python duckDB/00_load_grid_date_master.py
```

### 주요 옵션

```powershell
python duckDB/00_load_grid_date_master.py --parquet-root data/grid_date_master --table grid_date_master --threads 4 --memory-limit 23GB
```

---

## 01. `01_weather_features.py`

### 기능

일별 기상 원천 데이터에서 유효습도, 강수 보정, DWI, 정규화 DWI 등 기상 위험 피처를 생성한다.

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

DuckDB 테이블 방식:

```powershell
python duckDB/01_weather_features.py
```

Parquet 출력 방식:

```powershell
python duckDB/01_weather_features.py `
  --source-parquet "data/grid_date_master/**/*.parquet" `
  --parquet-only `
  --export-parquet-dir output/stage/feat_weather_daily `
  --overwrite-parquet
```

---

## 02. `02_forest_features.py`

### 기능

`master_grid`의 산림 관련 정적 정보를 이용해 산림연료위험도 계열 피처를 생성한다.

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
python duckDB/02_forest_features.py `
  --parquet-only `
  --export-parquet output/stage/feat_forest_static.parquet `
  --overwrite-parquet
```

---

## 03. `03_terrain_ywi_features.py`

### 기능

지형 정보와 일별 기상 피처를 결합하여 지형 위험도, 양간지풍 가중치, YWI, TMI 계열 피처를 생성한다.

### 입력

```text
data/master_grid.parquet
output/stage/feat_weather_daily/**/*.parquet
```

또는 DuckDB 테이블:

```text
table: feat_weather_daily
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
python duckDB/03_terrain_ywi_features.py `
  --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" `
  --parquet-only `
  --export-parquet-dir output/stage/feat_terrain_ywi_daily `
  --overwrite-parquet
```

---

## 04. `04_power_access_pei_features.py`

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
python duckDB/04_power_access_pei_features.py `
  --parquet-only `
  --export-parquet output/stage/feat_power_access_static.parquet `
  --overwrite-parquet
```

---

## 05. `05_index_features.py`

### 기능

01~04 단계에서 생성한 기상·산림·지형·전력설비 피처를 결합하여 FFDRI, P-FFDRI 등 기존 지수 계열 피처를 생성한다.

주의: 본 프로젝트의 최종 전신주 점검 우선순위 산정에서는 11번 단계에서 DWI 기반 재산식화를 수행하므로, 05번의 P-FFDRI는 최종 점수에 직접 사용되지 않는다.

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
python duckDB/05_index_features.py `
  --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" `
  --forest-parquet output/stage/feat_forest_static.parquet `
  --terrain-parquet "output/stage/feat_terrain_ywi_daily/**/*.parquet" `
  --power-parquet output/stage/feat_power_access_static.parquet `
  --parquet-only `
  --export-parquet-dir output/stage/feat_pffdri_daily `
  --overwrite-parquet
```

---

## 06. `06_fire_target.py`

### 기능

산불 발생 이력 CSV의 위·경도 좌표를 EPSG:5179 좌표계로 변환한 뒤, 500m, 1km, 2km 단위 산불 발생 타깃 파일을 생성한다.

### 입력

기본값:

```text
data/(공통데이터)산불발생이력데이터_forest_fire_all_4326.csv
data/master_grid.parquet
```

현재 프로젝트 파일명을 사용할 경우:

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

기본 파일명 사용:

```powershell
python duckDB/06_fire_target.py
```

현재 프로젝트의 산불 이력 파일명을 지정하는 경우:

```powershell
python duckDB/06_fire_target.py --fire-history data/산불발생이력.csv
```

---

## 07. `07_prepare_eda_dataset.py`

### 기능

최종 feature와 산불 타깃 파일을 결합해 EDA용 샘플 데이터셋을 만든다. 양성 산불 발생 건은 전체 포함하고, 음성은 샘플링한다.

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
...

output/eda/eda_positive_2km.parquet
output/eda/eda_sample_2km.parquet
...
```

### 실행 명령어

```powershell
python duckDB/07_prepare_eda_dataset.py --overwrite
```

---

## 08. `08_eda_visual_report.py`

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
python duckDB/08_eda_visual_report.py
```

---

## 09. `09_build_final_dataset.py`

### 기능

01~05에서 생성한 기상·산림·지형·전력설비·기존 지수 피처를 `grid_id + date` 기준으로 병합하여 최종 일별 격자 feature dataset을 생성한다.

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

본 프로젝트의 최종 위험도 산정용 feature를 만들 때는 `--without-target`을 사용하여 산불 타깃 컬럼을 제외한다.

### 출력

```text
output/final/final_feature_daily/month=YYYY-MM/*.parquet
```

### 실행 명령어

```powershell
python duckDB/09_build_final_dataset.py `
  --weather-parquet "output/stage/feat_weather_daily/**/*.parquet" `
  --forest-parquet output/stage/feat_forest_static.parquet `
  --terrain-parquet "output/stage/feat_terrain_ywi_daily/**/*.parquet" `
  --power-parquet output/stage/feat_power_access_static.parquet `
  --index-parquet "output/stage/feat_pffdri_daily/**/*.parquet" `
  --without-target `
  --parquet-only `
  --export-parquet-dir output/final/final_feature_daily `
  --overwrite-parquet
```

---

# 5. 10~15 위험도 산정·검증 파이프라인

## 10. `10_check_final_feature_quality.py`

### 기능

`output/final/final_feature_daily`의 row 수, grid 수, date 수, 결측률, 범위, 월별 row 수, 중복 여부 등을 점검한다.

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
```

옵션 사용 시 추가 출력:

```text
output/report/qc_exact/final_feature_duplicate_summary.csv
output/report/qc_exact/final_feature_sample_1000.csv
output/report/qc_exact/final_feature_columns.txt
```

### 실행 명령어

```powershell
python duckDB/10_check_final_feature_quality.py --enable-progress
```

정확한 중복 점검과 샘플 저장까지 수행:

```powershell
python duckDB/10_check_final_feature_quality.py `
  --enable-progress `
  --exact-duplicate-check `
  --write-sample
```

---

## 11. `11_build_grid_risk_score.py`

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

고위험 격자:

```text
daily_high_risk_flag = 1 if final_grid_risk is daily top 5%
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
python duckDB/11_build_grid_risk_score.py --overwrite --enable-progress
```

봄철 산불 위험기 2~5월만 명시적으로 지정:

```powershell
python duckDB/11_build_grid_risk_score.py `
  --overwrite `
  --enable-progress `
  --fire-season-months 2,3,4,5
```

가중치를 변경해 실험하는 경우:

```powershell
python duckDB/11_build_grid_risk_score.py `
  --overwrite `
  --enable-progress `
  --weather-weight 0.60 `
  --static-weight 0.25 `
  --exposure-weight 0.15
```

---

## 12. `12_build_pole_risk_score.py`

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
python duckDB/12_build_pole_risk_score.py --overwrite --enable-progress
```

중간 산출물을 재사용하는 경우:

```powershell
python duckDB/12_build_pole_risk_score.py `
  --reuse-pole-grid-map `
  --reuse-grid-period-risk `
  --enable-progress
```

---

## 13. `13_summarize_risk_by_region.py`

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
output/report/risk/region_risk_summary_*.csv
output/report/risk/region_risk_top*_*.csv
```

옵션 사용 시 추가 출력:

```text
output/risk/pole_risk_with_region.parquet
```

### 실행 명령어

```powershell
python duckDB/13_summarize_risk_by_region.py --overwrite --enable-progress
```

전신주별 지역 정보가 붙은 parquet도 저장:

```powershell
python duckDB/13_summarize_risk_by_region.py `
  --overwrite `
  --enable-progress `
  --write-pole-with-region
```

---

## 14. `14_validate_fire_history.py`

### 기능

2020~2024년 봄철 산불 이력을 최종 grid-date 위험도와 매칭하여 사후 검증을 수행한다. 검증은 exact grid 기준과 neighborhood 기준을 모두 산출한다.

- exact 기준: 산불 발생 좌표가 속한 정확한 100m 격자가 해당 날짜 상위 위험군에 포함되는지 확인
- neighborhood 기준: 산불 발생 좌표 주변 ±N격자 내에 상위 위험군 격자가 존재하는지 확인

주의: neighborhood 기준은 주변 여러 격자 중 하나라도 고위험 격자가 있으면 hit로 계산되므로, 단순 무작위 기준 5%, 10%, 20%와 직접 비교하지 않는다. 운영권역 내 고위험 환경 포착 여부를 보는 보조 지표로 해석한다.

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
output/report/risk/fire_history_validation_top_events.csv
```

### 실행 명령어

반경 ±5격자, 강원 주소 키워드 기준:

```powershell
python duckDB/14_validate_fire_history.py `
  --overwrite `
  --enable-progress `
  --address-keywords 강원,강원도,강원특별자치도 `
  --radius-cells 5
```

기본 반경 ±1격자로 빠르게 확인:

```powershell
python duckDB/14_validate_fire_history.py `
  --overwrite `
  --enable-progress `
  --address-keywords 강원,강원도,강원특별자치도
```

---

## 15. `15_weight_sensitivity_oat.py`

### 기능

최종 위험도 산식의 가중치가 특정 조합에 과도하게 의존하는지 확인하기 위해 One-at-a-time 방식의 가중치 민감도 분석을 수행한다.

기준 산식:

```text
candidate_final_grid_risk
= w_dwi * dwi_pct
+ w_static * static_vulnerability
+ w_exposure * exposure_risk
```

기본 후보 가중치:

```text
baseline       0.60 / 0.25 / 0.15
weather_plus   0.70 / 0.20 / 0.10
weather_minus  0.50 / 0.30 / 0.20
static_plus    0.50 / 0.35 / 0.15
static_minus   0.70 / 0.15 / 0.15
exposure_plus  0.50 / 0.25 / 0.25
exposure_minus 0.70 / 0.25 / 0.05
balanced       0.50 / 0.30 / 0.20
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
python duckDB/15_weight_sensitivity_oat.py `
  --overwrite `
  --enable-progress `
  --address-keywords 강원,강원도,강원특별자치도 `
  --radius-cells 5
```

### 참고

이 단계는 날짜별 후보 가중치별 percentile rank를 계산하기 때문에 시간이 오래 걸릴 수 있다. 진행률이 멈춰 보이더라도 `python.exe`의 CPU 시간이 증가하고 있으면 계산이 진행 중인 것이다.

---

## 15b. `15b_weight_sensitivity_make_report_from_events.py`

### 기능

15번 실행 중 `weight_oat_sensitivity_events.parquet`까지 생성된 뒤 CSV/MD 저장 단계에서 오류가 발생했을 때, 이미 만들어진 event-level parquet를 이용해 요약 리포트를 복구한다.

### 입력

```text
output/sensitivity/weight_oat/weight_oat_sensitivity_events.parquet
```

### 출력

```text
output/report/sensitivity/weight_oat_sensitivity_summary.md
output/report/sensitivity/weight_oat_sensitivity_summary.csv
output/report/sensitivity/weight_oat_sensitivity_by_year.csv
```

### 실행 명령어

```powershell
python duckDB/15b_weight_sensitivity_make_report_from_events.py
```

---

# 6. 권장 전체 실행 명령어

## 6.1 최종 feature 생성

`run_all_features.py`가 있는 경우, 아래 명령으로 01~05, 09를 월별 Parquet 방식으로 일괄 실행한다.

```powershell
python duckDB/run_all_features.py --mode parquet --without-target --overwrite-parquet
```

`run_all_features.py`가 없는 경우, 01 → 02 → 03 → 04 → 05 → 09를 위 개별 명령어 순서대로 실행한다.

## 6.2 품질 점검 및 위험도 산정

```powershell
python duckDB/10_check_final_feature_quality.py --enable-progress

python duckDB/11_build_grid_risk_score.py --overwrite --enable-progress

python duckDB/12_build_pole_risk_score.py --overwrite --enable-progress

python duckDB/13_summarize_risk_by_region.py --overwrite --enable-progress --write-pole-with-region
```

## 6.3 산불 이력 검증 및 민감도 분석

```powershell
python duckDB/14_validate_fire_history.py `
  --overwrite `
  --enable-progress `
  --address-keywords 강원,강원도,강원특별자치도 `
  --radius-cells 5

python duckDB/15_weight_sensitivity_oat.py `
  --overwrite `
  --enable-progress `
  --address-keywords 강원,강원도,강원특별자치도 `
  --radius-cells 5
```

---

# 7. 주요 산출물 확인 위치

| 목적 | 파일 |
|---|---|
| 최종 feature QC | `output/report/qc_exact/final_feature_quality_summary_exact.md` |
| grid-date 위험도 산식 | `output/report/risk/risk_score_formula.md` |
| grid-date 위험도 요약 | `output/report/risk/grid_date_risk_summary.md` |
| 전신주 위험도 요약 | `output/report/risk/pole_risk_score_summary.md` |
| 지역별 위험도 요약 | `output/report/risk/region_risk_summary.md` |
| 산불 이력 검증 요약 | `output/report/risk/fire_history_validation_summary.md` |
| 가중치 민감도 분석 | `output/report/sensitivity/weight_oat_sensitivity_summary.md` |
| 최종 전신주 decision 포함 CSV | `output/risk/test_hanjeon_with_decision.csv` |

---

# 8. 제출용 파일 정리 제안

제출 또는 공유용 폴더는 아래처럼 정리하는 것을 권장한다.

```text
duckDB/
  pffdri_common.py
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
  15b_weight_sensitivity_make_report_from_events.py

requirements.txt
README_CODE_WORKFLOW.md
```

버전이 붙은 예전 파일은 혼동을 줄 수 있으므로 별도 `archive/` 폴더로 이동하는 것을 권장한다.

```text
duckDB/archive/
  10_check_final_feature_quality_fast.py
  11_build_grid_risk_score.py
  11_build_grid_risk_score_v2.py
  11_build_grid_risk_score_v3.py
  14_validate_fire_history.py
  14_validate_fire_history_v2.py
  15_weight_sensitivity_oat.py
```

단, `archive/`로 옮기기 전에는 최종 사용본이 정상 실행되는지 확인해야 한다.

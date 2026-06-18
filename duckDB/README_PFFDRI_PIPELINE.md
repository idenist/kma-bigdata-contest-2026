# P-FFDRI Feature Pipeline

이 파이프라인은 산불 위험도 모델링용 P-FFDRI 피처를 생성합니다. 모든 경로는 **프로젝트 루트 기준 상대경로**로 설명합니다.

프로젝트 루트 예시:

```text
kma-bigdata-contest-2026/
```

핵심 데이터 단위는 `grid_id + date`입니다. 월별 폴더로 저장하더라도 내부 row는 일별 격자 데이터입니다.

## 경로 기준

코드는 현재 작업 디렉터리 기준 상대경로가 아니라, 각 스크립트의 `__file__`에서 계산한 프로젝트 루트 기준으로 경로를 해석합니다.

예를 들어 `data/grid_date_master`는 내부적으로 다음 위치로 해석됩니다.

```text
<project_root>/data/grid_date_master
```

따라서 프로젝트 폴더 전체를 다른 드라이브로 옮겨도, 폴더 구조만 유지되면 그대로 실행할 수 있습니다.

## 권장 실행 순서

디스크 공간이 부족하면 DuckDB 테이블 저장 방식보다 Parquet 모드를 사용합니다.

1. 최종 피처를 월별 Parquet로 생성합니다.

```powershell
python duckDB/run_all_features.py --mode parquet --without-target --overwrite-parquet
```

기본값으로 `--chunk-by month`가 적용되어 월별 진행률이 출력됩니다. 연 단위로 끊으려면:

```powershell
python duckDB/run_all_features.py --mode parquet --chunk-by year --without-target --overwrite-parquet
```

진행 출력 예시:

```text
[CHUNK] 3/64 month=2020-04 months=['2020-04']
[START] task 9/256 chunk=2020-04 01_weather_features.py
[DONE] task 9/256 chunk=2020-04 01_weather_features.py elapsed=2m 14s
```

`--overwrite-parquet`를 함께 쓰면 `run_all_features.py`가 시작 시 아래 partitioned output root를 한 번만 삭제합니다.

```text
output/stage/feat_weather_daily
output/stage/feat_terrain_ywi_daily
output/stage/feat_pffdri_daily
output/final/final_feature_daily
```

그 다음 월별 청크는 `COPY ... PARTITION_BY (month), APPEND` 방식으로 누적 저장합니다. 즉, 월별 루프 안에서 앞 월 결과를 지우지 않습니다.

2. 산불 일별 타깃을 500m, 1km, 2km 단위로 생성합니다.

```powershell
python duckDB/06_fire_target.py
```

`06_fire_target.py`는 `pyproj`가 필요합니다. 기본 Python 환경에 `pyproj`가 없으면, `pyproj`가 설치된 conda/python 환경으로 실행하세요.

3. EDA용 압축 샘플을 생성합니다.

```powershell
python duckDB/07_prepare_eda_dataset.py --overwrite
```

4. 전체 feature 기반 EDA 시각화/요약 리포트를 생성합니다.

```powershell
python duckDB/08_eda_visual_report.py
```

생성 위치:

```text
output/report/eda
```

`08_eda_visual_report.py`는 지표 컬럼만이 아니라 EDA 샘플에 포함된 기상, 임상, 지형, 접근성, 시간, `grid_id` 기반 요약을 함께 생성합니다. Pearson correlation, feature summary, label/group summary, SVG 차트가 포함됩니다.

5. 아래 산출물이 확인된 뒤 기존 DuckDB 삭제를 검토합니다.

```text
output/final/final_feature_daily
output/target/target_fire_daily_500m.parquet
output/target/target_fire_daily_1km.parquet
output/target/target_fire_daily_2km.parquet
output/eda
output/report/eda
```

삭제할 경우:

```powershell
Remove-Item -LiteralPath duckDB/pffdri.duckdb
```

주의: `output/final/final_feature_daily`가 아직 없으면 EDA 샘플은 만들 수 없습니다.

## 주요 실행 옵션

월별 일부만 실행:

```powershell
python duckDB/run_all_features.py --mode parquet --months 2025-03 --without-target --overwrite-parquet
```

샘플 검증만 빠르게 실행:

```powershell
python duckDB/run_all_features.py --mode parquet --parquet-root data/grid_date_master/month=2020-02/part.0.parquet --months 2020-02 --without-target --overwrite-parquet
```

기존 DuckDB 테이블 방식 실행:

```powershell
python duckDB/run_all_features.py
```

이 방식은 단순하지만 `duckDB/pffdri.duckdb` 파일이 매우 커질 수 있으므로 디스크 공간이 부족하면 권장하지 않습니다.

## 단계별 산출물

| 단계 | 스크립트 | 기본 산출물 | 설명 |
|---|---|---|---|
| 00 | `duckDB/00_load_grid_date_master.py` | `grid_date_master` | 원천 월별 Parquet를 DuckDB 테이블로 적재 |
| 01 | `duckDB/01_weather_features.py` | `output/stage/feat_weather_daily` | 유효습도, 강수 보정, DWI, DWI_n 등 일별 기상 피처 |
| 02 | `duckDB/02_forest_features.py` | `output/stage/feat_forest_static.parquet` | 산림 타입 기반 FMI, FMI_n 정적 피처 |
| 03 | `duckDB/03_terrain_ywi_features.py` | `output/stage/feat_terrain_ywi_daily` | 지형 피처, 양간지풍 가중치 `Rm`, YWI, TMI_P |
| 04 | `duckDB/04_power_access_pei_features.py` | `output/stage/feat_power_access_static.parquet` | 전신주, 도로/하천 접근성, PEI 정적 피처 |
| 05 | `duckDB/05_index_features.py` | `output/stage/feat_pffdri_daily` | FFDRI, P-FFDRI 계산 |
| 06 | `duckDB/06_fire_target.py` | `output/target/target_fire_daily_*.parquet` | 일별 산불 이력 격자 타깃 생성 |
| 07 | `duckDB/07_prepare_eda_dataset.py` | `output/eda` | EDA용 양성 전체 + 음성 샘플 생성 |
| 08 | `duckDB/08_eda_visual_report.py` | `output/report/eda` | 전체 feature 기반 EDA 표/시각화/Pearson 생성 |
| 99 | `duckDB/99_build_final_dataset.py` | `output/final/final_feature_daily` | 모델링/EDA용 최종 병합 데이터 |

01/03/05/99는 `--append-parquet` 옵션을 지원합니다. 보통 직접 쓸 필요는 없고, `run_all_features.py --mode parquet` 월별/연별 청크 실행 시 자동으로 전달됩니다.

## data 폴더 구성

```text
data/
  master_grid.parquet
  grid_date_master/
    month=2020-02/
      part.0.parquet
      part.1.parquet
    month=2020-03/
    ...
  stage/
  final/
  eda/
```

### `data/master_grid.parquet`

격자별 정적 메타데이터입니다.

- `grid_id` 기준 정적 피처 생성
- 산림 정보: `is_forest`, `forest_type_code`, `age_class_code`, `tree_height_code`
- 지형 정보: `elevation`, `slope`, `aspect_sin`, `aspect_cos`
- 행정구역 정보: `city_name`
- 접근성/전력 정보: `pole_count`, `nearest_road_dist`, `nearest_river_dist`

주의:

- `data/master_grid.parquet`에는 같은 `grid_id`가 여러 행으로 중복된 케이스가 있습니다.
- 02/04는 중복 값이 동일하므로 `SELECT DISTINCT`로 제거합니다.
- 03은 행정구역 경계 중복이 있어 `grid_id`별 1행으로 줄이고, 지역 위험 가중치 `Rm`은 `MAX(rm_candidate)`를 사용합니다.

### `data/grid_date_master`

기상 기반 일별 격자 원천 데이터입니다.

```text
data/grid_date_master/month=YYYY-MM/part.N.parquet
```

현재 확인된 범위:

```text
grid 수: 273,001
날짜 수: 722
전체 row 수: 197,106,722
날짜 범위: 2020-02-01 ~ 2025-05-31
월 범위: 2020-02 ~ 2025-05
```

월별 row 수:

```text
28일 월: 7,644,028 rows
29일 월: 7,917,029 rows
30일 월: 8,190,030 rows
31일 월: 8,463,031 rows
```

### `output/stage`

`--mode parquet` 실행 시 생성되는 중간 Parquet 산출물입니다.

```text
output/stage/feat_weather_daily/month=YYYY-MM/*.parquet
output/stage/feat_forest_static.parquet
output/stage/feat_terrain_ywi_daily/month=YYYY-MM/*.parquet
output/stage/feat_power_access_static.parquet
output/stage/feat_pffdri_daily/month=YYYY-MM/*.parquet
```

### `output/final`

최종 모델링/EDA 입력 데이터입니다.

```text
output/final/final_feature_daily/month=YYYY-MM/*.parquet
```

월별 폴더로 저장되지만 내부 row는 `grid_id + date` 일별 데이터입니다.

### `output/eda`

EDA 확인용 압축 샘플입니다.

```text
output/eda/eda_positive_500m.parquet
output/eda/eda_sample_500m.parquet
output/eda/eda_label_counts_500m.csv
output/eda/eda_monthly_counts_500m.csv
```

`eda_positive_*`는 산불 발생 양성 건 전체, `eda_sample_*`는 양성 전체와 음성 샘플을 합친 파일입니다.

### `output/report/eda`

전체 feature 기반 EDA 리포트입니다.

```text
output/report/eda/eda_report_500m.md
output/report/eda/feature_summary_500m.csv
output/report/eda/pearson_fire_label_500m.csv
output/report/eda/feature_corr_matrix_500m.csv
output/report/eda/grid_summary_500m.csv
output/report/eda/month_summary_500m.csv
output/report/eda/*.svg
```

500m, 1km, 2km 단위별로 같은 파일이 생성됩니다.

## Parquet 결과 읽기

DuckDB에서 바로 확인:

```sql
SELECT *
FROM read_parquet(
  'output/final/final_feature_daily/**/*.parquet',
  union_by_name=true,
  hive_partitioning=true
)
LIMIT 100;
```

Python에서 확인:

```python
import duckdb

con = duckdb.connect()
df = con.execute("""
SELECT month, COUNT(*) AS row_count, COUNT(DISTINCT grid_id) AS grid_count
FROM read_parquet(
  'output/final/final_feature_daily/**/*.parquet',
  union_by_name=true,
  hive_partitioning=true
)
GROUP BY month
ORDER BY month
""").df()
print(df)
```

CSV 전체 저장은 권장하지 않습니다. 최종 데이터가 매우 크기 때문에 원본은 `ZSTD Parquet`로 유지하고, 사람이 확인할 용도로만 샘플 CSV를 따로 만드는 편이 안전합니다.

## 주요 계산 메모

### 03 양간지풍 지역 가중치

`duckDB/03_terrain_ywi_features.py`는 `city_name` 기준으로 `Rm`을 계산합니다.

```text
동해안 양간지풍 지역: 고성군, 속초시, 양양군, 강릉시, 동해시, 삼척시 -> Rm = 1.0
내륙 영향 지역: 인제군, 양구군, 평창군, 정선군 -> Rm = 0.5
그 외 지역 -> Rm = 0.0
```

`ywi` 계산:

```text
ywi = Rm * Ws * Dr * Da
```

`tmi_p` 계산:

```text
tmi_p = 0.55 * tmi_base_n + 0.25 * slope_n + 0.20 * ywi
```

### 05 P-FFDRI 계산

```text
ffdri = ((7.0 * dwi) + (1.5 * fmi) + (1.5 * (tmi_base_n * 10.0))) * day_weight
pffdri = 100.0 * (0.55 * dwi_n + 0.10 * fmi_n + 0.15 * tmi_p + 0.20 * pei) * day_weight
```

## 용량 메모

샘플 기반 추정:

```text
현재 duckDB/pffdri.duckdb 파일: 약 131 GiB
최종 output/final/final_feature_daily ZSTD Parquet 예상: 약 18 GiB
05 output/stage/feat_pffdri_daily ZSTD Parquet 예상: 약 6 GiB
```

디스크 공간이 부족하면:

1. `--mode parquet`를 사용합니다.
2. `--overwrite-parquet`는 필요한 월 폴더를 지우고 다시 씁니다.
3. 기존 `duckDB/pffdri.duckdb` 삭제는 원천 `data/grid_date_master`, `data/master_grid.parquet`가 안전하게 남아 있는지 확인한 뒤 진행합니다.

## 주의 사항

- 기존 `duckDB/pffdri.duckdb` 안의 테이블은 코드 수정 전 결과일 수 있습니다.
- 02/03/04 중복 제거 수정사항을 반영하려면 해당 단계를 다시 실행해야 합니다.
- `--include-load`는 `--mode duckdb`에서만 의미가 있습니다. `--mode parquet`는 `data/grid_date_master`를 직접 읽습니다.
- `tmi_base_n`은 현재 `elevation/aspect` 기반 proxy입니다. 공식 FFDRI의 `elevation_index`, `aspect_index`가 확보되면 교체해야 합니다.
- `fire_label` 계열 target/검증용 컬럼은 모델 feature에서 제외해야 합니다.

## 06 일별 산불 이력 격자 타깃

`duckDB/06_fire_target.py`는 산불 발생 이력 CSV의 `longitude`, `latitude`를 EPSG:4326에서 EPSG:5179로 변환한 뒤, 기존 `data/master_grid.parquet`의 `grid_id`와 매핑 가능한 건만 남깁니다.

기본 실행 범위는 강원도 2020-02-01~2024-12-31이며, 500m, 1km, 2km 단위 타깃 파일을 한 번에 생성합니다.

기본 산출물:

```text
output/target/target_fire_daily_500m.parquet
output/target/target_fire_daily_1km.parquet
output/target/target_fire_daily_2km.parquet
```

컬럼 구조:

- `date`: 산불 발생일
- `grid_id`: 기존 100m `data/master_grid.parquet`의 격자 ID
- `target_grid_id`: EPSG:5179 기준 500m/1km/2km 격자 ID
- `target_grid_size_m`, `target_grid_x`, `target_grid_y`: 타깃 격자 크기와 인덱스
- `fire_label`, `fire_count`: 산불 발생 라벨과 같은 날짜/타깃 격자 내 발생 건수
- `fire_objt_ids`, `fire_addresses`: 원본 산불 이력 식별자와 주소
- `event_lon_mean`, `event_lat_mean`, `event_x5179_mean`, `event_y5179_mean`: 타깃 격자 내 발생 지점 평균 좌표

주의: 이 파일은 전체 날짜×전체 격자를 만들지 않고, 실제 산불 발생 이력이 기존 `grid_id`와 매핑되는 양성 건만 저장합니다. 모델 검증 시에는 `date + grid_id`로 최종 피처와 조인하면 됩니다.

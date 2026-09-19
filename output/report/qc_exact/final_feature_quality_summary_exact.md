# 1단계 기존 전처리 산출물 품질 확인 결과 - EXACT v2

## 입력 경로
- final feature: `output/final/final_feature_daily/**/*.parquet`
- output dir: `output/report/qc_exact`

## 전체 요약
- row_count: `197106722`
- exact_grid_count: `273001`
- exact_date_count: `722`
- expected_panel_rows: `197106722`
- panel_row_diff: `0`
- date range: `2020-02-01` ~ `2025-05-31`

## P-FFDRI 유효 데이터 요약
- pffdri_null_count: `15162`
- pffdri_null_grid_count: `21`
- pffdri_null_date_count: `722`
- pffdri_valid_row_count: `197091560`
- pffdri_valid_grid_count: `272980`
- pffdri_valid_date_count: `722`
- pffdri_valid_expected_rows: `197091560`
- pffdri_valid_panel_row_diff: `0`

## 해석
- 이전 FAST 리포트의 `approx_grid_count`, `approx_date_count`는 `approx_count_distinct` 기반 근사값이므로 최종 보고서 수치로 사용하지 않습니다.
- 본 v2 리포트의 `exact_grid_count`, `exact_date_count`를 최종 보고서 수치로 사용합니다.
- `pffdri` 결측은 위험도 산정 단계에서 제외됩니다.

## PASS
- grid_id/date 결측이 없습니다.
- 원본 feature가 exact_grid_count × exact_date_count = row_count 구조입니다. (273,001 × 722 = 197,106,722)
- pffdri 유효 데이터가 균형 패널 구조입니다. (272,980 × 722 = 197,091,560)
- pffdri min/max가 0~100 범위 안에 있습니다.
- fire_label이 포함되지 않은 final feature입니다.

## WARNING / 확인 필요
- 중요 컬럼 누락: slope_pct
- pffdri 결측 존재: 15,162 rows, 21 grids
- 정확한 중복 key 검사는 생략했습니다. 필요 시 --exact-duplicate-check로 별도 실행하세요.

## 생성 파일
- `final_feature_overview_exact.csv`
- `final_feature_columns.txt`
- `final_feature_nulls_exact.csv`
- `final_feature_ranges_exact.csv`
- `final_feature_monthly_exact.csv`는 `--skip-monthly` 미사용 시 생성
- `final_feature_duplicate_summary.csv`는 `--exact-duplicate-check` 사용 시 생성
- `final_feature_sample_1000.csv`는 `--write-sample` 사용 시 생성

## 다음 단계
- 이 리포트에서 정확한 grid/date/row 구조를 확인한 뒤 `0623_결과보고서_v2.md`의 QC 수치를 정정합니다.
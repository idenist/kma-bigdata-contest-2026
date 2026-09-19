# 1단계 기존 전처리 산출물 품질 확인 결과 - FAST

## 입력 경로
- final feature: `output/final/final_feature_daily/**/*.parquet`
- output dir: `output/report/qc_fast`

## 전체 요약
- row_count: `197106722`
- approx_grid_count: `254783`
- approx_date_count: `751`
- date range: `2020-02-01` ~ `2025-05-31`

## PASS
- 위험도 산정 후보 중요 컬럼이 모두 존재합니다.
- grid_id/date 결측이 없습니다.
- pffdri min/max 기준 0~100 범위 안에 있습니다.
- fire_label이 포함되지 않은 final feature입니다.

## WARNING / 확인 필요
- pffdri 결측 존재: 15162
- 정확한 중복 key 검사는 생략했습니다. 필요 시 --exact-duplicate-check로 별도 실행하세요.

## 생성 파일
- `final_feature_overview.csv`
- `final_feature_columns.txt`
- `final_feature_nulls_fast.csv`
- `final_feature_ranges_fast.csv`
- `final_feature_sample_1000.csv`
- `final_feature_duplicate_summary.csv`는 `--exact-duplicate-check` 사용 시 생성
- `final_feature_monthly_fast.csv`는 `--monthly` 사용 시 생성

## 다음 단계
- 이 FAST 점검에서 핵심 컬럼과 결측/범위에 큰 문제가 없으면 `build_grid_risk_score.py` 작성으로 진행합니다.
- 정확한 중복 key 검사는 시간이 오래 걸릴 수 있으므로, 최종 제출 직전에 별도 실행하는 것을 권장합니다.
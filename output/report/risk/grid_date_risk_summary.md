# Grid-Date Risk Score 생성 결과 - DWI 기반 분리형 v4

## 입력
- final feature: `output/final/final_feature_daily/**/*.parquet`
- date filter: `None` ~ `None`
- fire season months: `2,3,4,5`
- fire-history validation years: `2020~2024`

## 산식
```text
final_grid_risk = 0.60 * dwi_pct + 0.25 * static_vulnerability + 0.15 * exposure_risk
```

## 출력
- grid-date risk parquet: `output/risk/grid_date_risk`
- formula document: `output/report/risk/risk_score_formula.md`
- summary csv: `output/report/risk/grid_date_risk_summary.csv`

## 요약
- row_count: `197106722`
- exact_grid_count: `273001`
- exact_date_count: `722`
- min_date: `2020-02-01 00:00:00`
- max_date: `2025-05-31 00:00:00`
- min_final_grid_risk: `0.0`
- avg_final_grid_risk: `0.40978588657557896`
- max_final_grid_risk: `0.9974452380952381`
- high_risk_row_count: `9855296.0`
- high_risk_row_rate: `0.04999979655691296`
- min_dwi: `0.1`
- avg_dwi: `3.306238958239936`
- max_dwi: `10.0`

## 해석
- 본 결과는 P-FFDRI를 최종 산식에서 제외한 DWI 기반 분리형 위험도 산정 결과이다.
- `exact_grid_count × exact_date_count`가 `row_count`와 일치하는지 확인한다.
- `high_risk_row_rate`가 설정한 상위 위험 비율과 일치하는지 확인한다.

## 다음 단계
1. `12_build_pole_risk_score.py --overwrite`를 실행하여 전신주별 위험도와 decision을 새로 산출한다.
2. `13_summarize_risk_by_region.py --overwrite`를 실행하여 지역별 위험도 요약을 새로 산출한다.
3. `14_validate_fire_history_v2.py --overwrite --address-keywords 강원,강원도,강원특별자치도 --radius-cells 5`를 실행하여 산불 이력 검증을 새로 수행한다.
4. 기존 P-FFDRI 기반 결과와 DWI 기반 결과의 검증 지표를 비교한다.
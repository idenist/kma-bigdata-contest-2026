# 가중치 One-at-a-time 민감도 분석 결과

## 목적

본 분석은 DWI 기반 최종 위험도 산식의 가중치가 특정 조합에만 과도하게 의존하는지 확인하기 위한 민감도 분석이다.
기존 실행에서 이미 생성된 event-level parquet를 사용하여 요약 결과를 복구하였다.

## 기준 산식

```text
candidate_final_grid_risk = w_dwi * dwi_pct + w_static * static_vulnerability + w_exposure * exposure_risk
```

## 입력
- event-level parquet: `C:\SKN projects\weather\output\sensitivity\weight_oat\weight_oat_sensitivity_events.parquet`

## 후보 가중치별 검증 요약

| model_name | w_dwi | w_static | w_exposure | description | validation_event_count | exact_risk_available_count | neighbor_risk_available_count | exact_top5_hit_rate | exact_top10_hit_rate | exact_top20_hit_rate | avg_neighbor_max_daily_risk_pct | neighbor_top5_hit_rate | neighbor_top10_hit_rate | neighbor_top20_hit_rate | delta_neighbor_top5_hit_rate_vs_baseline | delta_neighbor_top10_hit_rate_vs_baseline | delta_neighbor_top20_hit_rate_vs_baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 0.6 | 0.25 | 0.15 | 현재 기준안 | 196 | 43 | 147 | 0.116279 | 0.209302 | 0.27907 | 0.804963 | 0.340136 | 0.455782 | 0.598639 | 0 | 0 | 0 |
| exposure_plus | 0.5 | 0.25 | 0.25 | 전력설비 노출도 비중 강화 | 196 | 43 | 147 | 0.116279 | 0.209302 | 0.255814 | 0.849027 | 0.387755 | 0.517007 | 0.659864 | 0.047619 | 0.0612245 | 0.0612245 |
| balanced | 0.5 | 0.3 | 0.2 | 균형형 참고안 | 196 | 43 | 147 | 0.116279 | 0.209302 | 0.27907 | 0.84966 | 0.37415 | 0.52381 | 0.659864 | 0.0340136 | 0.0680272 | 0.0612245 |
| weather_minus | 0.5 | 0.3 | 0.2 | 기상위험 비중 축소 | 196 | 43 | 147 | 0.116279 | 0.209302 | 0.27907 | 0.84966 | 0.37415 | 0.52381 | 0.659864 | 0.0340136 | 0.0680272 | 0.0612245 |
| static_plus | 0.5 | 0.35 | 0.15 | 정적 취약도 비중 강화 | 196 | 43 | 147 | 0.139535 | 0.209302 | 0.302326 | 0.850154 | 0.37415 | 0.52381 | 0.673469 | 0.0340136 | 0.0680272 | 0.0748299 |
| static_minus | 0.7 | 0.15 | 0.15 | 정적 취약도 비중 축소 | 196 | 43 | 147 | 0.139535 | 0.209302 | 0.27907 | 0.763969 | 0.278912 | 0.394558 | 0.510204 | -0.0612245 | -0.0612245 | -0.0884354 |
| weather_plus | 0.7 | 0.2 | 0.1 | 기상위험 비중 강화 | 196 | 43 | 147 | 0.116279 | 0.232558 | 0.27907 | 0.766146 | 0.272109 | 0.394558 | 0.530612 | -0.0680272 | -0.0612245 | -0.0680272 |
| exposure_minus | 0.7 | 0.25 | 0.05 | 전력설비 노출도 비중 축소 | 196 | 43 | 147 | 0.139535 | 0.209302 | 0.302326 | 0.769952 | 0.258503 | 0.387755 | 0.557823 | -0.0816327 | -0.0680272 | -0.0408163 |

## 해석 기준

- `exact_top5_hit_rate`: 산불 좌표가 속한 정확한 100m 격자가 해당 날짜 위험도 상위 5%에 포함된 비율
- `neighbor_top5_hit_rate`: 산불 좌표 주변 ±N격자 내에서 해당 날짜 위험도 상위 5% 격자가 포착된 비율
- 무작위 기준으로 top5, top10, top20 hit rate의 기대값은 각각 약 5%, 10%, 20%이다.
- 본 분석은 최적 가중치를 탐색하기 위한 grid search가 아니라, 기준 가중치 주변의 안정성을 확인하는 축소형 민감도 분석이다.

## 출력
- summary csv: `C:\SKN projects\weather\output\report\sensitivity\weight_oat_sensitivity_summary.csv`
- by-year csv: `C:\SKN projects\weather\output\report\sensitivity\weight_oat_sensitivity_by_year.csv`
- summary md: `C:\SKN projects\weather\output\report\sensitivity\weight_oat_sensitivity_summary.md`
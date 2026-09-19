# Fire History Validation 결과

## 목적

본 검증은 산불 발생 여부를 학습한 모델 평가가 아니라, 2020~2024년 2~5월 실제 산불 발생 이력이 산출된 위험도 상위권에 얼마나 위치하는지 확인하는 사후 검증이다.

## 입력

- fire history csv: `data/산불발생이력.csv`
- master grid: `data/master_grid.parquet`
- grid-date risk: `output/risk/grid_date_risk/**/*.parquet`
- validation period: `2020-02-01` ~ `2024-05-31`
- fire-season months: `2,3,4,5`
- province codes filter: `NONE`
- address keywords filter: `강원,강원도,강원특별자치도`
- neighborhood radius cells: `5`

## 출력

- event-level parquet: `C:\SKN projects\weather\output\validation\fire_history_validation_events.parquet`
- event-level csv: `C:\SKN projects\weather\output\validation\fire_history_validation_events.csv`
- overall summary: `C:\SKN projects\weather\output\report\risk\fire_history_validation_summary.csv`
- by year: `C:\SKN projects\weather\output\report\risk\fire_history_validation_by_year.csv`
- by month: `C:\SKN projects\weather\output\report\risk\fire_history_validation_by_month.csv`
- by region: `C:\SKN projects\weather\output\report\risk\fire_history_validation_by_region.csv`
- top fire events: `C:\SKN projects\weather\output\report\risk\fire_history_validation_top_fire_events.csv`

## 매핑 요약

- original_fire_count: `6,676`
- filtered_fire_count: `196`
- matched_fire_count: `43`
- outside_master_grid_count: `153`
- invalid_coord_count: `0`
- region_col: `city_name`

## 검증 요약

- validation_event_count: `196`
- mapped_event_count_exact_grid: `43`
- valid_coord_event_count: `196`
- neighbor_grid_mapped_event_count: `147`
- exact_risk_available_count: `43`
- neighbor_risk_available_count: `147`

### Exact grid 기준

- avg_exact_final_grid_risk: `0.49381316125734726`
- avg_exact_daily_risk_pct: `0.5772730215520913`
- exact_top5_hit_rate: `11.63%`
- exact_top10_hit_rate: `20.93%`
- exact_top20_hit_rate: `27.91%`

### Neighborhood 기준

- avg_neighbor_max_final_grid_risk: `0.6600133226184247`
- avg_neighbor_max_daily_risk_pct: `0.8049726146869006`
- neighbor_top5_hit_rate: `34.01%`
- neighbor_top10_hit_rate: `45.58%`
- neighbor_top20_hit_rate: `59.86%`

## 연도별 요약

| occu_year | fire_count | exact_risk_available_count | avg_exact_final_grid_risk | avg_exact_daily_risk_pct | exact_top5_hit_rate | exact_top10_hit_rate | exact_top20_hit_rate | avg_neighbor_max_final_grid_risk | avg_neighbor_max_daily_risk_pct | neighbor_top5_hit_rate | neighbor_top10_hit_rate | neighbor_top20_hit_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2020 | 37 | 10 | 0.482779 | 0.563532 | 0.1 | 0.2 | 0.3 | 0.63947 | 0.777316 | 0.27027 | 0.432432 | 0.540541 |
| 2021 | 24 | 6 | 0.475325 | 0.529623 | 0 | 0.166667 | 0.333333 | 0.685557 | 0.824572 | 0.333333 | 0.5 | 0.666667 |
| 2022 | 38 | 7 | 0.580187 | 0.701259 | 0.428571 | 0.428571 | 0.428571 | 0.657067 | 0.811638 | 0.394737 | 0.421053 | 0.605263 |
| 2023 | 34 | 13 | 0.487342 | 0.570164 | 0.0769231 | 0.153846 | 0.230769 | 0.656309 | 0.803914 | 0.323529 | 0.441176 | 0.588235 |
| 2024 | 14 | 7 | 0.451068 | 0.526964 | 0 | 0.142857 | 0.142857 | 0.687508 | 0.828943 | 0.428571 | 0.571429 | 0.642857 |

## 지역별 상위 요약

| grid_region | fire_count | exact_risk_available_count | avg_exact_final_grid_risk | avg_exact_daily_risk_pct | exact_top5_hit_rate | exact_top10_hit_rate | exact_top20_hit_rate | avg_neighbor_max_final_grid_risk | avg_neighbor_max_daily_risk_pct | neighbor_top5_hit_rate | neighbor_top10_hit_rate | neighbor_top20_hit_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  | 104 | 0 |  |  |  |  |  | 0.658467 | 0.809971 | 0.365385 | 0.480769 | 0.615385 |
| 홍천군 | 7 | 7 | 0.411707 | 0.465324 | 0 | 0.142857 | 0.142857 | 0.591748 | 0.707776 | 0.142857 | 0.142857 | 0.285714 |
| 강릉시 | 6 | 6 | 0.769438 | 0.939027 | 0.5 | 0.833333 | 1 | 0.869616 | 0.993861 | 1 | 1 | 1 |
| 횡성군 | 6 | 6 | 0.259462 | 0.230634 | 0 | 0 | 0 | 0.49931 | 0.605554 | 0 | 0.166667 | 0.166667 |
| 평창군 | 5 | 5 | 0.371815 | 0.369412 | 0 | 0 | 0 | 0.513848 | 0.617956 | 0 | 0 | 0 |
| 원주시 | 4 | 4 | 0.398103 | 0.498225 | 0 | 0 | 0 | 0.613955 | 0.779007 | 0 | 0.25 | 0.5 |
| 삼척시 | 4 | 4 | 0.49047 | 0.586114 | 0 | 0 | 0 | 0.640087 | 0.801393 | 0 | 0.25 | 0.75 |
| 양양군 | 3 | 3 | 0.724408 | 0.899116 | 0 | 0.333333 | 1 | 0.898564 | 0.992802 | 1 | 1 | 1 |
| 춘천시 | 3 | 3 | 0.568533 | 0.725221 | 0.333333 | 0.333333 | 0.333333 | 0.733942 | 0.873922 | 0.333333 | 0.333333 | 0.666667 |
| 정선군 | 3 | 3 | 0.593802 | 0.70151 | 0.333333 | 0.333333 | 0.333333 | 0.752686 | 0.901349 | 0.333333 | 0.666667 | 1 |
| 영월군 | 2 | 2 | 0.55251 | 0.692773 | 0 | 0 | 0 | 0.722323 | 0.913758 | 0 | 0.5 | 1 |

## 해석 기준

- `exact_top5_hit_rate`: 실제 산불 발생 격자가 해당 날짜 위험도 상위 5%에 포함된 비율
- `neighbor_top5_hit_rate`: 실제 산불 발생 좌표 주변 5칸 이내에서 분석 대상 격자 중 상위 5% 위험 격자가 포착된 비율
- 무작위 기준으로는 상위 5%, 10%, 20% hit rate의 기대값이 각각 약 5%, 10%, 20%이다.
- 따라서 hit rate가 이 기준보다 높고 평균 `daily_risk_pct`가 0.5보다 높다면, 산출 위험도가 실제 산불 발생 위치와 일정 부분 정합성을 가진다고 해석할 수 있다.

## 주의

- 본 검증은 지도학습 모델의 정확도 평가가 아니라 위험도 스코어링 결과의 사후 타당성 확인이다.
- 산불 발생 이력은 학습에 사용하지 않았으며, 실제 발생 위치가 산출된 위험도 상위권에 얼마나 놓이는지 확인하는 용도로만 사용한다.
- exact grid 검증은 좌표 오차와 격자 경계 효과에 민감할 수 있으므로, neighborhood 기준 결과를 함께 해석해야 한다.

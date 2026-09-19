# 반경별 산불 이력 검증 비교 결과

## 목적

본 분석은 neighborhood 검증의 반경 변화에 따른 hit rate를 비교하고, 주변 후보 격자 내 고위험 격자 비율 및 random-neighborhood baseline을 함께 산출하기 위한 것이다.

## 핵심 해석 주의

- Exact 기준은 산불 좌표가 속한 정확한 100m 격자 하나를 평가하므로 top5/top10/top20을 각각 5%/10%/20% 기준과 직접 비교할 수 있다.
- Neighborhood 기준은 주변 여러 격자 중 하나라도 고위험 격자가 있으면 hit로 계산되므로, top5 hit rate를 단순 5% 기준과 직접 비교하면 안 된다.
- 따라서 본 분석에서는 실제 산불 위치 주변 결과를 동일 날짜·동일 반경 조건의 random-neighborhood baseline과 비교한다.
- `avg_event_neighbor_top5_grid_ratio`는 각 산불 사건의 주변 유효 격자 중 top5 격자가 차지하는 비율을 사건 단위로 평균한 값이다.

## 입력
- fire history csv: `data/산불발생이력.csv`
- master grid: `data/master_grid.parquet`
- grid-date risk: `output/risk/grid_date_risk/**/*.parquet`
- validation period: `2020-02-01` ~ `2024-05-31`
- fire-season months: `2,3,4,5`
- address keywords filter: `강원,강원도,강원특별자치도`
- radii: `0,1,3,5,10`
- random repeats: `100`
- random seed: `42`

## 매핑 요약

- original_fire_count: `6,676`
- filtered_fire_count: `196`
- exact matched_fire_count: `43`
- outside_master_grid_count: `153`
- invalid_coord_count: `0`

## 반경별 실제값 vs Random-neighborhood baseline

| radius_cells | validation_event_count | neighbor_risk_available_count | avg_neighbor_risk_grid_count_available | neighbor_top5_hit_rate_available | random_neighbor_top5_hit_rate_mean | random_neighbor_top5_hit_rate_p05 | random_neighbor_top5_hit_rate_p95 | neighbor_top5_hit_rate_diff_vs_random | neighbor_top5_hit_rate_lift_vs_random | avg_event_neighbor_top5_grid_ratio | random_avg_event_neighbor_top5_grid_ratio_mean | avg_event_neighbor_top5_grid_ratio_diff_vs_random | neighbor_top10_hit_rate_available | random_neighbor_top10_hit_rate_mean | neighbor_top10_hit_rate_diff_vs_random | neighbor_top20_hit_rate_available | random_neighbor_top20_hit_rate_mean | neighbor_top20_hit_rate_diff_vs_random | avg_neighbor_max_daily_risk_pct | random_avg_neighbor_max_daily_risk_pct_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 196 | 43 | 1 | 0.116279 | 0.0505612 | 0.0303571 | 0.0714286 | 0.0657178 | 2.29977 | 0.116279 | 0.0505612 | 0.0657178 | 0.209302 | 0.100714 | 0.108588 | 0.27907 | 0.201939 | 0.077131 | 0.577273 | 0.500915 |
| 1 | 196 | 119 | 4.11765 | 0.184874 | 0.128214 | 0.0918367 | 0.168367 | 0.0566597 | 1.44191 | 0.107756 | 0.0497579 | 0.0579986 | 0.252101 | 0.207806 | 0.0442947 | 0.403361 | 0.33551 | 0.0678511 | 0.689179 | 0.635258 |
| 3 | 196 | 143 | 18.3007 | 0.286713 | 0.205204 | 0.163265 | 0.265306 | 0.0815092 | 1.39721 | 0.0835257 | 0.0496043 | 0.0339214 | 0.370629 | 0.297245 | 0.0733845 | 0.559441 | 0.432449 | 0.126992 | 0.762966 | 0.714521 |
| 5 | 196 | 147 | 41.551 | 0.340136 | 0.249235 | 0.204082 | 0.30102 | 0.0909014 | 1.36472 | 0.0852326 | 0.0493184 | 0.0359143 | 0.455782 | 0.347551 | 0.108231 | 0.598639 | 0.484847 | 0.113793 | 0.804973 | 0.749879 |
| 10 | 196 | 150 | 138.847 | 0.42 | 0.314643 | 0.270153 | 0.372704 | 0.105357 | 1.33485 | 0.0739224 | 0.0488621 | 0.0250603 | 0.52 | 0.425816 | 0.0941837 | 0.66 | 0.570102 | 0.089898 | 0.840314 | 0.796705 |

## 주요 출력
- actual event metrics parquet: `C:\SKN projects\weather\output\validation\radius_compare\radius_actual_event_metrics.parquet`
- random event metrics parquet: `C:\SKN projects\weather\output\validation\radius_compare\radius_random_event_metrics.parquet`
- actual summary csv: `C:\SKN projects\weather\output\report\validation\radius_validation_actual_summary.csv`
- random summary csv: `C:\SKN projects\weather\output\report\validation\radius_validation_random_summary.csv`
- comparison csv: `C:\SKN projects\weather\output\report\validation\radius_validation_comparison.csv`
- summary md: `C:\SKN projects\weather\output\report\validation\radius_validation_comparison_summary.md`
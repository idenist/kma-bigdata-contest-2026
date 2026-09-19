# Pole Risk Score 생성 결과

## 입력

- pole csv: `data/test_hanjeon.csv`
- master grid: `data/master_grid.parquet`
- grid-date risk: `output/risk/grid_date_risk/**/*.parquet`

## 출력

- pole grid map: `output/risk/pole_grid_map.parquet`
- grid period risk: `output/risk/grid_period_risk.parquet`
- full pole risk parquet: `output/risk/pole_risk_score.parquet`
- full pole risk csv: `output/risk/pole_risk_score.csv`
- decision csv: `output/risk/test_hanjeon_with_decision.csv`

## 전신주-grid 매핑 요약

- pole_count: `1,387,831`
- matched_count: `1,387,783`
- unmatched_count: `48`
- match_rate: `99.9965%`
- invalid_coord_count: `0`
- master_grid_xy_count: `272,980`

## grid-period risk 요약

- grid_count: `273,001`
- min_grid_period_risk: `0.3040482490842491`
- avg_grid_period_risk: `0.6209035118629825`
- max_grid_period_risk: `0.9385600456201231`
- min_observed_days: `722`
- max_observed_days: `722`

## pole risk score 요약

- pole_count: `1,387,831`
- matched_pole_count: `1,387,783`
- unmatched_pole_count: `48`
- decision_1_count: `69,390`
- decision_1_rate: `4.9999%`
- min_pole_risk_score: `0.3040482490842491`
- avg_pole_risk_score: `0.632686108067593`
- max_pole_risk_score: `0.9385600456201231`

## 산식

```text
pole_risk_score
= 0.50 * max_daily_risk
+ 0.30 * mean_top5_daily_risk
+ 0.20 * high_risk_day_ratio
```

- `max_daily_risk`: 분석 기간 중 해당 전신주가 속한 grid의 최대 일별 위험도
- `mean_top5_daily_risk`: 분석 기간 중 해당 grid의 위험도 상위 5일 평균
- `high_risk_day_ratio`: 해당 grid가 날짜별 상위 5% 고위험 격자에 포함된 날짜 비율
- `decision`: `pole_risk_score` 기준 상위 5.0% 전신주에 1 부여

## 해석상 주의

- `decision=1`은 산불 발생 확률이 아니라 전력설비 점검 우선순위 상위 후보군을 의미한다.
- master_grid와 매핑되지 않은 전신주는 위험도 산정이 불가능하므로 `decision=0`으로 처리하고 `match_status=unmatched_grid`로 남긴다.

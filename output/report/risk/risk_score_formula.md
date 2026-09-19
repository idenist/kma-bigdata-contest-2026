# Grid-Date Risk Score 산식 - DWI 기반 분리형 v4

## 목적

본 산식은 산불 발생 여부를 직접 맞히는 지도학습 모델이 아니라, 봄철 산불위험기(2~5월)에 전력설비 주변 격자 중 산불 위험 환경에 상대적으로 많이 노출된 후보지를 우선순위화하기 위한 위험도 스코어링 모델이다.

## v4 변경 사항

기존 산식은 `pffdri_pct + static_vulnerability + exposure_risk` 구조였으나, P-FFDRI 내부에 산림·지형·전력설비 노출 성격이 일부 포함될 수 있어 정적 취약도와 노출도를 다시 더할 경우 중복 반영 지적을 받을 수 있었다.

따라서 v4에서는 최종 위험도 계산에서 `pffdri`를 제외하고, 기상위험을 나타내는 `dwi`를 기준 위험도로 사용한다.

```text
기존: final_grid_risk = 0.60 * pffdri_pct + 0.25 * static_vulnerability + 0.15 * exposure_risk
수정: final_grid_risk = 0.60 * dwi_pct    + 0.25 * static_vulnerability + 0.15 * exposure_risk
```

`pffdri`는 최종 위험도 산식에는 사용하지 않으며, 존재할 경우 비교·참고용 컬럼으로만 남긴다.

## 산식

### 1. 기상 위험도

```text
dwi_pct = 같은 날짜 안에서 dwi의 percentile rank
```

- 날짜별 전체 기상위험 수준이 다르므로, 원점수보다 같은 날짜 안의 상대적 위치를 사용한다.
- `dwi`가 NULL인 행은 위험도 산정에서 제외한다.

### 2. 정적 취약도

```text
static_raw
= 0.400 * fmi_n
+ 0.350 * tmi_p
+ 0.100 * slope_pct
+ 0.075 * road_prox
+ 0.075 * river_far

static_vulnerability = static_raw의 전체 grid 기준 percentile rank
```

- 산림 연료 위험, 지형 위험, 경사, 도로 접근성, 수계 이격도를 위치별 취약성으로 결합한다.
- 정적 변수는 날짜별로 반복되므로 grid_id 단위로 집계한 뒤 percentile을 계산한다.
- slope 처리: `slope` 원본 컬럼을 전체 grid 기준 percentile rank로 변환하여 slope_pct 파생

### 3. 전력설비 노출도

```text
exposure_risk = pei의 전체 grid 기준 percentile rank
```

- PEI는 전력설비 주변 노출도 지표로 사용한다.

### 4. 최종 격자-일자 위험도

```text
final_grid_risk
= 0.60 * dwi_pct
+ 0.25 * static_vulnerability
+ 0.15 * exposure_risk
```

### 5. 일자별 고위험 플래그

```text
daily_high_risk_flag = final_grid_risk가 해당 날짜 상위 5.0% 이내이면 1
```

## 산식 해석

- 기상 위험도 0.60: 일별 산불위험 변동을 설명하는 핵심 축이다.
- 정적 취약도 0.25: 같은 기상 조건에서도 산림, 지형, 경사, 접근성에 따라 취약도가 달라지는 점을 반영한다.
- 전력설비 노출도 0.15: 본 과제의 목적이 전력설비 주변 관리 우선순위 산정이므로 설비 노출도를 별도 항으로 반영한다.

## 산식 설계상 주의

- 이 점수는 산불 발생 확률이 아니라 상대적 위험노출도이다.
- 산불 발생 이력은 학습 타깃이 아니라 사후 검증 자료로만 사용한다.
- 본 v4 산식은 P-FFDRI와 static/exposure의 중복 반영 가능성을 줄이기 위한 분리형 baseline이다.
- 최종 채택 여부는 12~14번 재실행 후 전신주 decision 분포와 산불 이력 검증 결과를 비교하여 판단한다.

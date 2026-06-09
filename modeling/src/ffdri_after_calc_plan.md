# FFDRI 계산 이후 작업 계획

## 1. 현재 상태

`calc_ffdri.py`를 통해 강원도 100m 격자별·날짜별 산불위험지수 `FFDRI`를 계산했다고 가정합니다.

계산된 주요 컬럼은 다음과 같습니다.

| 컬럼 | 의미 |
|---|---|
| `dwi` | 기상위험지수 |
| `fmi` | 임상위험지수 |
| `tmi` | 지형위험지수 |
| `day_weight` | 일가중치 |
| `ffdri` | 최종 산불위험지수 |

공식은 다음과 같습니다.

```text
FFDRI = {7 × DWI + 1.5 × FMI + 1.5 × TMI} × 일가중치
```

## 2. 다음 단계

```text
1. 계산값 검산
2. 위험등급화
3. 위험 여부 0/1 생성
4. 실제 산불 발생 이력으로 검증
5. 시군구별 위험도 집계
6. 전신주 노출 위험도 집계
7. 보고서용 요약 테이블 생성
```

## 3. 위험등급 기준

| FFDRI | 등급 |
|---:|---|
| 86 이상 | Extreme |
| 66 이상 86 미만 | High |
| 51 이상 66 미만 | Moderate |
| 51 미만 | Low |

이진 라벨은 다음처럼 정의합니다.

```text
risk_binary = 1 if ffdri >= 66 else 0
```

## 4. 실제 발생 이력 검증

실제 산불 발생 이력은 학습용 타겟이 아니라 검증용으로 사용합니다.

핵심 질문은 다음과 같습니다.

```text
실제 산불이 발생한 위치와 날짜가 FFDRI 기준 고위험으로 잡혔는가?
```

주요 지표는 다음과 같습니다.

| 지표 | 의미 |
|---|---|
| Recall | 실제 발생 건 중 고위험으로 포착한 비율 |
| Precision | 고위험 예측 중 실제 발생한 비율 |
| F2-score | Recall을 Precision보다 더 중시한 지표 |
| Top-k Hit Rate | 상위 k% 위험 구간 안에 실제 발생 건이 포함된 비율 |
| 고위험 지정 비율 | 전체 격자·날짜 중 위험 1로 지정된 비율 |

## 5. 전신주 위험 노출도

전신주 개별 데이터를 날짜별로 모두 늘리면 데이터가 매우 커집니다.

따라서 전신주는 `grid_id` 단위의 `pole_count`로 집계하여 사용합니다.

```text
고위험 전신주 수 = 고위험 격자의 pole_count 합
고위험 전신주-일수 = 날짜별 고위험 전신주 수의 합
```

## 6. 산출물

| 파일 | 설명 |
|---|---|
| `processed/analysis/ffdri_monthly_check.parquet` | 월별 FFDRI 검산 요약 |
| `processed/analysis/ffdri_risk_grade/` | 등급/0,1 라벨이 붙은 FFDRI 결과 |
| `processed/analysis/fire_validation_result.csv` | 실제 산불 발생 이력 검증 결과 |
| `processed/analysis/city_pole_risk_summary.csv` | 시군구별 전신주 위험 노출 집계 |
| `processed/analysis/report_tables.xlsx` | 보고서용 요약 테이블 |

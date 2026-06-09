# 모델링 시 참고사항

## 1. 전처리 완료 파일 목록

| 파일 | 경로 | 설명 |
|---|---|---|
| `prep_master_grid.parquet` | `prep/` | 공간 피처 마스터 (273,001행 × 21컬럼) |
| `05_fire_label_master.parquet` | `prep/` | 공간 피처 + 화재 레이블 (273,001행 × 22컬럼) — **모델 입력 기준 파일** |
| `grid_date_master/` | `raw/` | 기상 데이터 (월별 파티션, Hive 구조) |

---

## 2. 공간 피처 마스터 (`05_fire_label_master.parquet`)

### 2-1. 기본 정보

- **격자 체계**: EPSG:5179 (한국 TM), 100m × 100m 격자
- **grid_id 형식**: `{floor(x/100)}_{floor(y/100)}` — x, y는 EPSG:5179 좌표(m)
- **대상 지역**: 강원특별자치도 (전신주 1개 이상 존재 격자만 포함)
- **행 수**: 273,001개 격자

### 2-2. 컬럼 목록

| 컬럼 | 타입 | 설명 | 비고 |
|---|---|---|---|
| `grid_id` | str | 격자 고유 ID | 기본키 |
| `pole_count` | int | 전신주 수 (개) | 이상치 상한 13개 초과 6,441개 존재 |
| `is_forest` | int | 산림 여부 (1=산림, 0=비산림) | 산림 75.3% |
| `elevation` | float | 고도 (m) | |
| `slope` | float | 경사도 (°) | |
| `aspect_sin` | float | 사면 방향 sin 성분 | 원형 데이터 sin/cos 분해 완료 |
| `aspect_cos` | float | 사면 방향 cos 성분 | 원형 데이터 sin/cos 분해 완료 |
| `nearest_road_dist` | float | 최근접 도로까지 거리 (m) | 음수 이상치 소수 존재 (-30.1) |
| `nearest_river_dist` | float | 최근접 하천까지 거리 (m) | 이상치 비율 7.61% |
| `city_name` | str | 시·군·구명 | 모델 사용 시 인코딩 필요 |
| `forest_exist_code` | Int64 | 임상 존재 코드 | 비산림 격자 결측 67,329개 |
| `forest_type_code` | Int64 | 임상 유형 코드 | 비산림 격자 결측 67,329개 |
| `forest_origin_code` | Int64 | 임상 기원 코드 | 비산림 격자 결측 67,329개 |
| `age_class_code` | Int64 | 영급 코드 | 비산림+무입목지 결측 73,523개 |
| `tree_height_code` | Int64 | 수고 코드 (2m 간격, 0·2·4…40) | 비산림+무입목지 결측 73,533개 |
| `forest_updated_year` | Int64 | 임상도 갱신 연도 | 산림 격자 일부 결측 20,900개, **피처 제외 권장** |
| `tree_species` | str | 수종명 | 결측 多, 피처 제외 검토 |
| `diameter_class_code` | str | 경급 코드 | 결측 73,523개, 모델 결정 후 인코딩 |
| `density_code` | str | 밀도 코드 | 결측 73,523개, 모델 결정 후 인코딩 |
| `fire_label` | int | **타겟 변수** (0=화재없음, 1=화재인근) | 아래 3절 참조 |

---

## 3. 타겟 변수 (`fire_label`)

### 3-1. 생성 기준

- **데이터 출처**: 산불발생이력 (행안부·산림청, `forest_fire_all_4326.csv`)
- **필터 조건**: 강원도(시도코드 42·51) + 봄철(2~5월) + 2020~2024년
- **적용 건수**: 196건

### 3-2. 공간 결합 방식 (500m 원형 버퍼)

- 각 산불 발생 지점에서 **반경 500m(격자 5칸) 이내**의 master_grid 격자를 `fire=1`로 표시
- 직접 매칭을 사용하지 않은 이유: master_grid는 전신주 격자만 포함하므로 산불 발생 정확 격자에 전신주가 없으면 매칭 불가
- 196건 중 145건(74.0%)이 버퍼 내 격자 보유, 51건은 500m 이내 전신주 격자 없음

### 3-3. 분포

| 레이블 | 격자 수 | 비율 |
|---|---|---|
| 0 (화재 없음) | 268,864개 | 98.485% |
| 1 (화재 인근 500m) | 4,137개 | 1.515% |

**클래스 불균형 비율: 약 65:1** → 모델 학습 시 반드시 처리 필요 (7절 참조)

---

## 4. 기상 데이터 (`raw/grid_date_master/`)

### 4-1. 구조

- **파티션 키**: `month` (예: `month=2020-02`)
- **파티션 범위**: 2020-02 ~ 2025-05 (봄철 2~5월 기준)
- **읽기 방법**:

```python
import pandas as pd
df = pd.read_parquet(
    "D:/prep/003. 전처리 데이터(preprocessing_data)/raw/grid_date_master",
    filters=[('month', '==', '2023-03')],
    engine='fastparquet'
)
```

- **격자 기준**: EPSG:5179, `grid_id` 동일 — `05_fire_label_master.parquet`과 `grid_id` 기준 결합

### 4-2. 주요 컬럼 및 스케일 변환 (필수 적용)

> 원본 NetCDF scale_factor 미적용 상태로 저장되어 있어 반드시 ÷10 적용 후 사용

| 컬럼 | 변환 | 단위 | 비고 |
|---|---|---|---|
| `ta_mean`, `ta_max` | **원본값 ÷ 10** | °C | |
| `hm_mean`, `hm_min` | **원본값 ÷ 10** | % | |
| `td_mean`, `td_min` | **원본값 ÷ 10** | °C | |
| `rn_day_mean`, `rn_day_max` | **원본값 ÷ 10** | mm | |
| `wind_ws_mean`, `wind_ws_max` | **원본값 ÷ 10** | m/s | 검증 완료: 최댓값 10.9 m/s |
| `wind_uu_mean`, `wind_vv_mean` | **원본값 ÷ 10** | m/s | |
| `wind_wd_sin_mean`, `wind_wd_cos_mean` | 변환 불필요 | — | 이미 sin/cos 성분 |

### 4-3. master_grid와 결합 방법 (예시)

```python
weather = pd.read_parquet(RAW_PATH, filters=[('month','==','2023-03')], engine='fastparquet')
for col in ['ta_mean','hm_mean','wind_ws_mean','rn_day_mean']:
    weather[col] = weather[col] / 10
merged = master_df.merge(weather, on='grid_id', how='left')
```

---

## 5. 훈련/검증 분할 전략

| 구분 | 기간 | 역할 |
|---|---|---|
| 훈련셋 | 2020~2023 봄철 (2~5월) | 모델 학습 |
| 검증셋 | 2024 봄철 (2~5월) | 성능 검증 |
| 예측 대상 | 2025 봄철 (2~5월) | 위험도 예측 (정답지 없음, 정성 검증) |

- **분할 방법**: 연도(year) 기준 시간 순서 분할 — random split 금지 (시계열 데이터 유출 방지)
- **비율**: 훈련(2020~2023) : 검증(2024) ≈ 8:2
- **2025 정성 검증**: 2025년 강원도 대형 산불 발생 지역에 모델이 높은 위험도를 사전 예측하는지 확인

---

## 6. 모델 입력 피처 후보

### 6-1. 공간 피처 (master_grid 기반)

| 피처 | 사용 여부 | 비고 |
|---|---|---|
| `pole_count` | 사용 | 로그 변환 또는 상한 캡 고려 |
| `elevation` | 사용 | 로그 변환 선택적 |
| `slope` | 사용 | 변환 불필요 |
| `aspect_sin`, `aspect_cos` | 사용 | 변환 불필요 |
| `nearest_road_dist` | 사용 | 로그 변환 권장, 음수 이상치 처리 필요 |
| `nearest_river_dist` | 사용 | 로그 변환 권장 |
| `is_forest` | 사용 | 변환 불필요 |
| `forest_type_code` | 사용 | 결측 = 비산림 (0으로 대체 가능) |
| `age_class_code` | 사용 | `tree_height_code`와 동시 사용 시 VIF 확인 |
| `tree_height_code` | 사용 | 위와 동일 |
| `city_name` | 선택적 | 인코딩 방식 결정 필요 |
| `forest_updated_year` | **제외 권장** | 산림 격자 일부 결측, 피처로서 의미 낮음 |
| `tree_species` | **제외 검토** | 결측 多 |
| `diameter_class_code`, `density_code` | 선택적 | 인코딩 방식 결정 후 사용 |

### 6-2. 기상 피처 (grid_date_master 기반)

| 피처 | 비고 |
|---|---|
| `ta_mean` | 일평균 기온 |
| `hm_mean` | 일평균 상대습도 |
| `wind_ws_mean` | 일평균 풍속 |
| `rn_day_mean` | 일강수량 |
| `wind_wd_sin_mean`, `wind_wd_cos_mean` | 풍향 성분 |

---

## 7. 모델링 단계 결정 사항

### 7-1. 클래스 불균형 처리 (65:1)

- `class_weight='balanced'` — sklearn 트리 계열 모델에서 소수 클래스 가중치 자동 부여
- SMOTE 오버샘플링 — 소수 클래스(fire=1) 합성 샘플 생성
- 두 방법 성능 비교 후 선택

### 7-2. 로그 변환 후보 (강한 우편향)

| 컬럼 | 왜도 | 적용 방법 |
|---|---|---|
| `nearest_road_dist` | 7.938 | `np.log1p(x)` |
| `nearest_river_dist` | 5.684 | `np.log1p(x)` |
| `pole_count` | 1.584 | `np.log1p(x)` 또는 상한 캡 |
| `elevation` | 0.937 | 선택적 |

### 7-3. 변환 불필요

| 컬럼 | 이유 |
|---|---|
| `slope` | 왜도 0.138, 대칭 분포 |
| `aspect_sin`, `aspect_cos` | sin/cos 성분으로 변환 완료 |
| `tree_height_code` | 왜도 -0.295, 대칭에 가까움 |
| `age_class_code` | 왜도 -0.641, 경미한 좌편향 |

### 7-4. 정규화/표준화 (스케일 차이)

| 컬럼 | 범위 |
|---|---|
| `elevation` | 0 ~ 1,570m |
| `nearest_river_dist` | 0 ~ 2,220m |
| `slope` | 0 ~ 84° |
| `pole_count` | 1 ~ 60개 |

- **RobustScaler 권장**: 중앙값·IQR 기반, 이상치 영향 최소화 (우편향 컬럼 多)
- 거리 기반 모델(KNN, SVM) 사용 시 스케일링 필수
- 트리 계열(RandomForest, XGBoost) 사용 시 스케일링 불필요

### 7-5. 다중공선성 주의

| 변수 쌍 | 상관계수 | 처리 방안 |
|---|---|---|
| `age_class_code` ↔ `tree_height_code` | +0.530 | 하나 제거 또는 PCA, 학습 후 VIF 확인 |
| `pole_count` ↔ `nearest_road_dist` | -0.432 | 각각 독립적 의미(전신주 밀도 vs 도로 접근성), 동시 사용 가능하나 VIF 확인 |

나머지 변수 쌍은 |r| < 0.3으로 다중공선성 문제 없음

### 7-6. 범주형 인코딩

| 컬럼 | 방법 |
|---|---|
| `city_name` | One-Hot 또는 Label Encoding (모델에 따라 선택) |
| `diameter_class_code`, `density_code` | 모델 결정 후 인코딩 방식 선택 |

---

## 8. 주의사항

1. **기상 데이터 스케일 미적용 시 오류**: 원본값에 반드시 ÷10 적용 후 사용. 미적용 시 기온 수백°C, 풍속 수십 m/s의 비정상값이 모델에 입력됨
2. **시계열 분할 필수**: 연도 기준으로 분할해야 함. random split 시 미래 데이터가 훈련셋에 포함되어 data leakage 발생
3. **음수 nearest_road_dist**: 하한 -30.1 이상치 존재. 절댓값 처리 또는 0으로 클리핑 검토
4. **2023년 강원도 코드 변경**: 2022년 이전 = 42(강원도), 2023년 이후 = 51(강원특별자치도) — 산불 필터에 이미 반영됨
5. **`forest_updated_year` 결측**: 산림 격자 20,900개에 갱신 이력 미기재. 피처 제외 권장
6. **산불 피해금액(`amount`) 단위 주의**: `amount`는 피해면적이 아닌 피해금액(원) 컬럼. 피해면적은 `ar`(ha)이나 196건 중 122건(62.2%)이 0으로 기록되어 있어 사용 불가. 두 컬럼 모두 타겟 변수 및 피처로 사용하지 않으므로 모델에 영향 없음

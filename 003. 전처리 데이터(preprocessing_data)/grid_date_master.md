# Grid Weather Daily Dataset

## 컬럼 설명

| 컬럼명 | 설명 |
|---------|---------|
| grid_id | 사용자 100m 격자 ID |
| kma_nx | 기상청 500m Grid X 인덱스 |
| kma_ny | 기상청 500m Grid Y 인덱스 |
| ta_mean | 일평균 기온 |
| ta_max | 일최고 기온 |
| hm_mean | 일평균 상대습도 |
| hm_min | 일최저 상대습도 |
| td_mean | 일평균 이슬점온도 |
| td_min | 일최저 이슬점온도 |
| wind_ws_mean | 일평균 풍속 |
| wind_ws_max | 일최대 풍속 |
| wind_uu_mean | 일평균 U 성분 풍속 (동서 방향) |
| wind_vv_mean | 일평균 V 성분 풍속 (남북 방향) |
| wind_wd_sin_mean | 풍향 Circular Mean (sin) |
| wind_wd_cos_mean | 풍향 Circular Mean (cos) |
| rn_day_mean | 일평균 강수량 |
| rn_day_max | 일최대 강수량 |
| date | 관측일 |

---

## Load 예시

### 전체 로드

```python
import pandas as pd

df = pd.read_parquet(
    "processed/grid_date_master"
)
```

### 특정 월 로드

```python
import pandas as pd

df = pd.read_parquet(
    "processed/grid_date_master",
    filters=[
        ("month", "==", "2025-03")
    ]
)
```

### 특정 Grid 조회

```python
grid_weather = df[
    df["grid_id"] == "10007_19696"
]
```

---

## 데이터 개요

- 원본 데이터: 기상청 500m 격자 기상자료
- 대상 기간: 2025-02 ~ 2025-05
- 관측 주기: 3시간 간격
- 집계 단위: 일(Daily)
- 공간 단위: 사용자 100m Grid

---

## 풍향 처리

풍향은 단순 평균 대신 Circular Mean 방식으로 집계하였습니다.

```python
sin_mean = mean(sin(theta))
cos_mean = mean(cos(theta))
```

필요 시 각도로 복원:

```python
import numpy as np

direction = np.rad2deg(
    np.arctan2(
        wind_wd_sin_mean,
        wind_wd_cos_mean
    )
)

direction = (direction + 360) % 360
```

---

## 단위 참고

기상청 원본 NetCDF 데이터는 일부 변수를 정수형(int16)으로 저장하며 `data_scale` 속성을 통해 실제값으로 변환하도록 정의되어 있습니다.

예시:

```text
value = raw_value / data_scale
```

현재 데이터셋은 원본값 기준으로 저장되어 있으며 실제 단위 적용 여부는 추후 검증이 필요합니다.

예상 스케일:

- ta: ÷10
- hm: ÷10
- td: ÷10
- wind_wd: ÷10
- rn_day: ÷10
- wind_ws: 확인 필요
- wind_uu: 확인 필요
- wind_vv: 확인 필요

---

## 결측치 처리

기상청 원본 결측값:

```text
-9990
-9999
```

전처리 과정에서 NaN 처리 후 통계를 계산하였습니다.
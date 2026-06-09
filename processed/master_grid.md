# Master Grid

강원도 100m × 100m 고정 격자 기반 마스터 테이블입니다.

전신주 위치 데이터를 기준으로 생성되었으며, 이후 임상도, 지형, 기상 등 다양한 공간 데이터를 `grid_id` 기준으로 결합하기 위한 기준 테이블로 사용합니다.

---

# Load 코드

```python
import pandas as pd

master_grid = pd.read_parquet(
    "processed/master_grid.parquet"
)
```

---

# 컬럼 설명

| 컬럼명 | 설명 | 비고 |
|---------|---------|---------|
| `grid_id` | 격자 고유 ID | Primary Key |
| `grid_x` | 격자 X 인덱스 | EPSG:5179 기준 100m 격자 |
| `grid_y` | 격자 Y 인덱스 | EPSG:5179 기준 100m 격자 |
| `pole_count` | 격자 내 전신주 개수 | 공간 대표성 확인용 |

---

# 임상(Forest)

| 컬럼명 | 설명 | 비고 |
|---------|---------|---------|
| `is_forest` | 산림 여부 | 1=산림, 0=비산림 |
| `forest_exist_code` | 임목 존재 코드 | 1=입목지, 2=무립목지, 0=비산림 |
| `forest_origin_code` | 임종 코드 | 1=인공림, 2=천연림 |
| `forest_type_code` | 임상 코드 | 1=침엽, 2=활엽, 3=혼효, 4=죽림, 0=무립목 |
| `tree_species` | 대표 수종명 | 소나무, 잣나무, 낙엽송 등 |
| `diameter_class_code` | 경급 코드 | 치수 / 소경목 / 중경목 / 대경목 |
| `age_class_code` | 영급 코드 | 1~9영급 |
| `density_code` | 밀도 코드 | A=소, B=중, C=밀 |
| `tree_height_code` | 임분고 코드 | 평균 수고(높이) 구간 |
| `forest_updated_year` | 갱신년도 | 최신 업데이트 연도 |

---

# 지형(Topography)

| 컬럼명 | 설명 | 비고 |
|---------|---------|---------|
| `elevation` | 해발고도(m) | 등고선 기반 추정 |
| `slope` | 경사도(°) | 0~90 |
| `aspect_sin` | 사면 방향 동서 성분 | +1=동향, -1=서향 |
| `aspect_cos` | 사면 방향 남북 성분 | +1=북향, -1=남향 |
| `city_name` | 지역 이름 | 시군구 |
| `nearest_road_dist` | 가장 가까운 도로까지의 거리 | m |
| `nearest_river_dist` | 가장 가까운 하천까지의 거리 | m |

---

# 좌표 체계

| 항목 | 값 |
|---------|---------|
| CRS | EPSG:5179 |
| 단위 | meter |
| 격자 크기 | 100m × 100m |

---

# 참고

- `grid_x`, `grid_y`는 실제 좌표가 아니라 100m 격자 인덱스입니다.
- 실제 좌표는 다음과 같이 복원할 수 있습니다.

```python
GRID_SIZE = 100

grid_min_x = grid_x * GRID_SIZE
grid_min_y = grid_y * GRID_SIZE

grid_center_x = grid_min_x + GRID_SIZE / 2
grid_center_y = grid_min_y + GRID_SIZE / 2
```

- `aspect_sin`, `aspect_cos`는 원형(Circular) 데이터인 사면 방향을 머신러닝 입력용으로 변환한 값입니다.
- 사면 방향을 각도(0~360°)로 저장하지 않고 Sin/Cos 성분으로 저장하여 방향의 연속성을 보존합니다.
# P-FFDRI DuckDB Feature Pipeline

## 실행 순서

`pffdri.duckdb`에 `grid_date_master` 테이블이 이미 있으면 01부터 실행한다.

```bash
python duckDB/01_weather_features.py
python duckDB/02_forest_features.py
python duckDB/03_terrain_ywi_features.py
python duckDB/04_power_access_pei_features.py
python duckDB/05_index_features.py
python duckDB/06_fire_target.py
python duckDB/99_build_final_dataset.py
```

전체 실행:

```bash
python duckDB/run_all_features.py
```

특정 월만 테스트:

```bash
python duckDB/run_all_features.py --months 2025-03
```

## 생성 테이블

| 파일 | 생성 테이블 | 역할 |
|---|---|---|
| `01_weather_features.py` | `feat_weather_daily` | 스케일, 실효습도, RNE, DWI, DWI_n |
| `02_forest_features.py` | `feat_forest_static` | FMI, FMI_n, 임상 feature |
| `03_terrain_ywi_features.py` | `feat_terrain_ywi_daily` | 지형, YWI, TMI_P |
| `04_power_access_pei_features.py` | `feat_power_access_static` | pole_n, road_prox, river_far, PEI |
| `05_index_features.py` | `feat_pffdri_daily` | FFDRI, P-FFDRI |
| `06_fire_target.py` | `target_fire_static` | 산불이력 target 분리 |
| `99_build_final_dataset.py` | `final_feature_daily` | EDA/모델링용 최종 병합 |

## 주의

- `grid_id`는 식별자와 조인키로만 사용한다.
- 산불이력은 feature가 아니라 target 테이블로 분리한다.
- `tmi_base_n`은 현재 `elevation/aspect` 기반 proxy다. 공식 FFDRI의 `elevation_index`, `aspect_index`가 확보되면 교체한다.
- 최종 모델 학습 시 `fire_label`, `occu_year`, `occu_mt`, `occu_date`는 target/검증용으로만 사용하고 feature에서 제외한다.

# DB 생성 및 데이터 적재 설명

이 문서는 `./db_query` 폴더에 있는 `create_database.py`, `load_data.py`의 역할과 실행 방법을 정리한 문서입니다.

## 1. 코드 작성 목적

기상 데이터 분석에 필요한 MySQL 데이터베이스와 테이블을 생성하고, 강원특별자치도 지역으로 필터링된 CSV 데이터를 DB에 적재하기 위해 작성했습니다.

- `create_database.py`: 데이터베이스와 테이블 생성
- `load_data.py`: CSV 데이터를 읽어 각 테이블에 적재

데이터 적재 시 CSV의 결측값은 DB에 `NULL`로 저장되도록 처리했습니다.

## 2. 실행 전 준비

프로젝트 루트에 `.env` 파일이 있어야 합니다.

```env
MYSQL_HOST=****
MYSQL_PORT=****
MYSQL_USER=****
MYSQL_PASSWORD=****
MYSQL_DATABASE=weather_fire_risk
```

필요한 패키지를 설치합니다.

```bash
pip install pandas pymysql python-dotenv
```

## 3. 실행 방법

프로젝트 루트에서 아래 순서대로 실행합니다.

```bash
python ./db_query/create_database.py
python ./db_query/load_data.py
```

`create_database.py`를 먼저 실행해야 테이블이 생성되고, 이후 `load_data.py`로 데이터를 적재할 수 있습니다.

## 4. 생성되는 테이블

총 5개의 테이블이 생성됩니다.

| 테이블명 | 설명 |
|---|---|
| `station_info` | AWS 관측 지점의 기본 정보 |
| `humidity` | 지점별 일 단위 습도 관측 데이터 |
| `precipitation` | 지점별 일 단위 강수량 관측 데이터 |
| `temperature` | 지점별 일 단위 기온 관측 데이터 |
| `wind` | 지점별 일 단위 바람 관측 데이터 |

`humidity`, `precipitation`, `temperature`, `wind` 테이블은 모두 `STN_ID`를 통해 `station_info` 테이블과 연결됩니다.

## 5. 테이블 상세 설명

### 5.1 `station_info`

AWS 관측 지점의 기본 정보를 담는 테이블입니다.

| 컬럼명 | 설명 |
|---|---|
| `STN_ID` | 지점번호 |
| `STN_KO` | 지점명 |
| `LAW_ADDR` | 법정동주소 |

### 5.2 `humidity`

지점별 일 단위 습도 정보를 담는 테이블입니다.

| 컬럼명 | 설명 |
|---|---|
| `TMA` | 관측일자, 형식: YYYYMMDD |
| `STN_ID` | 지점번호 |
| `LAT` | 위도, 단위: degree |
| `LON` | 경도, 단위: degree |
| `ALTD` | 노장 해발고도, 단위: m |
| `RHM_AVG` | 일평균상대습도, 단위: % |
| `RHM_MIN` | 최저상대습도, 단위: % |
| `RHM_MIN_OCUR_TMA` | 최저상대습도 발생시각, 형식: HHMM |

### 5.3 `precipitation`

지점별 일 단위 강수량 정보를 담는 테이블입니다.

| 컬럼명 | 설명 |
|---|---|
| `TMA` | 관측일자, 형식: YYYYMMDD |
| `STN_ID` | 지점번호 |
| `LAT` | 위도, 단위: degree |
| `LON` | 경도, 단위: degree |
| `ALTD` | 해발고도, 단위: m |
| `RN_DSUM` | 일합계강수량, 단위: mm |
| `RN_MAX_1HR` | 1시간최다강수량, 단위: mm |
| `RN_MAX_1HR_OCUR_TMA` | 1시간최다강수량 발생시각, 형식: HHMM |
| `RN_MAX_6HR` | 6시간최다강수량, 단위: mm |
| `RN_MAX_6HR_OCUR_TMA` | 6시간최다강수량 발생시각 |
| `RN_MAX_10M` | 10분최다강수량, 단위: mm |
| `RN_MAX_10M_OCUR_TMA` | 10분최다강수량 발생시각, 형식: HHMM |

### 5.4 `temperature`

지점별 일 단위 기온 정보를 담는 테이블입니다.

| 컬럼명 | 설명 |
|---|---|
| `TMA` | 관측일자, 형식: YYYYMMDD |
| `STN_ID` | 지점번호 |
| `LAT` | 위도, 단위: degree |
| `LON` | 경도, 단위: degree |
| `ALTD` | 해발고도, 단위: m |
| `TA_DAVG` | 일평균기온, 단위: ℃ |
| `TMX_DD` | 일최고기온, 단위: ℃ |
| `TMX_OCUR_TMA` | 일최고기온 발생시각, 형식: HHMM |
| `TMN_DD` | 일최저기온, 단위: ℃ |
| `TMN_OCUR_TMA` | 일최저기온 발생시각, 형식: HHMM |
| `MRNG_TMN` | 아침최저기온, 단위: ℃ |
| `MRNG_TMN_OCUR_TMA` | 아침최저기온 발생시각, 형식: HHMM |
| `DYTM_TMX` | 낮최고기온, 단위: ℃ |
| `DYTM_TMX_OCUR_TMA` | 낮최고기온 발생시각, 형식: HHMM |
| `NGHT_TMN` | 밤최저기온, 단위: ℃ |
| `NGHT_TMN_OCUR_TMA` | 밤최저기온 발생시각, 형식: HHMM |

### 5.5 `wind`

지점별 일 단위 바람 정보를 담는 테이블입니다.

| 컬럼명 | 설명 |
|---|---|
| `TMA` | 관측일자, 형식: YYYYMMDD |
| `STN_ID` | 지점번호 |
| `LAT` | 위도, 단위: degree |
| `LON` | 경도, 단위: degree |
| `ALTD` | 해발고도, 단위: m |
| `WS_DAVG` | 일평균풍속, 단위: m/s |
| `WS_INS_MAX` | 최대순간풍속, 단위: m/s |
| `WS_INS_MAX_OCUR_TMA` | 최대순간풍속 발생시각, 형식: HHMM |
| `WD_INS_MAX` | 최대순간풍속 시 풍향, 단위: degree |
| `WS_MAX` | 최대풍속, 단위: m/s |
| `WS_MAX_OCUR_TMA` | 최대풍속 발생시각, 형식: HHMM |
| `WD_MAX` | 최대풍속 시 풍향, 단위: degree |
| `WD_FRQ` | 최다풍향, 단위: degree |
| `WS_MIX` | 합성풍속, 단위: m/s |
| `WD_MIX` | 합성풍향, 단위: degree |

## 6. 참고 사항

- `station_info`를 먼저 적재해야 합니다.
- 나머지 4개 관측 테이블은 `station_info.STN_ID`를 참조합니다.
- 중복된 기본키 데이터가 들어오면 기존 행을 갱신하도록 처리했습니다.

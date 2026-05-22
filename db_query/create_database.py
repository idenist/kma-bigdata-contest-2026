import os
import mysql.connector
from dotenv import load_dotenv

"""
MySQL 데이터베이스/테이블 생성 스크립트

사용 전 .env 예시:
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=kma_fire_risk
DROP_EXISTING_TABLES=false

주의:
- CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블의 컬럼 타입/COMMENT를 변경하지 않습니다.
- 기존 테이블 구조를 새 정의로 다시 만들고 싶으면 .env에서 DROP_EXISTING_TABLES=true로 설정하세요.
"""

load_dotenv()

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
}

DB_NAME = os.getenv("MYSQL_DATABASE", "kma_fire_risk")
DROP_EXISTING_TABLES = os.getenv("DROP_EXISTING_TABLES", "false").lower() in {"1", "true", "yes", "y"}

TABLE_NAMES = [
    "humidity",
    "wind",
    "temperature",
    "precipitation",
    "station_info",
]


def get_connection(use_database: bool = False):
    config = MYSQL_CONFIG.copy()
    if use_database:
        config["database"] = DB_NAME
    return mysql.connector.connect(**config)


def execute_statements(cursor, statements):
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            cursor.execute(stmt)


CREATE_DATABASE_SQL = f"""
CREATE DATABASE IF NOT EXISTS `{DB_NAME}`
DEFAULT CHARACTER SET utf8mb4
DEFAULT COLLATE utf8mb4_0900_ai_ci;
"""


CREATE_TABLE_SQL_LIST = [
    """
    CREATE TABLE IF NOT EXISTS `station_info` (
        `STN_ID` INT NOT NULL COMMENT '지점번호',
        `LAT` DECIMAL(10, 6) NOT NULL COMMENT '위도(degree)',
        `LON` DECIMAL(10, 6) NOT NULL COMMENT '경도(degree)',
        `STN_SP` VARCHAR(50) NULL COMMENT '지점특성코드',
        `HT` DECIMAL(10, 3) NULL COMMENT '노장해발고도(m)',
        `HT_WD` DECIMAL(10, 3) NULL COMMENT '풍향/풍속계지상높이(m)',
        `LAU` VARCHAR(50) NULL COMMENT '라우 시스템 번호',
        `STN_AD` VARCHAR(50) NULL COMMENT '관리관서번호',
        `STN_KO` VARCHAR(100) NULL COMMENT '지점명(한글)',
        `STN_EN` VARCHAR(100) NULL COMMENT '지점명(영문)',
        `FCT_ID` VARCHAR(50) NULL COMMENT '예보구역코드',
        `LAW_ID` VARCHAR(50) NULL COMMENT '법정동코드',
        `BASIN` VARCHAR(50) NULL COMMENT '수계코드',
        `LAW_ADDR` VARCHAR(255) NULL COMMENT '법정동주소',
        PRIMARY KEY (`STN_ID`, `LAT`, `LON`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='관측소 기본정보';
    """,

    """
    CREATE TABLE IF NOT EXISTS `precipitation` (
        `STN_ID` INT NOT NULL COMMENT '지점번호',
        `LAT` DECIMAL(10, 6) NOT NULL COMMENT '위도(degree)',
        `LON` DECIMAL(10, 6) NOT NULL COMMENT '경도(degree)',
        `TMA` CHAR(8) NOT NULL COMMENT '날짜(yyyymmdd)',
        `ALTD` DECIMAL(10, 3) NULL COMMENT '해발고도(m)',
        `RN_DSUM` DECIMAL(10, 3) NULL COMMENT '합계강수량(mm)',
        `RN_MAX_1HR` DECIMAL(10, 3) NULL COMMENT '1시간최다강수량(mm)',
        `RN_MAX_1HR_OCUR_TMA` VARCHAR(4) NULL COMMENT '1시간최다강수량 발생시각',
        `RN_MAX_6HR` DECIMAL(10, 3) NULL COMMENT '6시간최다강수량(mm)',
        `RN_MAX_6HR_OCUR_TMA` VARCHAR(4) NULL COMMENT '6시간최다강수량 발생시각',
        `RN_MAX_10M` DECIMAL(10, 3) NULL COMMENT '10분최다강수량(mm)',
        `RN_MAX_10M_OCUR_TMA` VARCHAR(4) NULL COMMENT '10분최다강수량 발생시각',
        PRIMARY KEY (`STN_ID`, `LAT`, `LON`, `TMA`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='강수량';
    """,

    """
    CREATE TABLE IF NOT EXISTS `temperature` (
        `STN_ID` INT NOT NULL COMMENT '지점번호',
        `LAT` DECIMAL(10, 6) NOT NULL COMMENT '위도(degree)',
        `LON` DECIMAL(10, 6) NOT NULL COMMENT '경도(degree)',
        `TMA` CHAR(8) NOT NULL COMMENT '날짜(yyyymmdd)',
        `ALTD` DECIMAL(10, 3) NULL COMMENT '해발고도(m)',
        `TA_DAVG` DECIMAL(10, 3) NULL COMMENT '평균기온(℃)',
        `TMX_DD` DECIMAL(10, 3) NULL COMMENT '최고기온(℃)',
        `TMX_OCUR_TMA` CHAR(4) NULL COMMENT '최고기온 발생시각',
        `TMN_DD` DECIMAL(10, 3) NULL COMMENT '최저기온(℃)',
        `TMN_OCUR_TMA` CHAR(4) NULL COMMENT '최저기온 발생시각',
        `MRNG_TMN` DECIMAL(10, 3) NULL COMMENT '아침최저기온(℃)',
        `MRNG_TMN_OCUR_TMA` CHAR(4) NULL COMMENT '아침최저기온 발생시각',
        `DYTM_TMX` DECIMAL(10, 3) NULL COMMENT '낮최고기온(℃)',
        `DYTM_TMX_OCUR_TMA` CHAR(4) NULL COMMENT '낮최고기온 발생시각',
        `NGHT_TMN` DECIMAL(10, 3) NULL COMMENT '밤최저기온 (℃)',
        `NGHT_TMN_OCUR_TMA` CHAR(4) NULL COMMENT '밤최저기온 발생시각',
        PRIMARY KEY (`STN_ID`, `LAT`, `LON`, `TMA`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='기온';
    """,

    """
    CREATE TABLE IF NOT EXISTS `wind` (
        `STN_ID` INT NOT NULL COMMENT '지점번호',
        `LAT` DECIMAL(10, 6) NOT NULL COMMENT '위도(degree)',
        `LON` DECIMAL(10, 6) NOT NULL COMMENT '경도(degree)',
        `TMA` CHAR(8) NOT NULL COMMENT '날짜(yyyymmdd)',
        `ALTD` DECIMAL(10, 3) NULL COMMENT '해발고도(m)',
        `WS_DAVG` DECIMAL(10, 3) NULL COMMENT '평균풍속(m/s)',
        `WS_INS_MAX` DECIMAL(10, 3) NULL COMMENT '최대순간풍속(m/s)',
        `WS_INS_MAX_OCUR_TMA` CHAR(4) NULL COMMENT '최대순간풍속 발생시각',
        `WD_INS_MAX` DECIMAL(10, 3) NULL COMMENT '최대순간풍속시풍향(degree)',
        `WS_MAX` DECIMAL(10, 3) NULL COMMENT '최대풍속(m/s)',
        `WS_MAX_OCUR_TMA` CHAR(4) NULL COMMENT '최대풍속 발생시각',
        `WD_MAX` DECIMAL(10, 3) NULL COMMENT '최대풍속시풍향(degree)',
        `WD_FRQ` DECIMAL(10, 3) NULL COMMENT '최다풍향(degree)',
        `WS_MIX` DECIMAL(10, 3) NULL COMMENT '합성풍속(m/s)',
        `WD_MIX` DECIMAL(10, 3) NULL COMMENT '합성풍향(degree)',
        PRIMARY KEY (`STN_ID`, `LAT`, `LON`, `TMA`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='바람';
    """,

    """
    CREATE TABLE IF NOT EXISTS `humidity` (
        `STN_ID` INT NOT NULL COMMENT '지점번호',
        `LAT` DECIMAL(10, 6) NOT NULL COMMENT '위도(degree)',
        `LON` DECIMAL(10, 6) NOT NULL COMMENT '경도(degree)',
        `TMA` CHAR(8) NOT NULL COMMENT '날짜(yyyymmdd)',
        `ALTD` DECIMAL(10, 3) NULL COMMENT '해발고도(m)',
        `RHM_AVG` DECIMAL(10, 3) NULL COMMENT '평균상대습도(%)',
        `RHM_MIN` DECIMAL(10, 3) NULL COMMENT '최저상대습도(%)',
        `RHM_MIN_OCUR_TMA` CHAR(4) NULL COMMENT '최저상대습도 발생시각',
        PRIMARY KEY (`STN_ID`, `LAT`, `LON`, `TMA`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='습도';
    """,
]


def drop_existing_tables(cursor):
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table_name in TABLE_NAMES:
        cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        print(f"테이블 삭제 완료 또는 존재하지 않음: {table_name}")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")


def main():
    conn = get_connection(use_database=False)
    cursor = conn.cursor()

    cursor.execute(CREATE_DATABASE_SQL)
    conn.commit()

    cursor.close()
    conn.close()

    conn = get_connection(use_database=True)
    cursor = conn.cursor()

    if DROP_EXISTING_TABLES:
        print("DROP_EXISTING_TABLES=true: 기존 테이블을 삭제한 뒤 다시 생성합니다.")
        drop_existing_tables(cursor)
        conn.commit()

    execute_statements(cursor, CREATE_TABLE_SQL_LIST)
    conn.commit()

    cursor.close()
    conn.close()

    print(f"데이터베이스 및 테이블 생성 완료: {DB_NAME}")


if __name__ == "__main__":
    main()

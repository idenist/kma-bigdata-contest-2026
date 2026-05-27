# create_database.py

import os
import pymysql
from dotenv import load_dotenv


def get_env_variable(name: str) -> str:
    """
    .env에서 필수 환경변수를 가져온다.
    값이 없으면 명확한 에러를 발생시킨다.
    """
    value = os.getenv(name)

    if value is None or value.strip() == "":
        raise ValueError(f".env 파일에 {name} 값이 없습니다.")

    return value


def get_connection(database: str | None = None):
    """
    MySQL 연결 객체를 생성한다.
    database가 None이면 특정 DB를 선택하지 않고 접속한다.
    """
    config = {
        "host": get_env_variable("MYSQL_HOST"),
        "port": int(get_env_variable("MYSQL_PORT")),
        "user": get_env_variable("MYSQL_USER"),
        "password": get_env_variable("MYSQL_PASSWORD"),
        "charset": "utf8mb4",
        "autocommit": True,
    }

    if database is not None:
        config["database"] = database

    return pymysql.connect(**config)


def create_database(database_name: str):
    """
    데이터베이스가 없으면 생성한다.
    """
    sql = f"""
    CREATE DATABASE IF NOT EXISTS `{database_name}`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;
    """

    conn = get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        print(f"데이터베이스 생성 또는 확인 완료: {database_name}")
    finally:
        conn.close()


def create_tables(database_name: str):
    """
    weather_fire_risk 데이터베이스 안에 5개 테이블을 생성한다.
    """
    conn = get_connection(database=database_name)

    table_queries = [
        """
        CREATE TABLE IF NOT EXISTS station_info (
            STN_ID INT NOT NULL COMMENT '지점번호',
            STN_KO VARCHAR(15) COMMENT '지점명(한글)',
            LAW_ADDR VARCHAR(50) COMMENT '법정동주소',

            PRIMARY KEY (STN_ID)
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_general_ci
          COMMENT='AWS 지점 정보';
        """,

        """
        CREATE TABLE IF NOT EXISTS humidity (
            TMA VARCHAR(8) NOT NULL COMMENT '시각(년월일)',
            STN_ID INT NOT NULL COMMENT '지점번호',
            LAT FLOAT COMMENT '위도(degree)',
            LON FLOAT COMMENT '경도(degree)',
            ALTD FLOAT COMMENT '노장 해발고도(m)',
            RHM_AVG FLOAT COMMENT '일평균상대습도, 결측치 -99.9',
            RHM_MIN FLOAT COMMENT '최저상대습도, 결측치 -99.9',
            RHM_MIN_OCUR_TMA VARCHAR(4) COMMENT '최저상대습도 발생시각, RHM_MIN이 -99.9이면 결측치',

            PRIMARY KEY (TMA, STN_ID),

            CONSTRAINT fk_humidity_station_info
                FOREIGN KEY (STN_ID)
                REFERENCES station_info (STN_ID)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_general_ci
          COMMENT='습도 관측 데이터';
        """,

        """
        CREATE TABLE IF NOT EXISTS precipitation (
            TMA VARCHAR(8) NOT NULL COMMENT '시각(년월일)',
            STN_ID INT NOT NULL COMMENT '지점번호',
            LAT FLOAT COMMENT '위도(degree)',
            LON FLOAT COMMENT '경도(degree)',
            ALTD FLOAT COMMENT '해발고도(m)',
            RN_DSUM FLOAT COMMENT '일합계강수량(mm), 결측치 -99.9',
            RN_MAX_1HR FLOAT COMMENT '1시간최다강수량(mm), 결측치 -99.9',
            RN_MAX_1HR_OCUR_TMA VARCHAR(4) COMMENT '1시간최다강수량 발생시각, 결측치 -99.9',
            RN_MAX_6HR FLOAT COMMENT '6시간최다강수량(mm), 결측치 -99.9',
            RN_MAX_6HR_OCUR_TMA VARCHAR(12) COMMENT '6시간최다강수량 발생시각, 결측치 -999',
            RN_MAX_10M FLOAT COMMENT '10분최다강수량(mm), 결측치 -99.9',
            RN_MAX_10M_OCUR_TMA VARCHAR(4) COMMENT '10분최다강수량 발생시각, 결측치 -99.9',

            PRIMARY KEY (TMA, STN_ID),

            CONSTRAINT fk_precipitation_station_info
                FOREIGN KEY (STN_ID)
                REFERENCES station_info (STN_ID)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_general_ci
          COMMENT='강수량 관측 데이터';
        """,

        """
        CREATE TABLE IF NOT EXISTS temperature (
            TMA VARCHAR(8) NOT NULL COMMENT '시각(년월일)',
            STN_ID INT NOT NULL COMMENT '지점번호',
            LAT FLOAT COMMENT '위도(degree)',
            LON FLOAT COMMENT '경도(degree)',
            ALTD FLOAT COMMENT '해발고도(m)',
            TA_DAVG FLOAT COMMENT '일평균기온(℃), 결측치 -99.9',
            TMX_DD FLOAT COMMENT '일최고기온(℃), 결측치 -99.9',
            TMX_OCUR_TMA VARCHAR(4) COMMENT '일최고기온 발생시각, TMX_DD가 -99.9이면 결측치',
            TMN_DD FLOAT COMMENT '일최저기온(℃), 결측치 -99.9',
            TMN_OCUR_TMA VARCHAR(4) COMMENT '일최저기온 발생시각, TMN_DD가 -99.9이면 결측치',
            MRNG_TMN FLOAT COMMENT '아침최저기온(℃), 결측치 -99.9',
            MRNG_TMN_OCUR_TMA VARCHAR(4) COMMENT '아침최저기온 발생시각, MRNG_TMN이 -99.9이면 결측치',
            DYTM_TMX FLOAT COMMENT '낮최고기온(℃), 결측치 -99.9',
            DYTM_TMX_OCUR_TMA VARCHAR(4) COMMENT '낮최고기온 발생시각, DYTM_TMX가 -99.9이면 결측치',
            NGHT_TMN FLOAT COMMENT '밤최저기온(℃), 결측치 -99.9',
            NGHT_TMN_OCUR_TMA VARCHAR(4) COMMENT '밤최저기온 발생시각, NGHT_TMN이 -99.9이면 결측치',

            PRIMARY KEY (TMA, STN_ID),

            CONSTRAINT fk_temperature_station_info
                FOREIGN KEY (STN_ID)
                REFERENCES station_info (STN_ID)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_general_ci
          COMMENT='기온 관측 데이터';
        """,

        """
        CREATE TABLE IF NOT EXISTS wind (
            TMA VARCHAR(8) NOT NULL COMMENT '시각(년월일)',
            STN_ID INT NOT NULL COMMENT '지점번호',
            LAT FLOAT COMMENT '위도(degree)',
            LON FLOAT COMMENT '경도(degree)',
            ALTD FLOAT COMMENT '해발고도(m)',
            WS_DAVG FLOAT COMMENT '일평균풍속(m/s), 결측치 -99.9',
            WS_INS_MAX FLOAT COMMENT '최대순간풍속(m/s), 결측치 -99.9',
            WS_INS_MAX_OCUR_TMA VARCHAR(4) COMMENT '최대순간풍속 발생시각, WS_INS_MAX가 -99.9이면 결측치',
            WD_INS_MAX FLOAT COMMENT '최대순간풍속시풍향(degree), 결측치 -99.9',
            WS_MAX FLOAT COMMENT '최대풍속(m/s), 결측치 -99.9',
            WS_MAX_OCUR_TMA VARCHAR(4) COMMENT '최대풍속 발생시각, WS_MAX가 -99.9이면 결측치',
            WD_MAX FLOAT COMMENT '최대풍속시풍향(degree), 결측치 -99.9',
            WD_FRQ FLOAT COMMENT '최다풍향(degree), 결측치 -99.9',
            WS_MIX FLOAT COMMENT '합성풍속(m/s), 결측치 -999',
            WD_MIX FLOAT COMMENT '합성풍향(degree), 결측치 -99.9',

            PRIMARY KEY (TMA, STN_ID),

            CONSTRAINT fk_wind_station_info
                FOREIGN KEY (STN_ID)
                REFERENCES station_info (STN_ID)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        ) ENGINE=InnoDB
          DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_general_ci
          COMMENT='바람 관측 데이터';
        """
    ]

    try:
        with conn.cursor() as cursor:
            for query in table_queries:
                cursor.execute(query)

        print("테이블 생성 또는 확인 완료")
        print("- station_info")
        print("- humidity")
        print("- precipitation")
        print("- temperature")
        print("- wind")

    finally:
        conn.close()


def main():
    load_dotenv()

    database_name = get_env_variable("MYSQL_DATABASE")

    create_database(database_name)
    create_tables(database_name)


if __name__ == "__main__":
    main()
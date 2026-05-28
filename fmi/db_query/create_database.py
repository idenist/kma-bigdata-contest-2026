import os
import mysql.connector
from dotenv import load_dotenv

"""
MySQL 임상도 테이블 생성 스크립트

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
    "password": os.getenv("MYSQL_PASSWORD", "1234"),
}

DB_NAME = os.getenv("MYSQL_DATABASE", "kma_fire_risk")
DROP_EXISTING_TABLES = os.getenv("DROP_EXISTING_TABLES", "false").lower() in {"1", "true", "yes", "y"}

TABLE_NAMES = ["forest_fmi"]


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
    CREATE TABLE IF NOT EXISTS `forest_fmi` (
        `polygon_id`   INT            NOT NULL COMMENT '구역 고유 ID (다른 데이터와 조인 key)',
        `STORUNST`     TINYINT        NULL     COMMENT '임목존재코드 (1=입목지, 2=무립목지, 0=비산림)',
        `FROR_CD`      TINYINT        NULL     COMMENT '임종코드 (1=인공림, 2=천연림)',
        `FRTP_CD`      TINYINT        NULL     COMMENT '임상코드 (1=침엽, 2=활엽, 3=혼효, 4=죽림, 0=무립목)',
        `FRTP_NM`      VARCHAR(20)    NULL     COMMENT '임상명 (침엽수림/활엽수림/혼효림/죽림/무립목지)',
        `FMI`          TINYINT        NOT NULL COMMENT '임상지수 (침엽=10, 혼효=3, 활엽=2, 죽림=1, 비산림=0)',
        `KOFTR_NM`     VARCHAR(50)    NULL     COMMENT '수종명 (소나무, 잣나무, 낙엽송, 신갈나무 등)',
        `AGCLS_CD`     TINYINT        NULL     COMMENT '영급코드 (1~9, 10년 단위)',
        `AGCLS_NM`     VARCHAR(20)    NULL     COMMENT '영급명 (1영급=1~10년생)',
        `DMCLS_CD`     TINYINT        NULL     COMMENT '경급코드',
        `DMCLS_NM`     VARCHAR(20)    NULL     COMMENT '경급명 (치수/소경목/중경목/대경목)',
        `DNST_CD`      CHAR(1)        NULL     COMMENT '밀도코드 (A=소, B=중, C=밀)',
        `DNST_NM`      VARCHAR(10)    NULL     COMMENT '밀도명',
        `HEIGHT`       TINYINT        NULL     COMMENT '임분고코드 (2m 단위)',
        `HEIGHT_NM`    VARCHAR(30)    NULL     COMMENT '임분고명',
        `갱신년도`     SMALLINT       NULL     COMMENT '해당 구역 마지막 갱신 연도',
        `Shape_Area`   DOUBLE         NULL     COMMENT '구역 면적 (㎡)',
        `centroid_lon` DECIMAL(10, 6) NOT NULL COMMENT '중심점 경도 (WGS84) — 기상 관측소 매핑용',
        `centroid_lat` DECIMAL(10, 6) NOT NULL COMMENT '중심점 위도 (WGS84) — 기상 관측소 매핑용',
        PRIMARY KEY (`polygon_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='임상도 및 임상지수(FMI) — 강원도 562,276 구역';
    """
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

    print(f"데이터베이스 및 테이블 생성 완료: {DB_NAME}.forest_fmi")


if __name__ == "__main__":
    main()

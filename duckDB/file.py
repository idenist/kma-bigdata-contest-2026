from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = PROJECT_ROOT / "duckDB" / "pffdri.duckdb"


con = duckdb.connect(str(DUCKDB_PATH))

print(con.execute("SHOW TABLES").df())

print(
    con.execute(
        """
        SELECT month, COUNT(*) AS n, COUNT(DISTINCT grid_id) AS grid_count
        FROM grid_date_master
        GROUP BY month
        ORDER BY month
        """
    ).df()
)

con.close()

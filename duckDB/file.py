import duckdb

con = duckdb.connect(
    r"D:\bootcamp\공모전\kma-bigdata-contest-2026-main\duckDB\pffdri.duckdb"
)

print(con.execute("SHOW TABLES").df())

print(con.execute("""
SELECT month, COUNT(*) AS n, COUNT(DISTINCT grid_id) AS grid_count
FROM grid_date_master
GROUP BY month
ORDER BY month
""").df())
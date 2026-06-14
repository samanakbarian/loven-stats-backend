from google.cloud import bigquery


PROJECT = "granskaren-d51a1"
TABLE_ID = f"{PROJECT}.raw_sports.swehockey_seasons"


SEASON_ROWS_SQL = """
SELECT 'ha_2324' season_key, 'HockeyAllsvenskan 2023/24' season_name,
       'HA' league, 14678 regular_season_id, 15784 playoff_id,
       DATE '2023-09-15' start_date, DATE '2024-03-10' end_date, FALSE is_active
UNION ALL
SELECT 'shl_2425', 'SHL 2024/25', 'SHL', 15977, 17557,
       DATE '2024-09-21', DATE '2025-03-11', FALSE
UNION ALL
SELECT 'ha_2425', 'HockeyAllsvenskan 2024/25', 'HA', 15986, 17571,
       DATE '2024-09-20', DATE '2025-03-07', FALSE
UNION ALL
SELECT 'shl_2526', 'SHL 2025/26', 'SHL', 18263, 19791,
       DATE '2025-09-13', DATE '2026-03-10', FALSE
UNION ALL
SELECT 'ha_2526', 'HockeyAllsvenskan 2025/26', 'HA', 18266, 19979,
       DATE '2025-09-19', DATE '2026-03-06', FALSE
UNION ALL
SELECT 'shl_2627', 'SHL 2026/27', 'SHL', 20961, NULL,
       DATE '2026-09-19', DATE '2027-03-16', TRUE
UNION ALL
SELECT 'ha_2627', 'HockeyAllsvenskan 2026/27', 'HA', 20962, NULL,
       DATE '2026-09-18', DATE '2027-03-05', TRUE
"""


def main() -> None:
    client = bigquery.Client(project=PROJECT)
    query = f"""
    MERGE `{TABLE_ID}` target
    USING ({SEASON_ROWS_SQL}) source
      ON target.season_key = source.season_key
    WHEN MATCHED THEN UPDATE SET
      season_name = source.season_name,
      league = source.league,
      regular_season_id = source.regular_season_id,
      playoff_id = source.playoff_id,
      start_date = source.start_date,
      end_date = source.end_date,
      is_active = source.is_active
    WHEN NOT MATCHED THEN INSERT (
      season_key,
      season_name,
      league,
      regular_season_id,
      playoff_id,
      start_date,
      end_date,
      is_active
    ) VALUES (
      source.season_key,
      source.season_name,
      source.league,
      source.regular_season_id,
      source.playoff_id,
      source.start_date,
      source.end_date,
      source.is_active
    )
    """
    client.query(query).result()

    rows = client.query(
        f"""
        SELECT season_key, league, regular_season_id, playoff_id, is_active
        FROM `{TABLE_ID}`
        ORDER BY start_date, league
        """
    ).result()
    for row in rows:
        print(dict(row))


if __name__ == "__main__":
    main()

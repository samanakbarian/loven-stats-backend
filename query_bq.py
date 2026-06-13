from google.cloud import bigquery
import pandas as pd
import json

client = bigquery.Client()
project = client.project

queries = [
    """
    SELECT rank, team_name, games_played, wins, ot_wins, ot_losses, losses, points, scraped_at
    FROM `granskaren-d51a1.raw_sports.swehockey_standings`
    ORDER BY scraped_at DESC, rank ASC
    LIMIT 20
    """,
    """
    SELECT player_name, team_code, games_played, goals, assists, points, scraped_at
    FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
    ORDER BY scraped_at DESC, points DESC
    LIMIT 20
    """,
    """
    SELECT COUNT(*) as count, player_name, points
    FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM `granskaren-d51a1.raw_sports.swehockey_player_stats`)
    GROUP BY player_name, points
    ORDER BY count DESC
    LIMIT 10
    """
]

for i, q in enumerate(queries):
    print(f"--- Query {i+1} ---")
    try:
        df = client.query(q).to_dataframe()
        print(df.to_string())
    except Exception as e:
        print(f"Error: {e}")
    print("\n")

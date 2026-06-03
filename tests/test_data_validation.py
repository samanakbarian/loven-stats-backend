import pytest
from google.cloud import bigquery
import os

# Project config
GCP_PROJECT = os.environ.get("GCP_PROJECT", "granskaren-d51a1")
DATASET = "raw_sports"

@pytest.fixture(scope="module")
def bq_client():
    return bigquery.Client(project=GCP_PROJECT)

def test_no_duplicate_players_in_regular_season(bq_client):
    """Säkerställ att inga spelare ligger inne som dubbletter för en given säsong."""
    query = f"""
    SELECT season_group_id, player_name, COUNT(*) as c
    FROM `{GCP_PROJECT}.{DATASET}.swehockey_player_stats`
    WHERE season_group_id IN (14678, 18266)
    GROUP BY season_group_id, player_name
    HAVING c > 1
    """
    results = list(bq_client.query(query))
    # If there are duplicates, fail the test
    assert len(results) == 0, f"Hittade dubbletter av spelare: {[dict(r) for r in results]}"

def test_schedule_data_not_garbled(bq_client):
    """Säkerställ att spelschemat är korrekt parsat (inga gigantiska layout-strängar)."""
    query = f"""
    SELECT season_group_id, home_team, away_team 
    FROM `{GCP_PROJECT}.{DATASET}.swehockey_schedule`
    WHERE season_group_id IN (14678, 18266) AND (LENGTH(home_team) > 50 OR LENGTH(away_team) > 50)
    """
    results = list(bq_client.query(query))
    assert len(results) == 0, f"Spelschema innehåller felaktigt parsade lagnamn: {len(results)} rader."

def test_myles_powell_goals_ha_2324(bq_client):
    """Validera exakt mål-antal för Myles Powell i grundserien 23/24 (season 14678)."""
    query = f"""
    SELECT goals 
    FROM `{GCP_PROJECT}.{DATASET}.swehockey_player_stats`
    WHERE season_group_id = 14678 AND player_name LIKE '%Powell%'
    """
    results = list(bq_client.query(query))
    assert len(results) == 1, "Förväntade exakt 1 rad för Myles Powell."
    goals = int(results[0]['goals'])
    
    # 20 mål i grundserien (Slutspel exkluderat från denna season_group_id)
    assert goals == 20, f"Myles Powell förväntades ha 20 mål i grundserien, men har {goals}."

def test_season_config_mapping(bq_client):
    """Säkerställ att season_key är korrekt kopplat till rätt season_group_id."""
    query = f"""
    SELECT season_key, regular_season_id 
    FROM `{GCP_PROJECT}.{DATASET}.swehockey_seasons`
    WHERE season_key IN ('ha_2324', 'ha_2526')
    """
    results = {row['season_key']: row['regular_season_id'] for row in bq_client.query(query)}
    
    assert results.get('ha_2324') == 14678, "ha_2324 har fel regular_season_id!"
    assert results.get('ha_2526') == 18266, "ha_2526 har fel regular_season_id!"

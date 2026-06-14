from functions.etl_runtime import BigQueryRunLogger, checks_passed, validate_rows


class FakeBigQueryClient:
    project = "test-project"

    def __init__(self):
        self.inserted_rows = []

    def create_dataset(self, dataset, exists_ok):
        return dataset

    def create_table(self, table, exists_ok):
        return table

    def insert_rows_json(self, table_id, rows):
        self.inserted_rows.extend({"table_id": table_id, **row} for row in rows)
        return []


def test_validate_rows_accepts_complete_unique_snapshot():
    rows = [
        {"season_group_id": 1, "team_code": "BIF", "player_name": "A"},
        {"season_group_id": 1, "team_code": "BIF", "player_name": "B"},
    ]

    checks = validate_rows(
        rows,
        required_fields=("season_group_id", "team_code", "player_name"),
        key_fields=("season_group_id", "team_code", "player_name"),
    )

    assert checks_passed(checks)
    assert {check.name: check.observed_value for check in checks} == {
        "rows_present": "2",
        "required_fields_complete": "0",
        "business_keys_unique": "0",
    }


def test_validate_rows_rejects_empty_snapshot():
    checks = validate_rows(
        [],
        required_fields=("season_group_id", "player_name"),
        key_fields=("season_group_id", "player_name"),
    )

    assert not checks_passed(checks)
    assert next(check for check in checks if check.name == "rows_present").passed is False


def test_validate_rows_allows_warning_for_expected_empty_snapshot():
    checks = validate_rows(
        [],
        required_fields=("season_group_id", "player_name"),
        key_fields=("season_group_id", "player_name"),
        empty_severity="WARNING",
    )

    assert checks_passed(checks)
    rows_present = next(check for check in checks if check.name == "rows_present")
    assert rows_present.passed is False
    assert rows_present.severity == "WARNING"


def test_validate_rows_rejects_missing_fields_and_duplicate_keys():
    rows = [
        {"season_group_id": 1, "team_code": "BIF", "player_name": "A"},
        {"season_group_id": 1, "team_code": "BIF", "player_name": "A"},
        {"season_group_id": 1, "team_code": "", "player_name": "B"},
    ]

    checks = validate_rows(
        rows,
        required_fields=("season_group_id", "team_code", "player_name"),
        key_fields=("season_group_id", "team_code", "player_name"),
    )
    by_name = {check.name: check for check in checks}

    assert not checks_passed(checks)
    assert by_name["required_fields_complete"].observed_value == "1"
    assert by_name["business_keys_unique"].observed_value == "1"


def test_run_logger_writes_append_only_start_and_finish_events():
    client = FakeBigQueryClient()
    logger = BigQueryRunLogger(client)

    run_id = logger.start_run(
        pipeline_name="swehockey_stats",
        source="swehockey",
        season_group_ids=[20962, 20961, 20962],
    )
    logger.finish_run(
        run_id=run_id,
        status="SUCCESS",
        fetched_rows=100,
        loaded_rows=100,
        failed_steps=0,
        metadata={"types": {"players": {"rows": 100}}},
    )

    run_events = [
        row for row in client.inserted_rows if row["table_id"].endswith(".ingestion_runs")
    ]
    assert [event["status"] for event in run_events] == ["RUNNING", "SUCCESS"]
    assert {event["run_id"] for event in run_events} == {run_id}
    assert run_events[-1]["pipeline_name"] == "swehockey_stats"
    assert run_events[-1]["season_group_ids"] == [20961, 20962]

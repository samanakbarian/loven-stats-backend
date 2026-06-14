import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from google.cloud import bigquery


OPS_DATASET = os.environ.get("BQ_OPS_DATASET", "raw_ops")
OPS_LOCATION = os.environ.get("BQ_LOCATION", "europe-west1")


@dataclass(frozen=True)
class QualityCheck:
    name: str
    passed: bool
    severity: str
    observed_value: str
    expected_value: str
    details: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_rows(
    rows: list[dict[str, Any]],
    *,
    required_fields: Iterable[str],
    key_fields: Iterable[str],
    empty_severity: str = "ERROR",
) -> list[QualityCheck]:
    required = tuple(required_fields)
    keys = tuple(key_fields)
    missing_required = 0
    duplicate_count = 0
    seen_keys: set[tuple[str, ...]] = set()

    for row in rows:
        if any(row.get(field) in (None, "") for field in required):
            missing_required += 1

        key = tuple(str(row.get(field, "")).strip().casefold() for field in keys)
        if key in seen_keys:
            duplicate_count += 1
        else:
            seen_keys.add(key)

    return [
        QualityCheck(
            name="rows_present",
            passed=bool(rows),
            severity=empty_severity,
            observed_value=str(len(rows)),
            expected_value="> 0",
        ),
        QualityCheck(
            name="required_fields_complete",
            passed=missing_required == 0,
            severity="ERROR",
            observed_value=str(missing_required),
            expected_value="0",
            details="Antal rader med saknade obligatoriska fält.",
        ),
        QualityCheck(
            name="business_keys_unique",
            passed=duplicate_count == 0,
            severity="ERROR",
            observed_value=str(duplicate_count),
            expected_value="0",
            details="Antal dubbletter inom den hämtade snapshoten.",
        ),
    ]


def checks_passed(checks: Iterable[QualityCheck]) -> bool:
    return all(check.passed or check.severity != "ERROR" for check in checks)


class BigQueryRunLogger:
    def __init__(
        self,
        client: bigquery.Client,
        *,
        dataset_id: str = OPS_DATASET,
        location: str = OPS_LOCATION,
    ):
        self.client = client
        self.dataset_id = dataset_id
        self.location = location
        self.project = client.project
        self._started_at_by_run: dict[str, datetime] = {}
        self._context_by_run: dict[str, dict[str, Any]] = {}
        self._ensure_tables()

    @property
    def ingestion_table_id(self) -> str:
        return f"{self.project}.{self.dataset_id}.ingestion_runs"

    @property
    def quality_table_id(self) -> str:
        return f"{self.project}.{self.dataset_id}.data_quality_runs"

    def _ensure_tables(self) -> None:
        dataset = bigquery.Dataset(f"{self.project}.{self.dataset_id}")
        dataset.location = self.location
        self.client.create_dataset(dataset, exists_ok=True)

        ingestion_schema = [
            bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("pipeline_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("trigger_type", "STRING"),
            bigquery.SchemaField("pipeline_version", "STRING"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("finished_at", "TIMESTAMP"),
            bigquery.SchemaField("fetched_rows", "INTEGER"),
            bigquery.SchemaField("loaded_rows", "INTEGER"),
            bigquery.SchemaField("failed_steps", "INTEGER"),
            bigquery.SchemaField("season_group_ids", "INTEGER", mode="REPEATED"),
            bigquery.SchemaField("metadata_json", "STRING"),
            bigquery.SchemaField("error_message", "STRING"),
        ]
        quality_schema = [
            bigquery.SchemaField("quality_run_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("pipeline_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("entity_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("season_group_id", "INTEGER"),
            bigquery.SchemaField("check_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("severity", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("passed", "BOOLEAN", mode="REQUIRED"),
            bigquery.SchemaField("observed_value", "STRING"),
            bigquery.SchemaField("expected_value", "STRING"),
            bigquery.SchemaField("details", "STRING"),
            bigquery.SchemaField("checked_at", "TIMESTAMP", mode="REQUIRED"),
        ]

        self.client.create_table(
            bigquery.Table(self.ingestion_table_id, schema=ingestion_schema),
            exists_ok=True,
        )
        self.client.create_table(
            bigquery.Table(self.quality_table_id, schema=quality_schema),
            exists_ok=True,
        )

    def start_run(
        self,
        *,
        pipeline_name: str,
        source: str,
        season_group_ids: Iterable[int],
        trigger_type: str = "http",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        run_id = str(uuid4())
        started_at = utc_now()
        self._started_at_by_run[run_id] = started_at
        season_ids = sorted(set(season_group_ids))
        self._context_by_run[run_id] = {
            "pipeline_name": pipeline_name,
            "source": source,
            "trigger_type": trigger_type,
            "season_group_ids": season_ids,
        }
        row = {
            "event_id": str(uuid4()),
            "run_id": run_id,
            "pipeline_name": pipeline_name,
            "source": source,
            "trigger_type": trigger_type,
            "pipeline_version": os.environ.get("K_REVISION")
            or os.environ.get("GIT_SHA")
            or "local",
            "status": "RUNNING",
            "started_at": started_at.isoformat(),
            "fetched_rows": 0,
            "loaded_rows": 0,
            "failed_steps": 0,
            "season_group_ids": season_ids,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        }
        errors = self.client.insert_rows_json(self.ingestion_table_id, [row])
        if errors:
            raise RuntimeError(f"Failed to create ingestion run: {errors}")
        return run_id

    def record_checks(
        self,
        *,
        run_id: str,
        pipeline_name: str,
        entity_name: str,
        season_group_id: int,
        checks: Iterable[QualityCheck],
    ) -> None:
        checked_at = utc_now().isoformat()
        rows = [
            {
                "quality_run_id": str(uuid4()),
                "run_id": run_id,
                "pipeline_name": pipeline_name,
                "entity_name": entity_name,
                "season_group_id": season_group_id,
                "check_name": check.name,
                "severity": check.severity,
                "passed": check.passed,
                "observed_value": check.observed_value,
                "expected_value": check.expected_value,
                "details": check.details,
                "checked_at": checked_at,
            }
            for check in checks
        ]
        errors = self.client.insert_rows_json(self.quality_table_id, rows)
        if errors:
            raise RuntimeError(f"Failed to write data quality checks: {errors}")

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        fetched_rows: int,
        loaded_rows: int,
        failed_steps: int,
        metadata: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        finished_at = utc_now()
        context = self._context_by_run.get(run_id, {})
        row = {
            "event_id": str(uuid4()),
            "run_id": run_id,
            "pipeline_name": context.get("pipeline_name", "unknown"),
            "source": context.get("source", "unknown"),
            "trigger_type": context.get("trigger_type", "http"),
            "pipeline_version": os.environ.get("K_REVISION")
            or os.environ.get("GIT_SHA")
            or "local",
            "status": status,
            "started_at": self._started_at_by_run.get(run_id, finished_at).isoformat(),
            "finished_at": finished_at.isoformat(),
            "fetched_rows": fetched_rows,
            "loaded_rows": loaded_rows,
            "failed_steps": failed_steps,
            "season_group_ids": context.get("season_group_ids", []),
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            "error_message": error_message,
        }
        errors = self.client.insert_rows_json(self.ingestion_table_id, [row])
        if errors:
            raise RuntimeError(f"Failed to finalize ingestion run: {errors}")


def ensure_lineage_columns(
    client: bigquery.Client,
    table_id: str,
    *,
    include_source_url: bool = True,
) -> None:
    table = client.get_table(table_id)
    existing = {field.name for field in table.schema}
    new_fields = list(table.schema)

    if "run_id" not in existing:
        new_fields.append(bigquery.SchemaField("run_id", "STRING"))
    if include_source_url and "source_url" not in existing:
        new_fields.append(bigquery.SchemaField("source_url", "STRING"))

    if len(new_fields) != len(table.schema):
        table.schema = new_fields
        client.update_table(table, ["schema"])

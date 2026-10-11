"""Mandatory AWS integration for Environmental Hacks (Track 03 — Waste and Energy).

UnitWatt is software-only by design (see README), so AWS is used for exactly the two
things a factory ledger needs once it leaves a single laptop:

- **Amazon DynamoDB** — one item per factory-day of the energy/carbon ledger
  (``elec_kwh``, ``sec_kwh_per_t``, ``total_t``, data-quality score). This is the
  telemetry store the "Smart Consumption" track criterion asks for: every day's
  energy-per-unit number, queryable by factory and date without re-running the pipeline.
- **Amazon S3** — the audit-ready artifacts a bank or auditor asks for (the IPMVP
  savings report PDF and the SHA-256 audit-trail export), versioned by factory and
  timestamp, so "audit-ready proof of savings" (P15/P17 in the README) has a durable,
  shareable home instead of a local temp file.

Everything here is optional at import time: no AWS account is required to run the rest
of the app. Set ``UNITWATT_AWS_ENABLED=1`` (and the credentials/endpoint below) to turn
it on. Point ``AWS_ENDPOINT_URL`` at a LocalStack container to exercise the same code
path with zero AWS account ("Build It" track option) before real credentials exist.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    _BOTO3_AVAILABLE = True
except ImportError:  # boto3 not installed yet
    _BOTO3_AVAILABLE = False
    BotoCoreError = ClientError = Exception  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class AWSConfig:
    """Resolved from environment variables; never hardcode credentials."""

    enabled: bool
    region: str
    endpoint_url: str | None
    bucket: str
    table: str
    access_key_set: bool

    @classmethod
    def from_env(cls) -> "AWSConfig":
        return cls(
            enabled=os.getenv("UNITWATT_AWS_ENABLED", "").lower() in {"1", "true", "yes"},
            region=os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1")),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
            bucket=os.getenv("UNITWATT_S3_BUCKET", "unitwatt-energy-ledger"),
            table=os.getenv("UNITWATT_DYNAMODB_TABLE", "unitwatt_daily_ledger"),
            access_key_set=bool(os.getenv("AWS_ACCESS_KEY_ID")),
        )


@dataclass
class SyncResult:
    ok: bool
    message: str
    detail: dict | None = None


class AWSClient:
    """Thin, failure-tolerant wrapper. Every public method catches AWS errors and
    returns a :class:`SyncResult` instead of raising, so a missing/expired credential
    never takes down the Streamlit dashboard or the pipeline."""

    def __init__(self, config: AWSConfig | None = None):
        self.config = config or AWSConfig.from_env()
        self._session = None

    def available(self) -> SyncResult:
        if not _BOTO3_AVAILABLE:
            return SyncResult(False, "boto3 is not installed (pip install -r requirements.txt)")
        if not self.config.enabled:
            return SyncResult(False, "AWS integration is off (set UNITWATT_AWS_ENABLED=1 in .env)")
        return SyncResult(True, "ready")

    def _client(self, service: str):
        kwargs = {"region_name": self.config.region}
        if self.config.endpoint_url:
            kwargs["endpoint_url"] = self.config.endpoint_url
        return boto3.client(service, **kwargs)

    def _resource(self, service: str):
        kwargs = {"region_name": self.config.region}
        if self.config.endpoint_url:
            kwargs["endpoint_url"] = self.config.endpoint_url
        return boto3.resource(service, **kwargs)

    # ------------------------------------------------------------------ setup
    def ensure_resources(self) -> SyncResult:
        """Create the S3 bucket and DynamoDB table if they don't exist yet. Idempotent;
        safe to call on every app start. Works against LocalStack or a real AWS account."""
        ready = self.available()
        if not ready.ok:
            return ready
        try:
            s3 = self._client("s3")
            try:
                s3.head_bucket(Bucket=self.config.bucket)
            except ClientError:
                create_kwargs: dict[str, object] = {"Bucket": self.config.bucket}
                if self.config.region != "us-east-1" and not self.config.endpoint_url:
                    create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.config.region}
                s3.create_bucket(**create_kwargs)

            ddb = self._resource("dynamodb")
            existing = {t.name for t in ddb.tables.all()}
            if self.config.table not in existing:
                table = ddb.create_table(
                    TableName=self.config.table,
                    KeySchema=[
                        {"AttributeName": "factory_id", "KeyType": "HASH"},
                        {"AttributeName": "date", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "factory_id", "AttributeType": "S"},
                        {"AttributeName": "date", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                table.wait_until_exists()
            return SyncResult(True, f"bucket '{self.config.bucket}' and table '{self.config.table}' ready")
        except (BotoCoreError, ClientError) as exc:
            return SyncResult(False, f"could not provision AWS resources: {exc}")

    # ------------------------------------------------------------- telemetry
    def sync_ledger(self, ledger: pd.DataFrame, factory_id: str) -> SyncResult:
        """Write one DynamoDB item per day: the energy-per-unit telemetry a factory,
        bank or auditor can query directly, without re-running the Python pipeline."""
        ready = self.available()
        if not ready.ok:
            return ready
        try:
            ddb = self._resource("dynamodb")
            table = ddb.Table(self.config.table)
            written = 0
            with table.batch_writer(overwrite_by_pkeys=["factory_id", "date"]) as batch:
                for day, row in ledger.iterrows():
                    item = {
                        "factory_id": factory_id,
                        "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                        "elec_kwh": _to_decimal(row.get("elec_kwh")),
                        "total_t": _to_decimal(row.get("total_t")),
                        "sec_kwh_per_t": _to_decimal(row.get("sec_kwh_per_t")),
                        "grid_kwh": _to_decimal(row.get("grid_kwh")),
                        "dg_kwh": _to_decimal(row.get("dg_kwh")),
                        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    batch.put_item(Item={k: v for k, v in item.items() if v is not None})
                    written += 1
            return SyncResult(True, f"synced {written} daily ledger rows to DynamoDB", {"rows": written})
        except (BotoCoreError, ClientError) as exc:
            return SyncResult(False, f"DynamoDB sync failed: {exc}")

    def ledger_item_count(self, factory_id: str) -> int | None:
        ready = self.available()
        if not ready.ok:
            return None
        try:
            ddb = self._resource("dynamodb")
            table = ddb.Table(self.config.table)
            resp = table.query(
                KeyConditionExpression="factory_id = :f",
                ExpressionAttributeValues={":f": factory_id},
                Select="COUNT",
            )
            return int(resp.get("Count", 0))
        except (BotoCoreError, ClientError):
            return None

    # ----------------------------------------------------------------- audit
    def upload_report(self, key: str, content: bytes, content_type: str) -> SyncResult:
        """Upload an audit-ready artifact (savings report PDF, audit-trail CSV) to S3."""
        ready = self.available()
        if not ready.ok:
            return ready
        try:
            s3 = self._client("s3")
            s3.put_object(Bucket=self.config.bucket, Key=key, Body=content, ContentType=content_type)
            location = self.config.endpoint_url or f"https://{self.config.bucket}.s3.{self.config.region}.amazonaws.com"
            return SyncResult(True, f"uploaded to s3://{self.config.bucket}/{key}", {"uri": f"{location}/{key}"})
        except (BotoCoreError, ClientError) as exc:
            return SyncResult(False, f"S3 upload failed: {exc}")

    def list_reports(self, prefix: str) -> list[str]:
        ready = self.available()
        if not ready.ok:
            return []
        try:
            s3 = self._client("s3")
            resp = s3.list_objects_v2(Bucket=self.config.bucket, Prefix=prefix)
            return [obj["Key"] for obj in resp.get("Contents", [])]
        except (BotoCoreError, ClientError):
            return []


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
        return Decimal(str(round(float(value), 4)))
    except (TypeError, ValueError):
        return None


def audit_trail_csv_bytes(audit_frame: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    audit_frame.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

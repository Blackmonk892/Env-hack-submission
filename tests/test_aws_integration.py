"""The AWS integration is optional at import time and must never crash the app when
it's off, unconfigured, or boto3 isn't installed. These tests cover that contract
without touching a network or requiring real AWS/LocalStack, and then exercise the
actual boto3 calls end to end against an in-process AWS mock (moto), so the sync
logic is proven correct, not just "fails soft"."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from unitwatt.aws_integration import AWSClient, AWSConfig, audit_trail_csv_bytes

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


def test_config_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("UNITWATT_AWS_ENABLED", raising=False)
    cfg = AWSConfig.from_env()
    assert cfg.enabled is False
    assert cfg.region
    assert cfg.bucket and cfg.table


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("UNITWATT_AWS_ENABLED", "1")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setenv("UNITWATT_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("UNITWATT_DYNAMODB_TABLE", "my-table")
    cfg = AWSConfig.from_env()
    assert cfg.enabled is True
    assert cfg.region == "ap-south-1"
    assert cfg.endpoint_url == "http://localhost:4566"
    assert cfg.bucket == "my-bucket"
    assert cfg.table == "my-table"


def test_client_reports_disabled_without_crashing(monkeypatch):
    monkeypatch.delenv("UNITWATT_AWS_ENABLED", raising=False)
    client = AWSClient(AWSConfig.from_env())
    status = client.available()
    assert status.ok is False
    assert "off" in status.message.lower()
    # Every public method must fail soft (no exception) when AWS is off.
    assert client.sync_ledger(pd.DataFrame(), "demo_forge").ok is False
    assert client.upload_report("k", b"x", "text/plain").ok is False
    assert client.ensure_resources().ok is False
    assert client.ledger_item_count("demo_forge") is None
    assert client.list_reports("prefix/") == []


def test_audit_trail_csv_bytes_roundtrips():
    frame = pd.DataFrame([{"id": 1, "kind": "document:electricity_bill", "name": "bill.pdf"}])
    content = audit_trail_csv_bytes(frame)
    assert isinstance(content, bytes)
    assert b"electricity_bill" in content


@pytest.fixture
def mocked_config(monkeypatch):
    """A config pointed at moto's in-process AWS mock: real boto3 calls, no network,
    no Docker, no credentials. Proves the sync logic actually works end to end."""
    monkeypatch.setenv("UNITWATT_AWS_ENABLED", "1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("UNITWATT_S3_BUCKET", "unitwatt-test-bucket")
    monkeypatch.setenv("UNITWATT_DYNAMODB_TABLE", "unitwatt_test_ledger")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    return AWSConfig.from_env()


def test_ensure_resources_creates_bucket_and_table(mocked_config):
    with mock_aws():
        client = AWSClient(mocked_config)
        res = client.ensure_resources()
        assert res.ok, res.message
        # idempotent: calling again must not fail or duplicate
        res2 = client.ensure_resources()
        assert res2.ok, res2.message


def test_sync_ledger_writes_queryable_items(mocked_config):
    with mock_aws():
        client = AWSClient(mocked_config)
        assert client.ensure_resources().ok
        ledger = pd.DataFrame(
            {
                "elec_kwh": [120.5, 98.2],
                "total_t": [1.1, 0.9],
                "sec_kwh_per_t": [109.5, 109.1],
                "grid_kwh": [110.0, 90.0],
                "dg_kwh": [10.5, 8.2],
            },
            index=pd.DatetimeIndex([date(2026, 7, 1), date(2026, 7, 2)]),
        )
        res = client.sync_ledger(ledger, "shakti_forge")
        assert res.ok, res.message
        assert res.detail == {"rows": 2}
        assert client.ledger_item_count("shakti_forge") == 2
        # a second factory's count must stay independent
        assert client.ledger_item_count("other_factory") == 0


def test_upload_and_list_reports_round_trip(mocked_config):
    with mock_aws():
        client = AWSClient(mocked_config)
        assert client.ensure_resources().ok
        res = client.upload_report("savings_reports/shakti_forge/2026-07-01.pdf", b"%PDF-fake", "application/pdf")
        assert res.ok, res.message
        assert "uri" in (res.detail or {})
        keys = client.list_reports("savings_reports/shakti_forge/")
        assert keys == ["savings_reports/shakti_forge/2026-07-01.pdf"]

from __future__ import annotations

import sys
import types

import pytest

sys.modules.setdefault("oci", types.ModuleType("oci"))

from app import instance_batch_service as service


def test_instance_batch_deduplicates_sync_accounts(monkeypatch):
    monkeypatch.setattr(
        service,
        "require_account",
        lambda account_id: {"id": account_id, "custom_name": f"租户 {account_id}"},
    )
    rows = service._dedupe_items(
        "SYNC_ACCOUNTS",
        [
            {"account_id": 1},
            {"account_id": 1, "account_name": "重复"},
            {"account_id": 2},
        ],
    )
    assert [row["item_key"] for row in rows] == ["account:1", "account:2"]
    assert all(row["item_type"] == "OCI_ACCOUNT" for row in rows)


def test_instance_batch_validates_and_deduplicates_instances(monkeypatch):
    monkeypatch.setattr(
        service,
        "require_account",
        lambda account_id: {"id": account_id, "custom_name": "伦敦"},
    )
    monkeypatch.setattr(
        service,
        "require_instance_region",
        lambda account_id, instance_id, region: {
            "id": instance_id,
            "display_name": "VM-1",
            "region": region or "uk-london-1",
        },
    )
    checked_private_ips = []
    monkeypatch.setattr(
        service,
        "require_private_ip",
        lambda account_id, private_ip_id: checked_private_ips.append((account_id, private_ip_id)),
    )

    rows = service._dedupe_items(
        "REPLACE_PUBLIC_IP",
        [
            {
                "account_id": 1,
                "instance_id": "ocid1.instance.test",
                "private_ip_id": "ocid1.privateip.test",
                "region": "uk-london-1",
            },
            {
                "account_id": 1,
                "instance_id": "ocid1.instance.test",
                "private_ip_id": "ocid1.privateip.test",
                "region": "uk-london-1",
            },
        ],
    )
    assert len(rows) == 1
    assert rows[0]["item_key"] == "instance:1:ocid1.instance.test"
    assert rows[0]["payload"]["private_ip_id"] == "ocid1.privateip.test"
    assert checked_private_ips == [(1, "ocid1.privateip.test"), (1, "ocid1.privateip.test")]


def test_instance_batch_rejects_unknown_operation():
    with pytest.raises(ValueError, match="不支持"):
        service._normalize_operation("TERMINATE")

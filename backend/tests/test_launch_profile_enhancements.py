from __future__ import annotations

import sys
import types

sys.modules.setdefault("oci", types.ModuleType("oci"))

from app import launch_profile_service as service


def test_cross_tenant_template_removes_tenant_bound_resources():
    payload = {
        "catalog_token": "token",
        "compartment_id": "ocid1.compartment.source",
        "network_compartment_id": "ocid1.compartment.network",
        "availability_domain": "AD-1",
        "subnet_id": "ocid1.subnet.source",
        "image_id": "ocid1.image.source",
        "region": "uk-london-1",
        "architecture": "arm",
        "display_name": "N&T",
        "requested_count": 3,
        "max_attempts": 100,
    }

    copied = service.launch_profile_template(payload)

    for key in service.TENANT_BOUND_PROFILE_FIELDS:
        assert key not in copied
    assert copied["architecture"] == "ARM"
    assert copied["shape"] == "VM.Standard.A1.Flex"
    assert copied["display_name"] == "N&T"
    assert copied["requested_count"] == 3
    assert copied["max_attempts"] == 100


def test_preflight_reuses_valid_resources(monkeypatch):
    monkeypatch.setattr(
        service,
        "load_launch_catalog",
        lambda account_id: {
            "catalog_token": "new-token",
            "region": "uk-london-1",
            "compartments": [
                {"id": "ocid1.compartment.root", "name": "root"},
            ],
            "availability_domains": ["AD-1"],
        },
    )
    monkeypatch.setattr(
        service,
        "load_launch_resources",
        lambda *args, **kwargs: {
            "shape": "VM.Standard.A1.Flex",
            "shape_error": None,
            "image_error": None,
            "recommended_subnet_id": "ocid1.subnet.one",
            "subnets": [
                {
                    "id": "ocid1.subnet.one",
                    "display_name": "Public",
                    "compartment_id": "ocid1.compartment.root",
                }
            ],
            "images": [
                {"id": "ocid1.image.one", "display_name": "Ubuntu 24.04"}
            ],
        },
    )

    result = service.preflight_launch_payload(
        12,
        {
            "catalog_token": "old-token",
            "region": "uk-london-1",
            "compartment_id": "ocid1.compartment.root",
            "network_compartment_id": "ocid1.compartment.root",
            "availability_domain": "AD-1",
            "subnet_id": "ocid1.subnet.one",
            "image_id": "ocid1.image.one",
            "architecture": "ARM",
            "display_name": "N&T",
        },
    )

    assert result["ok"] is True
    assert result["payload"]["catalog_token"] == "new-token"
    assert result["payload"]["subnet_id"] == "ocid1.subnet.one"
    assert result["payload"]["image_id"] == "ocid1.image.one"
    assert "catalog_token" in result["changed_fields"]
    assert not [item for item in result["checks"] if item["status"] == "error"]


def test_preflight_auto_maps_copied_profile(monkeypatch):
    monkeypatch.setattr(
        service,
        "load_launch_catalog",
        lambda account_id: {
            "catalog_token": "target-token",
            "region": "us-phoenix-1",
            "compartments": [
                {"id": "ocid1.compartment.target", "name": "Target root"},
            ],
            "availability_domains": ["TARGET-AD-1"],
        },
    )
    monkeypatch.setattr(
        service,
        "load_launch_resources",
        lambda *args, **kwargs: {
            "shape": "VM.Standard.E2.1.Micro",
            "shape_error": None,
            "image_error": None,
            "recommended_subnet_id": "ocid1.subnet.target",
            "subnets": [
                {
                    "id": "ocid1.subnet.target",
                    "display_name": "Target public",
                    "compartment_id": "ocid1.compartment.target",
                }
            ],
            "images": [
                {"id": "ocid1.image.target", "display_name": "Ubuntu target"}
            ],
        },
    )

    result = service.preflight_launch_payload(
        99,
        {
            "architecture": "AMD",
            "shape": "VM.Standard.E2.1.Micro",
            "display_name": "copied",
        },
    )

    assert result["ok"] is True
    assert result["payload"]["region"] == "us-phoenix-1"
    assert result["payload"]["compartment_id"] == "ocid1.compartment.target"
    assert result["payload"]["availability_domain"] == "TARGET-AD-1"
    assert result["payload"]["subnet_id"] == "ocid1.subnet.target"
    assert result["payload"]["image_id"] == "ocid1.image.target"
    assert any(item["status"] == "updated" for item in result["checks"])


def test_preflight_reports_missing_network(monkeypatch):
    monkeypatch.setattr(
        service,
        "load_launch_catalog",
        lambda account_id: {
            "catalog_token": "token",
            "region": "uk-london-1",
            "compartments": [{"id": "ocid1.compartment.root", "name": "root"}],
            "availability_domains": ["AD-1"],
        },
    )
    monkeypatch.setattr(
        service,
        "load_launch_resources",
        lambda *args, **kwargs: {
            "shape": "VM.Standard.A1.Flex",
            "shape_error": None,
            "image_error": None,
            "recommended_subnet_id": None,
            "subnets": [],
            "images": [{"id": "ocid1.image.one", "display_name": "Ubuntu"}],
        },
    )

    result = service.preflight_launch_payload(1, {"architecture": "ARM"})

    assert result["ok"] is False
    assert "N&T 网络" in result["error"]

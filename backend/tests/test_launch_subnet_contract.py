from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launch_network_is_automatic_and_tenant_scoped():
    service = (ROOT / "app/launch_service.py").read_text()
    router = (ROOT / "app/launch_router.py").read_text()
    frontend = (ROOT.parent / "frontend/app.js").read_text()
    assert "ensure_launch_network" in service
    assert "/launch/network/ensure" in router
    # New UX: network provisioning happens only on explicit launch submit.\n    submit = frontend.split("async function submitLaunchJob(event) {", 1)[1].split("async function loadLaunchJobs(", 1)[0]\n    assert "launch/network/ensure" in submit\n    assert "preferredSubnet: ensuredSubnet, forceRefresh: true" in submit\n    assert "提交开机任务时将自动准备" in frontend
    assert "recommended_subnet_id" in service
    assert 'tenant-launch-subnet" type="hidden' in frontend


def test_auto_network_has_vcn_gateway_route_subnet_and_rollback():
    service = (ROOT / "app/launch_service.py").read_text()
    for token in (
        "CreateVcnDetails", "CreateInternetGatewayDetails",
        "UpdateRouteTableDetails", "CreateSubnetDetails",
        "delete_subnet_and_wait_for_state",
        "delete_internet_gateway_and_wait_for_state",
        "delete_vcn_and_wait_for_state",
    ):
        assert token in service
    assert "AUTO_VCN_CIDRS" in service

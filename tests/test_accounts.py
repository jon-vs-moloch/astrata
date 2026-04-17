from pathlib import Path

from astrata.accounts.service import AccountControlPlaneRegistry


def test_desktop_registration_keeps_hosted_bridge_invite_gated(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "account_control_plane.json")

    result = registry.register_desktop_device(
        email="tester@example.com",
        display_name="Tester",
        device_label="Test Mac",
        profile_id="profile-1",
        relay_endpoint="https://relay.example.com",
    )

    assert result["status"] == "ok"
    assert result["hosted_bridge_eligibility"]["status"] == "invite_required"
    assert result["access_policy"]["public_access"]["desktop_install"] is True
    assert result["access_policy"]["invite_gated_access"]["gpt_bridge_sign_in"] is True


def test_invite_redemption_enables_hosted_bridge_access(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "account_control_plane.json")

    invite = registry.issue_invite_code(label="friendly tester")
    code = invite["invite"]["code"]

    redeemed = registry.redeem_invite_code(
        email="tester@example.com",
        code=code,
        display_name="Tester",
    )

    assert redeemed["status"] == "ok"
    assert redeemed["hosted_bridge_eligibility"]["status"] == "eligible"

    lookup = registry.hosted_bridge_eligibility(email="tester@example.com")
    assert lookup["status"] == "eligible"
    assert lookup["invite_required"] is False


def test_remote_host_bash_requires_explicit_acknowledgement(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "account_control_plane.json")
    registered = registry.register_desktop_device(
        email="tester@example.com",
        display_name="Tester",
        device_label="Test Mac",
        profile_id="profile-1",
        relay_endpoint="https://relay.example.com",
    )

    status = registry.remote_host_bash_status(profile_id="profile-1")
    assert status["enabled"] is False
    assert status["requires_special_acknowledgement"] is True

    updated = registry.set_remote_host_bash(profile_id="profile-1", enabled=True)
    assert updated["remote_host_bash"]["enabled"] is True
    assert updated["remote_host_bash"]["acknowledged_at"] is not None
    assert updated["profile"]["profile_id"] == registered["profile"]["profile_id"]

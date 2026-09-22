import plistlib

import pytest

from mixhand import doctor

ALL_PASS = {"checks": [{"id": i, "status": "pass", "summary": "ok"} for i in doctor.UPSTREAM_CHECKS]}


@pytest.fixture
def logic_installed(tmp_path, monkeypatch):
    def install(version):
        plist = tmp_path / "Info.plist"
        plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
        monkeypatch.setattr(doctor, "LOGIC_INFO_PLIST", plist)

    install(doctor.VERIFIED_LOGIC)
    return install


def statuses(checks):
    return {c.name: c.status for c in checks}


def test_a_ready_mac_passes_every_check(fake, logic_installed):
    fake.serve(doctor=ALL_PASS)
    assert set(statuses(doctor.run()).values()) == {"pass"}


def test_a_blank_binary_setting_fails_and_nothing_downstream_claims_a_pass(fake, logic_installed, monkeypatch):
    monkeypatch.setenv("MIXHAND_LOGICPROMCP", "  ")
    fake.serve(doctor=ALL_PASS)
    result = statuses(doctor.run())

    assert result["LogicProMCP binary"] == "fail"
    downstream = {name: status for name, status in result.items() if name not in ("LogicProMCP binary", "Logic Pro version")}
    assert set(downstream.values()) == {"unknown"}


def test_a_check_logicpromcp_skipped_is_reported_as_unknown_not_pass(fake, logic_installed):
    report = {"checks": [dict(c) for c in ALL_PASS["checks"]]}
    report["checks"][0]["status"] = "skipped"
    fake.serve(doctor=report)
    assert statuses(doctor.run())["Accessibility permission"] == "unknown"


def test_another_logic_version_fails(fake, logic_installed):
    logic_installed("12.4")
    fake.serve(doctor=ALL_PASS)
    assert statuses(doctor.run())["Logic Pro version"] == "fail"


def test_another_logicpromcp_version_fails(fake, logic_installed):
    fake.serve(doctor=ALL_PASS, version="3.17.0")
    assert statuses(doctor.run())["LogicProMCP binary"] == "fail"


def test_another_project_in_front_fails(fake, logic_installed):
    fake.serve(doctor=ALL_PASS, resources={"logic://project/info": [{"data": {"filePath": "/elsewhere.logicx"}}]})
    assert statuses(doctor.run())["Front project"] == "fail"


def test_no_project_open_is_reported_not_crashed_on(fake, logic_installed):
    fake.serve(doctor=ALL_PASS, resources={"logic://project/info": [{"data": None}]})
    assert statuses(doctor.run())["Front project"] == "unknown"


def test_a_logicpromcp_that_stops_answering_leaves_front_project_unknown(fake, logic_installed, monkeypatch):
    monkeypatch.setattr("mixhand.executor.logicpro.RESPONSE_WITHIN_S", 2.0)
    fake.serve(doctor=ALL_PASS, silent_on=["resources/read"])
    front = next(c for c in doctor.run() if c.name == "Front project")
    assert front.status == "unknown"
    assert "did not answer resources/read" in front.detail

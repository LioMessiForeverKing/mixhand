import pytest
from conftest import tracks

from mixhand.executor import ExecutorError
from mixhand.executor.fader import pan_contract, volume_contract
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_pan, set_volume


def moved(observed_raw, state="A", verified=True):
    return {"state": state, "verified": verified, "observed_raw": observed_raw, "trace_id": "t1"}


def serve(fake, command, *replies):
    fake.serve(
        resources={"logic://tracks": [tracks("Adlib", "Lead Vocal")]},
        tools={f"logic_mixer.{command}": list(replies)},
    )


def sent(fake, command):
    return [c["params"] for c in fake.calls() if c["call"] == f"logic_mixer.{command}"]


@pytest.mark.parametrize(
    ("db", "contract", "landed_raw", "detail"),
    [
        (0.0, 0.7578947368421053, 173, "Set Lead Vocal to +0.0 dB (asked +0.0 dB)"),
        (-9.0, 0.5, 98, "Set Lead Vocal to -9.0 dB (asked -9.0 dB)"),
        (-17.0, None, 58, "Set Lead Vocal to -17.0 dB (asked -17.0 dB)"),
        (-3.2, None, 143, "Set Lead Vocal to -3.0 dB (asked -3.2 dB)"),
    ],
    ids=["unity", "half-contract", "floor", "nearest-detent"],
)
def test_volume_in_db_reaches_logicpromcp_as_its_fader_contract(fake, db, contract, landed_raw, detail):
    serve(fake, "set_volume", moved(landed_raw))
    with LogicPro.from_env() as logic:
        result = set_volume(logic, "Lead Vocal", db)

    assert result.ok and result.verified
    assert result.detail == detail
    [params] = sent(fake, "set_volume")
    assert params["track"] == 1
    assert params["target_ref"] == "trk_Lead Vocal"
    if contract is not None:
        assert contract < params["value"] < contract + 0.002
    assert [e["event"] for e in fake.log()] == ["set_volume.start", "set_volume.done"]


def test_pan_in_logic_units_reaches_logicpromcp_as_its_header_slider_contract(fake):
    serve(fake, "set_pan", moved(24))
    with LogicPro.from_env() as logic:
        result = set_pan(logic, "Lead Vocal", -40)

    assert result.detail == "Panned Lead Vocal to -40 (asked -40)"
    [params] = sent(fake, "set_pan")
    assert 24 < 63.5 + params["value"] * 63.5 < 24.5


@pytest.mark.parametrize("pan", [-64, 63])
def test_a_hard_pan_stays_inside_logicpromcps_contract(fake, pan):
    serve(fake, "set_pan", moved(pan + 64))
    with LogicPro.from_env() as logic:
        set_pan(logic, "Lead Vocal", pan)

    [params] = sent(fake, "set_pan")
    assert -1.0 <= params["value"] <= 1.0
    assert round(63.5 + params["value"] * 63.5) == pan + 64


def test_pan_reports_where_it_landed_when_the_detent_grid_is_offset(fake):
    serve(fake, "set_pan", moved(28))
    with LogicPro.from_env() as logic:
        result = set_pan(logic, "Lead Vocal", -40)

    assert result.detail == "Panned Lead Vocal to -36 (asked -40)"


@pytest.mark.parametrize(
    ("move", "value"),
    [(set_volume, -17.1), (set_volume, 6.5), (set_pan, -65), (set_pan, 64)],
)
def test_an_out_of_range_value_touches_nothing(fake, move, value):
    serve(fake, "set_volume")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="outside"):
        move(logic, "Lead Vocal", value)

    assert fake.calls() == []


def test_a_move_that_could_not_be_read_back_says_so(fake):
    serve(fake, "set_volume", {"state": "B", "verified": False, "observed_raw": None, "reason": "readback_unavailable"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="could not be read back.*readback_unavailable"):
        set_volume(logic, "Lead Vocal", -3.0)

    assert fake.log()[-1]["verified"] is False


def test_a_move_that_landed_away_from_the_target_says_where(fake):
    serve(fake, "set_volume", moved(160))
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="landed at raw 160, not within 5 of 143"):
        set_volume(logic, "Lead Vocal", -3.0)

    assert fake.log()[-1]["verified"] is False


def test_a_track_that_changed_under_the_move_is_refused_not_moved_elsewhere(fake):
    serve(fake, "set_volume", {"state": "C", "error": "stale_target_reference", "write_attempted": False})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="stale_target_reference"):
        set_volume(logic, "Lead Vocal", -3.0)

    [params] = sent(fake, "set_volume")
    assert params["target_ref"] == "trk_Lead Vocal"


def test_a_refused_move_is_logged_and_raised(fake):
    serve(fake, "set_pan", {"state": "C", "error": "mixer_not_visible"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="mixer_not_visible"):
        set_pan(logic, "Lead Vocal", 10)

    assert fake.log()[-1] == {**fake.log()[-1], "event": "set_pan.refused", "error": "mixer_not_visible"}


@pytest.mark.parametrize(
    ("command", "level", "contract", "was"),
    [("set_volume", "volume", volume_contract(143), -3.0), ("set_pan", "pan", pan_contract(-5), -5), ("set_pan", "pan", 0.0, None)],
    ids=["volume", "pan", "unread-pan"],
)
def test_a_move_reports_the_level_it_replaced_so_undo_can_put_it_back(fake, command, level, contract, was):
    listing = tracks("Adlib", "Lead Vocal")
    listing["data"][1][level] = contract
    fake.serve(resources={"logic://tracks": [listing]}, tools={f"logic_mixer.{command}": [moved(98)]})
    with LogicPro.from_env() as logic:
        result = set_volume(logic, "Lead Vocal", -9.0) if command == "set_volume" else set_pan(logic, "Lead Vocal", 34)

    assert (result.undo_steps, result.was) == (0, was)

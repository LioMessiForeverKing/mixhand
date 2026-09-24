import pytest
from conftest import PROJECT, inventory, slot, tracks

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_plugin_param


def landed(shown, state="A", verified=True):
    return {"state": state, "verified": verified, "observed_display": shown, "trace_id": "t1"}


def refused(**payload):
    return {"state": "C", "error": "increment_walk_no_progress", **payload}


def serve(fake, command, *replies, plugins=(slot(0, "Compressor"), slot(1, "Channel EQ"))):
    fake.serve(
        resources={"logic://tracks": [tracks("Adlib", "Lead Vocal")]},
        tools={"logic_plugins.get_inventory": [inventory(*plugins)], f"logic_plugins.{command}": list(replies)},
    )


def written(fake):
    return [c for c in fake.calls() if c["call"] != "logic_plugins.get_inventory"]


def test_compressor_threshold_reaches_logicpromcp_bound_to_the_track_it_names(fake):
    serve(fake, "set_param_verified", landed("60 %"))
    with LogicPro.from_env() as logic:
        result = set_plugin_param(logic, "Lead Vocal", "Compressor", "Threshold", 60)

    assert result.ok and result.verified
    assert result.detail == "Set Lead Vocal's Compressor Threshold to 60 % (asked 60 %; undo does not restore it)"
    [call] = written(fake)
    assert call["call"] == "logic_plugins.set_param_verified"
    assert call["params"] == {
        "track": 1,
        "insert": 0,
        "target_ref": "trk_Lead Vocal",
        "mode": "duplicate_applyback",
        "project_expected_path": PROJECT,
        "plugin": "Compressor",
        "param": "threshold",
        "value": 60,
    }
    assert [e["event"] for e in fake.log()] == ["set_plugin_param.start", "set_plugin_param.done"]


@pytest.mark.parametrize(
    ("db", "raw", "shown"),
    [(3.0, 270, "+3.0 dB"), (0.0, 240, "0.0 dB"), (-5.5, 185, "-5.5 dB"), (-24.0, 0, "-24.0 dB")],
    ids=["whole-boost", "flat", "cut", "floor"],
)
def test_eq_gain_is_walked_by_raw_position_and_confirmed_by_logics_own_text(fake, db, raw, shown):
    serve(fake, "set_eq_band_verified", landed(shown))
    with LogicPro.from_env() as logic:
        result = set_plugin_param(logic, "Lead Vocal", "Channel EQ", "Peak 1 Gain", db)

    assert result.detail == f"Set Lead Vocal's Channel EQ Peak 1 Gain to {shown} (asked {db:g} dB; undo does not restore it)"
    [call] = written(fake)
    assert call["params"]["insert"] == 1
    assert {k: call["params"][k] for k in ("band", "parameter", "value", "unit")} == {
        "band": "Peak 1",
        "parameter": "Gain",
        "value": raw,
        "unit": "raw_ax_value",
    }


def test_eq_gain_lands_on_the_nearest_tenth_and_says_so(fake):
    serve(fake, "set_eq_band_verified", landed("+2.4 dB"))
    with LogicPro.from_env() as logic:
        result = set_plugin_param(logic, "Lead Vocal", "Channel EQ", "Peak 3 Gain", 2.37)

    assert result.detail.startswith("Set Lead Vocal's Channel EQ Peak 3 Gain to +2.4 dB (asked 2.37 dB")


def test_eq_frequency_is_asked_for_in_hz(fake):
    serve(fake, "set_eq_band_verified", landed("805 Hz"))
    with LogicPro.from_env() as logic:
        set_plugin_param(logic, "Lead Vocal", "Channel EQ", "Peak 2 Frequency", 805)

    [call] = written(fake)
    assert {k: call["params"][k] for k in ("band", "parameter", "value", "unit")} == {
        "band": "Peak 2",
        "parameter": "Frequency",
        "value": 805,
        "unit": "Hz",
    }


@pytest.mark.parametrize(
    ("plugin", "param"),
    [("Compressor", "Ratio"), ("Channel EQ", "Low Cut Frequency"), ("ChromaVerb", "Mix"), ("Compressor", "threshold")],
)
def test_a_param_outside_the_map_touches_nothing(fake, plugin, param):
    serve(fake, "set_param_verified")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="param not mapped"):
        set_plugin_param(logic, "Lead Vocal", plugin, param, 1)

    assert fake.calls() == []


@pytest.mark.parametrize(
    ("plugin", "param", "value"),
    [
        ("Compressor", "Threshold", 35.5),
        ("Compressor", "Threshold", 101),
        ("Channel EQ", "Peak 1 Frequency", 99),
        ("Channel EQ", "Peak 1 Frequency", 150.5),
        ("Channel EQ", "Peak 1 Frequency", 19001),
        ("Channel EQ", "Peak 1 Gain", 24.1),
    ],
)
def test_a_value_logic_cannot_show_touches_nothing(fake, plugin, param, value):
    serve(fake, "set_param_verified")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=param):
        set_plugin_param(logic, "Lead Vocal", plugin, param, value)

    assert fake.calls() == []


@pytest.mark.parametrize(
    ("plugins", "count"),
    [((slot(0, "Channel EQ"),), 0), ((slot(0, "Compressor"), slot(1, "Compressor")), 2)],
    ids=["absent", "twice"],
)
def test_a_track_without_exactly_one_such_plugin_is_refused_before_writing(fake, plugins, count):
    serve(fake, "set_param_verified", plugins=plugins)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=f"has {count} Compressor plugins"):
        set_plugin_param(logic, "Lead Vocal", "Compressor", "Threshold", 60)

    assert written(fake) == []


def test_a_readback_showing_another_value_is_not_reported_as_set(fake):
    serve(fake, "set_eq_band_verified", landed("+3.1 dB"))
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="reads '\\+3.1 dB', not '\\+3.0 dB'"):
        set_plugin_param(logic, "Lead Vocal", "Channel EQ", "Peak 1 Gain", 3)

    assert fake.log()[-1]["verified"] is False


@pytest.mark.parametrize(
    ("payload", "said"),
    [
        ({"error": "stale_target_reference", "write_attempted": False}, "nothing changed"),
        (
            {"write_attempted": True, "rollback_succeeded": True, "last_observed_display": "98.5 Hz"},
            "Logic stopped at 98.5 Hz, and it was put back where it was",
        ),
        ({"write_attempted": True, "rollback_succeeded": True}, "; it was put back where it was"),
        ({"write_attempted": True, "rollback_succeeded": False}, "may have moved, and undo does not restore it"),
        ({"error": "window_open_failed"}, "may have moved"),
    ],
    ids=["before-writing", "rolled-back", "rolled-back-unread", "rollback-failed", "unknown"],
)
def test_a_refused_write_says_only_what_it_knows_about_the_plugin(fake, payload, said):
    serve(fake, "set_eq_band_verified", refused(**payload))
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=said):
        set_plugin_param(logic, "Lead Vocal", "Channel EQ", "Peak 2 Frequency", 1000)

    assert fake.log()[-1]["event"] == "set_plugin_param.refused"

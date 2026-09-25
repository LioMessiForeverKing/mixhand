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
    assert result.detail == "Set Lead Vocal's Compressor Threshold to 60 % (asked 60 %)"
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

    assert result.detail == f"Set Lead Vocal's Channel EQ Peak 1 Gain to {shown} (asked {db:g} dB)"
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


@pytest.fixture
def delay(monkeypatch):
    done = {"calls": [], "reply": None}

    def step(*args):
        done["calls"].append(args)
        if isinstance(done["reply"], Exception):
            raise done["reply"]
        return done["reply"]

    monkeypatch.setattr("mixhand.executor.primitives.set_delay_param", step)
    return done


def serve_delay(fake, plugins=(slot(0, "St-Delay"),)):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal", "Echo")]},
        tools={"logic_plugins.get_inventory": [inventory(*plugins)]},
    )


@pytest.mark.parametrize(
    ("param", "value", "row", "wanted", "target"),
    [
        ("Crossfeed L->R", 60, "Crossfeed L->R:", "60 %", "60"),
        ("Right Feedback", 0, "Right Feedback:", "0 %", "0"),
        ("Left Note", 0.75, "Left Note:", "1/8 dotted", ""),
        ("Right Note", 1, "Right Note:", "1/4", ""),
    ],
    ids=["crossfeed", "feedback-floor", "dotted-note", "whole-beat"],
)
def test_a_stereo_delay_row_is_found_by_its_label_and_confirmed_by_logics_own_text(fake, delay, param, value, row, wanted, target):
    serve_delay(fake)
    delay["reply"] = ("35 %", wanted, 25)
    with LogicPro.from_env() as logic:
        result = set_plugin_param(logic, "Echo", "Stereo Delay", param, value)

    assert result.ok and result.verified and result.undo_steps == 0
    assert result.detail.startswith(f"Set Echo's Stereo Delay {param} to {wanted} (asked {value:g} ")
    assert delay["calls"] == [("Echo", row, wanted, target, 110)]
    assert [e["event"] for e in fake.log()] == ["set_plugin_param.start", "set_plugin_param.done"]


def test_a_stereo_delay_row_already_there_is_reported_as_unchanged(fake, delay):
    serve_delay(fake)
    delay["reply"] = ("60 %", "60 %", 0)
    with LogicPro.from_env() as logic:
        result = set_plugin_param(logic, "Echo", "Stereo Delay", "Crossfeed R->L", 60)

    assert result.verified and result.detail == "Echo's Stereo Delay Crossfeed R->L is already 60 %"


@pytest.mark.parametrize(
    ("param", "value", "said"),
    [
        ("Left Feedback", 50.5, "whole percent from 0 to 100"),
        ("Crossfeed L->R", 101, "whole percent from 0 to 100"),
        ("Left Note", 0.33, "note length in beats"),
        ("Right Note", 4, "note length in beats"),
        ("Left Input", 1, "param not mapped"),
    ],
)
def test_a_stereo_delay_value_logic_cannot_show_touches_nothing(fake, delay, param, value, said):
    serve_delay(fake)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=said):
        set_plugin_param(logic, "Echo", "Stereo Delay", param, value)

    assert fake.calls() == [] and delay["calls"] == []


@pytest.mark.parametrize(
    ("plugins", "count"),
    [((slot(0, "ChromaVerb"),), 0), ((slot(0, "St-Delay"), slot(1, "St-Delay")), 2)],
    ids=["absent", "twice"],
)
def test_a_track_without_exactly_one_stereo_delay_is_refused_before_writing(fake, delay, plugins, count):
    serve_delay(fake, plugins=plugins)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=f"has {count} Stereo Delay plugins"):
        set_plugin_param(logic, "Echo", "Stereo Delay", "Left Feedback", 10)

    assert delay["calls"] == []


def test_a_stereo_delay_readback_showing_another_value_is_not_reported_as_set(fake, delay):
    serve_delay(fake)
    delay["reply"] = ("0 %", "59 %", 110)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="reads '59 %', not '60 %', and read '0 %' before"):
        set_plugin_param(logic, "Echo", "Stereo Delay", "Crossfeed L->R", 60)

    assert fake.log()[-1]["verified"] is False


def test_a_stereo_delay_write_that_fails_part_way_says_it_may_have_moved(fake, delay):
    serve_delay(fake)
    delay["reply"] = ExecutorError("Crossfeed L->R: stopped moving at 12 %")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="stopped moving at 12 %; .* may have moved, and undo does not restore it"):
        set_plugin_param(logic, "Echo", "Stereo Delay", "Crossfeed L->R", 60)

    assert fake.log()[-1]["event"] == "set_plugin_param.refused"

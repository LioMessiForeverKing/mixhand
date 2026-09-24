import json

import pytest
from conftest import PROJECT
from typer.testing import CliRunner

from mixhand.cli import app
from mixhand.executor import ExecutorError
from mixhand.executor.fader import RAW_MAX, pan_at_contract, pan_contract, raw_at_contract, raw_nearest, volume_contract
from mixhand.executor.logicpro import LogicPro
from mixhand.state.models import Selection, as_json
from mixhand.state.reader import read_session

LIVE_TRANSPORT = {"source": "ax_live", "data": {"state": {"tempo": 92}}}
SAVED_INFO = {"data": {"filePath": PROJECT, "timeSignature": "4/4"}}


def listing(*tracks, epoch=0):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [
            {
                "id": i,
                "name": name,
                "track_ref": f"trk_{epoch}_{i}",
                "volume": volume_contract(raw_nearest(db)) if db is not None else 0.0,
                "pan": pan_contract(pan),
            }
            for i, (name, db, pan) in enumerate(tracks)
        ],
    }


def strip(name, inputs=("Input 1",), sends=()):
    return "\t".join([name, "|".join(inputs), "Stereo Output", "|".join(sends), "1"])


def slots(*names):
    return {
        "state": "A",
        "complete": True,
        "plugins": [{"insert": i, "name": n, "occupied": n is not None} for i, n in enumerate(names)],
    }


TRACKS = [("Lead Vocal", -6.0, -40), ("Adlib", -3.2, 25), ("Delay", 0.0, 0)]
MIXER = "\n".join(
    [
        strip("Lead Vocal", sends=("Bus 4",)),
        strip("Adlib", sends=("Bus 4", "Bus 3")),
        strip("Delay", inputs=("Bus 4",)),
        strip("", inputs=("Bus 3",)),
        strip("Stereo Out", inputs=()),
    ]
)
INVENTORIES = [slots("Compressor", None, "Channel EQ"), slots(None), slots("St-Delay")]


@pytest.fixture
def mixer(monkeypatch):
    shown = {"routes": MIXER}
    monkeypatch.setattr("mixhand.executor.primitives.read_routes", lambda: shown["routes"])
    return shown


def serve(fake, tracks=None, inventories=INVENTORIES, transport=LIVE_TRANSPORT):
    fake.serve(
        resources={
            "logic://project/info": [SAVED_INFO],
            "logic://tracks": tracks or [listing(*TRACKS)],
            "logic://transport/state": [transport],
        },
        tools={"logic_plugins.get_inventory": list(inventories)},
    )


def read(**kwargs):
    with LogicPro.from_env() as logic:
        return json.loads(as_json(read_session(logic, **kwargs)))


def test_the_session_names_every_track_aux_plugin_and_send_the_mixer_shows(fake, mixer):
    serve(fake)
    session = read(key="A major", selection=Selection(33, 49))

    assert session == {
        "project": {"path": PROJECT, "tempo": 92, "time_sig_saved": "4/4", "key": "A major"},
        "selection": {"start_bar": 33, "end_bar": 49},
        "tracks": [
            {
                "name": "Lead Vocal",
                "volume_db": -6.0,
                "pan": -40,
                "plugins": ["Compressor", "Channel EQ"],
                "sends": [{"bus": 4, "aux": "Delay"}],
                "bus": None,
            },
            {
                "name": "Adlib",
                "volume_db": -3.2,
                "pan": 25,
                "plugins": [],
                "sends": [{"bus": 4, "aux": "Delay"}, {"bus": 3, "aux": None}],
                "bus": None,
            },
        ],
        "auxes": [
            {"name": "Delay", "volume_db": 0.0, "pan": 0, "plugins": ["Stereo Delay"], "sends": [], "bus": 4}
        ],
        "available_plugins": ["Gain", "Channel EQ", "Compressor", "ChromaVerb", "Stereo Delay"],
    }
    assert [c["params"]["track"] for c in fake.calls()] == [0, 1, 2]


def test_a_send_to_a_bus_two_auxes_listen_on_names_neither(fake, mixer):
    serve(fake)
    mixer["routes"] = MIXER + "\n" + strip("Verb", inputs=("Bus 4",))

    assert [s["aux"] for s in read()["tracks"][0]["sends"]] == [None]


def test_a_fader_all_the_way_down_prints_as_minus_infinity_not_invalid_json(fake, mixer):
    serve(fake, tracks=[listing(("Lead Vocal", None, 0))], inventories=[slots()])
    mixer["routes"] = strip("Lead Vocal")

    assert read()["tracks"][0]["volume_db"] == "-inf"


def test_every_fader_and_pan_position_reads_back_as_the_one_that_was_set():
    assert [raw_at_contract(volume_contract(raw)) for raw in range(RAW_MAX + 1)] == list(range(RAW_MAX + 1))
    assert [pan_at_contract(pan_contract(pan)) for pan in range(-64, 64)] == list(range(-64, 64))


def test_tracks_reissued_while_reading_are_refused_rather_than_mixed(fake, mixer):
    serve(fake, tracks=[listing(*TRACKS), listing(*TRACKS, epoch=1)])

    with pytest.raises(ExecutorError, match="changed while the session was read"):
        read()


def test_a_track_the_mixer_does_not_show_is_refused_rather_than_read_as_sending_nothing(fake, mixer):
    serve(fake)
    mixer["routes"] = "\n".join(line for line in MIXER.splitlines() if not line.startswith("Adlib"))

    with pytest.raises(ExecutorError, match="0 strips named 'Adlib'"):
        read()


def test_a_plugin_whose_name_cannot_be_read_is_refused_rather_than_dropped(fake, mixer):
    unreadable = {"state": "A", "complete": True, "plugins": [{"insert": 0, "name": None, "occupied": True}]}
    serve(fake, inventories=[unreadable])

    with pytest.raises(ExecutorError, match="could not be read"):
        read()


@pytest.mark.parametrize(
    "transport",
    [{"source": "project_file", "data": {"state": {"tempo": 120}}}, {"source": "ax_live", "data": {"state": {}}}],
)
def test_a_tempo_that_never_goes_live_is_refused(fake, mixer, monkeypatch, transport):
    monkeypatch.setattr("mixhand.state.reader.TRACKS_READABLE_WITHIN_S", 0.2)
    serve(fake, transport=transport)

    with pytest.raises(ExecutorError, match="tempo was not readable"):
        read()


def test_state_prints_the_session_and_treats_a_blank_key_as_none(fake, mixer):
    serve(fake)
    result = CliRunner().invoke(app, ["state", "--key", "  ", "--start-bar", "33", "--end-bar", "49"])

    assert result.exit_code == 0, result.output
    session = json.loads(result.stdout)
    assert session["project"]["key"] is None
    assert session["selection"] == {"start_bar": 33, "end_bar": 49}


@pytest.mark.parametrize("flags", [["--start-bar", "33"], ["--start-bar", "33", "--end-bar", "33"]])
def test_state_refuses_a_selection_that_is_not_a_range(fake, mixer, flags):
    serve(fake)
    result = CliRunner().invoke(app, ["state", *flags])

    assert result.exit_code == 2
    assert fake.calls() == []


def test_state_names_the_problem_instead_of_a_traceback(fake, mixer):
    fake.serve(resources={"logic://project/info": [{"data": {"filePath": "/tmp/Other.logicx"}}]})
    result = CliRunner().invoke(app, ["state"])

    assert result.exit_code == 1
    assert "Mixhand only touches MIXHAND_PROJECT" in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)

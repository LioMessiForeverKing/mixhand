import pytest
from conftest import PROJECT

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import add_send

TRACKS = [("Lead Vocal", "audio"), ("Adlib", "audio"), ("Verb", "aux")]
PAIR = [("Lead Vocal", "audio"), ("Verb", "aux")]


def listing(*tracks):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [{"id": i, "name": name, "track_ref": f"trk_{i}", "type": kind} for i, (name, kind) in enumerate(tracks)],
    }


def strip(name, inputs=("Input 1",), outputs=("Stereo Output",), sends=(), empty=1):
    return "\t".join([name, "|".join(inputs), "|".join(outputs), "|".join(sends), str(empty)])


def mixer(*strips):
    return "\n".join(strips)


@pytest.fixture
def routed(monkeypatch):
    done = {"reads": [], "picks": []}

    def read():
        return done["reads"].pop(0)

    monkeypatch.setattr("mixhand.executor.primitives.read_routes", read)
    monkeypatch.setattr("mixhand.executor.primitives.pick_route", lambda *args: done["picks"].append(args))
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)
    return done


def serve(fake, tracks=TRACKS, project=None):
    resources = {"logic://tracks": [listing(*tracks)]}
    if project:
        resources["logic://project/info"] = project
    fake.serve(resources=resources)


def test_an_aux_without_a_bus_is_given_the_lowest_free_one_and_the_track_sends_to_it(fake, routed):
    serve(fake)
    routed["reads"] = [
        mixer(
            strip("Lead Vocal"),
            strip("Adlib", outputs=("Bus 1",), sends=("Bus 2",)),
            strip("Verb"),
            strip("Stereo Out", inputs=(), outputs=(), empty=0),
            strip("", inputs=("Bus 3",), outputs=("No Output",)),
        ),
        "Lead Vocal\tInput",
        mixer(strip("Lead Vocal"), strip("Verb", inputs=("Bus 4",))),
        mixer(
            strip("Lead Vocal", sends=("Bus 4",)),
            strip("Adlib", outputs=("Bus 1",), sends=("Bus 2",)),
            strip("Verb", inputs=("Bus 4",)),
            strip("Stereo Out", inputs=(), outputs=(), empty=0),
            strip("", inputs=("Bus 3",), outputs=("No Output",)),
        ),
    ]
    with LogicPro.from_env() as logic:
        result = add_send(logic, "Lead Vocal", "Verb")

    assert result.ok and result.verified
    assert result.detail == "Sent Lead Vocal to Verb on Bus 4; undo 2 removes it"
    assert routed["picks"] == [
        ("Verb", "Input slot", "Input 1", ("Bus", "Bus 4")),
        ("Lead Vocal", "Send slot", "send button", ("Bus", "Bus 4 → Verb")),
    ]
    assert [e["event"] for e in fake.log()] == ["add_send.start", "add_send.done"]


def test_an_aux_already_on_a_bus_keeps_it_and_only_the_send_is_made(fake, routed):
    serve(fake)
    routed["reads"] = [
        mixer(strip("Lead Vocal"), strip("Adlib", sends=("Bus 7",)), strip("Verb", inputs=("Bus 7",))),
        mixer(strip("Lead Vocal", sends=("Bus 7",)), strip("Adlib", sends=("Bus 7",)), strip("Verb", inputs=("Bus 7",))),
    ]
    with LogicPro.from_env() as logic:
        result = add_send(logic, "Lead Vocal", "Verb")

    assert result.detail == "Sent Lead Vocal to Verb on Bus 7; undo 1 removes it"
    assert routed["picks"] == [("Lead Vocal", "Send slot", "send button", ("Bus", "Bus 7 → Verb"))]


def test_a_second_call_changes_nothing(fake, routed):
    serve(fake)
    routed["reads"] = [mixer(strip("Lead Vocal", sends=("Bus 1",), empty=0), strip("Adlib"), strip("Verb", inputs=("Bus 1",)))]
    with LogicPro.from_env() as logic:
        result = add_send(logic, "Lead Vocal", "Verb")

    assert result.verified and result.detail == "Lead Vocal already sends to Verb on Bus 1"
    assert routed["picks"] == []
    assert [e["event"] for e in fake.log()] == ["add_send.skipped"]


def test_a_bus_past_the_first_page_is_found_in_its_submenu(fake, routed):
    serve(fake)
    used = tuple(f"Bus {n}" for n in range(1, 41))
    routed["reads"] = [
        mixer(strip("Lead Vocal"), strip("Adlib", sends=used), strip("Verb")),
        mixer(strip("Lead Vocal"), strip("Adlib", sends=used), strip("Verb", inputs=("Bus 41",))),
        mixer(strip("Lead Vocal", sends=("Bus 41",)), strip("Adlib", sends=used), strip("Verb", inputs=("Bus 41",))),
    ]
    with LogicPro.from_env() as logic:
        add_send(logic, "Lead Vocal", "Verb")

    assert [p[3] for p in routed["picks"]] == [("Bus", "33 - 64", "Bus 41"), ("Bus", "33 - 64", "Bus 41 → Verb")]


@pytest.mark.parametrize(
    "tracks, aux, message",
    [
        (TRACKS, "Adlib", "'Adlib' is not an aux"),
        (TRACKS, "Space", "no track named 'Space'"),
        ([*TRACKS, ("Verb", "aux")], "Verb", "2 tracks are named 'Verb'"),
    ],
    ids=["not-an-aux", "missing", "two-named"],
)
def test_a_target_that_is_not_one_aux_is_refused_before_anything_is_touched(fake, routed, tracks, aux, message):
    serve(fake, tracks)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=message):
        add_send(logic, "Lead Vocal", aux)

    assert routed["picks"] == []


@pytest.mark.parametrize(
    "strips, message",
    [
        ([strip("Lead Vocal", empty=0), strip("Verb")], "'Lead Vocal' has no empty send slot"),
        ([strip("Lead Vocal"), strip("Verb"), strip("Verb")], "2 strips named 'Verb'"),
        ([strip("Lead Vocal")], r"shows no strip for \['Verb'\]"),
        ([strip("Lead Vocal"), strip("Verb", inputs=())], "0 input slots"),
        ([strip("Lead Vocal"), "Verb\tInput 1"], "could not read a Mixer strip's routing"),
    ],
    ids=["no-empty-send", "two-strips", "no-strip", "no-input", "junk"],
)
def test_a_mixer_that_cannot_take_the_send_is_refused_before_anything_is_touched(fake, routed, strips, message):
    serve(fake, PAIR)
    routed["reads"] = [mixer(*strips)]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=message):
        add_send(logic, "Lead Vocal", "Verb")

    assert routed["picks"] == []


def test_a_hidden_track_stops_the_send_before_its_bus_can_look_free(fake, routed):
    serve(fake)
    routed["reads"] = [mixer(strip("Lead Vocal"), strip("Verb"))]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=r"shows no strip for \['Adlib'\].*unhide"):
        add_send(logic, "Lead Vocal", "Verb")

    assert routed["picks"] == []


def test_a_hidden_track_is_not_masked_by_a_visible_one_of_the_same_name(fake, routed):
    serve(fake, [*TRACKS, ("Adlib", "audio")])
    routed["reads"] = [mixer(strip("Lead Vocal"), strip("Adlib"), strip("Verb"))]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=r"shows no strip for \['Adlib'\]"):
        add_send(logic, "Lead Vocal", "Verb")

    assert routed["picks"] == []


def test_a_failed_input_pick_makes_no_send_and_says_what_to_check(fake, routed, monkeypatch):
    def refuse(*args):
        raise ExecutorError("the Input slot menu on Verb did not open")

    monkeypatch.setattr("mixhand.executor.primitives.pick_route", refuse)
    serve(fake, PAIR)
    routed["reads"] = [mixer(strip("Lead Vocal"), strip("Verb"))]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="undo 1 only if it reads Bus 1"):
        add_send(logic, "Lead Vocal", "Verb")


def test_an_input_that_never_reads_back_makes_no_send(fake, routed):
    serve(fake, PAIR)
    unchanged = mixer(strip("Lead Vocal"), strip("Verb"))
    routed["reads"] = [unchanged] * 200
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=r"Verb's input did not read Bus 1.*in \('Input 1',\).*undo 1 only if it reads Bus 1"):
        add_send(logic, "Lead Vocal", "Verb")

    assert [p[1] for p in routed["picks"]] == ["Input slot"]


@pytest.mark.parametrize(
    "aux_input, recovery",
    [("Input 1", "undo 2 if one reads Bus 1, otherwise undo 1 to put Verb's input back"), ("Bus 1", "undo 1 if one reads Bus 1, otherwise nothing changed")],
    ids=["after-input", "send-only"],
)
def test_a_failed_send_pick_gives_the_recovery_for_both_outcomes(fake, routed, monkeypatch, aux_input, recovery):
    picks = []

    def send_fails(*args):
        picks.append(args)
        if args[1] == "Send slot":
            raise ExecutorError("Bus 1 → Verb is not in the menu")

    monkeypatch.setattr("mixhand.executor.primitives.pick_route", send_fails)
    serve(fake, PAIR)
    routed["reads"] = [mixer(strip("Lead Vocal"), strip("Verb", inputs=(aux_input,))), mixer(strip("Lead Vocal"), strip("Verb", inputs=("Bus 1",)))]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=recovery):
        add_send(logic, "Lead Vocal", "Verb")

    assert picks[-1][1] == "Send slot"


@pytest.mark.parametrize(
    "after",
    [
        mixer(strip("Lead Vocal", sends=("Bus 1",)), strip("Adlib", sends=("Bus 1",)), strip("Verb", inputs=("Bus 1",))),
        mixer(strip("Lead Vocal"), strip("Adlib", sends=("Bus 1",)), strip("Verb", inputs=("Bus 1",))),
        mixer(strip("Lead Vocal", sends=("Bus 1",)), strip("Adlib"), strip("Verb")),
        mixer(strip("Lead Vocal", sends=("Bus 1",)), strip("Adlib"), strip("Verb", inputs=("Bus 1",)), strip("Aux 2", inputs=("Bus 1",))),
    ],
    ids=["another-track-sends", "send-on-the-wrong-track", "aux-input-unchanged", "logic-made-an-aux"],
)
def test_anything_but_the_one_route_asked_for_is_not_confirmed(fake, routed, after):
    serve(fake)
    routed["reads"] = [
        mixer(strip("Lead Vocal"), strip("Adlib"), strip("Verb")),
        mixer(strip("Lead Vocal"), strip("Adlib"), strip("Verb", inputs=("Bus 1",))),
        *[after] * 200,
    ]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="was not confirmed within.*the Mixer shows.*check Logic before undoing"):
        add_send(logic, "Lead Vocal", "Verb")

    assert fake.log()[-1]["event"] == "add_send.start"


def test_a_mixer_that_cannot_be_read_after_the_send_is_not_called_misrouted(fake, routed):
    serve(fake, PAIR)
    routed["reads"] = [mixer(strip("Lead Vocal"), strip("Verb", inputs=("Bus 1",)))] + ["Lead Vocal\tInput"] * 200
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not confirmed.*the Mixer could not be read.*check Logic before undoing"):
        add_send(logic, "Lead Vocal", "Verb")


def test_a_project_switched_during_the_send_is_not_confirmed(fake, routed):
    serve(fake, PAIR, project=[{"data": {"filePath": PROJECT}}, {"data": {"filePath": "/Users/me/Music/Real Song.logicx"}}])
    routed["reads"] = [
        mixer(strip("Lead Vocal"), strip("Verb")),
        mixer(strip("Lead Vocal"), strip("Verb", inputs=("Bus 1",))),
        mixer(strip("Lead Vocal", sends=("Bus 1",)), strip("Verb", inputs=("Bus 1",))),
    ]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="Real Song.*could not be confirmed afterwards.*which project"):
        add_send(logic, "Lead Vocal", "Verb")

    assert fake.log()[-1]["event"] == "add_send.start"

import pytest

from mixhand.executor import ActionResult
from mixhand.executor.primitives import EQ_HZ_MAX, INSERTABLE, PAN_MAX, PAN_MIN, PLUGIN_MENU, SEND_DB_MIN, VOLUME_DB_MAX, VOLUME_DB_MIN
from mixhand.planner.validate import MAX_ACTIONS, InvalidAction, Plan, validate
from mixhand.state.models import Channel, Project, Send, Session

WHY = "because the session needs it"


def session(*channels: Channel) -> Session:
    return Session(
        project=Project(path="/tmp/x.logicx", tempo=92, time_sig_saved="4/4", key=None),
        selection=None,
        tracks=list(channels) or [channel("Lead Vocal"), channel("Adlib"), channel("Verb", bus=4)],
        available_plugins=[*INSERTABLE, *PLUGIN_MENU],
    )


def channel(name, volume_db=0.0, sends=(), bus=None, plugins=()):
    return Channel(name=name, volume_db=volume_db, pan=0, plugins=list(plugins), sends=list(sends), bus=bus)


def done(steps=1):
    return ActionResult(ok=True, detail="done", verified=True, undo_steps=steps)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("duplicate_track", {"source": "Lead Vocal", "new_name": "Chorus Double L"}),
        ("insert_plugin", {"track": "Lead Vocal", "plugin": "ChromaVerb"}),
        ("set_volume", {"track": "Adlib", "db": VOLUME_DB_MIN}),
        ("set_volume", {"track": "Adlib", "db": VOLUME_DB_MAX}),
        ("set_pan", {"track": "Adlib", "value": PAN_MIN}),
        ("set_pan", {"track": "Adlib", "value": PAN_MAX}),
        ("create_aux", {"name": "Delay", "plugin": "Stereo Delay"}),
        ("add_send", {"track": "Adlib", "aux": "Verb"}),
    ],
)
def test_an_action_inside_what_the_executor_accepts_passes(tool, args):
    validate(tool, {**args, "reason": WHY}, Plan.of(session()))


@pytest.mark.parametrize(
    ("tool", "args", "refusal"),
    [
        ("set_volume", {"track": "Adlib", "db": VOLUME_DB_MIN - 0.1}, "outside"),
        ("set_volume", {"track": "Adlib", "db": VOLUME_DB_MAX + 0.1}, "outside"),
        ("set_pan", {"track": "Adlib", "value": PAN_MIN - 1}, "outside"),
        ("set_pan", {"track": "Adlib", "value": PAN_MAX + 1}, "outside"),
        ("set_pan", {"track": "Adlib", "value": 12.5}, "must be an integer"),
        ("set_pan", {"track": "Adlib", "value": True}, "must be an integer"),
        ("insert_plugin", {"track": "Lead Vocal", "plugin": "Pitch Correction"}, "must be one of"),
        ("insert_plugin", {"track": "Lead Vox", "plugin": "Compressor"}, "no track named 'Lead Vox'"),
        ("duplicate_track", {"source": "Lead Vocal", "new_name": "Adlib"}, "already exists"),
        ("duplicate_track", {"source": "Lead Vocal", "new_name": " Double"}, "not a usable track name"),
        ("create_aux", {"name": "Verb", "plugin": "ChromaVerb"}, "already exists"),
        ("add_send", {"track": "Verb", "aux": "Verb"}, "cannot send to itself"),
        ("set_send_level", {"track": "Adlib", "aux": "Verb", "db": -12.0}, "no send to 'Verb'; add_send first"),
        ("delete_track", {"track": "Adlib"}, "no tool named"),
    ],
)
def test_an_action_the_executor_would_refuse_or_undo_could_not_reverse_is_refused(tool, args, refusal):
    with pytest.raises(InvalidAction, match=refusal):
        validate(tool, {**args, "reason": WHY}, Plan.of(session()))


def test_an_action_without_a_reason_or_with_extra_fields_is_refused():
    plan = Plan.of(session())
    with pytest.raises(InvalidAction, match="takes exactly"):
        validate("set_pan", {"track": "Adlib", "value": 0}, plan)
    with pytest.raises(InvalidAction, match="takes exactly"):
        validate("set_pan", {"track": "Adlib", "value": 0, "reason": WHY, "slot": 2}, plan)
    with pytest.raises(InvalidAction, match="needs a reason"):
        validate("set_pan", {"track": "Adlib", "value": 0, "reason": "  "}, plan)


def test_a_track_or_send_made_earlier_in_the_run_can_be_used_later():
    plan = Plan.of(session())
    with pytest.raises(InvalidAction, match="no track named 'Double'"):
        validate("set_pan", {"track": "Double", "value": -40, "reason": WHY}, plan)
    plan.apply("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}, done(2))
    plan.apply("create_aux", {"name": "Delay", "plugin": "Stereo Delay"}, done(4))
    plan.apply("add_send", {"track": "Double", "aux": "Delay"}, done(2))

    validate("set_pan", {"track": "Double", "value": -40, "reason": WHY}, plan)
    validate("set_send_level", {"track": "Double", "aux": "Delay", "db": SEND_DB_MIN, "reason": WHY}, plan)
    with pytest.raises(InvalidAction, match="already exists"):
        validate("duplicate_track", {"source": "Lead Vocal", "new_name": "Double", "reason": WHY}, plan)


def test_a_send_that_was_there_before_the_run_keeps_its_level_so_undo_never_has_to_restore_one():
    plan = Plan.of(session(channel("Lead Vocal", sends=[Send(4, "Verb")]), channel("Verb", bus=4)))
    with pytest.raises(InvalidAction, match="was there before the run"):
        validate("set_send_level", {"track": "Lead Vocal", "aux": "Verb", "db": -12.0, "reason": WHY}, plan)

    plan.apply("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}, done(2))
    validate("set_send_level", {"track": "Double", "aux": "Verb", "db": -12.0, "reason": WHY}, plan)


def test_a_fader_below_what_mixhand_can_set_is_left_alone_so_undo_can_put_it_back():
    plan = Plan.of(session(channel("Lead Vocal", volume_db=-20.0), channel("Verb", volume_db=float("-inf"), bus=4)))
    for track in ("Lead Vocal", "Verb"):
        with pytest.raises(InvalidAction, match="undo could not put it back"):
            validate("set_volume", {"track": track, "db": -6.0, "reason": WHY}, plan)
    plan.apply("create_aux", {"name": "Delay", "plugin": "Stereo Delay"}, done(4))
    validate("set_volume", {"track": "Delay", "db": -6.0, "reason": WHY}, plan)


def test_a_plugin_parameter_is_set_only_on_a_plugin_this_run_put_there():
    plan = Plan.of(session(channel("Lead Vocal", plugins=["Compressor"])))
    param = {"track": "Lead Vocal", "plugin": "Compressor", "param": "Threshold", "value": 40, "reason": WHY}
    with pytest.raises(InvalidAction, match="undo could not put back"):
        validate("set_plugin_param", param, plan)
    plan.apply("insert_plugin", {"track": "Lead Vocal", "plugin": "Compressor"}, done(0))
    with pytest.raises(InvalidAction, match="undo could not put back"):
        validate("set_plugin_param", param, plan)

    plan.apply("insert_plugin", {"track": "Lead Vocal", "plugin": "Channel EQ"}, done(1))
    eq = {"track": "Lead Vocal", "plugin": "Channel EQ", "param": "Peak 1 Frequency", "reason": WHY}
    validate("set_plugin_param", {**eq, "value": EQ_HZ_MAX}, plan)
    with pytest.raises(InvalidAction, match=f"from 100 to {EQ_HZ_MAX}"):
        validate("set_plugin_param", {**eq, "value": EQ_HZ_MAX + 1}, plan)
    with pytest.raises(InvalidAction, match="param not mapped"):
        validate("set_plugin_param", {**eq, "param": "Threshold", "value": 40}, plan)


def test_a_copy_made_this_run_can_have_its_copied_plugins_set():
    plan = Plan.of(session(channel("Lead Vocal", plugins=["Compressor"])))
    plan.apply("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}, done(2))
    validate("set_plugin_param", {"track": "Double", "plugin": "Compressor", "param": "Threshold", "value": 40, "reason": WHY}, plan)


def test_the_run_stops_taking_actions_at_the_limit():
    plan = Plan.of(session())
    for _ in range(MAX_ACTIONS):
        validate("set_pan", {"track": "Adlib", "value": 0, "reason": WHY}, plan)
        plan.apply("set_pan", {"track": "Adlib", "value": 0}, done(0))
    with pytest.raises(InvalidAction, match=f"at most {MAX_ACTIONS} actions"):
        validate("set_pan", {"track": "Adlib", "value": 0, "reason": WHY}, plan)


def test_a_second_plugin_on_a_track_is_refused_because_the_executor_cannot_insert_one():
    plan = Plan.of(session(channel("Lead Vocal", plugins=["Compressor"]), channel("Adlib")))
    with pytest.raises(InvalidAction, match="only one plugin on a track"):
        validate("insert_plugin", {"track": "Lead Vocal", "plugin": "Channel EQ", "reason": WHY}, plan)
    validate("insert_plugin", {"track": "Lead Vocal", "plugin": "Compressor", "reason": WHY}, plan)

    plan.apply("insert_plugin", {"track": "Adlib", "plugin": "Channel EQ"}, done(1))
    plan.apply("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}, done(2))
    plan.apply("create_aux", {"name": "Delay", "plugin": "Stereo Delay"}, done(4))
    for track in ("Adlib", "Double", "Delay"):
        with pytest.raises(InvalidAction, match="only one plugin on a track"):
            validate("insert_plugin", {"track": track, "plugin": "Gain", "reason": WHY}, plan)

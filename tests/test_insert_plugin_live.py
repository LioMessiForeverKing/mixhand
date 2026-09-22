import os

import pytest

from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import insert_plugin, inserts, track_index, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
PLUGIN = "Channel EQ"


def plugins_on(logic, index):
    return [s["name"] for s in inserts(logic, index, TRACK) if s["occupied"]]


@pytest.mark.parametrize("run", range(1, 11))
def test_insert_channel_eq_on_lead_vocal_then_undo(run):
    with LogicPro.from_env() as logic:
        index = track_index(logic, TRACK)
        assert PLUGIN not in plugins_on(logic, index)

        result = insert_plugin(logic, TRACK, PLUGIN)
        try:
            assert result.verified
            assert PLUGIN in plugins_on(logic, index)

            again = insert_plugin(logic, TRACK, PLUGIN)
            assert "already" in again.detail
            assert plugins_on(logic, index).count(PLUGIN) == 1
        finally:
            undo(logic)
        assert PLUGIN not in plugins_on(logic, index)

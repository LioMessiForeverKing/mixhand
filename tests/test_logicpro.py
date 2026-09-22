import os
import time

import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro


def test_a_logicpromcp_that_stops_answering_fails_loudly(fake, monkeypatch):
    fake.serve(silent_on=["resources/read"])
    with LogicPro.from_env() as logic:
        monkeypatch.setattr("mixhand.executor.logicpro.RESPONSE_WITHIN_S", 0.2)
        with pytest.raises(ExecutorError, match="did not answer resources/read"):
            logic.track_names()


def test_messages_that_are_not_the_answer_do_not_extend_the_wait(fake, monkeypatch):
    fake.serve(silent_on=["resources/read"], chatter=20)
    with LogicPro.from_env() as logic:
        monkeypatch.setattr("mixhand.executor.logicpro.RESPONSE_WITHIN_S", 0.5)
        started = time.monotonic()
        with pytest.raises(ExecutorError, match="did not answer resources/read"):
            logic.track_names()
        assert time.monotonic() - started < 1.5


def test_a_refused_handshake_leaves_no_logicpromcp_running(fake):
    fake.serve(refuse_initialize=True)
    with pytest.raises(ExecutorError, match="initialize"):
        LogicPro.from_env()

    with pytest.raises(ProcessLookupError):
        os.kill(fake.pid(), 0)

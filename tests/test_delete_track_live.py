import os

import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import delete_track, duplicate_track, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

SOURCE = "Lead Vocal"
COPY = "Lead Vocal Double"


def session():
    with LogicPro.from_env() as logic:
        names = [t["name"] for t in logic.tracks()]
        spans = [
            sorted((r["startPosition"], r["endPosition"]) for r in logic.read(f"logic://tracks/{i}/regions"))
            for i in range(len(names))
        ]
    return names, spans


@pytest.fixture
def no_copy_left():
    with LogicPro.from_env() as logic:
        if COPY in [t["name"] for t in logic.tracks()]:
            pytest.fail(f"delete {COPY!r} from the test project before running this")
    yield
    with LogicPro.from_env() as logic:
        if COPY in [t["name"] for t in logic.tracks()]:
            delete_track(logic, COPY)


@pytest.mark.parametrize("run", range(10))
def test_delete_a_duplicate_and_undo_the_delete(run, no_copy_left):
    before, before_spans = session()

    with LogicPro.from_env() as logic:
        duplicate_track(logic, SOURCE, COPY)
    duplicated = session()

    with LogicPro.from_env() as logic:
        assert delete_track(logic, COPY).verified
    assert session() == (before, before_spans)

    with LogicPro.from_env() as logic:
        undo(logic, 1)
    assert session() == duplicated

    with LogicPro.from_env() as logic:
        delete_track(logic, COPY)
        with pytest.raises(ExecutorError, match="no track named"):
            delete_track(logic, COPY)
    assert session() == (before, before_spans)

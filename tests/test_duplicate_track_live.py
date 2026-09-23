import os

import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import duplicate_track, undo

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


@pytest.mark.parametrize("run", range(10))
def test_duplicate_lead_vocal_with_its_regions(run):
    before, before_spans = session()
    source = before.index(SOURCE)
    assert before_spans[source], f"{SOURCE} needs at least one region for this test to mean anything"

    with LogicPro.from_env() as logic:
        first = duplicate_track(logic, SOURCE, COPY)
        with pytest.raises(ExecutorError, match="already exists"):
            duplicate_track(logic, SOURCE, COPY)
    assert first.verified

    names, spans = session()
    assert names == before[: source + 1] + [COPY] + before[source + 1 :]
    assert spans[source + 1] == before_spans[source]

    with LogicPro.from_env() as logic:
        undo(logic, 2)
    assert session() == (before, before_spans)

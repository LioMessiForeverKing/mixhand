import json
import os
from collections.abc import Callable
from pathlib import Path

import openai
from openai.types.responses import Response

from mixhand.executor import ActionResult
from mixhand.executor.actionlog import log
from mixhand.executor.group import Group, begin_group, end_group, record, resume_group
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import (
    add_send,
    create_aux,
    duplicate_track,
    insert_plugin,
    set_pan,
    set_plugin_param,
    set_send_level,
    set_volume,
)
from mixhand.planner.tools import TOOLS
from mixhand.planner.validate import InvalidAction, Plan, validate
from mixhand.state.models import Session, as_json

MODEL_ENV = "MIXHAND_MODEL"
DEFAULT_MODEL = "gpt-6-sol"
RETRIES = 2
SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.md")
# The SDK hands back a final response only after response.completed, so a cut-off reply is read off its own event.
FINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")
EXECUTE: dict[str, Callable[..., ActionResult]] = {
    "duplicate_track": duplicate_track,
    "insert_plugin": insert_plugin,
    "set_volume": set_volume,
    "set_pan": set_pan,
    "create_aux": create_aux,
    "add_send": add_send,
    "set_send_level": set_send_level,
    "set_plugin_param": set_plugin_param,
}


class PlannerError(Exception):
    pass


def produce(
    logic: LogicPro,
    client: openai.OpenAI,
    prompt: str,
    session: Session,
    text: Callable[[str], None],
    line: Callable[..., None],
    follow_up: Callable[[], str | None],
) -> bool:
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
    plan = Plan.of(session)
    items: list = [{"role": "user", "content": f"{prompt}\n\nThe session, read from Logic just now:\n{as_json(session)}"}]
    group = begin_group(prompt, session.project.path, [c.name for c in session.tracks])
    try:
        saved = False
        while True:
            try:
                _run(logic, client, model, plan, group, items, text, line)
            except BaseException as e:
                if plan.actions or not saved:
                    end_group(logic, group, failed=None if isinstance(e, (PlannerError, openai.APIError)) else _why(e))
                raise
            saved = end_group(logic, group)
            prompt = follow_up()
            if prompt is None:
                return saved
            log("planner.follow_up", run=group.run, text=prompt)
            resume_group(logic, group)
            plan.actions = 0
            items.append({"role": "user", "content": prompt})
    except BaseException as e:
        log("planner.stopped", run=group.run, detail=_why(e))
        raise


def _why(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _run(
    logic: LogicPro,
    client: openai.OpenAI,
    model: str,
    plan: Plan,
    group: Group,
    items: list,
    text: Callable[[str], None],
    line: Callable[..., None],
) -> None:
    refused = 0
    while True:
        response = _turn(client, model, items, text, group.run)
        if response.status != "completed":
            reason = response.incomplete_details.reason if response.incomplete_details else response.status
            raise PlannerError(f"the model's reply stopped short ({reason}), so none of its actions were run")
        if any(part.type == "refusal" for o in response.output if o.type == "message" for part in o.content):
            raise PlannerError("the model declined to go on")
        items += response.output
        calls = [o for o in response.output if o.type == "function_call"]
        if not calls:
            return
        for call in calls:
            try:
                args = _arguments(call.arguments)
                validate(call.name, args, plan)
            except InvalidAction as e:
                refused += 1
                detail = f"Refused {call.name}: {e}"
                log("planner.refused", run=group.run, tool=call.name, detail=detail)
                line("fail", detail)
                if refused > RETRIES:
                    raise PlannerError(f"the model gave {refused} invalid actions in a row, so the run stopped") from e
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": f"Refused: {e}"})
                continue
            refused = 0
            reason = args.pop("reason")
            plan.actions += 1
            result = EXECUTE[call.name](logic, **args)
            record(group, call.name, args, result)
            plan.apply(call.name, args, result)
            log("planner.action", run=group.run, tool=call.name, args=args, detail=result.detail, reason=reason)
            line("pass", result.detail, reason)
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": result.detail})


def _arguments(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError as e:
        raise InvalidAction(f"the arguments were not valid JSON: {raw!r}") from e


def _turn(client: openai.OpenAI, model: str, items: list, text: Callable[[str], None], run: str) -> Response:
    shown: list[str] = []
    try:
        with client.responses.stream(
            model=model,
            instructions=SYSTEM_PROMPT.read_text(),
            input=items,
            tools=TOOLS,
            parallel_tool_calls=False,
        ) as stream:
            final = None
            for event in stream:
                if event.type == "response.output_text.delta":
                    text(event.delta)
                    shown.append(event.delta)
                elif event.type in FINAL_EVENTS:
                    final = event.response
    finally:
        if shown:
            log("planner.text", run=run, text="".join(shown))
    if final is None:
        raise PlannerError("the model's reply ended without a final response, so none of its actions were run")
    return final

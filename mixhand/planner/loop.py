import os
from collections.abc import Callable
from pathlib import Path

import anthropic

from mixhand.executor import ActionResult
from mixhand.executor.group import Group, begin_group, end_group, record
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
DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 32000
RETRIES = 2
SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.md")
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
    client: anthropic.Anthropic,
    prompt: str,
    session: Session,
    text: Callable[[str], None],
    line: Callable[[str, str], None],
) -> None:
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
    plan = Plan.of(session)
    messages: list = [{"role": "user", "content": f"{prompt}\n\nThe session, read from Logic just now:\n{as_json(session)}"}]
    group = begin_group(prompt, [c.name for c in session.tracks])
    try:
        _run(logic, client, model, plan, group, messages, text, line)
    except (PlannerError, anthropic.APIError):
        end_group(group)
        raise
    except BaseException as e:
        end_group(group, failed=str(e) or type(e).__name__)
        raise
    end_group(group)


def _run(
    logic: LogicPro,
    client: anthropic.Anthropic,
    model: str,
    plan: Plan,
    group: Group,
    messages: list,
    text: Callable[[str], None],
    line: Callable[[str, str], None],
) -> None:
    refused = 0
    while True:
        response = _turn(client, model, messages, text)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            raise PlannerError(f"the model declined to go on ({getattr(response.stop_details, 'category', None)})")
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "max_tokens":
            raise PlannerError(f"the model ran out of room after {MAX_TOKENS} tokens, so its last action was not run")
        if not calls:
            return
        results = []
        for call in calls:
            try:
                validate(call.name, call.input, plan)
            except InvalidAction as e:
                refused += 1
                line("fail", f"Refused {call.name}: {e}")
                if refused > RETRIES:
                    raise PlannerError(f"the model gave {refused} invalid actions in a row, so the run stopped") from e
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True, "content": str(e)})
                continue
            refused = 0
            args = {k: v for k, v in call.input.items() if k != "reason"}
            result = EXECUTE[call.name](logic, **args)
            record(group, call.name, args, result)
            plan.apply(call.name, args, result)
            line("pass", f"{result.detail} — {call.input['reason']}")
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": result.detail})
        messages.append({"role": "user", "content": results})


def _turn(client: anthropic.Anthropic, model: str, messages: list, text: Callable[[str], None]):
    with client.beta.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT.read_text(),
        tools=TOOLS,
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        thinking={"type": "adaptive"},
        cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        for chunk in stream.text_stream:
            text(chunk)
        return stream.get_final_message()

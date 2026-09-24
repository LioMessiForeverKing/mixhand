import openai
import typer

from mixhand import doctor as doctor_checks
from mixhand import explain as explain_log
from mixhand.executor import ExecutorError
from mixhand.executor.group import undo_group
from mixhand.executor.logicpro import LogicPro
from mixhand.planner import loop
from mixhand.state.models import Selection, as_json
from mixhand.state.reader import read_session

NOTHING_TO_UNDO = "mixhand undo has nothing from this run to reverse."
FOLLOW_UP = "Say what to change next, or press Return to finish."
PROMPT = "›"

app = typer.Typer(no_args_is_help=True, add_completion=False)

MARKS = {
    "pass": typer.style("✔", fg="green"),
    "fail": typer.style("✖", fg="red"),
    "unknown": typer.style("?", fg="yellow"),
}


class Out:
    def __init__(self) -> None:
        self.fresh = True

    def text(self, chunk: str) -> None:
        if chunk:
            typer.echo(chunk, nl=False)
            self.fresh = chunk.endswith("\n")

    def line(self, status: str, detail: str, reason: str | None = None) -> None:
        self.end_line()
        typer.echo(f"{MARKS[status]} {detail}" + (typer.style(f" — {reason}", dim=True) if reason else ""))

    def end_line(self) -> None:
        if not self.fresh:
            typer.echo()
        self.fresh = True

    def follow_up(self) -> str | None:
        self.end_line()
        typer.echo(typer.style(FOLLOW_UP, dim=True))
        try:
            return typer.prompt(PROMPT, default="", show_default=False, prompt_suffix=" ").strip() or None
        except typer.Abort:
            typer.echo()
            return None


@app.callback()
def main() -> None:
    """Claude Code for Logic Pro."""


@app.command()
def doctor() -> None:
    """Check that this Mac and Logic are ready for Mixhand."""
    checks = doctor_checks.run()
    for check in checks:
        typer.echo(f"{MARKS[check.status]} {check.name}  {typer.style(check.detail, dim=True)}")
    if any(check.status != "pass" for check in checks):
        raise typer.Exit(1)


@app.command()
def state(
    key: str | None = typer.Option(None, help="The song's key, which Logic does not expose."),
    start_bar: int | None = typer.Option(None, min=1, help="First bar of the section to work on."),
    end_bar: int | None = typer.Option(None, min=2, help="Bar the section ends at."),
) -> None:
    """Print the session Mixhand sees, as JSON."""
    selection = _selection(start_bar, end_bar)
    try:
        with LogicPro.from_env() as logic:
            session = read_session(logic, key=(key or "").strip() or None, selection=selection)
    except ExecutorError as e:
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        raise typer.Exit(1)
    typer.echo(as_json(session))


@app.command()
def produce(
    prompt: str = typer.Argument(..., help="What you want done to the vocals, in a sentence."),
    key: str | None = typer.Option(None, help="The song's key, which Logic does not expose."),
    start_bar: int | None = typer.Option(None, min=1, help="First bar of the section to work on."),
    end_bar: int | None = typer.Option(None, min=2, help="Bar the section ends at."),
) -> None:
    """Have the model plan the change and carry it out in Logic, one logged action at a time."""
    selection = _selection(start_bar, end_bar)
    if not prompt.strip():
        raise typer.BadParameter("say what you want done")
    started = False
    out = Out()
    try:
        client = openai.OpenAI()
        with LogicPro.from_env() as logic:
            session = read_session(logic, key=(key or "").strip() or None, selection=selection)
            started = True
            saved = loop.produce(logic, client, prompt.strip(), session, text=out.text, line=out.line, follow_up=out.follow_up)
    except ExecutorError as e:
        out.end_line()
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        if started:
            typer.echo("mixhand undo says what the finished actions left to undo.", err=True)
        raise typer.Exit(1)
    except (loop.PlannerError, openai.OpenAIError) as e:
        out.end_line()
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        if started:
            typer.echo("mixhand undo reverses what was done.", err=True)
        raise typer.Exit(1)
    out.end_line()
    typer.echo(typer.style("mixhand undo reverses this run." if saved else NOTHING_TO_UNDO, dim=True))


@app.command()
def explain() -> None:
    """Reprint what Mixhand's last run showed: its plan, each action and the reason for it."""
    try:
        run = explain_log.last_run()
    except explain_log.Unexplained as e:
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        raise typer.Exit(1)
    typer.echo(typer.style(f"Last run, {run.began:%Y-%m-%d %H:%M}: {run.label}", dim=True))
    out = Out()
    for event in run.shown:
        if event["event"] == "planner.text":
            out.text(event["text"])
        elif event["event"] == "planner.follow_up":
            out.end_line()
            typer.echo(typer.style(FOLLOW_UP, dim=True))
            typer.echo(f"{PROMPT} {event['text']}")
        elif event["event"] == "planner.action":
            out.line("pass", event["detail"], event["reason"])
        else:
            out.line("fail", event["detail"])
    out.end_line()
    if run.end is None:
        typer.echo(f"{MARKS['unknown']} The log has no end for this run: it is still going, or it stopped before it could write one.")
    elif not run.end.get("saved"):
        typer.echo(typer.style(NOTHING_TO_UNDO, dim=True))
    if run.undo_began is not None:
        typer.echo(typer.style(f"mixhand undo began taking this run back at {run.undo_began:%H:%M}.", dim=True))


@app.command()
def undo() -> None:
    """Undo Mixhand's last run: put back what it moved, then undo what it made."""
    clean = True
    try:
        with LogicPro.from_env() as logic:
            for status, detail in undo_group(logic):
                typer.echo(f"{MARKS[status]} {detail}")
                clean = clean and status == "pass"
    except ExecutorError as e:
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        raise typer.Exit(1)
    if not clean:
        raise typer.Exit(1)


def _selection(start_bar: int | None, end_bar: int | None) -> Selection | None:
    if (start_bar is None) != (end_bar is None):
        raise typer.BadParameter("give --start-bar and --end-bar together")
    if start_bar is not None and end_bar <= start_bar:
        raise typer.BadParameter(f"--end-bar {end_bar} must come after --start-bar {start_bar}")
    return Selection(start_bar, end_bar) if start_bar is not None else None

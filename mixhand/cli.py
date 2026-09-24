import openai
import typer

from mixhand import doctor as doctor_checks
from mixhand.executor import ExecutorError
from mixhand.executor.group import undo_group
from mixhand.executor.logicpro import LogicPro
from mixhand.planner import loop
from mixhand.state.models import Selection, as_json
from mixhand.state.reader import read_session

app = typer.Typer(no_args_is_help=True, add_completion=False)

MARKS = {
    "pass": typer.style("✔", fg="green"),
    "fail": typer.style("✖", fg="red"),
    "unknown": typer.style("?", fg="yellow"),
}


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
    try:
        with LogicPro.from_env() as logic:
            session = read_session(logic, key=(key or "").strip() or None, selection=selection)
            loop.produce(
                logic,
                openai.OpenAI(),
                prompt.strip(),
                session,
                text=lambda chunk: typer.echo(chunk, nl=False),
                line=lambda status, detail: typer.echo(f"\n{MARKS[status]} {detail}"),
            )
    except ExecutorError as e:
        typer.echo(f"\n{MARKS['fail']} {e}", err=True)
        typer.echo("mixhand undo says what the finished actions left to undo.", err=True)
        raise typer.Exit(1)
    except (loop.PlannerError, openai.APIError) as e:
        typer.echo(f"\n{MARKS['fail']} {e}", err=True)
        typer.echo("mixhand undo reverses what was done.", err=True)
        raise typer.Exit(1)
    typer.echo(typer.style("\nmixhand undo reverses this run.", dim=True))


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

import typer

from mixhand import doctor as doctor_checks
from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
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
    if (start_bar is None) != (end_bar is None):
        raise typer.BadParameter("give --start-bar and --end-bar together")
    if start_bar is not None and end_bar <= start_bar:
        raise typer.BadParameter(f"--end-bar {end_bar} must come after --start-bar {start_bar}")
    selection = Selection(start_bar, end_bar) if start_bar is not None else None
    try:
        with LogicPro.from_env() as logic:
            session = read_session(logic, key=(key or "").strip() or None, selection=selection)
    except ExecutorError as e:
        typer.echo(f"{MARKS['fail']} {e}", err=True)
        raise typer.Exit(1)
    typer.echo(as_json(session))

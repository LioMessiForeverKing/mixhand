import typer

from mixhand import doctor as doctor_checks

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

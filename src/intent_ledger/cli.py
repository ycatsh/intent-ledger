from importlib.metadata import version

import click
from flask.cli import FlaskGroup

from intent_ledger import create_app


def _print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return

    click.echo(f"intent-ledger {version('intent-ledger')}")
    ctx.exit()


@click.group(cls=FlaskGroup, create_app=lambda: create_app(), add_version_option=False)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the intent-ledger version and exit.",
)
def main() -> None:
    """intent-ledger command-line interface."""


main.params = [p for p in main.params if p.name != "env_file"]
for _param in main.params:
    if _param.name == "app":
        _param.help = "Flask app to load."
    elif _param.name == "debug":
        _param.help = "Run with the debugger/reloader enabled."

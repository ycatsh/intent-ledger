import sqlite3
from importlib.metadata import version

import click
from flask.cli import FlaskGroup

from intent_ledger import create_app
from intent_ledger.migrate import migrate


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


@main.command("migrate", with_appcontext=False)
@click.option("--dry-run", is_flag=True, help="Report what would change on a copy, without writing.")
def migrate_command(dry_run: bool) -> None:
    """Upgrade the database to this release's schema. Stop the app first."""
    try:
        report = migrate(dry_run=dry_run)
    except (RuntimeError, sqlite3.Error) as e:
        raise click.ClickException(f"Migration stopped, and the database is unchanged: {e}") from None

    for line in report:
        click.echo(line)


main.params = [p for p in main.params if p.name != "env_file"]
for _param in main.params:
    if _param.name == "app":
        _param.help = "Flask app to load."
    elif _param.name == "debug":
        _param.help = "Run with the debugger/reloader enabled."

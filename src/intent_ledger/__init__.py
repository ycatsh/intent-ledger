import click
from flask import Flask, request
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from intent_ledger import config
from intent_ledger.accounting.accounts import get_accounts_needing_review_count
from intent_ledger.accounting.inbox import get_unknown_txn_count
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules import get_rules_needing_review_count
from intent_ledger.db import db
from intent_ledger.routes import register_blueprints
from intent_ledger.routes.flash import import_problems, rebuild_impact_message
from intent_ledger.routes.navigation import current_location, return_target
from intent_ledger.service import (
    export_all,
    import_pending_statements,
    initialize_database,
    ledger_change,
)

csrf = CSRFProtect()

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    if config.TRUSTED_PROXIES:
        hops = config.TRUSTED_PROXIES
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    csrf.init_app(app)
    app.teardown_appcontext(db.close_session)

    initialize_database()

    register_blueprints(app)
    app.after_request(_security_headers)
    register_cli(app)
    register_context_processors(app)
    register_template_filters(app)

    return app


def _security_headers(response):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def register_cli(app: Flask) -> None:
    @app.cli.command("import")
    def import_command():
        """Import every pending uploaded statement and rebuild the ledger."""
        with ledger_change():
            summaries = import_pending_statements()

        inserted = sum(s["inserted"] for s in summaries)
        click.echo(f"Imported {inserted} transaction(s) across {len(summaries)} file(s).")
        for problem in import_problems(summaries):
            click.echo(problem, err=True)

    @app.cli.command("rebuild-ledger")
    def rebuild_ledger_command():
        """Recompute the ledger from transactions, rules, and overrides."""
        changed = rebuild_ledger()
        click.echo(f"Ledger rebuilt.{rebuild_impact_message(changed)}")

    @app.cli.command("export-all")
    @click.option("--accounts/--no-accounts", default=True, help="Export every account's statement.")
    @click.option("--projects/--no-projects", default=True, help="Export every project's report.")
    @click.option("--yearly/--no-yearly", default=True, help="Export a yearly report for each year.")
    def export_all_command(accounts, projects, yearly):
        """Export account statements, project reports, and yearly reports at once."""
        if not (accounts or projects or yearly):
            raise click.UsageError("Select at least one of --accounts, --projects, --yearly.")

        paths = export_all(accounts=accounts, projects=projects, yearly=yearly)

        if not paths:
            click.echo("Nothing to export.")
            return

        for path in paths:
            click.echo(f"Wrote {path}")
        click.echo(f"Exported {len(paths)} file(s).")


def register_context_processors(app: Flask) -> None:
    @app.context_processor
    def inject_nav_badges():
        return {
            "nav_badges": {
                "inbox.inbox": get_unknown_txn_count(),
                "mappings.mappings": get_accounts_needing_review_count(),
                "rules.rules": get_rules_needing_review_count(),
            }
        }

    @app.context_processor
    def inject_navigation():
        """Expose `here` (link an editor back to this page) and `origin`
        (where the current edit came from) to every template.
        """
        return {"here": current_location(), "origin": return_target()}


def register_template_filters(app: Flask) -> None:
    @app.template_filter("money")
    def money_filter(value):
        if value is None:
            return "-"
        return f"{value:,.2f}"

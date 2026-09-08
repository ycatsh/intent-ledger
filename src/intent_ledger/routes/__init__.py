from flask import Flask

from intent_ledger.routes.accounts import accounts_bp
from intent_ledger.routes.budget import budget_bp
from intent_ledger.routes.charts import charts_bp
from intent_ledger.routes.dashboard import dashboard_bp
from intent_ledger.routes.expenses import expenses_bp
from intent_ledger.routes.import_ import import_bp
from intent_ledger.routes.inbox import inbox_bp
from intent_ledger.routes.mappings import mappings_bp
from intent_ledger.routes.projects import projects_bp
from intent_ledger.routes.rules import rules_bp
from intent_ledger.routes.settings import settings_bp
from intent_ledger.routes.subscriptions import subscriptions_bp

BLUEPRINTS = (
    dashboard_bp,
    import_bp,
    inbox_bp,
    budget_bp,
    accounts_bp,
    expenses_bp,
    charts_bp,
    rules_bp,
    mappings_bp,
    projects_bp,
    subscriptions_bp,
    settings_bp,
)


def register_blueprints(app: Flask) -> None:
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

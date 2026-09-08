from collections.abc import Sequence
from urllib.parse import urlparse

from flask import redirect, request, url_for

RETURN_FIELD = "return_to"


def is_internal_path(target: str | None) -> bool:
    if not target or not target.startswith("/") or target.startswith("//"):
        return False

    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def return_target() -> str | None:
    target = request.values.get(RETURN_FIELD)
    return target if is_internal_path(target) else None


def redirect_back(endpoint: str, **values):
    return redirect(return_target() or url_for(endpoint, **values))


def current_location() -> str:
    if not request.query_string:
        return request.path

    return f"{request.path}?{request.query_string.decode()}"


def active_tab(tabs: Sequence[str], default: str) -> str:
    requested = request.args.get("tab")
    return requested if requested in tabs else default

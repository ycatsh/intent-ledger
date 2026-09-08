import json
import time
from datetime import datetime
from pathlib import Path

from werkzeug.utils import secure_filename

from intent_ledger.config import DATA_DIR
from intent_ledger.importer.parsers import PARSERS

INGEST_DIR = DATA_DIR / "ingest" / "pending"
MANIFEST_PATH = INGEST_DIR.parent / "manifest.json"

MAX_FILES_PER_UPLOAD = 25


def _read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}

    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest))


def save_uploaded_statements(files, account_id: int, parser_slug: str, user_agent: str) -> dict:
    INGEST_DIR.mkdir(parents=True, exist_ok=True)

    parser = PARSERS.get(parser_slug)

    if parser is None:
        return {"saved": [], "errors": [f"unknown import format '{parser_slug}'"]}

    saved = []
    errors = []

    for file in files[:MAX_FILES_PER_UPLOAD]:
        original_name = file.filename or ""
        filename = secure_filename(original_name)

        if not filename:
            errors.append(f"{original_name or 'unnamed file'}: invalid filename")
            continue

        if not filename.lower().endswith(parser.file_extensions):
            errors.append(
                f"{filename}: only {', '.join(parser.file_extensions)} files are accepted"
                f" for {parser.display_name}"
            )
            continue

        dest = _unique_destination(filename)

        if dest.resolve().parent != INGEST_DIR.resolve():
            errors.append(f"{filename}: invalid destination")
            continue

        file.save(dest)

        if not parser.sniff(dest):
            dest.unlink(missing_ok=True)
            errors.append(f"{filename}: doesn't look like a valid {parser.display_name} file")
            continue

        manifest = _read_manifest()
        manifest[dest.name] = {
            "uploaded_at": time.time(),
            "source": _describe_agent(user_agent),
            "account_id": account_id,
            "parser_slug": parser_slug,
        }
        _write_manifest(manifest)

        saved.append(dest.name)

    if len(files) > MAX_FILES_PER_UPLOAD:
        errors.append(
            f"Only the first {MAX_FILES_PER_UPLOAD} files were processed; upload the rest separately."
        )

    return {"saved": saved, "errors": errors}


def _unique_destination(filename: str) -> Path:
    stem, suffix = filename.rsplit(".", 1)
    suffix = f".{suffix}"

    candidate = INGEST_DIR / filename
    n = 1

    while candidate.exists():
        n += 1
        candidate = INGEST_DIR / f"{stem}-{n}{suffix}"

    return candidate


def _describe_agent(user_agent: str) -> str:
    ua = user_agent or ""

    if "iPhone" in ua:
        device = "iPhone"
    elif "iPad" in ua:
        device = "iPad"
    elif "Android" in ua:
        device = "Android"
    elif "Macintosh" in ua:
        device = "Mac"
    elif "Windows" in ua:
        device = "Windows"
    elif "Linux" in ua:
        device = "Linux"
    else:
        device = "device"

    if "Edg/" in ua:
        browser = "Edge"
    elif "CriOS" in ua or ("Chrome/" in ua and "Chromium" not in ua):
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua and "Chrome" not in ua:
        browser = "Safari"
    else:
        browser = "browser"

    return f"{browser} on {device}"


def list_pending_statements() -> list[dict]:
    if not INGEST_DIR.exists():
        return []

    manifest = _read_manifest()
    entries = []

    for path in INGEST_DIR.iterdir():
        if not path.is_file():
            continue

        stat = path.stat()
        meta = manifest.get(path.name)
        when = meta["uploaded_at"] if meta else stat.st_mtime

        entries.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "when": datetime.fromtimestamp(when).strftime("%b %d, %H:%M"),
                "sort_key": when,
                "source": meta["source"] if meta else None,
                "account_id": meta["account_id"] if meta else None,
                "parser_slug": meta["parser_slug"] if meta else None,
            }
        )

    entries.sort(key=lambda e: e["sort_key"], reverse=True)
    return entries


def delete_pending_statement(filename: str):
    safe = secure_filename(filename)

    if not safe:
        raise ValueError("Invalid filename.")

    path = (INGEST_DIR / safe).resolve()

    if path.parent != INGEST_DIR.resolve() or not path.is_file():
        raise ValueError("Statement not found.")

    path.unlink()

    manifest = _read_manifest()
    if manifest.pop(safe, None) is not None:
        _write_manifest(manifest)

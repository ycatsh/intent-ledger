from intent_ledger.importer.parsers.base import StatementParser
from intent_ledger.importer.parsers.canonical import CanonicalParser

PARSERS: dict[str, StatementParser] = {
    "canonical": CanonicalParser(),
}

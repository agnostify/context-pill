"""Match excerpts against the scanner's saved page text and RSS summary."""
import re
import unicodedata
from pathlib import Path


def _normalize(text):
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))
    text = "".join("-" if unicodedata.category(ch) == "Pd" or ch == "\u2212" else ch for ch in text)
    return " ".join(text.casefold().split())


def quote_found(excerpt, text):
    if not isinstance(excerpt, str) or not isinstance(text, str):
        return False
    quote = _normalize(excerpt)
    return bool(quote and re.search(rf"(?<!\w){re.escape(quote)}(?!\w)", _normalize(text)))


def valid_excerpt(excerpt):
    return isinstance(excerpt, str) and len(excerpt) <= 300 and 0 < len(excerpt.split()) <= 25


def matches_item(excerpt, item, pages, speaker=None):
    if not item or not valid_excerpt(excerpt):
        return False
    try:
        saved = ""
        if item.get("snapshot"):
            path = Path(item["snapshot"]).resolve()
            if not path.is_relative_to(pages.resolve()) or not path.is_file():
                return False
            with path.open(encoding="utf-8") as f:
                saved = f.read(24000)
        summary = item.get("summary")
        summary = summary[:1500] if isinstance(summary, str) else ""
        if speaker and not (quote_found(speaker, saved) or quote_found(speaker, summary)):
            return False
        return quote_found(excerpt, saved) or quote_found(excerpt, summary)
    except (OSError, ValueError, RuntimeError, TypeError):
        return False

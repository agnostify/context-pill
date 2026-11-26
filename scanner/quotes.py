"""Match excerpts against the scanner's saved page text and RSS summary."""
import re
import unicodedata
from pathlib import Path


def _normalize(text):
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))
    text = "".join("-" if unicodedata.category(ch) == "Pd" or ch == "\u2212" else ch for ch in text)
    return " ".join(text.casefold().split())


SPEAKER_WINDOW = 300          # the speaker's name must appear within this many characters of the quote, in the same text
BYLINE_ZONE = 1000            # or, for the page's own author, in the first this-many characters of the saved page (the byline)


def _find(needle, hay):
    """Spans of whole-word matches of the normalized needle in the normalized haystack."""
    return [m.span() for m in re.finditer(rf"(?<!\w){re.escape(needle)}(?!\w)", hay)] if needle else []


def quote_found(excerpt, text):
    if not isinstance(excerpt, str) or not isinstance(text, str):
        return False
    return bool(_find(_normalize(excerpt), _normalize(text)))


def quote_near_speaker(excerpt, speaker, text, window=SPEAKER_WINDOW):
    """True when the quote appears and the speaker's name appears within `window` characters of it, in this one text.
    A name that only appears in the page header or a byline far from the quote does not count."""
    if not all(isinstance(x, str) for x in (excerpt, speaker, text)):
        return False
    hay = _normalize(text)
    names = _find(_normalize(speaker), hay)
    return any(ns < qe + window and ne > qs - window for qs, qe in _find(_normalize(excerpt), hay) for ns, ne in names)


def valid_excerpt(excerpt):
    return isinstance(excerpt, str) and len(excerpt) <= 300 and 0 < len(excerpt.split()) <= 25


def valid_speaker(speaker):
    """A person's name as printed on the page: 2 to 6 words, at most 80 characters, no URLs or @handles."""
    return (isinstance(speaker, str) and len(speaker.strip()) <= 80 and 2 <= len(speaker.split()) <= 6
            and not re.search(r"https?://|www\.|@|/", speaker))


def in_byline(speaker, page_text, zone=BYLINE_ZONE):
    return isinstance(speaker, str) and isinstance(page_text, str) and bool(_find(_normalize(speaker), _normalize(page_text[:zone])))


def is_source_person(speaker, source_name):
    """The speaker is the person the source is named after (e.g. 'Armin Ronacher' for the source 'Armin Ronacher')."""
    return valid_speaker(speaker) and isinstance(source_name, str) and bool(_find(_normalize(speaker), _normalize(source_name)))


def matches_item(excerpt, item, pages, speaker=None, byline_ok=False):
    """The excerpt must appear in the item's saved page text or its RSS summary. With a speaker (takes), the speaker's
    name must appear near the quote in the same text; or, when byline_ok (the speaker is the source's own person, or the
    source is a lab page), the name may instead sit in the saved page's byline zone with the quote anywhere on the page."""
    if not item or not valid_excerpt(excerpt):
        return False
    try:
        texts = []
        if item.get("snapshot"):
            path = Path(item["snapshot"]).resolve()
            if not path.is_relative_to(pages.resolve()) or not path.is_file():
                return False
            with path.open(encoding="utf-8") as f:
                texts.append(f.read(24000))
        summary = item.get("summary")
        if isinstance(summary, str):
            texts.append(summary[:1500])
        if speaker is not None:
            if not valid_speaker(speaker):
                return False
            if any(quote_near_speaker(excerpt, speaker, t) for t in texts):
                return True
            page = texts[0] if item.get("snapshot") and texts else ""
            return bool(byline_ok and page and in_byline(speaker, page) and quote_found(excerpt, page))
        return any(quote_found(excerpt, t) for t in texts)
    except (OSError, ValueError, RuntimeError, TypeError):
        return False

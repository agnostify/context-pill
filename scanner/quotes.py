"""Match excerpts against the scanner's saved page text and RSS summary."""
import re
import unicodedata
from pathlib import Path


def _normalize(text):
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))
    text = "".join("-" if unicodedata.category(ch) == "Pd" or ch == "\u2212" else ch for ch in text)
    return " ".join(text.casefold().split())


SPEAKER_WINDOW = 200          # the speaker's name must appear within this many characters before the quote, in the same text
SPEAKER_AFTER = 80            # or this close after it ('...,' said Ada Builder)
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
    # a name inside the quote itself ("Boris Cherny says ...") is someone talking about him, not him talking
    return any((ne <= qs and ne > qs - window) or (ns >= qe and ns < qe + SPEAKER_AFTER)
               for qs, qe in _find(_normalize(excerpt), hay) for ns, ne in names)


def valid_excerpt(excerpt):
    return isinstance(excerpt, str) and len(excerpt) <= 300 and 0 < len(excerpt.split()) <= 25


def valid_speaker(speaker):
    """A person's name as printed on the page: 2 to 6 words, at most 80 characters, only letters, single spaces,
    apostrophes, periods and hyphens (so it can't carry a link, markup or a line break into latest.md)."""
    return (isinstance(speaker, str) and len(speaker) <= 80 and speaker == " ".join(speaker.split())
            and 2 <= len(speaker.split()) <= 6 and all(ch.isalpha() or ch in " .'-\u2019" for ch in speaker))


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")


def date_forms(iso):
    """Common ways a page prints a date: 2026-11-18, November 18, 2026, Nov 18, 2026, Nov. 18, 2026, 18 November 2026, 11/18/2026."""
    y, m, d = (int(x) for x in iso.split("-"))
    full, short = MONTHS[m - 1], MONTHS[m - 1][:3]
    return {iso, f"{full} {d}, {y}", f"{short} {d}, {y}", f"{short}. {d}, {y}", f"{d} {full} {y}", f"{d} {short} {y}",
            f"{m}/{d}/{y}", f"{m:02d}/{d:02d}/{y}", f"{full} {d:02d}, {y}", f"{short} {d:02d}, {y}"}


def date_on_item(iso, item, pages):
    """The original publication date appears near the top of the item's saved page, or equals the feed entry's date."""
    try:
        forms = date_forms(iso)
    except (ValueError, IndexError, AttributeError):
        return False
    if str((item or {}).get("published") or "")[:10] == iso:
        return True
    # only the page's top (where the byline and date sit), so a date mentioned in the body can't pass as the post's own
    page = _texts({"snapshot": item.get("snapshot")}, pages) if item and item.get("snapshot") else []
    return bool(page) and any(quote_found(f, page[0][:BYLINE_ZONE]) for f in forms)      # the saved page only, never the summary


def _texts(item, pages):
    out = []
    try:
        if item and item.get("snapshot"):
            path = Path(item["snapshot"]).resolve()
            if path.is_relative_to(pages.resolve()) and path.is_file():
                with path.open(encoding="utf-8") as f:
                    out.append(f.read(24000))
        if item and isinstance(item.get("summary"), str):
            out.append(item["summary"][:1500])
    except (OSError, ValueError, RuntimeError, TypeError):
        pass
    return out


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


HTTPS_LINK = re.compile(r"^https://[^\"'<>@/\s]+(?:/[^\"'<>\s]*)?$")      # the same shape mcp/server.mjs httpsOnly() accepts


def https_link(url):
    return isinstance(url, str) and len(url) <= 500 and all(0x21 <= ord(ch) <= 0x7e for ch in url) and bool(HTTPS_LINK.match(url))

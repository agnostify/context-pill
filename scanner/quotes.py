"""Match excerpts against the scanner's saved page text and RSS summary."""
import re
import unicodedata
from collections import Counter
from pathlib import Path


def _normalize(text):
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))
    text = "".join("-" if unicodedata.category(ch) == "Pd" or ch == "\u2212" else ch for ch in text)
    return " ".join(text.casefold().split())


SPEAKER_WINDOW = 200          # the speaker's name must appear within this many characters before the quote, in the same text
SPEAKER_AFTER = 80            # or this close after it ('...,' said Ada Builder)
BYLINE_ZONE = 1000            # or, for the page's own author, in the first this-many characters of the saved page (the byline)
MAX_SNAPSHOT_CHARS = 200_000

# Detect boundaries even when the label cannot identify a speaker.
_NAME_WORD = r"@?[^\W_]\w*(?:[.'\u2019\u2010\u2011-]\w+)*\.?"
_TIMESTAMP = r"[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?"
_TIME_MARK = rf"(?:\({_TIMESTAMP}\)|\[{_TIMESTAMP}\]|{_TIMESTAMP})"
_TURN_LABEL = re.compile(
    rf"^[ \t]*(?P<prefix>(?:(?P<before>{_TIME_MARK})[ \t]*)?"
    rf"(?P<label>{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{0,5}}?)"
    rf"(?:[ \t]*(?P<after>{_TIME_MARK}))?)[ \t]*(?:(?P<colon>:)|\r?$)", re.MULTILINE)


def turn_labels(text):
    """Return each boundary's line start, label end, and name, in text order."""
    labels = []
    for match in _TURN_LABEL.finditer(text.replace("\u00a0", " ")):
        if len(match["prefix"]) > 60:
            continue
        words = match["label"].split()
        if not (match["colon"] or match["before"] or match["after"]):
            # Bare labels need to look like names or handles, since speech also wraps onto its own lines.
            if (words[-1].endswith(".") and len(words[-1]) > 2
                    or len(words) > 1 and (not words[0][0].isupper() or words[-1][0].islower())):
                continue
        labels.append((match.start(), match.end(), " ".join(words)))
    return labels


def _creditable_labels(labels):
    counts = Counter(_normalize(label) for _, _, label in labels if valid_speaker(label))
    return {label for label, count in counts.items() if count >= 3}


def has_turns(labels):
    return len(_creditable_labels(labels)) >= 2


def quote_in_turn(excerpt, speaker, text, labels):
    """Find the quote between the end of its speaker label and the next boundary."""
    name = _normalize(speaker)
    return any(valid_speaker(label) and _normalize(label) == name and quote_found(excerpt, text[end:next_start])
               for (_, end, label), (next_start, _, _) in zip(labels, labels[1:]))


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
            if not path.is_relative_to(pages.resolve()) or not path.is_file():
                return []
            with path.open(encoding="utf-8") as f:
                out.append(f.read(MAX_SNAPSHOT_CHARS))
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
    """Match takes using transcript turns or the source's proximity and byline rules."""
    if not item or not valid_excerpt(excerpt):
        return False
    texts = _texts(item, pages)
    if speaker is None:
        return any(quote_found(excerpt, t) for t in texts)
    if not texts or not valid_speaker(speaker):
        return False
    text = texts[0]
    if item.get("snapshot"):
        if item.get("transcript") is True:
            turns = turn_labels(text)
            creditable = _creditable_labels(turns)
            if len(creditable) >= 2:
                return _normalize(speaker) in creditable and quote_in_turn(excerpt, speaker, text, turns)
            return is_source_person(speaker, item.get("source")) and quote_near_speaker(excerpt, speaker, text)
        return any(quote_near_speaker(excerpt, speaker, t) for t in texts) or bool(
            byline_ok and in_byline(speaker, text) and quote_found(excerpt, text))
    return (is_source_person(speaker, item.get("source")) and not item.get("summary_has_turns")
            and not has_turns(turn_labels(text)) and quote_near_speaker(excerpt, speaker, text))


HTTPS_LINK = re.compile(r"^https://[^\"'<>@/\s]+(?:/[^\"'<>\s]*)?$")      # the same shape mcp/server.mjs httpsOnly() accepts


def https_link(url):
    return isinstance(url, str) and len(url) <= 500 and all(0x21 <= ord(ch) <= 0x7e for ch in url) and bool(HTTPS_LINK.match(url))

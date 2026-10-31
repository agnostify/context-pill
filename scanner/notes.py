"""Mechanical check of the short public notes the resolver and auditor write (resolution_note, audit_note, changelog lines).
The model returns, per claim, a note plus the single source_url it relies on and a verbatim quote from that source.
validate_note() is strict. Text is normalized by lowercasing and collapsing whitespace only; punctuation is kept. The source
page must have been fetched and every quote part must be on it. Any absence claim ("not listed", "discontinued", ...) is
rejected. The note (minus one trailing period) must equal a run of WHOLE clauses of one quote part: it starts at the part's
start or right after a boundary char [.;:,!?] plus a space, and ends at the part's end or right before a boundary char ("."
inside a number like 4.5 is not a boundary). So "open source" can't come from "isn't truly open source", nor "supports X"
from "no longer supports X", and a span can't run across a sentence. A rejected or malformed note is replaced by a
deterministic one ("Outcome: good. Source: <url>", source_url only if its page was fetched, else the claim's evidence URL);
the outcome or verdict itself never changes.
Deterministic; the only network use is fetch_text (through util.get), which callers can swap out."""
import html, re
from . import util

MAX_WORDS = 20
ABSENCE = re.compile(r"\b(not (?:shown|listed|mentioned|found|included|present|stated|available|offered)|"
                     r"isn['’]?t (?:shown|listed|mentioned|included|available|offered)|no longer(?: \w+)?|unavailable|"
                     r"retired|discontinued|dropped|deprecated|omit(?:s|ted)?|removed|lack(?:s|ing)?|"
                     r"absent|missing|no mention|(?:doesn['’]?t|does not|don['’]?t|do not) (?:mention|show|list|include))\b", re.I)
BOUNDARY = ".;:,!?"


def _norm(s):
    """Lowercase, whitespace collapsed to single spaces; nothing else changes."""
    return " ".join((s or "").lower().split())


def _boundary(q, j):
    """q[j] ends a clause: a boundary char, but not a '.' between two digits (4.5)."""
    if q[j] not in BOUNDARY:
        return False
    return not (q[j] == "." and 0 < j < len(q) - 1 and q[j - 1].isdigit() and q[j + 1].isdigit())


def _whole_clauses(n, q):
    """n occurs in q starting at q's start or after '<boundary> ', and ending at q's end or right before a boundary."""
    i = q.find(n)
    while i >= 0:
        j = i + len(n)
        if (i == 0 or (i >= 2 and q[i - 1] == " " and _boundary(q, i - 2))) and (j == len(q) or _boundary(q, j)):
            return True
        i = q.find(n, i + 1)
    return False


def quote_parts(quote):
    """The quote's non-empty pieces; [] when it is missing or anything in it is not a string."""
    parts = list(quote) if isinstance(quote, (list, tuple)) else [quote or ""]
    if not all(isinstance(q, str) for q in parts):
        return []
    return [p.strip(" .\"'") for q in parts for p in re.split(r"\.\.\.|…", q) if p.strip(" .\"'")]


def validate_note(note, quote, source_text=None):
    """Problems with a note (empty list = the note may be published). quote: str or list of str from ONE source;
    source_text: that source's full page text; None (not fetched) always rejects."""
    if note is not None and not isinstance(note, str):
        return ["note is not a string"]
    note = (note or "").strip()
    parts = quote_parts(quote)
    if not note:
        return ["empty note"]
    if not parts:
        return ["no quote"]
    if not isinstance(source_text, str):
        return ["source page not fetched"]
    problems = []
    if len(note.split()) > MAX_WORDS:
        problems.append(f"over {MAX_WORDS} words")
    src = _norm(source_text)
    missing = [p for p in parts if _norm(p) not in src]
    if missing:
        problems.append(f"quote not found in source: {missing[0][:60]!r}")
    if ABSENCE.search(note):
        problems.append("absence claim")
    n = _norm(note[:-1] if note.endswith(".") else note)
    if not n or not any(_whole_clauses(n, _norm(p)) for p in parts):
        problems.append("note is not a run of whole clauses of the quote")
    return problems


def _page_text(raw):
    """Full page text for the quote check: every section, including nav/footer and script data."""
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fetch_text(url):
    """The source's full text, or None (blocked, stopped, robots, HTTP error, network error): then the note falls back."""
    try:
        r = util.get(url, timeout=20)
        return _page_text(r.text) if r.status_code < 400 else None
    except Exception:
        return None


def _usable(url):
    return isinstance(url, str) and url.startswith("http") and not util.blocked(url)


def _checked(r, label, value, fallback_url, fetch):
    """The note if it passes; else the fallback, citing source_url only when its page was fetched (else the evidence URL)."""
    r = r if isinstance(r, dict) else {}
    note, url, quote = r.get("note"), r.get("source_url"), r.get("quote")
    text = None
    if _usable(url) and fetch:
        try:
            text = fetch(url)
        except Exception:
            text = None
        text = text if isinstance(text, str) else None
    if _usable(url) and isinstance(note, str) and note.strip() and quote_parts(quote):
        try:
            if not validate_note(note, quote, text):
                return note
        except Exception:
            pass
    src = url if text is not None else fallback_url
    value = value if value is not None else {"Verdict": "not run", "Outcome": "unknown"}.get(label)
    return f"{label}: {value}. Source: {src}" if src else f"{label}: {value}."


def _evidence_url(evidence):
    return next((e["url"] for e in evidence or () if isinstance(e, dict) and _usable(e.get("url"))), None)


def resolution_note(r, outcome, evidence=(), fetch=fetch_text):
    """The note to store for a resolution result r ({outcome, note, source_url, quote}); fallback when it fails the check."""
    return _checked(r, "Outcome", outcome, _evidence_url(evidence), fetch)


def audit_note(r, evidence=(), fetch=fetch_text):
    """The note to publish for an audit result r ({verdict, note, source_url, quote}); fallback when it fails the check."""
    return _checked(r, "Verdict", r.get("verdict") if isinstance(r, dict) else None, _evidence_url(evidence), fetch)

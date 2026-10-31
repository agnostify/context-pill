"""Mechanical check of the short public notes the resolver and auditor write (resolution_note, audit_note, changelog lines).
The model returns, per claim, a note plus the single source_url it relies on and a verbatim quote from that source.
validate_note() is strict: after normalizing (lowercase, whitespace collapsed, punctuation dropped except $ % . / inside
figures) the whole note must be ONE contiguous span of one quote part, not directly after a negation, and the quote must be
on the fetched page. Spans are never joined, so "Claude Haiku 4.5" + "Input $3 / MTok" can't be lifted from a Haiku-then-Sonnet
price list. Without the fetched page the note must also hold no figure, number word, negation or absence claim. An absence
claim needs the page fetched with none of its terms on it; the rest of the note is then the span. A rejected or malformed note
is replaced by a deterministic one ("Outcome: good. Source: <url>", source_url only if its page was fetched, else the claim's
evidence URL); the outcome or verdict itself never changes.
Deterministic; the only network use is fetch_text (through util.get), which callers can swap out."""
import html, re
from . import util

MAX_WORDS = 20
NEGATIONS = {"not", "no", "nor", "never", "cannot", "unavailable", "without", "none"}   # plus any "n't" word ("cant", "isnt" once flattened)
_NT = {"ca", "is", "are", "was", "were", "do", "does", "did", "wo", "would", "should", "could", "has", "have", "had", "must", "ai"}

NUM_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                              "fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
                  "hundred": "hundred", "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion",
                  "dozen": "dozen", "single": "1", "half": "half", "twice": "2", "double": "2", "triple": "3"})
_TOKEN = re.compile(r"(\$)\s?(\d[\d,]*(?:\.\d+)?)"                     # $1, $0.10, $1,000
                    r"|(?<![\w.])(\d[\d,]*(?:\.\d+)*)\s?(%|percent\b|x\b|×)?"  # 12%, 17x, 4.5, 2026
                    r"|\b(" + "|".join(NUM_WORDS) + r")\b", re.I)

LABEL_WORDS = {"post", "blog", "announcement", "arxiv", "abstract", "paper", "preprint", "docs", "documentation", "changelog",
               "repo", "repository", "readme", "github", "rss", "feed", "article"}       # naming the source isn't content
ABSENCE = re.compile(r"\b(not (?:shown|listed|mentioned|found|included|present|stated|available|offered)|"
                     r"isn'?t (?:shown|listed|mentioned|included|available|offered)|no longer(?: \w+)?|unavailable|"
                     r"retired|discontinued|dropped|deprecated|omit(?:s|ted)?|removed|lack(?:s|ing)?|"
                     r"absent|missing|no mention|(?:doesn'?t|does not|don'?t|do not) (?:mention|show|list|include))\b", re.I)

STOP = set("""a an the and or but nor of in on at to for from by with as is are was were be been being it its this that these
those there here than then so such only also both all any each per via into onto over under up down out about after before
within inside across between among through around near
can could may might will would should must has have had do does did done new now still just more most less same very own
which who whom whose what when where while how why says said say confirm confirms confirmed show shows shown list lists listed
state states stated report reports reported note notes match matches matched page pages source sources site item items
claim claims quote quotes per their they them we our us you your he she his her""".split())


def _stem(w):
    w = w.lower().replace("’", "'")
    w = re.sub(r"'s$", "", w)
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    else:
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[:-len(suf)]; break
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def numbers(text):
    """Figure tokens as (kind, value): kind is '$', '%', 'x' or '' (a bare number or number word)."""
    out = []
    for m in _TOKEN.finditer(text or ""):
        if m.group(1):
            out.append(("$", _num(m.group(2))))
        elif m.group(3):
            suf = (m.group(4) or "").lower()
            out.append(({"%": "%", "percent": "%", "x": "x", "×": "x"}.get(suf, ""), _num(m.group(3))))
        else:
            out.append(("", NUM_WORDS[m.group(5).lower()]))
    return out


def _num(s):
    s = s.replace(",", "")
    return s.rstrip("0").rstrip(".") if s.count(".") == 1 else s     # 4.50 -> 4.5, $1.00 -> 1


def _has_number(tok, pool):
    kind, val = tok
    return any(v == val and (not kind or k == kind) for k, v in pool)


def _tokens(text):
    """Every word in order: its stem if it is a content word, else None (stopword, number, source label)."""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z'’]*|\d[\d.,]*", text or ""):
        lw = w.lower()
        content = w[0].isalpha() and len(w) > 1 and lw not in STOP and lw not in NUM_WORDS and lw not in LABEL_WORDS
        out.append(_stem(w) if content and _stem(w) not in STOP else None)
    return out


def _words(text):
    """Content-word stems in order."""
    return [t for t in _tokens(text) if t]


def _flat(s):
    """Lowercase words and figures, all other punctuation and spacing reduced to single spaces ("We've" -> "weve"); $ % . /
    survive only inside figures ("$1", "12%", "4.5", "1/5")."""
    s = (s or "").lower()
    for a, b in (("’", "'"), ("‘", "'"), ("×", "x")):
        s = s.replace(a, b)
    s = re.sub(r"(?<=\d),(?=\d)", "", s.replace("'", ""))
    s = re.sub(r"\$\s+(?=\d)", "$", re.sub(r"(?<=\d)\s+%", "%", s))
    s = re.sub(r"[^a-z0-9$%./]+", " ", s)
    s = re.sub(r"\.(?!\d)|(?<!\d)\.|/(?!\$?\d)|(?<!\d)/|\$(?!\d)|(?<!\d)%", " ", s)
    return " ".join(s.split())


def _neg(w):
    """A flattened word that negates what follows it."""
    return w in NEGATIONS or (w.endswith("nt") and w[:-2] in _NT)


def _span(text, parts):
    """The flattened text is one contiguous run of words in one quote part, somewhere not directly after a negation (so
    "veto publication" can't come from "cannot veto publication")."""
    n = _flat(text).split()
    for p in parts:
        q = _flat(p).split()
        if any(q[i:i + len(n)] == n and not (i and _neg(q[i - 1])) for i in range(len(q) - len(n) + 1)):
            return bool(n)
    return False


def quote_parts(quote):
    """The quote's non-empty pieces; [] when it is missing or anything in it is not a string."""
    parts = list(quote) if isinstance(quote, (list, tuple)) else [quote or ""]
    if not all(isinstance(q, str) for q in parts):
        return []
    return [p.strip(" .\"'") for q in parts for p in re.split(r"\.\.\.|…", q) if p.strip(" .\"'")]


def _clauses(note):
    return [c for c in re.split(r"[;:.,()]\s|[;:()]|\.$", note) if c.strip()]


def _absence_subject(note):
    """The rest of each clause with an absence phrase (what is said to be missing, before or after "omits"), clause by clause."""
    subj, rest = [], []
    for c in _clauses(note):
        if ABSENCE.search(c):
            subj.append(ABSENCE.sub(" ", c))
        else:
            rest.append(c)
    return subj, "; ".join(rest)


def validate_note(note, quote, source_text=None):
    """Problems with a note (empty list = the note may be published). quote: str or list of str from ONE source;
    source_text: that source's full page text when it was fetched (enables the quote and absence checks; without it a
    note with any figure, number word, negation or absence claim is rejected)."""
    if note is not None and not isinstance(note, str):
        return ["note is not a string"]
    if source_text is not None and not isinstance(source_text, str):
        source_text = None
    note = (note or "").strip()
    parts = quote_parts(quote)
    if not note:
        return ["empty note"]
    if not parts:
        return ["no quote"]
    problems = []
    if len(note.split()) > MAX_WORDS:
        problems.append(f"over {MAX_WORDS} words")
    if source_text is not None:
        src = f" {_flat(source_text)} "
        missing = [p for p in parts if f" {_flat(p)} " not in src]
        if missing:
            problems.append(f"quote not found in source: {missing[0][:60]!r}")
    else:
        if numbers(note):
            problems.append("figures without the full source text checked")
        if any(_neg(w) for w in _flat(note).split()):
            problems.append("negation without the full source text checked")

    body = note
    subjects, rest = _absence_subject(note)
    if subjects:
        if source_text is None:
            problems.append("absence claim without the full source text checked")
        else:
            src_words, src_nums = set(_words(source_text)), numbers(source_text)
            for s in subjects:
                terms = [("w", w) for w in _words(s)] + [("n", t) for t in numbers(s)]
                present = [t for t in terms if (t[1] in src_words if t[0] == "w" else _has_number(t[1], src_nums))]
                if not terms or present:                # any subject term on the page: it may well be listed there
                    problems.append(f"absence claim but the source contains {s.strip()!r}")
        body = rest                         # verified-absent subjects are not expected in the quote
        if not _flat(body):
            return problems
    if not _span(body, parts):
        problems.append("note is not one contiguous span of the quote")
    return problems


def _page_text(raw):
    """Full page text for the absence and quote checks: every section, including nav/footer and script data."""
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fetch_text(url):
    """The source's full text, or None (blocked, stopped, robots, HTTP error, network error): then only the quote is checked."""
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
    return f"{label}: {value}. Source: {src}" if src else f"{label}: {value}."


def _evidence_url(evidence):
    return next((e["url"] for e in evidence or () if isinstance(e, dict) and _usable(e.get("url"))), None)


def resolution_note(r, outcome, evidence=(), fetch=fetch_text):
    """The note to store for a resolution result r ({outcome, note, source_url, quote}); fallback when it fails the check."""
    return _checked(r, "Outcome", outcome, _evidence_url(evidence), fetch)


def audit_note(r, evidence=(), fetch=fetch_text):
    """The note to publish for an audit result r ({verdict, note, source_url, quote}); fallback when it fails the check."""
    return _checked(r, "Verdict", r.get("verdict") if isinstance(r, dict) else None, _evidence_url(evidence), fetch)

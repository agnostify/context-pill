"""Mechanical check of the short public notes the resolver and auditor write (resolution_note, audit_note, changelog lines).
The model returns, per claim, a note plus the single source_url it relies on and a verbatim quote from that source.
validate_note() rejects a note that states a figure the quote doesn't (or in another order, or next to another word), mixes
sources, claims something is absent while any of its terms is on the page (or the page wasn't fetched), states any figure
without the page fetched, or recombines the quote's words into a phrase it doesn't contain. A rejected or malformed note is
replaced by a deterministic one ("Outcome: good. Source: <url>", source_url only if its page was fetched, else the claim's
evidence URL); the outcome or verdict itself never changes.
Deterministic; the only network use is fetch_text (through util.get), which callers can swap out."""
import html, re
from . import util

MAX_WORDS = 20
MIN_COVERAGE = 0.75             # share of the note's content words that must appear in the quote

NUM_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                              "fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
                  "hundred": "hundred", "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion",
                  "dozen": "dozen", "single": "1", "half": "half", "twice": "2", "double": "2", "triple": "3"})
_TOKEN = re.compile(r"(\$)\s?(\d[\d,]*(?:\.\d+)?)"                     # $1, $0.10, $1,000
                    r"|(?<![\w.])(\d[\d,]*(?:\.\d+)*)\s?(%|percent\b|x\b|×)?"  # 12%, 17x, 4.5, 2026
                    r"|\b(" + "|".join(NUM_WORDS) + r")\b", re.I)

# kinds of source a note can name; two different kinds in one note with figures = figures from more than one source
SOURCE_LABELS = {
    "post": r"post|blog|announcement",
    "paper": r"arxiv|abstract|paper|preprint",
    "docs": r"docs|documentation",
    "changelog": r"changelog|release notes",
    "repo": r"repo|repository|readme|github",
    "card": r"model card|system card",
    "feed": r"rss|feed",
    "article": r"article",
    "pricing": r"pricing page",
}
_LABEL = {k: re.compile(r"\b(?:" + v + r")s?\b", re.I) for k, v in SOURCE_LABELS.items()}
LABEL_WORDS = {"post", "blog", "announcement", "arxiv", "abstract", "paper", "preprint", "docs", "documentation", "changelog",
               "repo", "repository", "readme", "github", "rss", "feed", "article"}       # naming the source isn't content
AGREEMENT = re.compile(r"\b(agree|agrees|both (?:confirm|say|show|list|state|report)|sources? (?:confirm|agree))\b", re.I)
ABSENCE = re.compile(r"\b(not (?:shown|listed|mentioned|found|included|present|stated)|isn'?t (?:shown|listed|mentioned|included)|"
                     r"no longer (?:shown|listed|mentioned|included|present|available|offered)|omit(?:s|ted)?|removed|lack(?:s|ing)?|"
                     r"absent|missing|no mention|(?:doesn'?t|does not|don'?t|do not) (?:mention|show|list|include))\b", re.I)

STOP = set("""a an the and or but nor of in on at to for from by with without as is are was were be been being it its this that these
those there here than then so such not no only also both all any each per via into onto over under up down out about after before
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


def _figures(text):
    """Figures in order as ((kind, value), label): label is the stem of the content word directly before the figure (only
    whitespace between, so "input, $5" has none), else None."""
    out = []
    for m, tok in zip(_TOKEN.finditer(text), numbers(text)):
        w = re.search(r"([A-Za-z][A-Za-z'’]*)[ \t]*$", text[:m.start()])
        out.append((tok, w and (_tokens(w.group(1)) or [None])[0]))
    return out


def _quote_figures(parts):
    """Figures of the quote in order, each with the word before it in its part (any punctuation between)."""
    out = []
    for p in parts:
        for m, tok in zip(_TOKEN.finditer(p), numbers(p)):
            w = re.search(r"([A-Za-z][A-Za-z'’]*)\W*$", p[:m.start()])
            out.append((tok, w and _stem(w.group(1))))
    return out


def _figures_in_order(note_figs, quote_figs):
    """Each note figure matches a later quote figure than the one before it, with the same value and (if the note puts a
    word right before the figure) the same word before it in the quote. Greedy earliest match decides a subsequence."""
    i = 0
    for tok, label in note_figs:
        while i < len(quote_figs) and not (_has_number(tok, [quote_figs[i][0]]) and (not label or quote_figs[i][1] == label)):
            i += 1
        if i == len(quote_figs):
            return False
        i += 1
    return True


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


def _note_pairs(text):
    """Content words standing side by side in the note (order-free)."""
    t = _tokens(text)
    return {frozenset((a, b)) for a, b in zip(t, t[1:]) if a and b and a != b}


def _joined(pair, toks, allowed, window=4):
    """The quote has both words at most `window` words apart, with nothing between them but stopwords, figures and words
    the note itself uses (so dropping a qualifier like "predefined" breaks the phrase)."""
    a, b = sorted(pair)
    at = {w: [i for i, t in enumerate(toks) if t == w] for w in (a, b)}
    return any(abs(i - j) <= window and all(t is None or t in allowed for t in toks[min(i, j) + 1:max(i, j)])
               for i in at[a] for j in at[b])


def _norm(s):
    s = (s or "").lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


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
    note with any figure, count or absence claim is rejected)."""
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
        src = _norm(source_text)
        missing = [p for p in parts if _norm(p) not in src]
        if missing:
            problems.append(f"quote not found in source: {missing[0][:60]!r}")
    qtext = " ... ".join(parts)

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
            body = rest                     # verified-absent subjects are not expected in the quote

    qnums = numbers(qtext)
    snums = numbers(source_text) if source_text is not None else None
    if source_text is None and numbers(body):
        problems.append("figures without the full source text checked")
    for t in numbers(body):
        if not _has_number(t, qnums):
            problems.append(f"figure {t[1]}{t[0] if t[0] != '$' else ''} not in the quote")
        elif snums is not None and not _has_number(t, snums):
            problems.append(f"figure {t[1]} not in the source")
    if all(_has_number(t, qnums) for t in numbers(body)):
        # figures keep the quote's order and the word right before each, so "Input $5; output $1" can't come from "Input $1; output $5"
        nfigs = [f for c in _clauses(body) for f in _figures(c)]
        if not _figures_in_order(nfigs, _quote_figures(parts)):
            problems.append("figures out of the quote's order or attached to different words")

    labels = {k for k, rx in _LABEL.items() if rx.search(note)}
    if AGREEMENT.search(note):
        problems.append("note combines sources (agreement)")
    elif len(labels) > 1 and numbers(note):
        problems.append(f"note mixes sources with figures ({', '.join(sorted(labels))})")

    # wording: most content words come from the quote, and two words side by side in the note that are both in the quote
    # also stand together there, so "redaction categories" can't come from "redactions within predefined categories"
    qset = set(_words(qtext))
    qtoks = [_tokens(p) for p in parts]
    nwords = _words(body)
    if nwords:
        cov = sum(w in qset for w in nwords) / len(nwords)
        if cov < MIN_COVERAGE:
            problems.append(f"only {cov:.0%} of the note's words are in the quote")
    for c in _clauses(body):
        for pair in sorted(_note_pairs(c), key=sorted):
            if pair <= qset and not any(_joined(pair, t, set(nwords)) for t in qtoks):
                problems.append(f"phrase {' '.join(sorted(pair))} not in the quote")
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

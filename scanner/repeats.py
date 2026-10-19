"""Cross-day repeat-story check: catches the same story published again under a different URL or wording.
Runs in scanner.run's publication checks, before review. A claim repeats an earlier story when, with no version
conflict, either
  (a) they cite the same specific evidence URL (index, docs, package and repo-home pages don't count) and share a name or
      2+ distinctive words, or
  (b) they share a name (a product, model or company word that isn't boilerplate) and their distinctive words
      overlap strongly.
Earlier stories: ledger claims from the last WINDOW_DAYS days, plus an optional JSONL history named by the
CP_STORY_HISTORY env var (one {"date", "text"|"headline", "urls"} object per line; bad lines are skipped).
It is a heuristic, so it only drops an exact duplicate (same text apart from letter case and whitespace). A match that
also says something new is kept and flagged for the review step. Every drop and flag is logged with the claim and the
matched story so a wrong drop can be restored."""
import json, os, re, datetime as dt
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode

WINDOW_DAYS = 14
OVERLAP = 0.5          # shared words / the smaller word set (figure rule)
COVER = 0.8            # share of the new claim's own words already in the earlier story
GENERIC_LAST = {"", "changelog", "changelog.md", "blog", "news", "releases", "docs", "pricing", "models", "research", "index.html",
                "release-notes", "whats-new", "updates", "readme.md"}
TRACKING = {"ref", "si", "feature", "source", "fbclid", "gclid"}
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "its", "it", "is", "are", "at", "by", "from", "as", "that",
        "this", "these", "than", "then", "into", "about", "more", "most", "less", "also", "can", "now", "new", "not", "but", "has", "have",
        "was", "were", "be", "been", "will", "would", "so", "up", "out", "via", "per", "which", "who", "when", "what", "how", "all", "any",
        "says", "said", "say", "released", "release", "releases", "adds", "added", "add", "ships", "shipped", "launched", "launch",
        "announced", "published", "publishes", "reports", "report", "reported", "available", "using", "uses", "use", "used", "lets",
        "first", "under", "supports", "support", "today", "week", "day", "days", "million", "tokens", "token", "input", "output",
        "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "2024", "2025", "2026", "2027"}
# vendor and product-family names appear in most items, so they never count as the shared name on their own
WEAK = {"anthropic", "claude", "code", "openai", "codex", "chatgpt", "gpt", "google", "gemini", "deepmind", "cli", "github", "meta",
        "microsoft", "nvidia", "mistral", "cursor", "hugging", "face", "ai", "llm", "sdk", "app", "agent", "agents", "pr", "prs", "api",
        "model", "models", "open", "weights", "beta", "preview"}
# license, weight-format, plan and size words are shared by many unrelated launches
BOILERPLATE = {"apache", "mit", "bsd", "gpl", "agpl", "lgpl", "mpl", "cc", "license", "licence", "bf16", "fp8", "fp16", "fp4", "nvfp4",
               "int4", "int8", "gguf", "awq", "gptq", "moe", "total", "active", "context", "1m", "pro", "max", "plus", "team", "teams",
               "enterprise", "free", "business", "paid", "plans", "plan", "tier", "hf", "huggingface", "version", "versions"}
LICENSES = {"apache", "mit", "bsd", "gpl", "agpl", "lgpl", "mpl", "cc"}
_TOK = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_/+']*[A-Za-z0-9]|[A-Za-z0-9]")
_VER = re.compile(r"v?(\d+(?:\.\d+)+(?:[.\-][a-z0-9]+)*)", re.I)


def norm_url(u):
    """Host lowercased, www. dropped, path case kept, trailing slash and fragment dropped, query kept (sorted, minus tracking)."""
    if not isinstance(u, str) or not u.strip():
        return ""
    try:
        p = urlsplit(u.strip())
        p.port                                   # raises on a malformed host or port
    except ValueError:
        return ""
    host = p.netloc.lower().removeprefix("www.")
    if not host:
        return ""
    q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING)
    return f"{host}{p.path.rstrip('/')}" + (f"?{urlencode(q)}" if q else "")


def specific(u):
    """True for a URL that names one story; False for index, docs, repo-root and package pages (they change over time)."""
    n = norm_url(u)
    if not n:
        return False
    host, _, path = n.partition("/")
    path = path.split("?", 1)[0]
    segs = [s for s in path.split("/") if s]
    if not segs or segs[-1].lower() in GENERIC_LAST:
        return False
    if "docs" in (s.lower() for s in segs) or host.startswith("docs.") or host.startswith("platform."):
        return False
    if host == "huggingface.co" and len(segs) < 2:
        return False
    if host == "github.com":                     # only one-story pages: a release tag, PR, issue, commit, discussion or advisory
        kind = [x.lower() for x in segs[2:4]]
        ok = (len(segs) >= 5 and kind == ["releases", "tag"]) or (len(segs) >= 4 and kind[0] in ("pull", "issues", "commit", "discussions")) \
            or (len(segs) >= 5 and kind == ["security", "advisories"])
        if not ok:
            return False
    if host == "gitlab.com":                     # group/subgroup/project/-/<kind>/...: only releases, tags, MRs and issues are specific
        if "-" not in segs:
            return False
        rest = segs[segs.index("-") + 1:]
        if len(rest) < 2 or rest[0].lower() not in ("releases", "tags", "merge_requests", "issues"):
            return False
    if segs[-1].lower() == "latest":
        return False
    if host in ("x.com", "twitter.com", "reddit.com", "old.reddit.com") and (len(segs) == 1 or (segs[0] == "r" and len(segs) <= 2)):
        return False                             # profiles and subreddit front pages
    if host == "arxiv.org" and segs[0] == "list":
        return False
    if host in ("npmjs.com", "pypi.org", "crates.io") or (host == "github.com" and segs[-1].lower() == "changelog.md"):
        return False
    return True


def _ver(t):
    """Canonical version for a token: dotted (2.1.302, v0.66.0-nightly.1) or v-prefixed integer (v2); else None."""
    if re.fullmatch(r"v\d+", t):
        return t[1:]
    if any(ch.isdigit() for ch in t) and "." in t:
        m = _VER.search(t)
        return m.group(1).lower() if m else None
    return None


def analyze(text):
    """Return (words, names, versions, numbers, status labels, money/percent figures, unused, exact text) for a claim text.
    A version right after a license name, with only "License"/"Version" between (Apache 2.0, Apache License,
    Version 2.0), and hyphenated licenses (apache-2.0) are ignored. A bare integer after a capitalized name (Opus 4,
    GPT-5) is a version. Figures after '$' or before '%' are numbers, not versions."""
    text = text if isinstance(text, str) else str(text or "")
    toks = []                                    # (token, money_or_percent)
    for m in _TOK.finditer(text):
        t = m.group(0).strip(".'").removesuffix("'s")
        before, after = text[max(0, m.start() - 1):m.start()], text[m.end():m.end() + 1]
        fig = before == "$" or after == "%"
        if t.lower().split("-", 1)[0] in LICENSES and "-" in t:      # apache-2.0, cc-by-4.0: a license, not a version
            toks.append(("", False))
        elif _ver(t.lower()) or fig:
            toks.append((t, fig))
        else:                                    # 501B-total -> 501B, total; mixture-of-experts -> mixture, experts
            parts = [p for p in re.split(r"[-_/+]", t) if p]
            toks += [(p, False) for p in parts]
    words, names, vers, nums = set(), set(), set(), set()
    low = text.lower()
    status = {label for label, pat in STATUS if re.search(pat, low)}
    figs = set()
    after_license = False                        # only "license", "licence", "version" may sit between a license and its version
    prev = ""
    for t, fig in toks:
        tl = t.lower()
        lic, after_license = after_license, False
        if tl in LICENSES:
            after_license = True
        elif lic and tl in ("license", "licence", "version"):
            after_license = True
        if not tl:
            prev = ""; continue
        v = None if fig else _ver(tl)
        # a bare integer right after a product name is a version (Opus 4, GPT-5, Rocket 3); not after a month or stop word
        if not v and not fig and tl.isdigit() and prev[:1].isupper() and prev.isalpha() and prev.lower() not in STOP:
            v = tl
        prev = t
        if v:
            if not lic:
                vers.add(v); words.add(v)
            continue
        if tl in STOP or tl in BOILERPLATE:
            continue
        if any(ch.isdigit() for ch in tl):
            words.add(tl); nums.add(tl)
            if fig:
                figs.add(tl)
            continue
        if len(tl) < 2 or tl in WEAK:
            continue
        if t[0].isupper():                       # sentence-initial words count too: products often start a sentence
            names.add(tl); words.add(tl)
        elif len(tl) >= 4:
            words.add(tl)
    exact = " ".join(text.casefold().split())    # only case and whitespace: <, >, +, -, unicode all count
    return words, names, vers, nums, status, figs, None, exact   # slot 6 unused (kept for tuple shape)


# status changes, matched by stem and mapped to one label so "deprecated" and "deprecates" agree
STATUS = [("ga", r"\bga\b|generally available"), ("deprecate", r"\bdeprecat\w*"), ("remove", r"\bremov\w*"),
          ("retire", r"\bretir\w*"), ("sunset", r"\bsunset\w*"), ("discontinue", r"\bdiscontinu\w*"),
          ("shutdown", r"\bshut\s*down\w*|\bshuts\s+down|\bshutting\s+down"), ("waitlist", r"\bwaitlist\w*"),
          ("open-source", r"\bopen[- ]sourc\w*"), ("price", r"\bpric\w*|\bcheaper\b"), ("stable", r"\bstable\b"),
          ("free", r"\bfree\b"), ("rollout", r"\brolls?\s+out|\brolling\s+out|\ball\s+users\b"),
          ("delay", r"\bdelay\w*|\bpostpon\w*|\bpaus\w*"), ("cancel", r"\bcancel\w*"), ("revert", r"\brevert\w*|\broll(?:s|ed)?\s+back"),
          ("incident", r"\boutage\w*|\bleak\w*|\bbreach\w*|\bvulnerab\w*")]


def conflict(a, b, numbers=True):
    """Both texts name versions and the sets differ (two releases of one tool are two stories), or, with numbers=True,
    each carries a number the other lacks (a new price or limit for the same product is news)."""
    def differ(x, y):                            # each side has a value the other lacks ($8 -> $4 next to a shared 100)
        return bool(x - y and y - x)
    # versions: any difference when both name one (Rocket 3.0 that mentions 2.0 is still a new release)
    if (a[4] ^ b[4]):                            # a status change on one side only (now GA, deprecated): news
        return True
    # money and percent figures are always compared ($8 -> $4, or "now $4, down from $8", is news even on the same page)
    return bool(a[2] and b[2] and a[2] != b[2]) or bool(a[5] and b[5] and a[5] != b[5]) or (numbers and differ(a[3], b[3]))


def text_repeat(a, b):
    """Shared words if new claim a tells the same story as earlier b by wording, else None. Needs a shared name and no
    conflict, then either (1) nearly all of a's own words were already in b (a follow-up adding a fact isn't covered),
    or (2) a and b agree on 2+ names-or-figures, at least one a figure like 501B or 300, over half the smaller set."""
    wa, na, wb, nb = a[0], a[1], b[0], b[1]
    if conflict(a, b) or not (na & nb) or not wa or not wb:
        return None
    shared = wa & wb
    if len(shared) >= 3 and len(shared) / len(wa) >= COVER:
        return shared
    figures = {w for w in shared if any(ch.isdigit() for ch in w) and w not in a[2]}
    if figures and len((na & nb) | figures) >= 3 and len(shared) / min(len(wa), len(wb)) >= OVERLAP:
        return shared
    return None


def _load_history(hp, lo, date, log):
    out = []
    p = Path(hp)
    if not p.exists():
        log(f"repeat check: CP_STORY_HISTORY file not found ({p.name}); history not used")
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        log(f"repeat check: history unreadable ({type(e).__name__}); history not used")
        return out
    bad = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            h = json.loads(line)
        except ValueError:
            bad += 1; continue
        if not isinstance(h, dict) or not isinstance(h.get("date"), str):
            bad += 1; continue
        try:
            h["date"] = dt.date.fromisoformat(h["date"]).isoformat()
        except ValueError:
            bad += 1; continue
        if not (lo <= h["date"] < date):
            continue
        urls = h.get("urls") or []
        urls = [urls] if isinstance(urls, str) else [u for u in urls if isinstance(u, str)] if isinstance(urls, list) else []
        out.append({"ref": f"history:{h['date']}#{i + 1}", "date": h["date"], "text": str(h.get("text") or h.get("headline") or ""), "urls": urls})
    if bad:
        log(f"repeat check: skipped {bad} malformed history lines")
    return out


def earlier_stories(ledger, date, history_path=None, log=lambda m: None):
    lo = (dt.date.fromisoformat(date) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    out = []
    for c in (ledger or {}).get("claims", []) or []:
        d = c.get("date") if isinstance(c, dict) else None
        if isinstance(d, str) and lo <= d < date:
            ev = c.get("evidence") or []
            urls = [e.get("url") for e in ev if isinstance(e, dict)]
            out.append({"ref": c.get("id", "?"), "date": d, "text": c.get("claim") or "", "urls": [u for u in urls if isinstance(u, str)]})
    n_ledger = len(out)
    hp = history_path or os.environ.get("CP_STORY_HISTORY")
    if hp:
        out += _load_history(hp, lo, date, log)
    for s in out:
        s["_a"] = analyze(s["text"]); s["_urls"] = {norm_url(u) for u in s["urls"] if specific(u)}
    log(f"repeat check: {n_ledger} ledger stories and {len(out) - n_ledger} history stories in the last {WINDOW_DAYS} days")
    return out


def _evidence_urls(claim):
    ev = claim.get("evidence") or []
    return {norm_url(e.get("url")) for e in ev if isinstance(e, dict) and specific(e.get("url"))}


def novel(a, b):
    """True unless the two claims have the same text apart from letter case and whitespace.
    Only an exact duplicate is a sure repeat; every heuristic match is flagged for review, never dropped."""
    return a[7] != b[7]


def find_repeat(claim, stories):
    """Return (ref, why, sure) for the first earlier story this claim matches, else None.
    sure=True: the claim's text equals the earlier story's (case and whitespace aside): safe to drop.
    sure=False: a possible repeat that also says something new; the caller keeps it and flags it for review.
    Only evidence URLs are compared: item_url is where we spotted it (one newsletter covers many stories)."""
    urls = _evidence_urls(claim)
    a = analyze(claim.get("claim"))
    maybe = None
    for s in stories:
        same = urls & s["_urls"] if urls and urls <= s["_urls"] else set()   # a new source URL means possible new news
        # a shared URL also needs some text agreement: a shared name, or 2+ shared words (one page can hold several stories)
        # plain numbers aren't compared here (one story page quotes many figures); money and percent figures are
        if same and not conflict(a, s["_a"], numbers=False) and ((a[1] & s["_a"][1]) or len(a[0] & s["_a"][0]) >= 2):
            why = f"same evidence URL as {s['ref']} ({sorted(same)[0]})"
            if not novel(a, s["_a"]):
                return s["ref"], why, True
            maybe = maybe or (s["ref"], why, False)
    for s in stories:
        shared = text_repeat(a, s["_a"])
        if shared:
            why = f"same story as {s['ref']} (shared: {', '.join(sorted(shared)[:6])})"
            if not novel(a, s["_a"]):
                return s["ref"], why, True
            maybe = maybe or (s["ref"], why, False)
    return maybe


class SameDay:
    """Same-day duplicate from a different page: two claims citing the same specific evidence URL with no version
    conflict and some text agreement (a shared name or 2+ shared words), but spotted on different pages (item_url), are one story credited twice. Claims spotted
    on the same page may share a URL (several facts from one release note), so those are kept."""
    def __init__(self):
        self.seen = []          # (urls, page, analysis)

    def check(self, claim):
        """Return (why, sure) like find_repeat, else None."""
        page = claim.get("item_url")
        urls = _evidence_urls(claim)
        a = analyze(claim.get("claim"))
        maybe = None
        for u2, p2, a2 in self.seen:
            same = urls & u2 if urls and urls <= u2 else set()
            if same and p2 != page and not conflict(a, a2, numbers=False) and ((a[1] & a2[1]) or len(a[0] & a2[0]) >= 2):
                why = f"same evidence URL as an earlier item today ({sorted(same)[0]})"
                if not novel(a, a2):
                    return why, True
                maybe = maybe or (why, False)
        return maybe

    def add(self, claim):
        self.seen.append((_evidence_urls(claim), claim.get("item_url"), analyze(claim.get("claim"))))

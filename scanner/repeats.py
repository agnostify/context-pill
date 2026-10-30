"""Cross-day repeat-story check: catches the same story published again under a different URL or wording.
Runs in scanner.run's publication checks, before review. A claim repeats an earlier story when, with no version
conflict, either
  (a) they cite the same specific evidence URL (index, docs, package and repo-home pages don't count) and share a name or
      2+ distinctive words, or
  (b) they share a name (a product, model or company word that isn't boilerplate) and their distinctive words
      overlap strongly, or
  (c) they share a strong identifier: a CVE or GHSA id, or a package name with the same version ("LiteLLM 1.107.0", or
      pypi.org/pypi/<name>/<ver>, github.com/<owner>/<repo>/releases/tag/<ver> in the evidence). Wording, evidence URLs and
      other numbers don't matter; two versions of one package are two stories.
Rules (a) and (b) skip a story when the two texts name different versions (or one side alone has a status change);
rule (c) doesn't check that (its identifier already holds the version). Rule (d) looks past a version mismatch (a tool's
new release re-announcing a model it already added, a launch post re-covered with new figures). It needs the same lead
model when both name one (same name words, variant words, sizes and versions: "Kimi K3.1 Pro" is not "Kimi K3.1",
"Gemini 4 Argon flash" is not "Gemini 4 Argon pro", "Llama 5 8B" is not "Llama 5 70B", "GPT-6.1 Terra" is not
"GPT-6.1 Terra Mini" or "o5-mini"), no one-sided status change, and one of the following; (d1)-(d3) also need the same subject (the whole first named thing, generic words like "Researchers"
aside: "T3 Code" is not "Claude Code", "Codex CLI" is not "Gemini CLI"; when the subject is only vendor names, as in
"Google says Gemini CLI ...", the next named product must agree instead)
  (d1) the same named model launched in both ("Moonshot released/announced Kimi K3.1 Pro"),
  (d2) the same named model and 3+ shared stems that make up half or more of the new claim's stemmed plain and name words,
  (d3) a shared run of 3+ stemmed words carrying the event ("threads settle once", "not optimized for Gemini 4"),
       with no money- or percent-figure conflict, and no version conflict unless the run itself holds the shared version, or
  (d4) 3+ shared money or percent figures ($100, $200, $500), a shared name and 2+ shared plain words ("API credits",
       "subscribers").
Rule (d) only ever flags; it never drops.
Earlier stories: ledger claims from the last WINDOW_DAYS days, plus an optional JSONL history named by the
CP_STORY_HISTORY env var (one {"date", "text"|"headline", "urls"} object per line; bad lines are skipped; a history whose
newest entry is more than STALE_DAYS before the run date is logged as stale).
It is a heuristic, so it only drops an exact duplicate (same text apart from letter case and whitespace). A match that
also says something new is kept and flagged for the review step. Every drop and flag is logged with the claim and the
matched story so a wrong drop can be restored."""
import json, os, re, datetime as dt
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode

WINDOW_DAYS = 14
OVERLAP = 0.5          # shared words / the smaller word set (figure rule)
COVER = 0.8            # share of the new claim's own words already in the earlier story
MODEL_COVER = 0.5      # rule (d2): share of the new claim's stemmed words in the earlier story, given the same named model
STALE_DAYS = 3         # warn when the history's newest entry is older than this
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
_CVE = re.compile(r"CVE-(\d{4})-(\d{4,})((?:/\d{4,})*)", re.I)         # CVE-2026-109618/109619 means both
_GHSA = re.compile(r"GHSA(?:-[a-z0-9]{4}){3}", re.I)
_PKGVER = re.compile(r"(?<![\w.\-])([A-Za-z][A-Za-z0-9_\-]{1,40})(?:(?:\s+(?:release|build|package))?,?(?:\s+version)?\s+v?|==|@)(\d+\.\d+\.\d+(?:[.\-][a-z0-9]+)*)(?![\w.]*\d)", re.I)
NOT_PKG = {"python", "node", "nodejs", "java", "ios", "macos", "windows", "android", "ubuntu", "linux", "go", "rust", "php", "ruby", "swift",
           "version", "versions", "v", "release", "build", "and", "or", "to", "from", "in", "of", "at"}


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


def text_ids(text):
    """Strong identifiers in free text: CVE ids, GHSA ids, and name+version pairs with a 3-part version (litellm@1.107.0)."""
    ids = set()
    for m in _CVE.finditer(text):
        for n in [m.group(2)] + [x for x in m.group(3).split("/") if x]:
            ids.add(f"cve-{m.group(1)}-{n}")
    ids |= {m.group(0).lower() for m in _GHSA.finditer(text)}
    for m in _PKGVER.finditer(text):
        name = m.group(1).lower()
        if name not in NOT_PKG and name not in STOP and name not in BOILERPLATE and name not in LICENSES:
            ids.add(f"{name}@{m.group(2).lower()}")
    return ids


def url_ids(u):
    """Strong identifiers in a URL: CVE and GHSA ids anywhere, pypi.org/pypi/<name>/<ver>, github.com/<o>/<repo>/releases/tag/<ver>."""
    if not isinstance(u, str):
        return set()
    ids = {m.group(0).lower() for m in _GHSA.finditer(u)} | {f"cve-{m.group(1)}-{m.group(2)}" for m in _CVE.finditer(u)}
    n = norm_url(u)
    host, _, path = n.partition("/")
    segs = [x for x in path.split("?", 1)[0].split("/") if x]
    name = ver = None
    if host == "pypi.org" and len(segs) >= 3 and segs[0] in ("pypi", "project"):
        name, ver = segs[1], segs[2]
    elif host == "github.com" and len(segs) >= 5 and [x.lower() for x in segs[2:4]] == ["releases", "tag"]:
        name, ver = segs[1], segs[4]
    elif host == "npmjs.com" and len(segs) >= 4 and segs[0] == "package" and segs[-2] == "v":
        name, ver = segs[1], segs[-1]
    v = _ver(ver.lower()) if ver else None
    if name and v and v.count(".") >= 2:
        ids.add(f"{name.lower()}@{v}")
    return ids


def analyze(text):
    """Return (words, names, versions, numbers, status labels, money/percent figures, unused, exact text, ids) for a claim text.
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
        if t[0].isupper() and tl not in GENERIC_SUBJ:   # sentence-initial words count too (products often start a sentence),
            names.add(tl); words.add(tl)                # but not generic ones ("Researchers")
        elif len(tl) >= 4:
            words.add(tl)
    exact = " ".join(text.casefold().split())    # only case and whitespace: <, >, +, -, unicode all count
    return words, names, vers, nums, status, figs, None, exact, text_ids(text)   # slot 6 unused (kept for tuple shape)


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


# rule (d): event-level features (stems, named models, subject) for flags that look past a version mismatch
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
          "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
LAUNCH = {"released", "releases", "launched", "launches", "announced", "announces", "introduced", "introduces", "unveiled",
          "unveils", "debuted", "debuts", "open-sourced", "shipped", "ships"}
# plain words that carry no event; kept in the token sequence (they don't break a run) but never count as event words
FUNC = {"their", "them", "they", "there", "every", "each", "once", "other", "some", "only", "just", "still", "over", "after",
        "before", "while", "where", "such", "both", "same", "like", "does", "into", "your", "you", "also", "very", "much"}
_PARTVER = re.compile(r"v?\d+(?:\.\d+)+|[a-z]\d+(?:\.\d+)+|v\d+")    # 6.1, v0.66.0, k3.1, v2 (not t3)
# size and speed variants: part of a model's identity (Kimi K3.1 Pro and Kimi K3.1 are two models)
VARIANT = {"pro", "max", "plus", "mini", "flash", "lite", "nano", "ultra", "turbo", "fast", "highspeed", "instant", "thinking",
           "air", "large", "medium", "small", "micro", "instruct", "coder"}
_SIZE = re.compile(r"\d+(?:\.\d+)?[bt]|\d+x\d+(?:\.\d+)?b")    # parameter sizes: 8B, 1.5B, 3T, 8x7B (Llama 5 8B is not Llama 5 70B)
# lowercase model ids: o5, o5-mini, r2-lite. Other bare letter+digit tokens (r2, p95, h100, s3) are too ambiguous to count
_LCMODEL = re.compile(r"(o)(\d+)((?:-[a-z]+)*)|([a-z]{1,3})(\d+(?:\.\d+)*)((?:-[a-z]+)+)")
# vendor names: a subject made only of these ("Google says ...") names no product
VENDOR = {"anthropic", "openai", "google", "deepmind", "meta", "microsoft", "nvidia", "mistral", "moonshot", "alibaba", "deepseek",
          "xai", "amazon", "aws", "apple", "ibm", "cohere", "baidu", "tencent", "bytedance", "zhipu", "minimax", "perplexity"}
_PERIOD = re.compile(r"[qh][1-4]|fy\d*")                        # Q3 2026, H2, FY27: a date, not a model
# generic first words that name no one thing, so they never make a subject
GENERIC_SUBJ = {"researchers", "researcher", "users", "developers", "engineers", "scientists", "experts", "analysts", "people",
                "hackers", "attackers", "companies", "startups", "teams", "study", "paper", "report", "survey", "security",
                "several", "many", "some", "two", "three", "one", "new", "most"}


def _stem(w):
    """Light stemmer: settles/settled/settle -> settl, threads -> thread, optimized -> optimiz."""
    if not w.isalpha() or len(w) < 4:
        return w
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            w = w[:-len(suf)]; break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def features(text):
    """Event features for rule (d):
    seq      stemmed tokens in order as (word, kind), kind lc (plain event word), name, weak, ver, num or stop; STOP words
             other than not/no are dropped so "not optimized for Gemini 4" and "is not optimized for Gemini 4" line up
    names    capitalized words, vendor names included
    models   named-model mentions in order, (alpha words, versions, launched): a run of adjacent capitalized or versioned
             tokens with a version and a non-vendor word ("GPT-6.1 Terra", "Kimi K3.1 Pro", lowercase "o5-mini"), extended
             by a following size or lowercase variant ("Llama 5 8B", "Gemini 4 Argon flash"); alpha words keep variants
             (pro, mini, flash) and sizes and drop vendor and other boilerplate words; launched when a launch verb comes
             right before it ("Moonshot released Kimi K3.1 Pro")
    subject  alpha words of the first named thing in the first four tokens ("T3 Code", "Gemini CLI", "Moonshot"), minus
             generic words ("Researchers"); when those are all vendor names ("Google says Gemini CLI ..."), the alpha
             words of the next named thing that isn't, minus vendor names (empty if there is none)
    stems    stemmed lc and name words"""
    text = text if isinstance(text, str) else str(text or "")
    toks = [(m.group(0).strip(".'").removesuffix("'s"), m.start(), m.end()) for m in _TOK.finditer(text)]
    toks = [t for t in toks if t[0]]
    seq, names, models, subj, subj_done, named = [], set(), [], [], False, []
    run, launched, prev_end, prev_cap = [], False, None, False

    def close():
        alpha = frozenset(p for p, k in run if k == "a" and (p in VARIANT or p not in BOILERPLATE) and p not in WEAK)
        vers = frozenset(p for p, k in run if k == "v")
        named.append(frozenset(p for p, k in run if k == "a" and p not in BOILERPLATE) - VENDOR)
        if alpha and vers:
            models.append((alpha, vers, launched))
    for i, (t, s, e) in enumerate(toks):
        fig = text[max(0, s - 1):s] == "$" or text[e:e + 1] == "%"
        tl = t.lower()
        lcm = None if fig or t[:1].isupper() else _LCMODEL.fullmatch(tl)
        if lcm:
            parts = [p for p in re.split(r"-", tl) if p]                # o5-mini -> o5, mini
        elif fig:
            parts = [t]
        elif _ver(tl) and "-" in t and not _PARTVER.fullmatch(tl):
            parts = [p for p in t.split("-") if p]           # GPT-6.1 -> GPT, 6.1; kimi-k3.1-pro -> kimi, k3.1, pro
        else:
            parts = [p for p in re.split(r"[-_/+]", t) if p]
        adjacent = prev_end is not None and text[prev_end:s].strip() == ""
        cap = t[:1].isupper() and tl not in STOP and tl not in MONTHS and not _PERIOD.fullmatch(tl)
        bare_int = tl.isdigit() and not fig and prev_cap and adjacent and not re.fullmatch(r"(?:19|20)\d\d", tl)   # Gemini 4 (not Nov 4, Q3 2026)
        tail = bool(run) and adjacent and (tl in VARIANT or bool(_SIZE.fullmatch(tl)))   # Llama 5 8B, Gemini 4 Argon flash
        mention = not fig and (cap or bare_int or tail or bool(lcm) or any(_PARTVER.fullmatch(p.lower()) for p in parts))
        if run and not (mention and adjacent):
            close(); run = []
        if mention and not run:
            j = i - 1
            while j > 0 and toks[j][0].lower() in ("its", "the", "a", "an", "new"):
                j -= 1
            launched = j >= 0 and toks[j][0].lower() in LAUNCH
        if not subj_done:
            if mention and (not subj or adjacent):
                subj += [p.lower() for p in parts if not _PARTVER.fullmatch(p.lower()) and p.lower() not in BOILERPLATE]
            elif subj or i >= 3:
                subj_done = True
        for p in parts:
            pl = p.lower()
            m5 = lcm and re.fullmatch(r"([a-z]{1,3})(\d+(?:\.\d+)*)", pl)
            if m5:                                                   # o5 -> name o, version 5
                seq.append((pl, "num"))
                run += [(m5.group(1), "a"), (m5.group(2), "v")]
                continue
            if fig:
                seq.append((pl, "num"))
            elif _PARTVER.fullmatch(pl) or bare_int:
                v = pl[1:] if pl[:1] == "v" else pl
                seq.append((v, "ver"))
                if mention:
                    run.append((v, "v"))
            elif any(ch.isdigit() for ch in pl):
                seq.append((pl, "num"))
                if mention:
                    run.append((pl, "a"))                    # T3, 3T: part of a name, not a version
            elif pl in STOP:
                if pl in ("not", "no"):
                    seq.append((pl, "stop"))
            else:
                kind = "weak" if pl in WEAK or pl in BOILERPLATE else "name" if p[:1].isupper() else \
                       "lc" if len(pl) >= 4 and pl not in FUNC else "stop"
                seq.append((_stem(pl), kind))
                if p[:1].isupper():
                    names.add(pl)
                if mention:
                    run.append((pl, "a"))
        prev_end, prev_cap = e, cap
    if run:
        close()
    subj = frozenset(subj) - GENERIC_SUBJ
    if subj and subj <= VENDOR:                  # "Google says Gemini CLI ...": the product is the subject
        subj = next((n for n in named if n), frozenset())
    return {"seq": seq, "names": names, "models": models, "subject": subj,
            "stems": {w for w, k in seq if k in ("lc", "name")}}


def _same_model(m, n):
    """Same name words, variants and versions (GPT-6.1 Terra is neither GPT-6.1 Terra Mini nor GPT-6.1 Sol)."""
    return m[0] == n[0] and m[1] == n[1]


def _shared_run(x, y, versions):
    """A shared run of 3+ seq tokens with 2+ plain event words, or 4+ tokens with one event word and a version in
    `versions` (the versions both claims name). Returns the run's words, else None."""
    sx, sy = x["seq"], y["seq"]
    best = None
    dp = [0] * (len(sy) + 1)
    for i in range(1, len(sx) + 1):
        nd = [0] * (len(sy) + 1)
        for j in range(1, len(sy) + 1):
            if sx[i - 1][0] == sy[j - 1][0]:
                nd[j] = dp[j - 1] + 1
                n = nd[j]
                if n >= 3:
                    run = sx[i - n:i]
                    lc = sum(k == "lc" for _, k in run)
                    ver = any(k == "ver" and w in versions for w, k in run)
                    if (lc >= 2 or (lc >= 1 and ver and n >= 4)) and (best is None or n > len(best[0])):
                        best = ([w for w, _ in run], ver)
        dp = nd
    return best


def event_repeat(a, x, b, y):
    """Why-text if new claim (analysis a, features x) repeats earlier story (b, y) at the event level (rule (d)), else None."""
    if a[4] ^ b[4]:                              # a status change on one side only is news
        return None
    mx, my = x["models"], y["models"]
    if mx and my and not _same_model(mx[0], my[0]):
        return None                              # different lead models (GPT-6.1 Terra vs GPT-6.1 Sol)
    same_subject = bool(x["subject"]) and x["subject"] == y["subject"]   # the whole subject: Codex CLI is not Gemini CLI
    shared_model = next((m for m in mx for n in my if _same_model(m, n)), None)
    label = lambda m: " ".join(sorted(m[0]) + sorted(m[1]))
    if same_subject and shared_model:
        if any(m[2] for m in mx if _same_model(m, shared_model)) and any(n[2] for n in my if _same_model(n, shared_model)):
            return f"same model launch ({label(shared_model)})"
        sx = x["stems"]
        if sx:
            shared = sx & y["stems"]
            if len(shared) >= 3 and len(shared) / len(sx) >= MODEL_COVER:
                return f"same model ({label(shared_model)}; shared: {', '.join(sorted(shared)[:6])})"
    if same_subject and not (a[5] and b[5] and a[5] != b[5]):
        run = _shared_run(x, y, a[2] & b[2])
        if run and (not (a[2] and b[2] and a[2] != b[2]) or run[1]):
            return f"shared phrase '{' '.join(run[0])}'"
    figs = a[5] & b[5]
    if len(figs) >= 3 and x["names"] & y["names"]:
        plain = {w for w, k in x["seq"] if k == "lc"} & {w for w, k in y["seq"] if k == "lc"}
        if len(plain) >= 2:
            return f"same figures ({', '.join(sorted(figs)[:4])}; shared: {', '.join(sorted(plain)[:4])})"
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
    bad, newest = 0, None
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
        if h["date"] < date:
            newest = max(newest or h["date"], h["date"])
        if not (lo <= h["date"] < date):
            continue
        urls = h.get("urls") or []
        urls = [urls] if isinstance(urls, str) else [u for u in urls if isinstance(u, str)] if isinstance(urls, list) else []
        out.append({"ref": f"history:{h['date']}#{i + 1}", "date": h["date"], "text": str(h.get("text") or h.get("headline") or ""), "urls": urls})
    if bad:
        log(f"repeat check: skipped {bad} malformed history lines")
    stale = (dt.date.fromisoformat(date) - dt.timedelta(days=STALE_DAYS)).isoformat()
    if newest is None:
        log(f"repeat check: WARNING: history is stale (no entries before {date}); recent stories are only in the ledger")
    elif newest < stale:
        log(f"repeat check: WARNING: history is stale (newest entry {newest} before {date}, over {STALE_DAYS} days old); "
            "recent stories are only in the ledger")
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
        s["_ids"] = s["_a"][8].union(*(url_ids(u) for u in s["urls"]))
        s["_x"] = features(s["text"])
    log(f"repeat check: {n_ledger} ledger stories and {len(out) - n_ledger} history stories in the last {WINDOW_DAYS} days")
    return out


def _evidence_urls(claim):
    ev = claim.get("evidence") or []
    return {norm_url(e.get("url")) for e in ev if isinstance(e, dict) and specific(e.get("url"))}


def _claim_ids(claim, a):
    ev = claim.get("evidence") or []
    return a[8].union(*(url_ids(e.get("url")) for e in ev if isinstance(e, dict)))


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
    ids = _claim_ids(claim, a)
    for s in stories:                            # shared CVE/GHSA id or package+version: same story whatever the wording (flag, keep)
        shared = ids & s["_ids"]
        if shared:
            why = f"same identifier as {s['ref']} ({', '.join(sorted(shared)[:4])})"
            if not novel(a, s["_a"]):
                return s["ref"], why, True
            maybe = maybe or (s["ref"], why, False)
    if maybe:
        return maybe
    x = features(claim.get("claim"))
    for s in stories:                            # rule (d): same event despite a version mismatch (flag only, never drop)
        why = event_repeat(a, x, s["_a"], s.get("_x") or features(s["text"]))
        if why:
            return s["ref"], f"possible same story as {s['ref']}: {why}", False
    return None


class SameDay:
    """Same-day duplicate from a different page: two claims citing the same specific evidence URL with no version
    conflict and some text agreement (a shared name or 2+ shared words), but spotted on different pages (item_url), are one story credited twice. Claims spotted
    on the same page may share a URL (several facts from one release note), so those are kept."""
    def __init__(self):
        self.seen = []          # (urls, page, analysis, ids)

    def check(self, claim):
        """Return (why, sure) like find_repeat, else None."""
        page = claim.get("item_url")
        urls = _evidence_urls(claim)
        a = analyze(claim.get("claim"))
        maybe = None
        ids = _claim_ids(claim, a)
        for u2, p2, a2, i2 in self.seen:
            if ids & i2 and p2 != page:
                why = f"same identifier as an earlier item today ({', '.join(sorted(ids & i2)[:4])})"
                if not novel(a, a2):
                    return why, True
                maybe = maybe or (why, False)
            same = urls & u2 if urls and urls <= u2 else set()
            if same and p2 != page and not conflict(a, a2, numbers=False) and ((a[1] & a2[1]) or len(a[0] & a2[0]) >= 2):
                why = f"same evidence URL as an earlier item today ({sorted(same)[0]})"
                if not novel(a, a2):
                    return why, True
                maybe = maybe or (why, False)
        return maybe

    def add(self, claim):
        a = analyze(claim.get("claim"))
        self.seen.append((_evidence_urls(claim), claim.get("item_url"), a, _claim_ids(claim, a)))

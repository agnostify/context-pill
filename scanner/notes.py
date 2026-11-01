"""Deterministic public notes for resolutions and audits. No model prose is ever published: the resolution note is
"Outcome: <outcome>. Source: <url>" (or "Sources: <url>, <url>" when the outcome rests on several pages), the audit note
"Verdict: <verdict>. Source(s): ..." (or "Verdict: not run."), built only from the validated outcome/verdict and up to MAX_URLS
validated URLs. The model's note, quote and reasoning go to WORK raw/<date>/*.reasoning.json (private, never pushed); split()
separates them from the structured fields everything downstream reads."""
import json, re, urllib.parse
from . import gate, util

OUTCOMES = ("good", "docs-verified", "partial", "bad", "inconclusive")
VERDICTS = ("pass", "fail", "inconclusive")
MAX_URLS = 3
FLAG = "all_facts_on_cited_pages"             # the resolver's own statement that every part of the claim is on a cited page


def usable_url(url):
    """An http(s) URL with a host, no whitespace or control characters, not on a blocked host, and nothing the publish
    gate would flag (X link or post ID, secret, private pattern), so a note built from it always passes the gate. Never raises."""
    try:
        if not isinstance(url, str) or not url or any(ch.isspace() or not ch.isprintable() for ch in url):
            return False
        p = urllib.parse.urlsplit(url)
        p.port                                          # raises on a malformed port
        return (p.scheme in ("http", "https") and bool(p.hostname) and not util.blocked(url)
                and not gate.scan_text("note", url, gate.private_patterns()[0]))
    except Exception:
        return False


def pick_url(source_url, evidence=()):
    """The model's source_url if usable, else the claim's first usable evidence URL, else None."""
    if usable_url(source_url):
        return source_url
    try:
        return next((e["url"] for e in evidence or () if isinstance(e, dict) and usable_url(e.get("url"))), None)
    except Exception:
        return None


def usable_urls_all(urls):
    """The usable URLs among urls (a list, or one URL), deduped in order, not truncated. Never raises."""
    out = []
    for u in (urls if isinstance(urls, (list, tuple)) else [urls]):
        if usable_url(u) and u not in out:
            out.append(u)
    return out


def usable_urls(urls):
    """The usable URLs among urls (a list, or one URL), deduped in order, at most MAX_URLS. Never raises."""
    return usable_urls_all(urls)[:MAX_URLS]


def pick_urls(urls, evidence=()):
    """The model's usable source URLs, else [the claim's first usable evidence URL], else []."""
    return usable_urls(urls) or [u for u in [pick_url(None, evidence)] if u]


def _note(label, value, urls):
    us = usable_urls(urls)                      # the builder re-checks every URL itself
    if not us:
        return f"{label}: {value}."
    return f"{label}: {value}. {'Source' if len(us) == 1 else 'Sources'}: {', '.join(us)}"


def resolution_note(outcome, urls):
    return _note("Outcome", outcome, urls)


def audit_note(verdict, urls):
    """'Verdict: not run.' (no source) when the verdict is missing or not one of VERDICTS."""
    return _note("Verdict", verdict, urls) if verdict in VERDICTS else "Verdict: not run."


_FIGURE = re.compile(r"\$?\d+(?:[.,]\d+)*(?:%|x\b|K\b|M\b)?")


def multi_fact(claim):
    """Rough test for a claim that states more than one checkable fact: 2+ distinct figures, or parts joined by ' and ' or ';'."""
    c = claim if isinstance(claim, str) else ""
    return len(set(_FIGURE.findall(c))) >= 2 or " and " in c or ";" in c


def downgrade(outcome, claim, urls, all_facts):
    """(outcome, why): a good/docs-verified outcome becomes partial unless the resolver said every part of the claim is on a
    page it cited (all_facts is True); why says which rule applied (None if unchanged)."""
    if outcome not in ("good", "docs-verified") or all_facts is True:
        return outcome, None
    if multi_fact(claim) and len(usable_urls(urls)) <= 1:
        return "partial", f"multi-fact claim with one cited page and {FLAG} not true"
    return "partial", f"{FLAG} not true (part of the claim not re-checked on a cited page, or inferred)"


def split(res, field, allowed, keep=()):
    """(clean, reasoning) from a model result {id: {field, source_urls, source_url, all_facts_on_cited_pages, note, ...}}.
    clean: entries whose field is in allowed, holding only field, source_urls (the usable ones from source_urls and the older
    single source_url, deduped, at most MAX_URLS), all_facts_on_cited_pages if it is a boolean, and the keys in keep;
    the flag is set False when a cited URL was invalid or cut by MAX_URLS, or no usable URL remains (so downgrade() makes
    good/docs-verified partial); an invalid entry is left out (not judged). reasoning: what was stripped (the model's text, invalid values), per id."""
    clean, why = {}, {}
    for k, r in (res.items() if isinstance(res, dict) else ()):
        if not isinstance(k, str):
            continue
        if not isinstance(r, dict):
            why[k] = {"invalid": repr(r)[:500]}
            continue
        v = r.get(field)
        ok = isinstance(v, str) and v in allowed
        if ok:
            given = r.get("source_urls") if isinstance(r.get("source_urls"), list) else []
            cited = given + [r.get("source_url")]
            us = usable_urls(cited)
            clean[k] = {field: v} | ({"source_urls": us} if us else {}) \
                | ({FLAG: r[FLAG]} if isinstance(r.get(FLAG), bool) else {}) | {x: r[x] for x in keep if x in r}
            # the flag vouches for the pages the resolver cited: if any of them is not in the note, it no longer holds
            lost = ("source_urls" in r and not isinstance(r["source_urls"], list)) \
                or any(u is not None and not usable_url(u) for u in cited) or len(usable_urls_all(cited)) > MAX_URLS
            if clean[k].get(FLAG) is True and (lost or not us):
                clean[k][FLAG] = False
                why.setdefault(k, {})["flag_cleared"] = f"{FLAG} was true but " + (
                    "no validated citation remains" if not us else "a cited URL was invalid or past the first " + str(MAX_URLS))
        def kept(x):                                # a URL field is stripped unless every value made it into source_urls
            if x not in ("source_url", "source_urls"):
                return x in clean[k] or x in keep
            vals = r[x] if x == "source_urls" and isinstance(r[x], list) else [r[x]]
            return all(u in clean[k].get("source_urls", ()) for u in vals)
        rest = {x: y for x, y in r.items() if not ok or not kept(x) or (x == FLAG and clean[k].get(FLAG) != y)}
        if rest:
            why[k] = why.get(k, {}) | rest
    return clean, why


def save_reasoning(path, why):
    """Merge into WORK raw/<date>/<stage>.reasoning.json (private); a rerun's fresh output replaces an id's entry."""
    if not why:
        return
    try:
        old = json.loads(path.read_text()) if path.exists() else {}
        old = old if isinstance(old, dict) else {}
    except Exception:
        old = {}
    path.write_text(json.dumps(old | why, indent=1, ensure_ascii=False))

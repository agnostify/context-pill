"""Deterministic public notes for resolutions and audits. No model prose is ever published: the resolution note is
"Outcome: <outcome>. Source: <url>", the audit note "Verdict: <verdict>. Source: <url>" (or "Verdict: not run."), built only
from the validated outcome/verdict and one URL. The model's note, quote and reasoning go to WORK raw/<date>/*.reasoning.json
(private, never pushed); split() separates them from the structured fields everything downstream reads."""
import json, urllib.parse
from . import gate, util

OUTCOMES = ("good", "docs-verified", "partial", "bad", "inconclusive")
VERDICTS = ("pass", "fail", "inconclusive")


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


def _note(label, value, url):
    return f"{label}: {value}. Source: {url}" if usable_url(url) else f"{label}: {value}."


def resolution_note(outcome, url):
    return _note("Outcome", outcome, url)


def audit_note(verdict, url):
    """'Verdict: not run.' (no source) when the verdict is missing or not one of VERDICTS."""
    return _note("Verdict", verdict, url) if verdict in VERDICTS else "Verdict: not run."


def split(res, field, allowed, keep=()):
    """(clean, reasoning) from a model result {id: {field, source_url, note, quote, reasoning, ...}}.
    clean: entries whose field is in allowed, holding only field, a usable source_url and the keys in keep;
    an invalid entry is left out (not judged). reasoning: what was stripped (the model's text, invalid values), per id."""
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
            clean[k] = {field: v} | ({"source_url": r["source_url"]} if usable_url(r.get("source_url")) else {}) \
                | {x: r[x] for x in keep if x in r}
        rest = {x: y for x, y in r.items() if not ok or (x not in clean[k] and x not in keep)}
        if rest:
            why[k] = rest
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

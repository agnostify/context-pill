"""Tests for the deterministic resolution/audit notes (scanner/notes.py): the published note is built from the outcome/verdict and
validated URLs only (every page the resolver used, at most 3), good/docs-verified is downgraded to partial unless every fact is on a
cited page, and model text (note, quote, reasoning) never reaches a public file. No network.
Run: python -m scanner.tests.test_notes"""
import json, shutil, sys, tempfile
from pathlib import Path
from unittest import mock
from scanner import notes as N

POST = "https://www.anthropic.com/research/designing-efficient-quantum-circuits"
ARXIV = "https://arxiv.org/abs/2610.22367"
METR = "https://www.anthropic.com/news/an-update-on-independent-evaluators"
PRICING = "https://claude.com/pricing"
OPENAI = "https://openai.com/news/rss.xml"
XURL = "https://" + "x" + ".com/a"          # a blocked host, built at runtime so the public-file gate sees no X link

# the four model notes that got the Oct 30-31 drops held (2026-10-16-77948875, 2026-10-16-c5ba7374, 2026-10-17-f0e1a13c resolutions;
# 2026-10-30-da3cbe61 audit), with the quotes the model gave
REAL = [
    ("outcome", "good", ARXIV, "Post: eight tasks, closed loop; arXiv: seven of eight; 12% more qubits, 17x fewer gates.",
     "significant reductions in resource cost on seven of the eight tasks"),
    ("outcome", "good", METR, "Six METR staff, near risk-team access; no veto; Anthropic decides redaction categories.",
     ["We've onboarded six METR employees", "Anthropic cannot veto publication"]),
    ("outcome", "good", PRICING, "Haiku 5.5 tiers match pricing page; Haiku 4.5 $1/$5 not shown there.",
     "Claude Haiku 5.5 Input $0.10 / MTok Output $0.50 / MTok"),
    ("verdict", "pass", OPENAI, "OpenAI RSS item dated 29 Oct 2026 confirms five core values: sound judgment, human agency, "
     "faithfulness to intent. Article page returned 403.",
     "aim for three things: sound judgment, respect for human agency, and faithfulness to the intent of its instructions"),
]


def model_words(*texts):
    """Distinctive words of the model's text (skips short words that a URL or label could contain)."""
    out = set()
    for t in texts:
        for q in (t if isinstance(t, list) else [t]):
            out |= {w.strip(".,;:$/").lower() for w in q.split() if len(w.strip(".,;:$/")) > 4}
    return out - {"source", "outcome", "verdict"}


def test_real_cases_published_note_is_deterministic():
    for field, value, url, note, quote in REAL:
        res = {"id1": {field: value, "note": note, "source_url": url, "quote": quote, "reasoning": "checked " + note}}
        clean, why = N.split(res, field, N.OUTCOMES if field == "outcome" else N.VERDICTS)
        assert clean == {"id1": {field: value, "source_urls": [url]}}, clean
        assert why["id1"]["note"] == note and why["id1"]["quote"] == quote, why
        r = clean["id1"]
        pub = (N.resolution_note(value, N.pick_urls(r.get("source_urls"), [{"url": POST}])) if field == "outcome"
               else N.audit_note(value, N.pick_urls(r.get("source_urls"), [{"url": POST}])))
        label = "Outcome" if field == "outcome" else "Verdict"
        assert pub == f"{label}: {value}. Source: {url}", pub
        leaked = {w for w in model_words(note, quote) if w in pub.lower().replace(url.lower(), "")}
        assert not leaked, (pub, leaked)


def test_url_choice():
    ev = [{"url": XURL}, {"url": "ftp://example.com/a"}, None, {"url": 5}, {"url": POST}]
    assert N.pick_url(ARXIV, ev) == ARXIV
    for bad in ("http://[", "http://[::1", "https://", "javascript:alert(1)", "example.com/a", XURL, "https://a.com/x y",
                "https://a.com/\nx", "http://a.com:99999999/", 42, None, ["https://a.com"], {"u": 1}, ""):
        assert not N.usable_url(bad), bad
        assert N.pick_url(bad, ev) == POST, bad
        assert N.pick_url(bad, []) is None and N.pick_url(bad, None) is None and N.pick_url(bad, "junk") is None
    assert N.resolution_note("good", N.pick_url("http://[", [])) == "Outcome: good."
    assert N.audit_note("fail", N.pick_url("http://[", [{"url": "http://["}, {"url": METR}])) == f"Verdict: fail. Source: {METR}"
    assert N.resolution_note("bad", "http://[") == "Outcome: bad."          # the builder re-checks the URL itself
    clean, _ = N.split({"a": {"outcome": "good", "source_url": "http://["}}, "outcome", N.OUTCOMES)
    assert clean == {"a": {"outcome": "good"}}, clean


def test_url_list():
    ev = [{"url": XURL}, {"url": POST}]
    for bad in ("http://[", XURL, "https://a.com/x y", "javascript:alert(1)", 42, None, {"u": 1}):
        assert N.pick_urls([bad, ARXIV, bad], ev) == [ARXIV], bad              # blocked/malformed URLs dropped from the list
        assert N.pick_urls([bad], ev) == [POST] and N.pick_urls(bad, ev) == [POST] and N.pick_urls([bad], []) == []
    assert N.pick_urls([POST, ARXIV, POST], ev) == [POST, ARXIV]              # deduped, in order
    assert N.pick_urls([[ARXIV]], ev) == [POST]                                 # a nested list is not a URL
    five = [f"https://example.com/{i}" for i in range(5)]
    assert N.pick_urls(five, ev) == five[:3]                                   # capped at 3
    assert N.resolution_note("good", five) == "Outcome: good. Sources: " + ", ".join(five[:3])
    assert N.resolution_note("good", [POST]) == f"Outcome: good. Source: {POST}"
    assert N.resolution_note("good", ["http://[", XURL]) == "Outcome: good."
    assert N.audit_note("pass", [POST, ARXIV]) == f"Verdict: pass. Sources: {POST}, {ARXIV}"
    # split: source_urls and the older single source_url are merged; dropped URLs and a non-boolean flag go to reasoning
    clean, why = N.split({"a": {"outcome": "good", "source_urls": [POST, XURL, POST], "source_url": ARXIV, N.FLAG: "true"}},
                         "outcome", N.OUTCOMES)
    assert clean == {"a": {"outcome": "good", "source_urls": [POST, ARXIV]}}, clean
    assert why == {"a": {"source_urls": [POST, XURL, POST], N.FLAG: "true"}}, why
    clean, why = N.split({"a": {"outcome": "good", "source_urls": [POST], N.FLAG: False}}, "outcome", N.OUTCOMES)
    assert clean == {"a": {"outcome": "good", "source_urls": [POST], N.FLAG: False}} and not why, (clean, why)
    clean, why = N.split({"a": {"outcome": "good", "source_urls": "junk"}}, "outcome", N.OUTCOMES)
    assert clean == {"a": {"outcome": "good"}} and why == {"a": {"source_urls": "junk"}}, (clean, why)


CHIRP, COLOPHON = "https://tools.simonwillison.net/chirp-pong", "https://tools.simonwillison.net/colophon"
# the three resolutions that got the Nov 1 drop held (the claims as published)
HAIKU = ("Claude Haiku 5.5 API pricing is $0.10 input and $0.50 output per million tokens for prompts up to 100K tokens, "
         "and $0.50/$2.50 above that. Haiku 4.5 costs $1/$5.")
QUANTUM = ("Anthropic says Claude, in a closed loop with an automated evaluator, designed better quantum circuits on eight tasks. "
           "The arXiv abstract reports significant reductions on seven of the eight. Anthropic's post says one point-addition design "
           "uses 12% more qubits but 17x fewer gates.")
PONG = ("Simon Willison built Chirp Pong with Claude Code and Claude Opus 5.5 Extra. It is a static web pong game for two phones "
        "that sends game state as audio chirps between the phones' speakers and microphones.")


def resolved(claim, r, evidence=()):
    """What run.py publishes for one resolver result: (outcome, note, downgrade reason)."""
    clean, _ = N.split({"c": r}, "outcome", N.OUTCOMES)
    c = clean["c"]; urls = N.pick_urls(c.get("source_urls"), evidence)
    out, why = N.downgrade(c["outcome"], claim, urls, c.get(N.FLAG))
    return out, N.resolution_note(out, urls), why


def test_held_nov1_cases():
    assert all(N.multi_fact(c) for c in (HAIKU, QUANTUM, PONG)) and not N.multi_fact("Claude Code 2.1 is out.")
    # case 1: single page, multi-fact claim, scored good without the flag -> partial
    for flag in ({}, {N.FLAG: False}, {N.FLAG: "yes"}):
        out, note, why = resolved(HAIKU, {"outcome": "good", "source_urls": [PRICING]} | flag)
        assert (out, note) == ("partial", f"Outcome: partial. Source: {PRICING}") and "multi-fact" in why, (out, note, why)
    # case 2: both pages cited and every fact on them -> good, note lists both
    out, note, why = resolved(QUANTUM, {"outcome": "good", "source_urls": [POST, ARXIV], N.FLAG: True})
    assert (out, note, why) == ("good", f"Outcome: good. Sources: {POST}, {ARXIV}", None), (out, note)
    # case 3: Claude Code inferred from a link (flag false) -> partial, even with two pages cited
    for o in ("good", "docs-verified"):
        out, note, why = resolved(PONG, {"outcome": o, "source_urls": [CHIRP, COLOPHON], N.FLAG: False})
        assert (out, note) == ("partial", f"Outcome: partial. Sources: {CHIRP}, {COLOPHON}") and "inferred" in why, (out, note, why)
    # partial/bad/inconclusive are never changed; good with the flag on a one-fact claim stays good
    for o in ("partial", "bad", "inconclusive"):
        assert resolved(HAIKU, {"outcome": o, "source_urls": [PRICING]})[0] == o
    assert resolved("Claude Code 2.1 is out.", {"outcome": "good", "source_urls": [POST], N.FLAG: True})[:2] == ("good", f"Outcome: good. Source: {POST}")


def test_dropped_citations_clear_flag():
    one = "Claude Code 2.1 is out."
    # no validated citation: the evidence fallback URL is shown but the flag no longer holds -> partial
    for r in ({}, {"source_url": "http://["}, {"source_urls": [XURL]}, {"source_urls": "junk"}):
        for o in ("good", "docs-verified"):
            out, note, why = resolved(one, {"outcome": o, N.FLAG: True} | r, [{"url": POST}])
            assert (out, note) == ("partial", f"Outcome: partial. Source: {POST}") and N.FLAG in why, (r, out, note, why)
    # an invalid URL next to a valid one, or four pages cut to three -> partial
    for urls in ([POST, XURL], [POST, "http://["], [POST, ARXIV, METR, PRICING]):
        out, note, why = resolved(QUANTUM, {"outcome": "good", "source_urls": urls, N.FLAG: True})
        assert out == "partial" and note.startswith("Outcome: partial. Source"), (urls, out, note)
    clean, why = N.split({"a": {"outcome": "good", "source_urls": [POST, ARXIV, METR, PRICING], N.FLAG: True}}, "outcome", N.OUTCOMES)
    assert clean["a"][N.FLAG] is False and clean["a"]["source_urls"] == [POST, ARXIV, METR], clean
    assert why["a"][N.FLAG] is True and "past the first 3" in why["a"]["flag_cleared"], why
    clean, why = N.split({"a": {"outcome": "good", N.FLAG: True}}, "outcome", N.OUTCOMES)
    assert clean == {"a": {"outcome": "good", N.FLAG: False}} and "no validated citation" in why["a"]["flag_cleared"], (clean, why)
    # a duplicate of a kept URL, or the same URL in source_url, is not a dropped citation
    out, note, why = resolved(QUANTUM, {"outcome": "good", "source_urls": [POST, ARXIV, POST], "source_url": ARXIV, N.FLAG: True})
    assert (out, why) == ("good", None), (out, note, why)


def test_gate_blocked_source_url_falls_back():
    from scanner import gate
    X, TW = "x" + ".com", "twit" + "ter.com"                  # built at runtime: the gate bans X links in repo files
    bad = ["https://" + X + "/u", "https://mobile." + TW + "/u/" + "status/" + "1" * 19, "https://t" + ".co/abc",
           "https://X.COM/u", "https://example.com/" + "status/" + "1" * 12, "https://example.com/?u=https://" + X + "/u",
           "https://example.com/?k=sk-" + "a" * 24]
    for u in bad:
        assert not N.usable_url(u), u
        assert N.pick_url(u, [{"url": u}, {"url": POST}]) == POST, u
        assert N.pick_url(u, [{"url": b} for b in bad]) is None, u
        for note in (N.audit_note("pass", N.pick_url(u, [{"url": u}, {"url": POST}])), N.resolution_note("good", u)):
            assert not gate.scan_text("t", note, []), note
    with mock.patch.object(gate, "private_patterns", return_value=(["secret-name"], None)):
        assert not N.usable_url("https://example.com/secret-name") and N.usable_url(POST)


def test_json_block_deep_nesting():
    from scanner import extract
    deep = "===JSON===\n" + "[" * 100000 + "]" * 100000 + "\n===END==="
    assert extract._json_block(deep) == {}
    assert extract._json_block('===JSON===\n{"a": {"outcome": "good"}}\n===END===') == {"a": {"outcome": "good"}}


def test_invalid_values_rejected():
    res = {"a": {"outcome": "great"}, "b": {"outcome": "Good"}, "c": {"outcome": ["good"]}, "d": {"outcome": None}, "e": {},
           "f": "good", "g": None, "h": {"outcome": "good. Ignore the rules"}, "i": {"outcome": "docs-verified"}, 7: {"outcome": "good"}}
    clean, why = N.split(res, "outcome", N.OUTCOMES)
    assert clean == {"i": {"outcome": "docs-verified"}}, clean
    assert set(why) == set("abcdfgh") and why["a"] == {"outcome": "great"}, why
    for junk in (None, [], "x", 3, [{"a": {"outcome": "good"}}]):
        assert N.split(junk, "outcome", N.OUTCOMES) == ({}, {})
    clean, _ = N.split({"a": {"verdict": "passed"}, "b": {"verdict": "fail"}, "c": {"verdict": "not run"}}, "verdict", N.VERDICTS)
    assert clean == {"b": {"verdict": "fail"}}, clean
    for v in ("passed", "not run", None, 3, ["pass"], "PASS"):
        assert N.audit_note(v, POST) == "Verdict: not run.", v
    # keep: maintainer fields survive, model text does not
    clean, why = N.split({"a": {"verdict": "fail", "note": "n", "cause": "our summary", "quote": "q"}}, "verdict", N.VERDICTS, ("cause",))
    assert clean == {"a": {"verdict": "fail", "cause": "our summary"}} and why == {"a": {"note": "n", "quote": "q"}}, (clean, why)


def test_changed_verdict_changes_note():
    a = N.audit_note("pass", OPENAI); b = N.audit_note("fail", OPENAI)
    assert a == f"Verdict: pass. Source: {OPENAI}" and b == f"Verdict: fail. Source: {OPENAI}"
    assert N.audit_note("pass", OPENAI) == a                                # a pure function: same input, same note


def test_save_reasoning():
    p = Path(tempfile.mkdtemp()) / "audit.reasoning.json"
    N.save_reasoning(p, {}); assert not p.exists()
    N.save_reasoning(p, {"a": {"note": "1"}, "b": {"note": "2"}}); N.save_reasoning(p, {"a": {"note": "3"}})
    assert json.loads(p.read_text()) == {"a": {"note": "3"}, "b": {"note": "2"}}
    p.write_text("not json"); N.save_reasoning(p, {"c": {"note": "4"}})
    assert json.loads(p.read_text()) == {"c": {"note": "4"}}
    shutil.rmtree(p.parent)


def test_no_model_text_in_public_outputs():
    """run.py end to end on a fixture copy (as in test_tools.py): a due claim is resolved and a T1 item audited, with model notes,
    quotes and reasoning carrying a marker; the marker is in WORK raw/<date>/*.reasoning.json and in no public file."""
    import yaml
    src = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__", "daily", "weekly", "ledger", "latest.*"))
    (tmp / "ledger").mkdir(); (tmp / "weekly").mkdir()
    from scanner import run as runmod, extract, util
    work = tmp.parent / "work"; D = "2026-11-02"; raw = work / "raw" / D; raw.mkdir(parents=True)
    reg = {"sources": [{"id": "fx", "name": "Fixture source", "home_url": "https://example.com", "enabled": True, "seed": True,
                        "register": {k: "x" for k in ("route", "basis", "republish", "limit", "stop_rule", "opt_out")},
                        "feeds": [{"type": "rss", "url": "https://example.com/feed"}], "trust": {"tier": "T1-seed"}}]}
    (tmp / "sources.yaml").write_text(yaml.safe_dump(reg)); (tmp / "CHANGELOG-sources.md").write_text("# Source changes\n")
    ev = [{"url": "https://example.com/a", "kind": "primary"}]
    due = {"id": "2026-10-19-0000aaaa", "date": "2026-10-19", "category": "release", "claim": "old claim", "evidence": ev,
           "verification": {"status": "verified"}, "scorable": True, "state": "pending", "recheck_on": "2026-11-02", "source_id": "fx"}
    bad = dict(due, id="2026-10-19-0000bbbb", claim="other old claim")
    multi = dict(due, id="2026-10-19-0000cccc", claim=HAIKU)            # scored good on one page, no flag: downgraded
    both = dict(due, id="2026-10-19-0000dddd", claim=QUANTUM)           # two pages, every fact on them: stays good
    lost = dict(due, id="2026-10-19-0000eeee", claim=QUANTUM)           # flag set but one cited URL dropped: partial
    (tmp / "ledger" / "claims.json").write_text(json.dumps({"started": "2026-10-16", "claims": [due, bad, multi, both, lost], "audits": []}))
    claim = {"source_id": "fx", "item_url": "https://example.com/b", "claim": "new claim", "category": "release", "why_it_matters": "x",
             "try_this": "do x", "evidence": ev, "verification": {"status": "verified", "method": "m"}}
    (raw / "claims.raw.json").write_text(json.dumps([claim])); (raw / "items.json").write_text("[]")
    M = "ZQXMARKER"
    def model(field, value):
        return lambda xs, *a: {x["id"]: {field: value, "note": f"{M} note five core values", "quote": f"{M} quote",
                                         "reasoning": f"{M} reasoning", "source_url": "http://["} for x in xs}
    def resolve(xs, *a):
        out = model("outcome", "partial")([x for x in xs if x["id"] == due["id"]])
        asked = {x["id"] for x in xs}                       # a rerun only asks about claims still pending
        return {k: v for k, v in (out | {bad["id"]: {"outcome": f"{M}-not-an-outcome", "note": f"{M} n"},
                      multi["id"]: {"outcome": "good", "source_urls": [PRICING], "reasoning": f"{M} r"},
                      both["id"]: {"outcome": "good", "source_urls": [POST, ARXIV], N.FLAG: True},
                      lost["id"]: {"outcome": "good", "source_urls": [POST, ARXIV, XURL], N.FLAG: True}}).items() if k in asked}
    runmod.WORK = work; runmod.REPO = tmp
    for verdict in ("pass", "fail"):                                       # a verdict change on rerun publishes a new note
        if verdict == "fail":
            ar = json.loads((raw / "audit.result.json").read_text())
            assert all(M not in json.dumps(r) for r in ar.values()), ar        # result file holds only structured fields
            for r in ar.values(): r["verdict"] = "fail"
            (raw / "audit.result.json").write_text(json.dumps(ar))
        with mock.patch.object(sys, "argv", ["run", "--date", D, "--skip-extract"]), \
             mock.patch.object(util, "url_alive", return_value=(True, 200)), \
             mock.patch.object(extract, "resolve", side_effect=resolve), \
             mock.patch.object(extract, "audit", side_effect=model("verdict", verdict)):
            runmod.main()
        led = json.loads((tmp / "ledger" / "claims.json").read_text())
        byid = {c["id"]: c for c in led["claims"]}
        assert byid[due["id"]]["outcome"] == "partial"
        assert byid[due["id"]]["resolution_note"] == "Outcome: partial. Source: https://example.com/a", byid[due["id"]]
        assert byid[bad["id"]]["state"] == "pending" and "outcome" not in byid[bad["id"]], byid[bad["id"]]    # invalid: not judged
        assert (byid[multi["id"]]["outcome"], byid[multi["id"]]["resolution_note"]) == ("partial", f"Outcome: partial. Source: {PRICING}")
        assert (byid[both["id"]]["outcome"], byid[both["id"]]["resolution_note"]) == ("good", f"Outcome: good. Sources: {POST}, {ARXIV}")
        assert (byid[lost["id"]]["outcome"], byid[lost["id"]]["resolution_note"]) == ("partial", f"Outcome: partial. Sources: {POST}, {ARXIV}")
        rr = json.loads((raw / "resolve.reasoning.json").read_text())
        assert rr[lost["id"]]["downgraded"]["to"] == "partial" and "flag_cleared" in rr[lost["id"]], rr
        assert rr[multi["id"]]["downgraded"]["from"] == "good" and rr[multi["id"]]["downgraded"]["to"] == "partial", rr
        assert rr[multi["id"]]["reasoning"] == f"{M} r" and "downgraded" not in rr.get(both["id"], {}), rr
        new = [c for c in led["claims"] if c["date"] == D]
        assert len(new) == 1 and new[0]["verification"]["audit_verdict"] == verdict, new
        assert new[0]["verification"]["audit_note"] == f"Verdict: {verdict}. Source: https://example.com/a", new[0]["verification"]
        assert f"audit of {new[0]['id']} (Fixture source): {verdict} (Verdict: {verdict}. Source: https://example.com/a)" in \
            (tmp / "CHANGELOG-sources.md").read_text()
        pub = [p for p in tmp.rglob("*") if p.is_file() and "scanner" not in p.relative_to(tmp).parts]
        names = {str(p.relative_to(tmp)) for p in pub}
        assert {"ledger/claims.json", "latest.md", "latest.json", "CHANGELOG-sources.md"} <= names and any(n.startswith("daily/") for n in names), names
        leaks = [str(p) for p in pub if M in p.read_text(errors="ignore")]
        assert not leaks, leaks
        assert M in (raw / "resolve.reasoning.json").read_text() and M in (raw / "audit.reasoning.json").read_text()
    assert json.loads((raw / "resolve.reasoning.json").read_text())[bad["id"]]["outcome"] == f"{M}-not-an-outcome"
    shutil.rmtree(tmp.parent)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
    print("test_notes: all passed")

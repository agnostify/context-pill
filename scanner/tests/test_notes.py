"""Tests for the deterministic resolution/audit notes (scanner/notes.py): the published note is built from the outcome/verdict and
one URL only, and model text (note, quote, reasoning) never reaches a public file. No network.
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
        assert clean == {"id1": {field: value, "source_url": url}}, clean
        assert why["id1"]["note"] == note and why["id1"]["quote"] == quote, why
        r = clean["id1"]
        pub = (N.resolution_note(value, N.pick_url(r.get("source_url"), [{"url": POST}])) if field == "outcome"
               else N.audit_note(value, N.pick_url(r.get("source_url"), [{"url": POST}])))
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
    (tmp / "ledger" / "claims.json").write_text(json.dumps({"started": "2026-10-16", "claims": [due, bad], "audits": []}))
    claim = {"source_id": "fx", "item_url": "https://example.com/b", "claim": "new claim", "category": "release", "why_it_matters": "x",
             "try_this": "do x", "evidence": ev, "verification": {"status": "verified", "method": "m"}}
    (raw / "claims.raw.json").write_text(json.dumps([claim])); (raw / "items.json").write_text("[]")
    M = "ZQXMARKER"
    def model(field, value):
        return lambda xs, *a: {x["id"]: {field: value, "note": f"{M} note five core values", "quote": f"{M} quote",
                                         "reasoning": f"{M} reasoning", "source_url": "http://["} for x in xs}
    def resolve(xs, *a):
        out = model("outcome", "partial")([x for x in xs if x["id"] == due["id"]])
        return out | {bad["id"]: {"outcome": f"{M}-not-an-outcome", "note": f"{M} n"}}
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

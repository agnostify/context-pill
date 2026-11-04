"""Tests for the auditor-set failure cause (METHOD.md section 3): a T1 audit that fails because our summary misstated the source is
logged and counts toward tightening, but demotes the source only when the source's own statement is false. The auditor's
failure_cause sets cause "our summary" only when it is exactly that string on a fail; anything else demotes as before, and a
maintainer's cause always wins. No network, no model.
Run: python -m scanner.tests.test_audit_cause"""
import json, shutil, sys, tempfile
from pathlib import Path
from unittest import mock
from scanner import notes as N

FREEWAY = "Simon Willison's Freeway takes 46 to 65 minutes per prompt"     # the source gives no time for Freeway
MALFORMED = ("Our Summary", "OUR SUMMARY", " our summary", "summary", "ours", "", 1, 0, True, None, ["our summary"],
             {"cause": "our summary"})


def test_audit_cause_values():
    assert N.AUDIT_CAUSES == ("our summary", "source", "unclear")
    assert N.audit_cause({"verdict": "fail", "failure_cause": "our summary"}) == "our summary"
    for c in ("source", "unclear") + MALFORMED:
        assert N.audit_cause({"verdict": "fail", "failure_cause": c}) is None, c
    assert N.audit_cause({"verdict": "fail"}) is None
    for v in ("pass", "inconclusive", "Fail", None):                    # only a fail can be our summary's
        assert N.audit_cause({"verdict": v, "failure_cause": "our summary"}) is None, v
    for r in (None, "fail", ["our summary"], 3):
        assert N.audit_cause(r) is None


def test_with_audit_cause():
    clean = {"verdict": "fail", "source_urls": ["https://simonwillison.net/a"]}
    out, bad = N.with_audit_cause(clean, clean | {"failure_cause": "our summary"})
    assert out == clean | {"cause": "our summary", "cause_by": "auditor"} and bad is None, out
    assert "cause" not in clean                                          # the input entry is not changed
    for c in ("source", "unclear"):
        out, bad = N.with_audit_cause(clean, clean | {"failure_cause": c})
        assert out == clean and bad is None, (c, out)
    for c in MALFORMED:
        out, bad = N.with_audit_cause(clean, clean | {"failure_cause": c})
        assert out == clean, (c, out)
        assert bad == (None if c is None else c), (c, bad)               # the raw invalid value, for the reasoning file
    out, bad = N.with_audit_cause(clean, clean)
    assert out == clean and bad is None
    # the maintainer's cause wins over the auditor's, either way
    for c in ("source", "our summary", "unclear", "Our Summary"):
        out, _ = N.with_audit_cause(clean, clean | {"failure_cause": c}, {"cause": "our summary", "superseded_by": "x"})
        assert out == clean | {"cause": "our summary", "superseded_by": "x"}, (c, out)
    out, _ = N.with_audit_cause(clean, clean | {"failure_cause": "our summary"}, {"cause": "source misquoted"})
    assert out == clean | {"cause": "source misquoted"}, out
    out, _ = N.with_audit_cause(clean, clean | {"failure_cause": "our summary"}, {"superseded_by": "x"})
    assert out == clean | {"superseded_by": "x", "cause": "our summary", "cause_by": "auditor"}, out
    pas = {"verdict": "pass"}
    assert N.with_audit_cause(pas, pas | {"failure_cause": "our summary"}) == (pas, None)


def fixture(prior_fail=False):
    """A copy of the repo with one T1-seed source (Simon Willison) and one verified claim from it in today's raw claims."""
    import yaml
    src = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__", "daily", "weekly", "ledger", "latest.*"))
    (tmp / "ledger").mkdir(); (tmp / "weekly").mkdir()
    work = tmp.parent / "work"; D = "2026-11-02"; raw = work / "raw" / D; raw.mkdir(parents=True)
    reg = {"sources": [{"id": "simonw", "name": "Simon Willison", "home_url": "https://simonwillison.net", "enabled": True, "seed": True,
                        "register": {k: "x" for k in ("route", "basis", "republish", "limit", "stop_rule", "opt_out")},
                        "feeds": [{"type": "rss", "url": "https://simonwillison.net/atom/everything/"}], "trust": {"tier": "T1-seed"}}]}
    (tmp / "sources.yaml").write_text(yaml.safe_dump(reg)); (tmp / "CHANGELOG-sources.md").write_text("# Source changes\n")
    audits = [{"date": "2026-10-30", "item_id": "2026-10-30-0000aaaa", "source_id": "other", "tier": "T1", "verdict": "fail"}] if prior_fail else []
    (tmp / "ledger" / "claims.json").write_text(json.dumps({"started": "2026-10-16", "claims": [], "audits": audits}))
    ev = [{"url": "https://simonwillison.net/2026/Nov/1/freeway/", "kind": "primary"}]
    claim = {"source_id": "simonw", "item_url": ev[0]["url"], "claim": FREEWAY, "category": "tool", "why_it_matters": "x",
             "try_this": "do x", "evidence": ev, "verification": {"status": "verified", "method": "m"}}
    (raw / "claims.raw.json").write_text(json.dumps([claim])); (raw / "items.json").write_text("[]")
    return tmp, work, raw, D


def run(tmp, work, D, audit):
    from scanner import run as runmod, extract, util
    runmod.WORK = work; runmod.REPO = tmp
    am = mock.Mock(side_effect=audit)
    with mock.patch.object(sys, "argv", ["run", "--date", D, "--skip-extract"]), \
         mock.patch.object(util, "url_alive", return_value=(True, 200)), \
         mock.patch.object(extract, "resolve", return_value={}), \
         mock.patch.object(extract, "audit", am):
        runmod.main()
    import yaml
    reg = yaml.safe_load((tmp / "sources.yaml").read_text())
    led = json.loads((tmp / "ledger" / "claims.json").read_text())
    return am, reg, led, (tmp / "CHANGELOG-sources.md").read_text()


def auditor(failure_cause=None, verdict="fail", omit=False):
    def f(xs, *a):
        return {x["id"]: {"verdict": verdict, "source_urls": ["https://simonwillison.net/2026/Nov/1/freeway/"],
                          "reasoning": "the post gives no time for Freeway"} | ({} if omit else {"failure_cause": failure_cause}) for x in xs}
    return f


def test_simonw_our_summary_no_demotion():
    """Nov 2 case: the summary said 46 to 65 minutes per prompt; the post gives no time for Freeway. The auditor says the fail
    was our summary's: the cause is recorded, the source stays T1-seed, and the failure still counts toward tightening."""
    tmp, work, raw, D = fixture(prior_fail=True)
    am, reg, led, cl = run(tmp, work, D, auditor("our summary"))
    assert am.call_count == 1
    s = reg["sources"][0]
    assert s["trust"]["tier"] == "T1-seed" and not s.get("audit_demoted"), s
    assert "Simon Willison tier" not in cl, cl
    rec = [x for x in led["audits"] if x["date"] == D]
    assert len(rec) == 1 and rec[0]["verdict"] == "fail" and rec[0]["cause"] == "our summary" and rec[0]["tier"] == "T1-seed", rec
    assert "cause_by" not in rec[0] and "failure_cause" not in rec[0], rec
    iid = rec[0]["item_id"]
    assert f"audit of {iid} (Simon Willison): fail (Verdict: fail. Source: https://simonwillison.net/2026/Nov/1/freeway/); cause: our summary" in cl, cl
    assert reg["tightened"].get("T1") == D, reg["tightened"]                    # 2 fails in the last 20 T1 audits
    ar = json.loads((raw / "audit.result.json").read_text())
    assert ar[iid]["cause"] == "our summary" and ar[iid]["cause_by"] == "auditor" and "failure_cause" not in ar[iid], ar
    item = next(c for c in led["claims"] if c["id"] == iid)
    assert item["verification"]["status"] == "unverified" and item["verification"]["audit_verdict"] == "fail", item  # the item is still withheld
    assert "withheld: source demoted" not in item["verification"]["method"], item
    # a rerun with --skip-extract reuses the stored cause (no new audit) and still does not demote
    am, reg, led, cl = run(tmp, work, D, auditor("source"))
    assert am.call_count == 0
    assert reg["sources"][0]["trust"]["tier"] == "T1-seed" and not reg["sources"][0].get("audit_demoted"), reg["sources"][0]
    assert [x.get("cause") for x in led["audits"] if x["date"] == D] == ["our summary"], led["audits"]
    assert json.loads((raw / "audit.result.json").read_text())[iid]["cause_by"] == "auditor"
    shutil.rmtree(tmp.parent)


def test_source_falsehood_demotes():
    tmp, work, raw, D = fixture()
    am, reg, led, cl = run(tmp, work, D, auditor("source"))
    s = reg["sources"][0]
    assert s.get("audit_demoted") and not s["trust"]["tier"].startswith("T1") and s["audit_demoted_from"] == "T1-seed", s
    assert f"Simon Willison tier T1-seed -> {s['trust']['tier']}" in cl, cl
    rec = [x for x in led["audits"] if x["date"] == D]
    assert len(rec) == 1 and rec[0]["verdict"] == "fail" and "cause" not in rec[0], rec
    assert "; cause:" not in cl
    assert "cause" not in json.loads((raw / "audit.result.json").read_text())[rec[0]["item_id"]]
    shutil.rmtree(tmp.parent)


def test_malformed_or_missing_cause_demotes():
    for c, omit in [(x, False) for x in ("unclear", "Our Summary", 1, ["our summary"], "summary", None)] + [(None, True)]:
        tmp, work, raw, D = fixture()
        am, reg, led, cl = run(tmp, work, D, auditor(c, omit=omit))
        s = reg["sources"][0]
        assert s.get("audit_demoted") and not s["trust"]["tier"].startswith("T1"), (c, s)
        rec = [x for x in led["audits"] if x["date"] == D]
        assert "cause" not in rec[0] and "; cause:" not in cl, (c, rec)
        iid = rec[0]["item_id"]
        assert "cause" not in json.loads((raw / "audit.result.json").read_text())[iid], c
        why = json.loads((raw / "audit.reasoning.json").read_text())[iid]
        if not omit:
            assert why["failure_cause"] == c, (c, why)                      # the raw value stays private
        if c not in (None, "unclear"):
            assert why["failure_cause_invalid"] == c, (c, why)
        shutil.rmtree(tmp.parent)


def test_pass_with_our_summary_sets_no_cause():
    tmp, work, raw, D = fixture()
    am, reg, led, cl = run(tmp, work, D, auditor("our summary", verdict="pass"))
    assert reg["sources"][0]["trust"]["tier"] == "T1-seed"
    rec = [x for x in led["audits"] if x["date"] == D]
    assert rec[0]["verdict"] == "pass" and "cause" not in rec[0], rec
    assert "cause" not in json.loads((raw / "audit.result.json").read_text())[rec[0]["item_id"]]
    shutil.rmtree(tmp.parent)


def test_maintainer_cause_wins():
    """An entry the maintainer marked (cause "our summary") with no valid verdict is re-audited; the auditor now says "source",
    but the maintainer's cause stands: no demotion, and no auditor mark on it."""
    tmp, work, raw, D = fixture()
    am, reg, led, cl = run(tmp, work, D, auditor("our summary", verdict="inconclusive"))
    iid = [x for x in led["audits"] if x["date"] == D][0]["item_id"]
    (raw / "audit.result.json").write_text(json.dumps({iid: {"verdict": "", "cause": "our summary", "superseded_by": "2026-11-02-ffffffff"}}))
    am, reg, led, cl = run(tmp, work, D, auditor("source"))
    assert am.call_count == 1
    s = reg["sources"][0]
    assert s["trust"]["tier"] == "T1-seed" and not s.get("audit_demoted"), s
    ar = json.loads((raw / "audit.result.json").read_text())[iid]
    assert ar["verdict"] == "fail" and ar["cause"] == "our summary" and ar["superseded_by"] == "2026-11-02-ffffffff", ar
    assert "cause_by" not in ar, ar
    rec = [x for x in led["audits"] if x["date"] == D]
    assert rec[0]["cause"] == "our summary", rec
    assert f"audit of {iid} (Simon Willison): fail (Verdict: fail. Source: https://simonwillison.net/2026/Nov/1/freeway/); cause: our summary" in cl
    # an auditor-set cause in a held entry is not the maintainer's: the re-audit decides it again
    (raw / "audit.result.json").write_text(json.dumps({iid: {"verdict": "", "cause": "our summary", "cause_by": "auditor"}}))
    am, reg, led, cl = run(tmp, work, D, auditor("source"))
    assert am.call_count == 1 and reg["sources"][0].get("audit_demoted"), reg["sources"][0]
    assert "cause" not in json.loads((raw / "audit.result.json").read_text())[iid]
    shutil.rmtree(tmp.parent)


def test_auditor_set_cause():
    assert N.auditor_set_cause({"cause": "our summary", "cause_by": "auditor"})
    for r in ({"cause": "source", "cause_by": "auditor"}, {"cause": "Our Summary", "cause_by": "auditor"}, {"cause": "our summary"},
              {"cause": "our summary", "cause_by": "maintainer"}, {"cause_by": "auditor"}, {}, None, "our summary"):
        assert not N.auditor_set_cause(r), r


def test_maintainer_changed_auditor_cause_wins():
    """The maintainer changes an auto-set cause to "source" and clears the verdict but leaves cause_by "auditor": the cause is
    the maintainer's now, so a re-audit saying "our summary" must not replace it, and the source is demoted."""
    tmp, work, raw, D = fixture()
    am, reg, led, cl = run(tmp, work, D, auditor("our summary"))
    iid = [x for x in led["audits"] if x["date"] == D][0]["item_id"]
    ar = json.loads((raw / "audit.result.json").read_text())
    assert ar[iid]["cause"] == "our summary" and ar[iid]["cause_by"] == "auditor", ar
    ar[iid] |= {"verdict": "", "cause": "source"}
    (raw / "audit.result.json").write_text(json.dumps(ar))
    am, reg, led, cl = run(tmp, work, D, auditor("our summary"))
    assert am.call_count == 1
    s = reg["sources"][0]
    assert s.get("audit_demoted") and not s["trust"]["tier"].startswith("T1"), s
    ar = json.loads((raw / "audit.result.json").read_text())[iid]
    assert ar["verdict"] == "fail" and ar["cause"] == "source" and "cause_by" not in ar, ar
    rec = [x for x in led["audits"] if x["date"] == D]
    assert rec[0]["cause"] == "source", rec
    shutil.rmtree(tmp.parent)


def test_removed_item_keeps_cause_and_does_not_demote():
    """A failed audit whose item has left the drop (the first loop in run.py) logs the cause and does not demote either."""
    tmp, work, raw, D = fixture()
    am, reg, led, cl = run(tmp, work, D, auditor("our summary"))
    iid = [x for x in led["audits"] if x["date"] == D][0]["item_id"]
    (raw / "claims.raw.json").write_text("[]")                                   # the claim was withdrawn
    am, reg, led, cl = run(tmp, work, D, auditor("source"))
    assert am.call_count == 0
    assert reg["sources"][0]["trust"]["tier"] == "T1-seed" and not reg["sources"][0].get("audit_demoted")
    rec = [x for x in led["audits"] if x["date"] == D]
    assert len(rec) == 1 and rec[0]["item_id"] == iid and rec[0]["cause"] == "our summary", rec
    shutil.rmtree(tmp.parent)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
    print("test_audit_cause: all passed")

"""Tests for remove.py, correct.py, gate item rules and robots stop handling, on a self-contained fixture
(never on the live drop, so an empty or held day can't break the daily run). Run: python -m scanner.tests.test_tools"""
import json, shutil, subprocess, sys, tempfile, os, datetime as dt
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2]
tmp = Path(tempfile.mkdtemp()) / "repo"
shutil.copytree(SRC, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__", "daily", "weekly", "ledger", "latest.*"))
(tmp / "ledger").mkdir(); (tmp / "weekly").mkdir()
sys.path.insert(0, str(tmp))
from scanner import render, gate, util  # noqa: E402  (all writes below go to the fixture copy via explicit paths)

D = "2026-01-01"
src = {"id": "fx", "name": "Fixture source", "home_url": "https://example.com", "tier": "T1-seed", "trust_mean": 0.5, "lb10": 0.2, "seed": True}
def item(n, status="verified"):
    return {"id": f"{D}-0000000{n}", "date": D, "category": "release", "claim": f"claim {n}", "why_it_matters": "x", "try_this": "do x",
            "source": dict(src), "also_seen_in": [], "item_url": "https://example.com/a", "evidence": [{"url": "https://example.com/a", "kind": "primary"}],
            "verification": {"status": status, "method": "m", "checked_at": "t", "provisional": True, "t1_audit_sample": n == 1, "audit_verdict": "pass" if n == 1 else None, "audit_note": None},
            "excerpt": None, "excerpt_url": None, "scorable": True, "prediction_deadline": None, "state": "pending", "recheck_on": "2026-01-15"}
items = [item(1), item(2)]
render.write(tmp, D, 1, items, {}, 2, 1)
(tmp / "ledger" / "claims.json").write_text(json.dumps({"claims": [dict(i, source_id="fx") for i in items],
                                                        "audits": [{"date": D, "item_id": items[0]["id"], "source_id": "fx", "tier": "T1-seed", "verdict": "pass"}]}))
(tmp / "CHANGELOG-sources.md").write_text("# Source changes\n")
env = dict(os.environ, CP_PRIVATE_PATTERNS="/dev/null")
run = lambda *a: subprocess.run([sys.executable, "-m", *a], cwd=tmp, env=env, capture_output=True, text=True)

# correct: pending -> corrected (never scored), second correction refused
r = run("scanner.correct", items[0]["id"], "--reason", "test"); assert r.returncode == 0, r.stdout + r.stderr
c = json.loads((tmp / "ledger" / "claims.json").read_text())["claims"][0]
assert c["state"] == "corrected" and "reversed_on" not in c, c
assert run("scanner.correct", items[0]["id"], "--reason", "again").returncode == 1
# correct: a claim resolved "bad" becomes corrected with no trust change (no reversed_on: it already took its beta +1)
led = json.loads((tmp / "ledger" / "claims.json").read_text())
led["claims"][1].update(state="resolved", outcome="bad", resolved_on="2026-01-15"); (tmp / "ledger" / "claims.json").write_text(json.dumps(led))
r = run("scanner.correct", items[1]["id"], "--reason", "source now says y", "--date", "2026-01-20"); assert r.returncode == 0, r.stdout + r.stderr
c = json.loads((tmp / "ledger" / "claims.json").read_text())["claims"][1]
assert c["state"] == "corrected" and "reversed_on" not in c and c["correction"]["state_before"] == "resolved", c
assert "already scored bad" in (tmp / "CHANGELOG-sources.md").read_text()
# remove --all (held day, includes the audited item): exit 0, audits kept and marked removed, archive empty
r = run("scanner.remove", D, "--all", "--reason", "held: test"); assert r.returncode == 0, r.stdout + r.stderr
led = json.loads((tmp / "ledger" / "claims.json").read_text())
assert not led["claims"] and all(x.get("removed") for x in led["audits"])
assert json.loads((tmp / "daily" / "2026" / "01" / f"{D}.json").read_text())["items"] == []
# remove --all again on the now-empty day, and on a day with no archive: both exit 0
assert run("scanner.remove", D, "--all").returncode == 0
assert run("scanner.remove", "2026-01-02", "--all").returncode == 0
# gate item rules
bad = item(3, "unverified"); assert any("try-this" in p for p in gate.check_item(bad, "t"))
fv = item(4, "face-value-T1"); assert any("face-value" in p for p in gate.check_item(fv, "t"))      # provisional
fv["verification"]["provisional"] = False; fv["source"]["tier"] = "T1"; assert not gate.check_item(fv, "t")
fv["category"] = "tool"; assert any("face-value" in p for p in gate.check_item(fv, "t"))
# robots: a 403 on robots.txt stops the host for the day; later requests make no network call
class R:
    def __init__(s, code): s.status_code, s.is_redirect, s.headers, s.text = code, False, {}, ""
util.init_state(tmp.parent, "2026-01-01"); util._robots.clear(); util.MIN_GAP = 0
with mock.patch.object(util.requests, "get", return_value=R(403)) as g:
    assert util.url_alive("https://stop.example/page")[0] is None
    n = g.call_count
    assert util.url_alive("https://stop.example/other")[0] is None and g.call_count == n
assert "stop.example" in util.stopped_hosts()

# fetch: a same-day rerun skips feeds already fetched but keeps the items the first run ingested
import yaml  # noqa: E402
from scanner import fetch, run as runmod, extract  # noqa: E402
work = tmp.parent / "work"; D2 = "2026-11-01"
reg = {"sources": [{"id": "fx", "name": "Fixture source", "home_url": "https://example.com", "enabled": True, "seed": True,
                    "register": {k: "x" for k in ("route", "basis", "republish", "limit", "stop_rule", "opt_out")},
                    "feeds": [{"type": "rss", "url": "https://example.com/feed"}]}]}
(tmp / "sources.yaml").write_text(yaml.safe_dump(reg))
one = [{"url": "https://example.com/a", "source_id": "fx", "title": "a"}]
util.init_state(work, D2)
with mock.patch.object(fetch, "feed_items", return_value=one) as fi, mock.patch.object(fetch, "snapshot"):
    got1, _ = fetch.run(tmp, work, D2, 36)
    util.init_state(work, D2)
    got2, rep2 = fetch.run(tmp, work, D2, 36)
assert fi.call_count == 1 and len(got1) == 1 and [i["url"] for i in got2] == ["https://example.com/a"], (got2, rep2)
# run: a second full run on the same day refuses (exit 3) and changes nothing
(work / "raw" / D2 / "claims.raw.json").write_text("[]")
before = (tmp / "sources.yaml").read_text()
r = subprocess.run([sys.executable, "-m", "scanner.run", "--date", D2], cwd=tmp, env=dict(env, CP_WORK=str(work)), capture_output=True, text=True)
assert r.returncode == 3 and (tmp / "sources.yaml").read_text() == before, r.stdout + r.stderr
# audit: a failed audit caused by our summary is kept in the ledger (linked to the corrected claim), does not demote,
# and the corrected claim is re-audited; stable across two --skip-extract reruns
reg["sources"][0]["trust"] = {"tier": "T1-seed"}
(tmp / "sources.yaml").write_text(yaml.safe_dump(reg)); (tmp / "CHANGELOG-sources.md").write_text("# Source changes\n")
for f in (tmp / "ledger").glob("*"): f.unlink()
claim = {"source_id": "fx", "item_url": "https://example.com/a", "claim": "corrected claim", "category": "release", "why_it_matters": "x",
         "try_this": "do x", "evidence": [{"url": "https://example.com/a", "kind": "primary"}], "verification": {"status": "verified", "method": "m"}}
(work / "raw" / D2 / "claims.raw.json").write_text(json.dumps([claim]))
nid = D2 + "-" + format(runmod._h("fx|https://example.com/a|corrected claim"), "x")[:8]
old = D2 + "-deadbeef"
(work / "raw" / D2 / "audit.result.json").write_text(json.dumps({old: {"verdict": "fail", "note": "n", "source_id": "fx", "tier": "T1-seed",
                                                                      "cause": "our summary", "superseded_by": nid}}))
runmod.WORK = work; runmod.REPO = tmp      # the package is imported from the real checkout; point every write at the fixture
for _ in range(2):
    with mock.patch.object(sys, "argv", ["run", "--date", D2, "--skip-extract"]), \
         mock.patch.object(util, "url_alive", return_value=(True, 200)), \
         mock.patch.object(extract, "audit", side_effect=lambda smp, *a: {x["id"]: {"verdict": "pass", "note": "ok"} for x in smp}) as au:
        runmod.main()
    led = json.loads((tmp / "ledger" / "claims.json").read_text())
    byid = {x["item_id"]: x for x in led["audits"]}
    assert set(byid) == {old, nid} and byid[old]["verdict"] == "fail" and byid[old]["superseded_by"] == nid, led["audits"]
    assert byid[nid]["verdict"] == "pass" and byid[nid]["tier"] == "T1-seed"
    s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
    assert not s.get("audit_demoted"), s
    assert led["claims"][0]["verification"]["audit_verdict"] == "pass"
    # the published audit note is built from the verdict and a URL only (scanner/notes.py); the model note "ok" is never used
    assert led["claims"][0]["verification"]["audit_note"] == "Verdict: pass. Source: https://example.com/a", led["claims"][0]["verification"]
assert au.call_count == 0          # second rerun reuses the saved verdict
cl = (tmp / "CHANGELOG-sources.md").read_text()
assert cl.count(f"audit of {old}") == 1 and "cause: our summary" in cl
assert f"audit of {nid} (Fixture source): pass (Verdict: pass. Source: https://example.com/a)" in cl and "(ok)" not in cl, cl
assert f"audit of {old} (Fixture source): fail (Verdict: fail.); cause: our summary" in cl, cl      # no evidence on record: no Source
# audit: an invalid verdict in audit.result.json is re-audited, but the entry's cause/superseded_by survive the rewrite
(work / "raw" / D2 / "audit.result.json").write_text(json.dumps({old: {"verdict": "failed", "cause": "our summary", "superseded_by": nid},
                                                                  nid: {"verdict": ["pass"], "cause": "c1"}}))
with mock.patch.object(sys, "argv", ["run", "--date", D2, "--skip-extract"]), \
     mock.patch.object(util, "url_alive", return_value=(True, 200)), \
     mock.patch.object(extract, "audit", side_effect=lambda smp, *a: {x["id"]: {"verdict": "pass"} for x in smp}) as au:
    runmod.main()
assert [x["id"] for x in au.call_args[0][0]] == [nid], au.call_args
saved = json.loads((work / "raw" / D2 / "audit.result.json").read_text())
assert saved[old] == {"cause": "our summary", "superseded_by": nid}, saved
assert saved[nid]["verdict"] == "pass" and saved[nid]["cause"] == "c1", saved
led = json.loads((tmp / "ledger" / "claims.json").read_text())
assert [(x["item_id"], x["verdict"]) for x in led["audits"]] == [(nid, "pass")], led["audits"]
# audit: a failed audit of the source's own claim demotes the source; two reruns keep one record at the start-of-day tier
(tmp / "sources.yaml").write_text(yaml.safe_dump(reg)); (tmp / "CHANGELOG-sources.md").write_text("# Source changes\n")
for f in (tmp / "ledger").glob("*"): f.unlink()
(work / "raw" / D2 / "audit.result.json").unlink()
for _ in range(2):
    with mock.patch.object(sys, "argv", ["run", "--date", D2, "--skip-extract"]), \
         mock.patch.object(util, "url_alive", return_value=(True, 200)), \
         mock.patch.object(extract, "audit", side_effect=lambda smp, *a: {x["id"]: {"verdict": "fail", "note": "false"} for x in smp}):
        runmod.main()
    led = json.loads((tmp / "ledger" / "claims.json").read_text())
    assert [(x["item_id"], x["tier"], x["verdict"]) for x in led["audits"]] == [(nid, "T1-seed", "fail")], led["audits"]
    s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
    assert s["audit_demoted"] and s["audit_demoted_from"] == "T1-seed" and not s["trust"]["tier"].startswith("T1"), s
    assert led["claims"][0]["verification"]["status"] == "unverified" and led["claims"][0]["try_this"] is None
def rebuild():
    with mock.patch.object(sys, "argv", ["run", "--date", D2, "--skip-extract"]), \
         mock.patch.object(util, "url_alive", return_value=(True, 200)), mock.patch.object(extract, "audit", return_value={}):
        runmod.main()
    return json.loads((tmp / "ledger" / "claims.json").read_text())
# correct: a same-day rebuild keeps the correction
r = run("scanner.correct", nid, "--reason", "test", "--date", D2); assert r.returncode == 0, r.stdout + r.stderr
led = rebuild(); c = led["claims"][0]
assert c["id"] == nid and c["state"] == "corrected" and c["correction"]["reason"] == "test", c
# supersede: a same-day rebuild keeps the source change (state, outcome and record)
r = run("scanner.supersede", nid, "--changed-on", D2, "--note", "index v2", "--date", D2); assert r.returncode == 0, r.stdout + r.stderr
led = rebuild(); c = led["claims"][0]
assert c["state"] == "superseded" and c["outcome"] == "source-changed" and c["source_change"]["note"] == "index v2", c
assert c["source_change"]["correction_before"]["reason"] == "test" and "correction" not in c, c
# resolution: a bad re-check the resolver flags as a source change keeps its outcome (and beta +1) and is flagged for review
led["claims"].append(dict(c, id="2026-10-18-feedf00d", date="2026-10-18", state="pending", recheck_on=D2, claim="flag me"))
for k in ("outcome", "resolved_on", "resolution_note", "source_change"): led["claims"][-1].pop(k)
(tmp / "ledger" / "claims.json").write_text(json.dumps(led))
with mock.patch.object(extract, "resolve", return_value={"2026-10-18-feedf00d": {"outcome": "bad", "source_urls": ["https://example.com/a"],
                                                                                 "source_changed": True}}):
    led = rebuild()
f = next(x for x in led["claims"] if x["id"] == "2026-10-18-feedf00d")
assert f["state"] == "resolved" and f["outcome"] == "bad" and f["source_change_flag"] == D2, f
assert "flagged a possible source change" in (tmp / "CHANGELOG-sources.md").read_text()
led["claims"] = [x for x in led["claims"] if x["id"] != f["id"]]; (tmp / "ledger" / "claims.json").write_text(json.dumps(led))
# remove: a same-day rebuild doesn't republish a removed item
r = run("scanner.remove", D2, nid, "--reason", "test"); assert r.returncode == 0, r.stdout + r.stderr
led = rebuild()
assert not led["claims"] and nid not in (tmp / "latest.md").read_text() and led["removed"][0]["id"] == nid, led
aud = [x for x in led["audits"] if x["item_id"] == nid]
assert len(aud) == 1 and aud[0]["removed"] and aud[0]["tier"] == "T1-seed" and aud[0]["verdict"] == "fail", led["audits"]
# quarantine: claims resolved on D2 go back to pending (re-resolved a week later), trust is recomputed from the ledger, today's
# changelog lines for them are dropped and the reason is recorded in WORK; other claims are left alone; a rerun changes nothing
from scanner import trust  # noqa: E402
def rc(n, **kw):
    return {"id": f"2026-10-18-0000000{n}", "date": "2026-10-18", "category": "release", "claim": f"old {n}", "evidence": [], "scorable": True,
            "source_id": "fx", "state": "resolved", "outcome": "good", "resolved_on": D2, "resolution_note": "Outcome: good.",
            "recheck_on": "2026-11-01"} | kw
qc = [rc(1), rc(2, outcome="docs-verified"), rc(3), rc(4, resolved_on="2026-10-31"), rc(5, state="pending", recheck_on="2026-11-03")]
for k in ("outcome", "resolved_on", "resolution_note"): qc[4].pop(k)
led = json.loads((tmp / "ledger" / "claims.json").read_text()); led["claims"] = qc
(tmp / "ledger" / "claims.json").write_text(json.dumps(led))
ev = lambda cs: [{"date": c["resolved_on"], "outcome": c["outcome"]} for c in cs if c["state"] == "resolved"]
regq = yaml.safe_load((tmp / "sources.yaml").read_text())
regq["sources"][0]["trust"] = trust.score(regq["sources"][0], ev(qc), dt.date(2026, 11, 1)) | {"items_seen_by_date": {D2: 1}, "items_seen": 1}
(tmp / "sources.yaml").write_text(yaml.safe_dump(regq))
keep_lines = f"- {D2}: Fixture source tier T1-seed -> T3\n- 2026-10-31: note on {qc[0]['id']}\n- {D2}: audit of {qc[2]['id']} (Fixture source): pass\n"
(tmp / "CHANGELOG-sources.md").write_text("# Source changes\n" + keep_lines + f"- {D2}: line about {qc[0]['id']}\n- {D2}: and {qc[1]['id']}\n")
qenv = dict(env, CP_WORK=str(work))
qrun = lambda *a: subprocess.run([sys.executable, "-m", "scanner.quarantine", *a], cwd=tmp, env=qenv, capture_output=True, text=True)
assert qrun(D2, qc[0]["id"]).returncode == 2                                         # --reason is required
args = (D2, qc[0]["id"], qc[1]["id"], qc[3]["id"], qc[4]["id"], "--reason", "review: note credits a page that lacks part of the claim")
r = qrun(*args); assert r.returncode == 0, r.stdout + r.stderr
led = json.loads((tmp / "ledger" / "claims.json").read_text()); byid = {c["id"]: c for c in led["claims"]}
for c in qc[:2]:
    q = byid[c["id"]]
    assert q["state"] == "pending" and q["recheck_on"] == "2026-11-08" and not {"outcome", "resolved_on", "resolution_note"} & set(q), q
assert byid[qc[2]["id"]] == qc[2] and byid[qc[3]["id"]] == qc[3] and byid[qc[4]["id"]] == qc[4]      # not named / other date / pending
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
want = trust.score(s, ev([qc[2], qc[3]]), dt.date(2026, 11, 1))
assert s["trust"]["resolved_tips"] == 2 and s["trust"]["alpha"] == want["alpha"] and s["trust"]["tier"] == want["tier"], s["trust"]
assert s["trust"]["items_seen_by_date"] == {D2: 1} and "research_criterion" in s["trust"], s["trust"]
assert (tmp / "CHANGELOG-sources.md").read_text() == "# Source changes\n" + keep_lines
qlog = json.loads((work / "raw" / D2 / "quarantine.json").read_text())
assert len(qlog) == 1 and qlog[0]["ids"] == sorted([qc[0]["id"], qc[1]["id"]]) and qlog[0]["reason"].startswith("review:"), qlog
snap = [(tmp / f).read_text() for f in ("ledger/claims.json", "sources.yaml", "CHANGELOG-sources.md")] + [(work / "raw" / D2 / "quarantine.json").read_text()]
r = qrun(*args); assert r.returncode == 0, r.stdout + r.stderr                       # idempotent
assert snap == [(tmp / f).read_text() for f in ("ledger/claims.json", "sources.yaml", "CHANGELOG-sources.md")] + [(work / "raw" / D2 / "quarantine.json").read_text()]
# quarantine undoes what the blocked resolutions did to the source that day: the run.py tier, disable and recovery lines stay
# (the changelog is append-only) and a dated "tier change reverted" line follows; a drop-rule disable from that day is lifted
def qstate(outcomes, src=(), lines=()):
    """Ledger of resolved claims [(resolved_on, outcome)], the source scored on them as run.py would, and run.py's changelog lines."""
    cs = [rc(n, id=f"2026-10-1{n % 10}-{n:08d}", resolved_on=d, outcome=o) for n, (d, o) in enumerate(outcomes)]
    l = json.loads((tmp / "ledger" / "claims.json").read_text()); l["claims"] = cs
    (tmp / "ledger" / "claims.json").write_text(json.dumps(l))
    sq = {k: v for k, v in regq["sources"][0].items() if not k.startswith("audit_demoted")} | {"seed": False, "enabled": True} | dict(src)
    sq["trust"] = trust.score(sq, ev(cs), dt.date(2026, 11, 1)); sq["trust"].pop("audit_demoted")
    (tmp / "sources.yaml").write_text(yaml.safe_dump({"sources": [sq]}))
    (tmp / "CHANGELOG-sources.md").write_text("# Source changes\n" + "".join(f"- {D2}: {m}\n" for m in lines))
    return cs, sq["trust"]
def tline(prev, t):                                                                 # run.py's tier-change line
    return f"Fixture source tier {prev} -> {t['tier']} (mean {t['trust_mean']}, LB10 {t['lb10']}, {t['resolved_tips']} resolved)"
reverted = lambda ids: f"- {D2}: Fixture source tier change reverted (resolutions quarantined: {', '.join(sorted(ids))})\n"
# 1. today's good resolution lifted the source T3 -> T2; quarantining it puts it back to T3
cs, t = qstate([("2026-10-20", "good")] * 14 + [(D2, "good")]); assert t["tier"] == "T2", t
lines = [tline("T3", t)]; qstate([("2026-10-20", "good")] * 14 + [(D2, "good")], lines=lines)
r = qrun(D2, cs[-1]["id"], "--reason", "review"); assert r.returncode == 0, r.stdout + r.stderr
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
assert s["trust"]["tier"] == "T3" and s["enabled"], s
assert (tmp / "CHANGELOG-sources.md").read_text() == f"# Source changes\n- {D2}: {lines[0]}\n" + reverted([cs[-1]["id"]])
# 2. today's bad resolution tripped the drop rule and disabled the source; quarantining it re-enables it at T3
out = [("2026-10-20", "bad")] * 14 + [(D2, "bad")]
lines = [tline("T3", {"tier": "drop", **{k: v for k, v in qstate(out)[1].items() if k != "tier"}}),
         "Fixture source disabled: trust mean below 0.50 after 15+ resolved tips"]
cs, t = qstate(out, {"enabled": False, "disabled_reason": f"dropped by trust rule on {D2}"}, lines); assert t["tier"] == "drop", t
r = qrun(D2, cs[-1]["id"], "--reason", "review"); assert r.returncode == 0, r.stdout + r.stderr
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
assert s["trust"]["tier"] == "T3" and s["enabled"] is True and "disabled_reason" not in s, s
assert (tmp / "CHANGELOG-sources.md").read_text() == "# Source changes\n" + "".join(f"- {D2}: {m}\n" for m in lines) + reverted([cs[-1]["id"]])
# a disable from another day is not lifted, and no line is written when nothing changes back
cs, t = qstate([("2026-10-20", "bad")] * 14 + [(D2, "bad")], {"enabled": False, "disabled_reason": "dropped by trust rule on 2026-10-25"})
r = qrun(D2, cs[-1]["id"], "--reason", "review"); assert r.returncode == 0, r.stdout + r.stderr
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
assert s["enabled"] is False and s["trust"]["tier"] == "T3", s
assert reverted([cs[-1]["id"]]) in (tmp / "CHANGELOG-sources.md").read_text()          # tier drop -> T3 changed back
cs, t = qstate([("2026-10-20", "good")] * 3 + [(D2, "good")])
r = qrun(D2, cs[-1]["id"], "--reason", "review"); assert r.returncode == 0, r.stdout + r.stderr
assert (tmp / "CHANGELOG-sources.md").read_text() == "# Source changes\n"                  # T3 -> T3: nothing to revert
# 3. today's resolutions earned back an audit demotion; without them the source is demoted again
out = [("2026-10-20", "good")] * 10 + [(D2, "good")] * 10
demoted = {"audit_demoted": True, "audit_demoted_on": "2026-10-17"}
assert not trust.score(demoted, ev([rc(0, resolved_on=d, outcome=o) for d, o in out]), dt.date(2026, 11, 1))["audit_demoted"]
cs, t = qstate(out, {"audit_demoted": False, "audit_demoted_on": "2026-10-17"},
               ["Fixture source recovered from audit demotion (15+ resolved since, LB10 >= 0.80)"])
r = qrun(D2, *[c["id"] for c in cs[10:]], "--reason", "review"); assert r.returncode == 0, r.stdout + r.stderr
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
assert s["audit_demoted"] is True and s["trust"]["tier"] == "T2-provisional", s
assert (tmp / "CHANGELOG-sources.md").read_text().endswith(reverted([c["id"] for c in cs[10:]])), (tmp / "CHANGELOG-sources.md").read_text()
# supersede: accurate when published, then the source changed its own numbers. A "bad" re-check (already corrected) becomes
# source-changed/superseded, its beta +1 is undone (trust recomputed as if it was never scored), and it can't be corrected again
srun = lambda *a: subprocess.run([sys.executable, "-m", "scanner.supersede", *a], cwd=tmp, env=qenv, capture_output=True, text=True)
cs, t = qstate([("2026-10-20", "good")] * 4 + [(D2, "bad")]); bid = cs[-1]["id"]
l = json.loads((tmp / "ledger" / "claims.json").read_text())
l["claims"][-1].update(state="corrected", resolution_note="Outcome: bad. Source: https://example.com/a",
                       correction={"on": "2026-11-05", "reason": "page now shows 46", "state_before": "resolved"})
(tmp / "ledger" / "claims.json").write_text(json.dumps(l))
arch_snap = {p: p.read_text() for p in (tmp / "daily").rglob("*.*")}; assert arch_snap and (tmp / "latest.json").exists()
assert srun(bid, "--note", "x").returncode == 2                                              # --changed-on is required
assert srun(bid, "--changed-on", "2026-10-01", "--note", "x", "--date", "2026-11-21").returncode == 1   # before the claim's date
r = srun(bid, "--changed-on", "2026-10-27", "--note", "Oct 23 launch · Oct 27 index update (v5)", "--date", "2026-11-21")
assert r.returncode == 0, r.stdout + r.stderr
c = next(x for x in json.loads((tmp / "ledger" / "claims.json").read_text())["claims"] if x["id"] == bid)
assert c["state"] == "superseded" and c["outcome"] == "source-changed" and "correction" not in c, c
assert {p: p.read_text() for p in (tmp / "daily").rglob("*.*")} == arch_snap                # archives stay as published
assert c["resolution_note"] == "Outcome: source-changed. Source: https://example.com/a", c
sc = c["source_change"]
assert sc["changed_on"] == "2026-10-27" and sc["note"].startswith("Oct 23 launch") and sc["now_shows"] == "page now shows 46", sc
assert sc["outcome_before"] == "bad" and sc["state_before"] == "corrected" and sc["correction_before"]["on"] == "2026-11-05", sc
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
want = trust.score(s, ev(cs[:4]), dt.date(2026, 11, 21))
assert s["trust"]["resolved_tips"] == 4 and s["trust"]["beta"] == want["beta"] and s["trust"]["alpha"] == want["alpha"], (s["trust"], want)
assert s["trust"]["beta"] < t["beta"] + 1e-9 and s["trust"]["resolved_tips"] == t["resolved_tips"] - 1, (s["trust"], t)
assert "source changed on " + bid in (tmp / "CHANGELOG-sources.md").read_text() and "-> superseded" in (tmp / "CHANGELOG-sources.md").read_text()
assert srun(bid, "--changed-on", "2026-10-27", "--note", "again", "--date", "2026-11-21").returncode == 1   # already superseded
assert run("scanner.correct", bid, "--reason", "x").returncode == 1                                  # not a wrong claim
# a pending claim superseded before its re-check is closed and never scored
cs, t = qstate([("2026-10-20", "good")] * 2); p = dict(cs[0], id="2026-10-18-0000beef", state="pending")
for k in ("outcome", "resolved_on", "resolution_note"): p.pop(k)
l = json.loads((tmp / "ledger" / "claims.json").read_text()); l["claims"].append(p); (tmp / "ledger" / "claims.json").write_text(json.dumps(l))
r = srun(p["id"], "--changed-on", "2026-10-19", "--note", "v2", "--date", "2026-11-21"); assert r.returncode == 0, r.stdout + r.stderr
c = next(x for x in json.loads((tmp / "ledger" / "claims.json").read_text())["claims"] if x["id"] == p["id"])
assert c["state"] == "superseded" and c["source_change"]["state_before"] == "pending" and "outcome_before" not in c["source_change"], c
assert yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]["trust"]["resolved_tips"] == 2
# a drop the superseded bad claim caused is lifted (the daily run never rescores a disabled source)
cs, t = qstate([("2026-10-20", "good")] * 1 + [("2026-10-20", "bad")] * 13 + [(D2, "bad")], {"enabled": False, "disabled_reason": "dropped by trust rule on 2026-11-01"})
assert t["tier"] == "drop", t
r = srun(cs[1]["id"], "--changed-on", "2026-10-19", "--note", "v2", "--date", "2026-11-21"); assert r.returncode == 0, r.stdout + r.stderr
s = yaml.safe_load((tmp / "sources.yaml").read_text())["sources"][0]
assert s["trust"]["resolved_tips"] == 14 and s["trust"]["tier"] == "T3" and s["enabled"] is True and "disabled_reason" not in s, s
# gemini: CP_GEMINI_FROM can't move the start before Oct 23
vid = [{"kind": "youtube", "url": "https://www.youtube.com/watch?v=x"}]
with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fixture", "CP_GEMINI_APPROVED": "1", "CP_GEMINI_FROM": "2026-10-16"}), \
     mock.patch.object(extract.requests, "post") as post:
    extract.gemini_video_notes(vid, work, "2026-10-20", lambda m: None)
assert post.call_count == 0 and "off" in vid[0]["video_status"], vid
# gemini: the default model is a live one, and a 404 stops the run loudly after the first call
assert extract.GEMINI_DEFAULT_MODEL != "gemini-2.5-flash" and extract.GEMINI_DEFAULT_MODEL == "gemini-3.8-flash"
vids = [{"kind": "youtube", "url": f"https://www.youtube.com/watch?v={x}"} for x in "abc"]
logs = []
fake404 = mock.Mock(status_code=404)
env = {"GEMINI_API_KEY": "fixture", "CP_GEMINI_APPROVED": "1"}
with mock.patch.dict(os.environ, env), mock.patch.object(extract.requests, "post", return_value=fake404) as post:
    os.environ.pop("GEMINI_MODEL", None); os.environ.pop("CP_GEMINI_FROM", None)
    extract.gemini_video_notes(vids, Path(tempfile.mkdtemp()), "2026-10-23", logs.append)
assert post.call_count == 1, post.call_count
assert any("HTTP 404" in m and extract.GEMINI_DEFAULT_MODEL in m and "GEMINI_MODEL" in m for m in logs), logs
assert all("404" in v["video_status"] for v in vids), vids
# remove: trust.scorable_claims is recounted from the ledger after one id and after --all.
# A non-scorable claim (removed or not) does not count; an older claim that stays does.
D3 = "2026-01-03"
def tblock(n):
    return {"tier": "T3", "trust_mean": 0.5, "lb10": 0.2, "resolved_tips": 0, "research_criterion": "x", "scorable_claims": n}
reg_sc = {"sources": [
    {"id": "oa", "name": "OpenAI", "home_url": "https://openai.com", "enabled": True, "seed": False, "trust": tblock(3)},
    {"id": "sw", "name": "Simon Willison", "home_url": "https://simonwillison.net", "enabled": True, "seed": False, "trust": tblock(2)},
    {"id": "bare", "name": "No trust yet", "home_url": "https://example.com", "enabled": False}]}
(tmp / "sources.yaml").write_text(yaml.safe_dump(reg_sc))
def day_item(n, sid, scorable=True):
    d = item(n); d.update(id=f"{D3}-{n:08d}", date=D3, scorable=scorable, claim=f"{'claim' if scorable else 'opinion'} {sid} {n}")
    d["source"] = {"id": sid, "name": sid, "home_url": "https://example.com", "tier": "T3", "trust_mean": 0.5, "lb10": 0.2, "seed": False}
    return d
day = [day_item(1, "oa"), day_item(2, "oa"), day_item(3, "oa", False), day_item(4, "sw")]
old = [{"id": "2026-01-01-00000005", "date": "2026-01-01", "source_id": "oa", "scorable": True, "claim": "older oa"},
       {"id": "2026-01-01-00000006", "date": "2026-01-01", "source_id": "oa", "scorable": False, "claim": "older opinion"},
       {"id": "2026-01-01-00000007", "date": "2026-01-01", "source_id": "sw", "scorable": True, "claim": "older sw"}]
render.write(tmp, D3, 80, day, {s["id"]: s for s in reg_sc["sources"]}, 4, 2, write_latest=False)
(tmp / "ledger" / "claims.json").write_text(json.dumps({"claims": [dict(d, source_id=d["source"]["id"]) for d in day] + old, "audits": []}))
def fresh(sid):
    cs = json.loads((tmp / "ledger" / "claims.json").read_text())["claims"]
    return sum(1 for c in cs if c["source_id"] == sid and c.get("scorable"))
def stored():
    return {s["id"]: (s.get("trust") or {}).get("scorable_claims") for s in yaml.safe_load((tmp / "sources.yaml").read_text())["sources"]}
assert stored()["oa"] == fresh("oa") == 3 and stored()["sw"] == fresh("sw") == 2 and stored()["bare"] is None
r = run("scanner.remove", D3, day[0]["id"], "--reason", "test"); assert r.returncode == 0, r.stdout + r.stderr
got = stored()
assert got["oa"] == 2 and got["oa"] == fresh("oa") and got["oa"] == 3 - 1, got   # one scorable claim left the ledger
assert got["sw"] == 2 and got["sw"] == fresh("sw") and got["bare"] is None, got   # untouched source, and the opinion still does not count
r = run("scanner.remove", D3, "--all", "--reason", "held: test"); assert r.returncode == 0, r.stdout + r.stderr
got = stored()
assert got["oa"] == fresh("oa") == 1 and got["sw"] == fresh("sw") == 1, got      # only the older scorable claims remain
assert json.loads((tmp / "daily" / "2026" / "01" / f"{D3}.json").read_text())["items"] == []
assert run("scanner.remove", D3, "--all").returncode == 0 and stored() == got     # empty day: counts stay at the recount
shutil.rmtree(tmp.parent)
# health flags: thin day, one source dominating, a source gone quiet (review only, never blocks)
srcs_h = {"a": {"name": "A", "enabled": True, "feeds": [{}], "trust": {"items_seen_by_date": {"2026-10-24": 3}}},
          "b": {"name": "B", "enabled": True, "feeds": [{}], "trust": {"items_seen_by_date": {"2026-10-10": 5}}},
          "c": {"name": "C", "enabled": False, "feeds": [{}]}}
drop_h = [{"source": {"name": "A"}}] * 4 + [{"source": {"name": "B"}}]
fl = runmod.health("2026-10-25", 3, drop_h, srcs_h)
assert any(f.startswith("thin day: 3") for f in fl) and any("4 of 5 items from A" in f for f in fl), fl
assert any(f.startswith("quiet") and "B" in f and "C" not in f and "A," not in f for f in fl), fl
assert runmod.health("2026-10-25", 40, drop_h[3:], srcs_h | {"b": dict(srcs_h["b"], trust={"items_seen_by_date": {"2026-10-25": 1}})}) == []
print("tool tests: OK")

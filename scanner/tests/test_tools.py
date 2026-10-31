"""Tests for remove.py, correct.py, gate item rules and robots stop handling, on a self-contained fixture
(never on the live drop, so an empty or held day can't break the daily run). Run: python -m scanner.tests.test_tools"""
import json, shutil, subprocess, sys, tempfile, os
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
# remove: a same-day rebuild doesn't republish a removed item
r = run("scanner.remove", D2, nid, "--reason", "test"); assert r.returncode == 0, r.stdout + r.stderr
led = rebuild()
assert not led["claims"] and nid not in (tmp / "latest.md").read_text() and led["removed"][0]["id"] == nid, led
aud = [x for x in led["audits"] if x["item_id"] == nid]
assert len(aud) == 1 and aud[0]["removed"] and aud[0]["tier"] == "T1-seed" and aud[0]["verdict"] == "fail", led["audits"]
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

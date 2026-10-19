"""Tests for the cross-day repeat-story check (scanner/repeats.py) on synthetic fixtures. Run: python -m scanner.tests.test_repeats"""
import json, os, tempfile
from pathlib import Path
from scanner import repeats as R

os.environ.pop("CP_STORY_HISTORY", None)        # fixtures only; never the private history

# FR / SD.check return only "sure" matches (the ones run.py drops); flagged-but-kept matches count as None here
def FR(claim, stories):
    r = R.find_repeat(claim, stories)
    return r if r and r[2] else None

class SD(R.SameDay):
    def check(self, claim):
        r = super().check(claim)
        return r if r and r[1] else None

def claim(cid, date, text, urls, item="https://news.example/p/1"):
    return {"id": cid, "date": date, "claim": text, "item_url": item, "evidence": [{"url": u} for u in urls]}

D = "2026-01-20"
ledger = {"claims": [
    claim("a", "2026-01-19", "Acme released Rocket 2.0, a 500B-total MoE under Apache 2.0 in BF16 and FP8.", ["https://hf.example/acme/rocket-2.0"]),
    claim("b", "2026-01-19", "Widgetly added a Pipelines API with 300 repositories per pipeline for managing cloud builds.", ["https://widgetly.example/changelog/pipelines"]),
    claim("c", "2026-01-19", "Toolz CLI v1.2.0 adds a --json flag.", ["https://github.com/toolz/toolz/releases/tag/v1.2.0"]),
    claim("d", "2026-01-19", "Fastco's Turbo mode costs $8 per million tokens.", ["https://fastco.example/docs/turbo-mode"]),
    claim("e", "2026-01-19", "Vidco explains Lumen streaming in a talk.", ["https://www.youtube.com/watch?v=AAA&utm_source=x"]),
    claim("f", "2026-01-19", "Acme Rocket adds streaming responses.", ["https://acme.example/blog/rocket-streaming"]),
    claim("g", "2026-01-06", "Edgeco shipped Prism 4 with 64 shards and 300 repositories.", ["https://edgeco.example/prism-4"]),   # exactly 14 days back
    claim("z", "2026-01-05", "Oldco shipped Zeta 9 with 300 repositories.", ["https://oldco.example/zeta-9"]),                    # 15 days back
    {"id": "bad", "date": None, "claim": None, "evidence": None},
]}
st = R.earlier_stories(ledger, D)
rep = lambda text, urls, item="https://other.example/x": FR(claim("n", D, text, urls, item), st)
flag = lambda text, urls, item="https://other.example/x": R.find_repeat(claim("n", D, text, urls, item), st)   # sure or flagged

# must match: same specific URL with some shared text (trailing slash, www, tracking params ignored)
assert flag("Rocket's license needs attribution from Acme.", ["https://hf.example/acme/rocket-2.0/"])
# must match: same story, different URL and wording
assert flag("Acme Rocket 2.0 is out: a 500B MoE.", ["https://acme.example/news/rocket"])
assert flag("Widgetly Pipelines now holds 300 repositories for managing cloud builds.", ["https://widgetly.example/changelog"])
# must match: version written with and without a leading v
assert flag("Toolz CLI 1.2.0 ships a JSON output flag for Toolz.", ["https://toolz.example/notes"])
# window: exactly 14 days back is compared, 15 days back is not
assert flag("Edgeco Prism 4 has 64 shards and 300 repositories.", ["https://edgeco.example/blog/prism"])
assert not rep("Oldco Zeta 9 holds 300 repositories.", ["https://oldco.example/zeta-9"])

# must not match: a new version of the same tool
assert not rep("Toolz CLI v1.3.0 adds a --yaml flag.", ["https://github.com/toolz/toolz/releases/tag/v1.3.0"])
# must not match: unrelated launch sharing license, weight formats and license version (Apache 2.0)
assert not rep("Bolt released Nova 3.0 under Apache 2.0 in BF16 and FP8.", ["https://hf.example/bolt/nova-3.0"])
# must not match: a new feature of the same product
assert not rep("Acme Rocket adds response caching.", ["https://acme.example/blog/rocket-caching"])
# must not match: generic index and docs pages are not shared evidence (docs pages change: a new price is news)
assert not rep("Gadgetco launched Sprocket.", ["https://widgetly.example/changelog"])
assert not rep("Fastco Turbo mode now costs $4.", ["https://fastco.example/docs/turbo-mode"])
# must not match: a different video on the same host/path (query string identifies it)
assert not rep("Vidco reviews the Halo headset.", ["https://youtube.com/watch?v=BBB"])
assert flag("Vidco on Lumen streaming.", ["https://youtube.com/watch?v=AAA"])
# must not match: different products sharing only plan names
assert not FR(claim("p", D, "Claude Security is available on Max, Team and Enterprise plans.", ["https://x.example/security"]),
                         R.earlier_stories({"claims": [claim("q", "2026-01-19", "Claude Code Review is available on Team and Enterprise plans.", ["https://x.example/review"])]}, D))
# must not match: changed version or price next to a shared dependency version or count
assert not FR(claim("r3", D, "Acme Rocket 3.0 supports Python 3.12.", ["https://acme.example/r3"]),
                         R.earlier_stories({"claims": [claim("r2", "2026-01-19", "Acme Rocket 2.0 supports Python 3.12.", ["https://acme.example/r2"])]}, D))
assert not FR(claim("p4", D, "Fastco Turbo costs $4 for 100 requests.", ["https://fastco.example/turbo-4"]),
                         R.earlier_stories({"claims": [claim("p8", "2026-01-19", "Fastco Turbo costs $8 for 100 requests.", ["https://fastco.example/turbo-8"])]}, D))
# must not match: two features citing the same repo homepage; a release tag URL stays specific
repo = R.earlier_stories({"claims": [claim("o1", "2026-01-19", "Space adds snapshots.", ["https://github.com/o/space"])]}, D)
assert not FR(claim("o2", D, "Space adds GPU sandboxes for builds.", ["https://github.com/o/space"]), repo)
assert R.specific("https://github.com/o/r/releases/tag/v1") and not R.specific("https://github.com/o/r/tree/main")
# integer versions; licenses a few words back; a new version that mentions the old one; money and percent figures
assert R.analyze("Acme Rocket v3 adds streaming.")[2] == {"3"}
assert R.analyze("under the Apache License, Version 2.0")[2] == set()
assert R.analyze("It costs $8.50 and scores 76.3% on the test.")[2] == set()
v2 = R.earlier_stories({"claims": [claim("v2", "2026-01-19", "Acme Rocket v2 adds streaming responses.", ["https://acme.example/post"])]}, D)
assert not FR(claim("v3", D, "Acme Rocket v3 adds streaming responses.", ["https://acme.example/post"]), v2)
r2 = R.earlier_stories({"claims": [claim("r2", "2026-01-19", "Acme released Rocket 2.0, a 500B MoE.", ["https://acme.example/r2"])]}, D)
assert not FR(claim("r3b", D, "Acme Rocket 3.0 replaces Rocket 2.0 as a 500B MoE.", ["https://acme.example/r3"]), r2)
sv = SD(); sv.add(claim("x1", D, "Acme Rocket v2 adds streaming.", ["https://acme.example/post"], "https://a.example"))
assert sv.check(claim("x2", D, "Acme Rocket v3 adds streaming.", ["https://acme.example/post"], "https://b.example")) is None
# integer versions after a name; projects named after a license keep their versions
one = lambda old, new: FR(claim("n", D, new, ["https://x.example/new"]), R.earlier_stories({"claims": [claim("o", "2026-01-19", old, ["https://x.example/old"])]}, D))
assert not one("Zeno released Opus 4, its strongest coding model, with hybrid reasoning.", "Zeno released Opus 4.1, its strongest coding model, with hybrid reasoning.")
assert not one("Acme Rocket 2.1 adds streaming responses and batch mode.", "Acme Rocket 3 adds streaming responses and batch mode.")
assert not one("Apache Kafka 4.0 adds queues for consumer groups.", "Apache Kafka 4.1 adds queues for consumer groups.")
assert not one("Apache 2.0 Rocket v2 adds streaming responses.", "Apache 2.0 Rocket v3 adds streaming responses.")
assert R.analyze("Apache Kafka 4.0")[2] == {"4.0"} and R.analyze("Rocket 3 and GPT-5")[2] == {"3", "5"}
assert R.analyze("released on October 16")[2] == set()
s2 = SD(); s2.add(claim("y1", D, "Acme Rocket 2 adds streaming responses.", ["https://acme.example/p"], "https://a.example"))
assert s2.check(claim("y2", D, "Acme Rocket 3 adds streaming responses.", ["https://acme.example/p"], "https://b.example")) is None
# a follow-up citing the old page plus a new one is kept; a status change on one side only is news
ga = R.earlier_stories({"claims": [claim("g0", "2026-01-19", "Acme Rocket adds streaming responses.", ["https://acme.example/rocket-x"])]}, D)
assert not FR(claim("g1", D, "Acme Rocket streaming is now GA at $4.", ["https://acme.example/rocket-x", "https://acme.example/rocket-ga"]), ga)
assert not FR(claim("g2", D, "Acme Rocket streaming responses now GA.", ["https://acme.example/other"]), ga)
assert R.find_repeat(claim("g3", D, "Acme Rocket streaming responses arrive.", ["https://acme.example/rocket-x"]), ga)
sg = SD(); sg.add(claim("g4", D, "Acme Rocket streaming.", ["https://acme.example/z"], "https://a.example"))
assert sg.check(claim("g5", D, "Acme Rocket streaming GA.", ["https://acme.example/z", "https://acme.example/z2"], "https://b.example")) is None
# profile, subreddit and listing pages are not specific
assert not R.specific("https://reddit.com/r/mcp") and not R.specific("https://arxiv.org/list/cs.AI/recent")
assert R.specific("https://reddit.com/r/mcp/comments/abc/title")
# follow-ups that repeat the old wording and add a fact are kept
fu = R.earlier_stories({"claims": [claim("f0", "2026-01-19", "Acme Rocket adds streaming responses.", ["https://acme.example/f0"])]}, D)
for new in ["Acme Rocket streaming responses now support Python and Go clients.", "Acme Rocket streaming responses rolls out to all users.",
            "Acme deprecates Rocket streaming responses.", "Acme removes Rocket streaming responses.", "Acme Rocket streaming responses are now free."]:
    assert not FR(claim("f1", D, new, ["https://acme.example/f1"]), fu), new
assert R.find_repeat(claim("f2", D, "Acme Rocket now has streaming responses.", ["https://acme.example/f2"]), fu)
cb = R.earlier_stories({"claims": [claim("c0", "2026-01-19", "Acme Rocket supports streaming responses for cloud builds.", ["https://acme.example/c0"])]}, D)
assert not FR(claim("c1", D, "Acme Rocket supports streaming responses for cloud builds and now adds encrypted backups.",
                               ["https://acme.example/c0", "https://acme.example/backups"]), cb)
# a changed price on the same page is news, cross-day and same day
pr = R.earlier_stories({"claims": [claim("m0", "2026-01-19", "Acme Rocket costs $8 for 100 requests.", ["https://acme.example/m"])]}, D)
assert not FR(claim("m1", D, "Acme Rocket costs $4 for 100 requests.", ["https://acme.example/m"]), pr)
sp = SD(); sp.add(claim("m2", D, "Acme Rocket costs $8.", ["https://acme.example/m"], "https://a.example"))
assert sp.check(claim("m3", D, "Acme Rocket costs $4.", ["https://acme.example/m"], "https://b.example")) is None
# GitHub: only one-story pages are specific
assert not R.specific("https://github.com/o/r/issues") and not R.specific("https://github.com/o/r/commits/main")
assert R.specific("https://github.com/o/r/pull/12") and R.specific("https://github.com/o/r/releases/tag/v2")
# GitLab: homepages, subgroups, trees and wikis are not specific; releases are
assert not R.specific("https://gitlab.com/group/subgroup/rocket") and not R.specific("https://gitlab.com/g/rocket/-/tree/main")
assert not R.specific("https://gitlab.com/g/rocket/-/wikis/home") and R.specific("https://gitlab.com/g/sub/rocket/-/releases/v1.2")
# a hyphenated license is not a version; malformed URLs never raise
assert R.analyze("Weights under apache-2.0.")[2] == set()
assert R.norm_url("https://[broken") == "" and not R.specific("https://[broken")
# must not match: vendor names alone
assert not rep("Anthropic says Claude Code got faster.", ["https://anthropic.example/news/fast"])
# missing fields never raise
assert not FR({"claim": None, "evidence": None}, st)
assert not FR({"claim": 5, "evidence": [None, {"url": 3}]}, st)

# history: read when given; malformed lines skipped and counted
hp = Path(tempfile.mkdtemp()) / "h.jsonl"
hp.write_text("\n".join(["not json", "null", "[]", '{"date": 5}', '{"date": "2026-01-1", "text": "x"}', "", '{"date": "2026-01-18", "text": 7, "urls": 3}',
                         json.dumps({"date": "2026-01-18", "text": "Bolt Labs shipped Quasar 3 with 12 agents.", "urls": "https://bolt.example/quasar-3"})]) + "\n")
logs = []
st2 = R.earlier_stories(ledger, D, str(hp), log=logs.append)
assert R.find_repeat(claim("h", D, "Quasar 3 from Bolt Labs has 12 agents.", ["https://bolt.example/quasar-3"]), st2)
assert any("skipped 5 malformed" in m for m in logs), logs
logs = []
R.earlier_stories(ledger, D, str(hp.parent / "missing.jsonl"), log=logs.append)
assert any("not found" in m for m in logs), logs

# same day: same evidence URL from a different page -> duplicate; from the same page, or another version -> kept
sd = SD()
c1 = claim("s1", D, "Vendor post on reviews.", ["https://v.example/post/1"], "https://a.example"); assert sd.check(c1) is None; sd.add(c1)
c2 = claim("s2", D, "Second fact, same post.", ["https://v.example/post/1"], "https://a.example"); assert sd.check(c2) is None; sd.add(c2)
assert R.SameDay.check(sd, claim("s3", D, "Vendor post on reviews, via a newsletter.", ["https://v.example/post/1"], "https://b.example"))
assert sd.check(claim("s5", D, "Gizmo pricing changed.", ["https://v.example/post/1"], "https://c.example")) is None
assert sd.check(claim("s4", D, "Unrelated.", ["https://v.example/post/2"], "https://b.example")) is None

# flags: a possible repeat that adds something new is returned with sure=False (kept, flagged for review)
r = R.find_repeat(claim("fl", D, "Acme Rocket streaming responses on Windows.", ["https://acme.example/fl"]), fu)
assert r and r[2] is False, r
assert not FR(claim("fl2", D, "Acme Rocket streaming responses on Bedrock.", ["https://acme.example/fl"]), fu)
# figure rule with a coincidental number and a new name is never a sure drop
wp = R.earlier_stories({"claims": [claim("w0", "2026-01-19", "Widgetly Pipelines API holds 300 repositories per pipeline.", ["https://w.example/0"])]}, D)
assert not FR(claim("w1", D, "Widgetly Pipelines now supports 300 concurrent jobs.", ["https://w.example/1"]), wp)
# price change quoting the old price is news
p8 = R.earlier_stories({"claims": [claim("q8", "2026-01-19", "Acme Rocket pricing is $8 for 100 requests.", ["https://acme.example/price"])]}, D)
assert not R.find_repeat(claim("q4", D, "Acme Rocket pricing is now $4, down from $8, for 100 requests.", ["https://acme.example/price"]), p8)
# a big new fact next to old figures is never a sure drop
bm = R.earlier_stories({"claims": [claim("b0", "2026-01-19", "Reflection released Beam with 501B total and 23B active parameters.", ["https://r.example/b"])]}, D)
assert not FR(claim("b1", D, "Reflection Beam has 501B total parameters and 23B active parameters; independent researchers discovered it exposes private training records through crafted prompts.", ["https://r.example/leak"]), bm)
# reviewers' round-8 examples: any new token, negation, vendor, plan, platform or delay is flagged, never dropped
ss = R.earlier_stories({"claims": [claim("e0", "2026-01-19", "Acme Rocket supports streaming responses for Max plans.", ["https://acme.example/e"])]}, D)
for new in ["Acme Rocket supports encrypted streaming responses for Max plans.", "Acme Rocket does not support streaming responses for Max plans.",
            "Acme Rocket no longer supports streaming responses for Max plans.", "Acme Rocket supports streaming responses for Max plans in Cursor.",
            "Acme Rocket supports streaming responses for Max plans on macOS.", "Acme Rocket supports streaming responses for Max plans on iOS.",
            "Acme delays Rocket streaming responses for Max plans.", "Acme Rocket will not get streaming responses for Max plans.",
            "Acme Rocket supports streaming responses for Pro plans."]:
    assert not FR(claim("e1", D, new, ["https://acme.example/e"]), ss), new
    se = SD(); se.add(claim("e2", D, "Acme Rocket supports streaming responses for Max plans.", ["https://acme.example/e"], "https://a.example"))
    assert not se.check(claim("e3", D, new, ["https://acme.example/e"], "https://b.example")), new
# the only sure drop: the same claim again, differing only in letter case and whitespace
assert not FR(claim("e4", D, "Acme Rocket supports streaming responses, for Max plans.", ["https://acme.example/e"]), ss)
assert FR(claim("e5", D, "ACME Rocket supports  streaming responses for Max plans.", ["https://acme.example/other"]), ss)
# round-9 examples: removed negations, swapped numbers, narrowed scope are never dropped
for old, new in [("Acme Rocket does not support streaming responses.", "Acme Rocket does support streaming responses."),
                 ("Acme Rocket supports streaming responses on Linux but not Windows.", "Acme Rocket supports streaming responses on Windows."),
                 ("Acme Rocket supports 300 input tokens and 100 output tokens.", "Acme Rocket supports 100 input tokens and 300 output tokens."),
                 ("Foo 2 launches for Pro users; Free users get it next month.", "Foo 2 launches for Free users."),
                 ("Acme Rocket latency is <100 ms.", "Acme Rocket latency is >100 ms."),
                 ("Acme Rocket throughput changed +10%.", "Acme Rocket throughput changed -10%.")]:
    o = R.earlier_stories({"claims": [claim("k0", "2026-01-19", old, ["https://acme.example/k"])]}, D)
    assert not FR(claim("k1", D, new, ["https://acme.example/k"]), o), new
    sk = SD(); sk.add(claim("k2", D, old, ["https://acme.example/k"], "https://a.example"))
    assert not sk.check(claim("k3", D, new, ["https://acme.example/k"], "https://b.example")), new
print("test_repeats: all passed")

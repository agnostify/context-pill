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
# identifier match: a shared CVE / GHSA id or package+version is the same story, whatever the wording or evidence URL
# (the repeat texts below are reconstructed: real texts live in the box work dir; earlier texts and evidence are real)
D2 = "2026-10-25"
php_old = claim("2026-10-23-4fdf9482", "2026-10-23", "Wiz Research says it found the PHP2Shell RCE bugs (CVE-2026-109618/109619) using AI, naming Wiz Atlas, Gemini 4 Argon and GPT-5.6 Sol. It credits OpenAI, Calif and the PHP team, and reported the bugs to PHP on Aug 24, 2026.",
                ["https://www.wiz.io/blog/php2shell-php-rce"])
lit_old = claim("2026-10-24-c98f14c8", "2026-10-24", "LiteLLM 1.107.0 was reported compromised in the project's own issue tracker. The report says it carried a credential-stealing .pth file and was built from a lookalike repo. The release is now gone from PyPI.",
                ["https://github.com/BerriAI/litellm/issues/47826", "https://pypi.org/pypi/litellm/1.107.0/json"])
idst = R.earlier_stories({"claims": [php_old, lit_old]}, D2)
idflag = lambda text, urls: R.find_repeat(claim("n", D2, text, urls), idst)
for text, urls in [("PHP patched two remote code execution flaws, CVE-2026-109618 and CVE-2026-109619, that Wiz found with AI-assisted bug hunting.", ["https://www.php.net/ChangeLog-8.php"]),
                   ("Two critical PHP RCE vulnerabilities (CVE-2026-109619 and CVE-2026-109618) were disclosed; PHP shipped fixes.", ["https://news.example/php-rce-fixes"]),
                   ("A malicious LiteLLM release, version 1.107.0, stole credentials via a .pth file before PyPI pulled it.", ["https://www.bleepingcomputer.com/news/security/litellm-pypi-compromise/"]),
                   ("Backdoored builds hit PyPI this week.", ["https://pypi.org/project/litellm/1.107.0/"])]:
    r = idflag(text, urls); assert r and r[2] is False, text          # flagged and kept, not dropped
# one shared CVE is enough even when other numbers differ; shorthand expands to both ids
assert idflag("CVE-2026-109618 now scores 9.8 under CVSS.", ["https://nvd.example/cve-1"])
assert R.analyze("bugs (CVE-2026-109618/109619)")[8] >= {"cve-2026-109618", "cve-2026-109619"}
assert R.analyze("see GHSA-abcd-1234-wxyz")[8] == {"ghsa-abcd-1234-wxyz"}
assert R.analyze("LiteLLM 1.107.0 broke")[8] == {"litellm@1.107.0"}
assert R.url_ids("https://pypi.org/pypi/litellm/1.107.0/json") == {"litellm@1.107.0"}
assert R.url_ids("https://github.com/BerriAI/litellm/releases/tag/v1.107.0") == {"litellm@1.107.0"}
# an exact duplicate is still a sure drop
assert FR(claim("n", D2, php_old["claim"], ["https://x.example/a"]), idst)
# must not match: a different CVE from another product, or another version of the same package
assert not idflag("Zlibco fixed CVE-2026-200001, a heap overflow in its decoder.", ["https://zlibco.example/sec"])
assert not idflag("LiteLLM 1.108.0 released with a new router option.", ["https://github.com/BerriAI/litellm/releases/tag/v1.108.0"])
# same-day: a shared CVE from a different page is flagged; an unrelated CVE is not
sdc = R.SameDay(); sdc.add(claim("i1", D2, "Wiz found CVE-2026-109618 in PHP.", ["https://a.example/1"], "https://a.example"))
assert R.SameDay.check(sdc, claim("i2", D2, "PHP fixes CVE-2026-109618.", ["https://b.example/2"], "https://b.example"))
assert R.SameDay.check(sdc, claim("i3", D2, "Zlibco fixes CVE-2026-200001.", ["https://c.example/3"], "https://c.example")) is None
# the repeats above with the published repeat texts (first parts real, rest reconstructed): still flagged, not dropped
for d, text, urls in [("2026-10-25", "LiteLLM release v1.107.0 was compromised: it shipped a credential-stealing .pth file and was pulled from PyPI.", ["https://news.example/litellm"]),
                      ("2026-10-24", "PHP 8.5.12, 8.4.27, 8.3.36 and 8.2.35 fix two critical bugs (CVE-2026-109618 and CVE-2026-109619).", ["https://www.php.net/releases/8_5_12.php"]),
                      ("2026-10-25", "PHP 8.5.12, 8.4.27, 8.3.36 and 8.2.35 are out and fix two critical bugs (CVE-2026-109618 and CVE-2026-109619).", ["https://www.php.net/ChangeLog-8.php"])]:
    r = R.find_repeat(claim("n", d, text, urls), R.earlier_stories({"claims": [php_old, lit_old]}, d)); assert r and r[2] is False, text

# rule (d): the same event past a version mismatch, flagged and kept (never dropped). Real missed repeats from the ledger.
def ev(old, new):
    """find_repeat for new (id, date, text, urls) against old alone."""
    return R.find_repeat(claim("n", new[1], new[2], new[3]), R.earlier_stories({"claims": [claim(*old)]}, new[1]))
missed = [
    # a tool's next release adds the same model to more catalogs
    (("2026-10-28-9b42804a", "2026-10-28", 'Codex CLI 0.165.0 adds a built-in Google Cloud Vertex AI provider (model_provider = "gcp") and GPT-6.1 Terra in its model catalogs. It also turns on memory v2 by default for fresh profiles.', ["https://github.com/openai/codex/releases/tag/rust-v0.165.0"]),
     ("n", "2026-10-30", "Codex CLI 0.166.0 adds GPT-6.1 Terra to its bundled, Vertex AI and Amazon Bedrock model catalogs.", ["https://github.com/openai/codex/releases/tag/rust-v0.166.0"]), "same model"),
    # same product, same event in other words (threads settle on PR); needs stemming and a short-token product name
    (("2026-10-26-49c9a624", "2026-10-26", "T3 Code nightly builds include a Working section that holds threads whose background work the agent is waiting on, and threads settle once their PR is merged.", ["https://github.com/pingdotgg/t3code/releases"]),
     ("n", "2026-10-30", "T3 Code links agent threads to pull requests and has a built-in PR review and merge view. With auto-settle enabled, a thread settles once every linked review is terminal.", ["https://github.com/pingdotgg/t3code/blob/main/docs/user/source-control.md"]), "shared phrase"),
    # same three money figures, same benefit, reported second-hand
    (("2026-10-29-2fd555c1", "2026-10-29", "OpenAI says eligible ChatGPT Pro subscribers can claim $100, $200 or $500 per month in API credits by tier. Business plans get $20 per Standard seat or $100 per Premium seat, pooled up to $500 per workspace.", ["https://www.youtube.com/watch?v=ujmyzWSj8IU"]),
     ("n", "2026-10-30", "Simon Willison reports that OpenAI now gives $100, $200 and $500 ChatGPT plan subscribers the same amount in API credits for one API organization, matching an earlier Anthropic benefit.", ["https://help.openai.com/en/articles/20001626-api-credits-benefit-for-eligible-chatgpt-subscriptions"]), "same figures"),
    # a PR's note, then the release that ships it: shared phrase "not optimized for Gemini 4"
    (("2026-10-17-dfea536b", "2026-10-17", "A Gemini CLI change merged Oct 16, 2026 (PR #29774) adds a README note that the CLI is not optimized for Gemini 4 Argon. The PR's bot summary says the note points users to the Antigravity CLI.", ["https://github.com/google-gemini/gemini-cli/pull/29774"]),
     ("n", "2026-10-28", "Gemini CLI v0.66.0 adds a banner stating that Gemini CLI is not optimized for Gemini 4. It also hardens SKILL.md frontmatter parsing and disables interactive credential and SSH prompts in its git environment.", ["https://github.com/google-gemini/gemini-cli/releases/tag/v0.66.0"]), "shared phrase"),
    # the same launch covered again with new figures, from a mirror domain
    (("2026-10-25-53c5cd3b", "2026-10-25", "Moonshot released Kimi K3.1 Pro, a 3T-class model with a 1M-token context window. It is on the Kimi API as kimi-k3.1-pro and kimi-k3.1-pro-highspeed, and in Kimi Code.", ["https://www.kimi.com/blog/kimi-k3-1-pro"]),
     ("n", "2026-10-27", "Moonshot announced Kimi K3.1 Pro, a 3T-class open-weight model with a 1M-token context, with full weights promised by Nov 4, 2026. Moonshot reports 65.7% on Terminal-Bench 4.0 versus 66.4% for Claude Opus 5.5.", ["https://kimi.ai/blog/kimi-k3-1-pro"]), "same model launch"),
]
for old, new, kind in missed:
    r = ev(old, new); assert r and r[0] == old[0] and r[2] is False and kind in r[1], (new[2], r)
assert R._stem("settles") == R._stem("settled") == R._stem("settle") and R._stem("threads") == "thread"
assert R.features("Moonshot released Kimi K3.1 Pro, a model.")["models"] == [(frozenset({"kimi", "pro"}), frozenset({"k3.1"}), True)]
assert R.features("A Gemini CLI change")["subject"] == {"gemini", "cli"} and R.features("T3 Code links")["subject"] == {"t3", "code"}
assert R.features("promised by Nov 4, 2026")["models"] == []                  # a date is not a model version
# rule (d) must not flag: different models from one vendor on different days, different releases with different features,
# a different named model with the same version, the same model in another kind of story, a different subject's run
for old, new in [
    ("Moonshot released Kimi K3.1 Pro, a 3T-class model with a 1M-token context window.", "Moonshot released Kimi K3.2, a 3T-class model with a 1M-token context window and faster decoding."),
    ("Acme released Rocket 2.0, a 500B MoE with a 1M context.", "Acme released Comet 1.0, a 500B MoE with a 1M context."),
    ("Codex CLI 0.163.0 runs independent read-only tools in parallel and lets you steer Codex while it responds.", "Codex CLI 0.165.0 adds a built-in Google Cloud Vertex AI provider and turns on memory v2 by default for fresh profiles."),
    ("Codex CLI 0.165.0 adds a built-in Google Cloud Vertex AI provider and GPT-6.1 Terra in its model catalogs.", "Codex CLI 0.166.0 adds a /review command that summarizes uncommitted changes before a commit."),
    ("OpenAI released GPT-6.1 Terra, API id gpt-6.1-terra, priced at $1 per million input tokens, $0.05 cached input and $5 output.",
     "OpenAI released GPT-6.1 Sol, API id gpt-6.1-sol, priced at $1 per million input tokens, $0.05 cached input and $5 output."),
    ("Moonshot released Kimi K3.1 Pro, a 3T-class model with a 1M-token context window.", "Artificial Analysis gives Kimi K3.1 Pro a score of 55 on its Intelligence Index, ranking it 4th of 215 models listed."),
    ("Acme Rocket 2.1 adds streaming responses and batch mode.", "Acme Rocket 3 adds streaming responses and batch mode."),
    ("GPT-6.1 Terra is generally available in GitHub Copilot for Pro and Business users.", "Grok Code Fast is generally available in GitHub Copilot for Pro and Business users."),
    ("Kimi K3.1 Pro scores 65.7% on Terminal-Bench, beating Claude Opus 5.5.", "Qwen 4 Max scores 61.2% on Terminal-Bench, beating Claude Opus 5.5."),
]:
    assert not ev(("o", "2026-10-25", old, ["https://x.example/old"]), ("n", "2026-10-27", new, ["https://x.example/new"])), new
# fix round 1: a shared generic or vendor word is not a shared subject; variants and lowercase ids are part of a model's identity
for old, new in [
    ("Gemini CLI adds support for custom system prompts in its settings file.", "Codex CLI adds support for custom system prompts in its settings file."),
    ("Claude Code links agent threads to pull requests; a thread settles once every linked review is terminal.",
     "T3 Code links agent threads to pull requests; a thread settles once every linked review is terminal."),
    ("Researchers found prompt injection attacks hijack coding agents.", "Researchers found prompt injection attacks hijack browser agents."),
    ("OpenAI released GPT-6.1 Terra with improved tool calling accuracy.", "OpenAI released o5-mini with improved tool calling accuracy."),
]:
    assert not ev(("o", "2026-10-25", old, ["https://x.example/old"]), ("n", "2026-10-27", new, ["https://x.example/new"])), new
assert R.features("Researchers found prompt injection")["subject"] == frozenset()
assert R.features("OpenAI released o5-mini today")["models"] == [(frozenset({"o", "mini"}), frozenset({"5"}), True)]
assert R.features("Acme revenue in Q3 2026 rose")["models"] == [] and R.features("Q3 2026 results")["models"] == []
assert not R._same_model((frozenset({"terra"}), frozenset({"6.1"}), False), (frozenset({"terra", "mini"}), frozenset({"6.1"}), False))

# fix round 2: variant checks live in rule (d) only; rules (a)-(c) behave as before (these were flagged before rule (d))
def evd(old, new):
    """Rule (d) alone: event_repeat for new text against old text."""
    return R.event_repeat(R.analyze(new), R.features(new), R.analyze(old), R.features(old))
r = R.find_repeat(claim("n", "2026-10-27", "Google released Gemini 4 Flash for low-latency agents.", ["https://blog.example/flash"]),
                  R.earlier_stories({"claims": [claim("o", "2026-10-25", "Google released Gemini 4 Flash and Gemini 4 Flash Lite for low-latency agents.", ["https://blog.example/flash"])]}, "2026-10-27"))
assert r and r[2] is False and "same evidence URL" in r[1], r
assert not R.conflict(R.analyze("Kimi K3.1 Pro is out."), R.analyze("Kimi K3.1 is out."))   # variants aren't a conflict in (a)-(c)
for old, new in [
    ("Moonshot released Kimi K3.1 Pro, a 3T-class model with a 1M-token context window for reasoning.",
     "Moonshot released Kimi K3.1, a 3T-class model with a 1M-token context window for coding."),
    ("Google released Gemini 4 Argon Flash with lower latency for agent tool calls.", "Google released Gemini 4 Argon Pro with lower latency for agent tool calls."),
    ("OpenAI released GPT-6.1 Terra with improved tool calling accuracy.", "OpenAI released GPT-6.1 Terra Mini with improved tool calling accuracy."),
]:
    r = ev(("o", "2026-10-25", old, ["https://x.example/old"]), ("n", "2026-10-27", new, ["https://x.example/new"]))
    assert r and "possible same story" not in r[1], (new, r)       # rule (b), as before
    assert evd(old, new) is None, new
# sizes and lowercase variants are part of a model's identity; a vendor-only subject needs the product to agree
for old, new in [
    ("Meta released Llama 5 8B with a 128K context window for on-device use.", "Meta released Llama 5 70B with a 128K context window for on-device use."),
    ("Alibaba released Qwen 4 32B under Apache 2.0.", "Alibaba released Qwen 4 235B under Apache 2.0."),
    ("Google released Gemini 4 Argon flash optimized for low latency and rapid responses.",
     "Google announced Gemini 4 Argon pro designed for complex reasoning and advanced mathematics."),
    ("Google says Gemini CLI adds support for custom system prompts in its settings file.",
     "Google says Android Studio adds support for custom system prompts in its settings file."),
]:
    assert not ev(("o", "2026-10-25", old, ["https://x.example/old"]), ("n", "2026-10-27", new, ["https://x.example/new"])), new
    assert evd(old, new) is None, new
assert "same model launch" in evd("Meta released Llama 5 8B with a 128K context window.", "Meta announced Llama 5 8B, now with 4-bit weights.")
assert "shared phrase" in evd("Google says Gemini CLI adds support for custom system prompts in its settings file.",
                              "Google says Gemini CLI v0.67.0 adds support for custom system prompts in its settings file.")
assert R.features("Meta released Llama 5 8B today")["models"] == [(frozenset({"llama", "8b"}), frozenset({"5"}), True)]
assert R.features("Google released Gemini 4 Argon flash optimized")["models"][0][0] == {"argon", "flash"}
assert R.features("Google says Gemini CLI adds")["subject"] == {"gemini", "cli"}
assert R.features("Google says it will")["subject"] == frozenset()
assert R.features("OpenAI said r2 is out")["models"] == []                    # bare lowercase r2 is not a model id

# a stale history is logged (the private history once stopped at 2026-10-16); a fresh one is not
hs = Path(tempfile.mkdtemp()) / "h.jsonl"
hs.write_text(json.dumps({"date": "2026-10-16", "text": "Old story."}) + "\n")
logs = []; R.earlier_stories({"claims": []}, "2026-10-30", str(hs), log=logs.append)
assert any("stale" in m and "2026-10-16" in m for m in logs), logs
logs = []; R.earlier_stories({"claims": []}, "2026-10-18", str(hs), log=logs.append)
assert not any("stale" in m for m in logs), logs
logs = []; R.earlier_stories({"claims": []}, "2026-10-16", str(hs), log=logs.append)   # no entry before the run date
assert any("stale (no entries before 2026-10-16)" in m for m in logs) and not any("none" in m for m in logs), logs
print("test_repeats: all passed")

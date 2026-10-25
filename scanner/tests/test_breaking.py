"""Breaking AI News: ranking, filters, dedupe, stable ids, fallbacks and rendering. No network.
Run: python -m scanner.tests.test_breaking"""
import json, os, shutil, tempfile, datetime as dt
from pathlib import Path
from scanner import breaking as B, render, gate

REPO = Path(__file__).resolve().parents[2]
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
DATE = NOW.astimezone(B.PT).date().isoformat()


def ago(h):
    return (NOW - dt.timedelta(hours=h)).isoformat()


def red(pid, title, up, com, link=None, h=2, sub="LocalLLaMA"):
    return {"platform": "reddit", "id": f"reddit:{pid}", "title": title, "url": f"https://www.reddit.com/r/{sub}/comments/{pid}/x/",
            "created": ago(h), "raw": {"upvotes": up, "comments": com}, "subreddit": sub, "links": [link] if link else [],
            "external": bool(link), "understated": True, "aggregator": False}


def xp(pid, title, likes, link=None, agg=False, h=2):
    return {"platform": "x", "id": f"x:{pid}", "title": title, "url": None, "created": ago(h),
            "raw": {"likes": likes, "reposts": likes // 10, "replies": likes // 20, "views": likes * 50},
            "links": [link] if link else [], "aggregator": agg, "handle": "someone"}


def comp(url, title, h=3):
    return {"platform": "company", "id": "url:" + B.repeats.norm_url(url), "title": title, "url": url, "created": ago(h),
            "raw": {}, "links": [url], "company": "OpenAI", "aggregator": False}


def setup():
    d = Path(tempfile.mkdtemp(prefix="cp-breaking-"))
    repo, work = d / "repo", d / "work"
    (repo / "schema").mkdir(parents=True); work.mkdir()
    shutil.copy(REPO / "sources.yaml", repo / "sources.yaml")
    for f in ("drop.schema.json", "breaking.schema.json"):
        shutil.copy(REPO / "schema" / f, repo / "schema" / f)
    return d, repo, work


def go(repo, work, fetchers, dry=False, date=DATE, now=NOW):
    return B.run(date, 24, dry, repo=repo, work=work, log=lambda m: None, fetchers=fetchers, check_links=False, now=now)


def const(items, note=None):
    return lambda: (list(items), note)


X_TITLES = ["OpenAI ships GPT-6.2 with a new reasoning mode", "Anthropic releases Claude Opus 5.6 for enterprise agents",
            "Nvidia unveils Rubin Ultra GPUs for AI datacenters", "Meta open-sources Llama 6 weights under a new license",
            "xAI raises $30 billion for Grok training clusters"]
R_TITLES = ["Qwen 4 open weights beat every local model on coding benchmarks", "Mistral launches Codestral 3 with 1M context for agents",
            "DeepSeek V5 paper reports training cost under $10 million", "Google DeepMind announces Gemini 4 Ultra availability",
            "Perplexity acquires a browser startup to build AI agents"]


def mixed():
    xs = [xp(f"x{n}", t, 100000 // (n + 1), link=f"https://openai.com/index/story-{n}") for n, t in enumerate(X_TITLES)]
    rs = [red(f"r{n}", t, 1000 // (n + 1), 200 // (n + 1), link=f"https://www.theverge.com/ai/story-{n}") for n, t in enumerate(R_TITLES)]
    return xs, rs


tmp, repo, work = setup()
try:
    # 1. normalization: X raw counts ~100x Reddit's still give a mixed ranking (per-platform percentiles)
    xs, rs = mixed()
    code, b = go(repo, work, {"x": const(xs), "reddit": const(rs), "company": const([])}, dry=True)
    assert code == 0, code
    top4 = {p for s in b["stories"][:4] for p in s["platforms"]}
    assert top4 == {"x", "reddit"}, [(s["title"], s["platforms"], s["score"]) for s in b["stories"]]
    assert b["ranking_basis"]["reddit"].startswith("within-day"), b["ranking_basis"]
    s = next(s for s in b["stories"] if "reddit" in s["platforms"])
    assert s["counts"]["reddit"]["upvotes_understated"] is True and s["primary_kind"] == "reddit"
    assert all(not B.util.blocked(l["url"]) for s in b["stories"] for l in s["links"]) and all(s["primary_url"] for s in b["stories"])

    # 2. threshold and cap (env overrides), empty on a quiet day
    os.environ["BREAKING_MAX"] = "3"
    code, b = go(repo, work, {"x": const(xs), "reddit": const(rs), "company": const([])}, dry=True)
    assert len(b["stories"]) == 3 and [s["rank"] for s in b["stories"]] == [1, 2, 3]
    del os.environ["BREAKING_MAX"]
    os.environ["BREAKING_MIN_SCORE"] = "5"
    code, b = go(repo, work, {"x": const(xs), "reddit": const(rs), "company": const([])}, dry=True)
    assert code == 0 and b["stories"] == [], b["stories"]
    assert render.breaking_md(b) == "", "an empty section is hidden"
    del os.environ["BREAKING_MIN_SCORE"]
    assert all(s["score"] >= 0.6 for s in go(repo, work, {"x": const(xs), "reddit": const(rs), "company": const([])}, dry=True)[1]["stories"])

    # 3. AI filter on aggregator accounts; Reddit drops memes and questions
    assert not B.keep(xp("a1", "BREAKING: Fed cuts rates by 50 basis points", 9000, agg=True))
    assert B.keep(xp("a2", "JUST IN: OpenAI is raising $40 billion at a $500 billion valuation", 9000, agg=True))
    assert B.keep(xp("a3", "Fed cuts rates", 9000, agg=False)), "non-aggregator company accounts skip the AI filter"
    assert not B.keep(red("m1", "Uh oh", 5000, 300))
    assert not B.keep(red("m2", "What is the best local model for coding right now?", 500, 300))
    assert B.keep(red("m3", "Kimi K3.1 removes cyberattack training data from open weight release", 700, 250))
    assert B.keep(red("m4", "lol", 10, 1, link="https://www.reuters.com/tech/ai-story"))

    # 4. dedupe (same URL and same headline) and stable ids across refreshes
    u = "https://openai.com/index/gpt-6-2"
    items = [red("d1", "OpenAI ships GPT-6.2 with a new reasoning mode", 900, 300, link=u),
             red("d2", "GPT-6.2 is out: OpenAI's new reasoning mode", 400, 100, link=u, sub="OpenAI"),
             red("d3", "Mistral launches Codestral 3 with 1M context window for coding agents", 800, 200, link="https://mistral.ai/news/codestral-3"),
             red("d4", "Mistral launches Codestral 3, 1M context window for coding agents", 300, 90, sub="singularity", h=1)]
    code, b1 = go(repo, work, {"x": const([]), "reddit": const(items), "company": const([comp(u, "Introducing GPT-6.2")])})
    assert code == 0, code
    g = next(s for s in b1["stories"] if s["primary_url"] == u)
    assert g["counts"]["reddit"]["posts"] == 2 and g["counts"]["reddit"]["upvotes"] == 1300 and g["counts"]["reddit"]["comments"] == 400, g
    assert g["primary_kind"] == "company" and g["title"] == "Introducing GPT-6.2" and set(g["platforms"]) == {"reddit", "company"}
    m = next(s for s in b1["stories"] if "Codestral" in s["title"])
    assert m["counts"]["reddit"]["posts"] == 2, "same headline without a shared URL clusters too"
    assert m["primary_kind"] == "company" and m["primary_url"] == "https://mistral.ai/news/codestral-3", "company domain link is primary"
    ids1 = {s["primary_url"]: s["id"] for s in b1["stories"]}
    items2 = [dict(i, raw={"upvotes": i["raw"]["upvotes"] * 3, "comments": i["raw"]["comments"] * 2}) for i in items]
    items2.append(red("d5", "OpenAI GPT-6.2 benchmarks thread", 50, 10, link=u))
    code, b2 = go(repo, work, {"x": const([]), "reddit": const(items2), "company": const([])}, now=NOW + dt.timedelta(hours=3))
    ids2 = {s["primary_url"]: s["id"] for s in b2["stories"]}
    assert all(ids2.get(k) == v for k, v in ids1.items()), (ids1, ids2)
    assert json.loads((repo / "breaking.json").read_text())["stories"] == b2["stories"]

    # 5. missing platforms: rank with what's left, note it
    def boom():
        raise RuntimeError("down")
    xs, rs = mixed()
    code, b = go(repo, work, {"x": const([], "no X API credentials and no connector file"), "reddit": boom, "company": boom}, dry=True)
    assert code == 4 and b is None, "nothing fetched at all -> keep the last good section"
    code, b = go(repo, work, {"x": const(xs), "reddit": boom, "company": const([])}, dry=True)
    assert code == 0 and b["stories"] and {m["platform"] for m in b["platforms_missing"]} == {"Reddit"}
    assert "Missing from this update: Reddit" in render.breaking_md(b)

    # 6. a failed refresh keeps the last good section (fetch failure, gate failure, crash)
    good = (repo / "breaking.json").read_text()
    good_md = (repo / "breaking.md").read_text()
    code, _ = go(repo, work, {"x": boom, "reddit": boom, "company": boom})
    assert code == 4 and (repo / "breaking.json").read_text() == good
    real = B.light_gate
    B.light_gate = lambda *a, **k: ["forced failure"]
    code, _ = go(repo, work, {"x": const([]), "reddit": const(rs), "company": const([])})
    B.light_gate = real
    assert code == 2 and (repo / "breaking.json").read_text() == good and (repo / "breaking.md").read_text() == good_md

    # 7. light gate catches counts that don't match the fetched data, and X links
    raw = {i["id"]: i for i in items2}                 # breaking.json holds the second refresh
    schema = json.loads((repo / "schema" / "breaking.schema.json").read_text())
    bad = json.loads(good)
    st = next(s for s in bad["stories"] if s["primary_url"] == u)
    members = {st["id"]: ["reddit:d1", "reddit:d2", "reddit:d5"]}
    bad["stories"] = [st]
    assert B.light_gate(bad, raw, members, schema, check_links=False) == [], B.light_gate(bad, raw, members, schema, check_links=False)
    st["counts"]["reddit"]["upvotes"] += 1
    assert any("don't match" in p for p in B.light_gate(bad, raw, members, schema, check_links=False))
    st["counts"]["reddit"]["upvotes"] -= 1; st["links"].append({"kind": "outlet", "url": "https://www.instagram.com/p/abc123/"})
    assert any("X/blocked" in p for p in B.light_gate(bad, raw, members, schema, check_links=False))

    # 8. X-only stories without a public non-X link are left out (X links are never published)
    code, b = go(repo, work, {"x": const([xp("o1", "OpenAI teases a new model tonight", 50000)]), "reddit": const(rs), "company": const([])}, dry=True)
    assert all("x" not in s["platforms"] or s["primary_kind"] != "x" for s in b["stories"]) and "X-only" in b["notes"][0]

    # 9. cross-day: a story featured yesterday isn't repeated today
    yday = (dt.date.fromisoformat(DATE) - dt.timedelta(days=1)).isoformat()
    (work / "breaking" / "archive").mkdir(parents=True, exist_ok=True)
    (work / "breaking" / "archive" / f"{yday}.json").write_text(json.dumps({"keys": {"b-0000000000": ["url:" + B.repeats.norm_url(u)]}}))
    shutil.rmtree(work / "breaking" / "state")
    code, b = go(repo, work, {"x": const([]), "reddit": const(items), "company": const([])}, dry=True)
    assert all(s["primary_url"] != u for s in b["stories"]) and any("Codestral" in s["title"] for s in b["stories"])

    # 10. rendering: the Breaking block sits under the agent warning, above "Checked claims"; schema-valid; gate passes
    render.write(repo, DATE, 10, [], {}, 0, 0)
    md = (repo / "latest.md").read_text()
    w, bk, ck = md.index("> **For agents:**"), md.index("## Breaking AI News"), md.index("## Checked claims")
    assert w < bk < ck, (w, bk, ck)
    assert "Ranked by engagement, not verified." in md[bk:ck]
    lj = json.loads((repo / "latest.json").read_text())
    assert lj["breaking"]["stories"] == json.loads((repo / "breaking.json").read_text())["stories"]
    probs = gate.check(repo)
    assert probs == [], probs
    # the 3-hourly splice replaces only the Breaking block, and an empty update hides it
    before_tail = md[ck:]
    empty = dict(lj["breaking"], stories=[])
    render.splice_breaking(repo, empty)
    md2 = (repo / "latest.md").read_text()
    assert "## Breaking AI News" not in md2 and md2.endswith(before_tail) and "breaking" not in json.loads((repo / "latest.json").read_text())
    render.splice_breaking(repo, lj["breaking"])
    md3 = (repo / "latest.md").read_text()
    assert md3.count("## Breaking AI News") == 1 and md3.endswith(before_tail)
    # a latest.md from before this feature (no markers) gets the block under the warning
    old = md.replace(md[md.index(render.B_START):md.index(render.B_END) + len(render.B_END) + 1], "")
    (repo / "latest.md").write_text(old)
    render.splice_breaking(repo, lj["breaking"])
    md4 = (repo / "latest.md").read_text()
    assert md4.index("> **For agents:**") < md4.index("## Breaking AI News") < md4.index("## Checked claims")
    # a stale breaking.json (older than a day) is not shown by the daily render
    stale = dict(lj["breaking"], updated_at=(NOW - dt.timedelta(hours=30)).isoformat())
    (repo / "breaking.json").write_text(json.dumps(stale))
    render.write(repo, DATE, 10, [], {}, 0, 0)
    assert "## Breaking AI News" not in (repo / "latest.md").read_text()
    # markdown in titles can't break the link
    assert "\\[" in render.breaking_md(dict(lj["breaking"], stories=[dict(lj["breaking"]["stories"][0], title="Odd [title] here")]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("breaking tests: OK")

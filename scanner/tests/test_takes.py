"""Offline fixture for builder takes, from saved extraction output to the public drop and ledger."""
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest import mock

import yaml

from scanner import extract, gate, quotes, run, trust, util

DAY = "2026-11-25"
SPEAKER = "Ada Builder"
EXCERPT = "Give the coding agent one example first."
XLINK = "https://" + "x" + ".com/someone/" + "sta" + "tus/" + "1234567890" + "1"   # built from parts: the gate scans this file too


def take(url, claim, sid="builder", **fields):
    return {
        "category": "take", "source_id": sid, "item_url": url, "claim": claim,
        "speaker": SPEAKER, "said_on": "2026-11-18", "excerpt": EXCERPT, "excerpt_url": url,
        "verification": {"status": "attributed"}, "try_this": "Write one example.",
    } | fields


def take_id(c):
    return DAY + "-" + format(run._h(f"{c['source_id']}|{c['item_url']}|{c['claim']}|take|{c['speaker']}"), "x")[:8]


def fixture(root, source_ids=("builder",)):
    repo = root / "repo"
    work = root / "work"
    raw = work / "raw" / DAY
    (raw / "pages").mkdir(parents=True)
    (repo / "ledger").mkdir(parents=True)
    shutil.copytree(Path(__file__).resolve().parents[2] / "schema", repo / "schema")
    sources = [{"id": sid, "name": sid, "home_url": "https://builder.example", "kind": "expert",
                "enabled": True, "seed": sid == "builder", "feeds": []} for sid in source_ids]
    (repo / "sources.yaml").write_text(yaml.safe_dump({"sources": sources}))
    (repo / "CHANGELOG-sources.md").write_text("# Source changes\n")
    return repo, work, raw


def publish(repo, work, raw, claims, items):
    (raw / "claims.raw.json").write_text(json.dumps(claims))
    (raw / "items.json").write_text(json.dumps(items))
    with mock.patch.object(run, "REPO", repo), mock.patch.object(run, "WORK", work), \
         mock.patch.object(sys, "argv", ["run", "--date", DAY, "--skip-extract"]), \
         mock.patch.object(util, "get", side_effect=AssertionError("unexpected network request")), \
         mock.patch.object(util, "url_alive", return_value=(True, 200)) as alive, \
         mock.patch.object(extract, "run_llm", side_effect=AssertionError("unexpected model call")), \
         mock.patch.object(extract, "audit", return_value={}) as audit:
        assert run.main() == 0
    drop = json.loads((repo / "latest.json").read_text())["items"]
    ledger = json.loads((repo / "ledger" / "claims.json").read_text())
    return drop, ledger, alive, audit


def test_quotes():
    assert quotes.quote_found('She said "don\'t ship - yet"', "SHE said \u201cdon\u2019t\n ship \u2014 yet\u201d.")
    assert quotes.quote_found("AI", "\uff21\uff29")
    assert not quotes.quote_found("test", "contest testing")
    assert not quotes.quote_found("one example", "one short example")
    assert not quotes.quote_found("", EXCERPT)
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "page.txt"
        item = {"snapshot": str(path), "summary": EXCERPT}
        assert not quotes.matches_item(EXCERPT, item, pages)
        path.write_bytes(b"\xff")
        assert not quotes.matches_item(EXCERPT, item, pages)
        path.write_text("x " * 12000 + EXCERPT)
        assert not quotes.matches_item(EXCERPT, item | {"summary": "x " * 750 + EXCERPT}, pages)
        assert quotes.matches_item(EXCERPT, item, pages)
        # the speaker must be named next to the quote; a name only in the byline counts only for the page's own author
        page = f"By {SPEAKER}. " + "filler " * 200 + "Comments. Bob Troll wrote: Never use type hints."
        path.write_text(page)
        troll = "Never use type hints."
        assert quotes.matches_item(troll, item, pages, "Bob Troll")
        assert not quotes.matches_item(troll, item, pages, SPEAKER)
        assert quotes.matches_item(troll, item, pages, SPEAKER, byline_ok=True)      # known limit, see scanner/breakage.md
        assert not quotes.matches_item(troll, item, pages, "Carol Nobody", byline_ok=True)
        assert not quotes.matches_item(troll, item, pages, "AI")
        inside = "Boris Cherny says plan mode is overrated for small fixes."
        path.write_text("Jane Roe wrote: " + inside)
        assert not quotes.matches_item(inside, item, pages, "Boris Cherny")
        assert quotes.matches_item(inside, item, pages, "Jane Roe")
        for bad in ("Boris](javascript:alert(1)) Cherny", "Ada\nBuilder", "Ada  Builder", "Ada <b>Builder</b>"):
            assert not quotes.valid_speaker(bad), bad
        assert quotes.valid_speaker("Mary-Jane O'Neil") and quotes.valid_speaker("J. R. Smith")
        path.write_text("Posted Nov. 18, 2026 by someone")
        assert quotes.date_on_item("2026-11-18", item, pages) and not quotes.date_on_item("2026-11-17", item, pages)
        assert quotes.date_on_item("2026-11-17", {"published": "2026-11-17T09:00:00+00:00"}, pages)
        path.write_text("filler " * 200 + "Back on November 18, 2026 we shipped it.")
        assert not quotes.date_on_item("2026-11-18", item, pages)          # a date in the body is not the post's date
        path.write_text(f"{EXCERPT} " + "filler " * 30 + SPEAKER)
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER)      # name too far after the quote
        path.write_text(f"\u201c{EXCERPT}\u201d said {SPEAKER}.")
        assert quotes.matches_item(EXCERPT, item, pages, SPEAKER)
        assert quotes.is_source_person("Armin Ronacher", "Armin Ronacher") and not quotes.is_source_person("Bob Troll", "Armin Ronacher")


def test_take_guards():
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp), ("builder", "lab"))
        reg = yaml.safe_load((repo / "sources.yaml").read_text())
        reg["sources"][1] |= {"kind": "lab", "take_paths": ["/engineering/"]}
        (repo / "sources.yaml").write_text(yaml.safe_dump(reg))
        page = f"By {SPEAKER}. November 18, 2026. {EXCERPT}"
        items, claims = [], []
        def add(sid, url, published=DAY, claim=None, **fields):
            snap = raw / "pages" / f"p{len(items)}.txt"; snap.write_text(page)
            items.append({"source_id": sid, "url": url, "snapshot": str(snap), "published": published, "summary": ""})
            claims.append(take(url, claim or f"Tip at {url}", sid=sid, **fields))
            return take_id(claims[-1])
        ok_lab = add("lab", "https://lab.example/engineering/a")
        news_lab = add("lab", "https://lab.example/news/b")
        late = add("builder", "https://builder.example/c", published="2026-11-10")
        xlink = add("builder", "https://builder.example/d", claim="See " + XLINK + " for more")
        drop, *_ = publish(repo, work, raw, claims, items)
        assert [d["id"] for d in drop] == [ok_lab], drop
        log = (raw / "run.log").read_text()
        assert f"take dated after its feed entry (2026-11-18 > 2026-11-10): {late}" in log
        assert "failed the publication scan" in log and xlink in log
        assert log.count("named speaker or original publication date") == 1   # the lab news page
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp), ("builder",))
        url = "https://builder.example/f"
        snap = raw / "pages" / "p.txt"; snap.write_text(f"By {SPEAKER}. November 18, 2026. {EXCERPT}")
        items = [{"source_id": "builder", "url": url, "snapshot": str(snap), "published": DAY, "summary": ""}]
        bad_row = take(url, "Malformed", item_url=[url])
        heading = take(url, "Line one\n## Fake heading [link](https://evil.example)")
        drop, *_ = publish(repo, work, raw, [bad_row, heading], items)
        assert "malformed extractor row" in (raw / "run.log").read_text() and len(drop) == 1
        md = (repo / "latest.md").read_text()
        assert "\n## Fake heading" not in md and "\\[link\\]" in md
        opinion = drop[0]
        assert any("status at publication" in x for x in gate.check_take(opinion | {"status_at_publication": "verified"}, "l"))
        assert any("only for takes" in x for x in gate.check_take(opinion | {"category": "release", "verification": {"status": "verified"}, "status_at_publication": "attributed"}, "l"))
        assert gate.check_take(opinion | {"excerpt_url": "http://builder.example/f", "item_url": "http://builder.example/f"}, "l")
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp), ("builder",))
        snap = raw / "pages" / "p.txt"
        snap.write_text(f"By {SPEAKER}. " + "filler " * 200 + EXCERPT)
        url = "https://builder.example/e"
        items = [{"source_id": "builder", "url": url, "snapshot": str(snap), "published": "2026-11-18", "summary": ""}]
        far = take(url, "Name only in the byline, source not opted in")
        drop, *_ = publish(repo, work, raw, [far], items)
        assert drop == [] and "quote not found" in (raw / "run.log").read_text()
        snap.write_text(f"By {SPEAKER}. {EXCERPT}")
        items[0]["published"] = DAY
        drop, *_ = publish(repo, work, raw, [take(url, "No date anywhere")], items)
        assert drop == [] and "original date not found" in (raw / "run.log").read_text()


def test_publication():
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp), ("builder", "second"))
        url = "https://builder.example/post"
        snapshot = raw / "pages" / "page.txt"
        snapshot.write_text(f"By {SPEAKER}. Originally published November 18, 2026. {EXCERPT}")
        items = [{"source_id": "builder", "url": url, "snapshot": str(snapshot), "published": DAY, "summary": ""}]
        good = take(url, "Start with an example when prompting a coding agent")
        missing_quote = take(url, "The unsupported quotation", excerpt="This was never said.")
        no_quote = take(url, "A missing quotation", excerpt=None)
        unnamed_guest = take(url, "An unnamed guest tip", speaker=None)
        invented_guest = take(url, "A misattributed guest tip", speaker="Unknown Guest")
        other_url = take(url, "A quote attached to the wrong page", excerpt_url=url + "-other")
        other_source = take(url, "A quote attached to the wrong source", sid="second")
        fact = take(url, "Example mode shipped in 1.2", category="release", excerpt="Fabricated quotation.",
                    verification={"status": "verified", "method": "release notes"},
                    evidence=[{"url": url + "-release-notes", "kind": "primary"}])
        claims = [missing_quote, no_quote, unnamed_guest, invented_guest, other_url, other_source, good, fact]
        drop, ledger, alive, audit = publish(repo, work, raw, claims, items)
        assert [d["category"] for d in drop] == ["release", "take"], drop
        checked, opinion = drop
        assert checked["excerpt"] is None and checked["excerpt_url"] is None
        assert checked["verification"]["status"] == "verified" and checked["scorable"]
        assert opinion["speaker"] == SPEAKER and opinion["said_on"] == "2026-11-18"
        assert opinion["excerpt"] == EXCERPT and opinion["excerpt_url"] == url
        assert opinion["verification"]["status"] == "attributed" and opinion["verification"]["method"] == gate.TAKE_METHOD
        assert opinion["try_this"] is None and opinion["scorable"] is False and opinion["recheck_on"] is None
        assert [x["id"] for x in audit.call_args.args[0]] == [checked["id"]]
        alive.assert_called_once_with(fact["evidence"][0]["url"])
        saved = next(c for c in ledger["claims"] if c["category"] == "take")
        assert saved["speaker"] == SPEAKER and saved["said_on"] == opinion["said_on"]
        assert saved["status_at_publication"] == "attributed"
        log = (raw / "run.log").read_text()
        assert log.count("quote not found") == 5 and "named speaker" in log
        assert take_id(missing_quote) in log
        md = (repo / "latest.md").read_text()
        assert "1 checked claims below, 0 listed as not verified." in md
        checked_md, take_md = md.split("## Builder takes (opinion; we checked who said it, not whether it's right)")
        assert "## Checked claims" in checked_md and "re-check" not in take_md
        assert f'"{EXCERPT}" ([{SPEAKER}]({url}), 2026-11-18)' in take_md
        assert not gate.check_item(opinion, "fixture")
        assert any("only for takes" in p for p in gate.check_item(opinion | {"category": "release"}, "fixture"))
        for fields in ({"excerpt": "x" * 301}, {"excerpt": "word " * 26}, {"said_on": "2026-02-30"}, {"speaker": " "},
                       {"said_on": "2026-12-01"}, {"speaker": "AI"}, {"speaker": "https://example.com/ada"},
                       {"try_this": "Run this."}):
            assert gate.check_item(opinion | fields, "fixture"), fields
        schema = json.loads((repo / "schema" / "drop.schema.json").read_text())
        errors = []
        gate._validate(checked | {"recheck_on": None}, schema["$defs"]["item"], schema, "fixture", errors)
        assert errors == ["fixture.recheck_on: wrong type"], errors
        print("fixture: attributed take published; missing quote rejected; checked fact kept with its invalid excerpt removed")


def test_extraction_stages_items_without_missing_snapshots():
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp))
        stage = Path(tmp) / "stage"
        stage.mkdir()
        item = {"source_id": "builder", "snapshot": str(raw / "pages" / "missing.txt"), "summary": EXCERPT}

        def fake_extractor(_prompt, cwd, *, stdout, **_kwargs):
            assert json.loads((cwd / "items-for-llm.json").read_text()) == [{"source_id": "builder", "summary": EXCERPT}]
            stdout.write("===JSON===\n[]\n===END===\n")

        with mock.patch.object(extract, "stage", return_value=stage), mock.patch.object(extract, "run_llm", fake_extractor):
            assert extract.extract([item], repo, work, DAY, lambda _: None, 41) == []


def test_caps_and_audit_paths():
    with tempfile.TemporaryDirectory() as tmp:
        sources = ("builder", "second", "third", "fourth", "fifth")
        repo, work, raw = fixture(Path(tmp), sources)
        claims, items = [], []
        for sid in sources:
            for n in range(3):
                url = f"https://builder.example/{sid}/{n}"
                claims.append(take(url, f"Tip {n} from {sid}", sid=sid))
                items.append({"source_id": sid, "url": url, "published": "2026-11-18", "summary": f"{SPEAKER}: {EXCERPT}"})
        cached_take = take_id(claims[0])
        replacement_take = take_id(claims[1])
        capped_take = take_id(claims[2])
        audit_path = raw / "audit.result.json"
        audit_path.write_text(json.dumps({
            cached_take: {"verdict": "fail", "source_id": "builder", "tier": "T1"},
            capped_take: {"verdict": "fail", "source_id": "builder", "tier": "T1"},
            DAY + "-12345678": {"superseded_by": replacement_take},
        }))
        for _ in range(2):  # includes a rerun from today's ledger and cached audit
            drop, ledger, alive, audit = publish(repo, work, raw, claims, items)
            assert Counter(d["source"]["id"] for d in drop) == dict.fromkeys(sources[:4], 2)
            assert {cached_take, replacement_take} <= {d["id"] for d in drop}
            assert not ledger["audits"]
            assert not ({cached_take, capped_take} & json.loads(audit_path.read_text()).keys())
            assert all(d["verification"]["audit_verdict"] is None and not d["verification"]["t1_audit_sample"] for d in drop)
            audit.assert_not_called()
            alive.assert_not_called()
            registry = yaml.safe_load((repo / "sources.yaml").read_text())
            assert registry["sources"][0]["trust"]["tier"] == "T1-seed"
            assert "T1" not in registry["tightened"]
            assert all((t["alpha"], t["beta"], t["resolved_tips"], t["scorable_claims"]) == (2, 2, 0, 0)
                       for t in (s["trust"] for s in registry["sources"]))
        log = (raw / "run.log").read_text()
        assert f"builder: take cap reached: {capped_take}" in log and "fifth: take cap reached" in log
        bad_events = [c | {"state": "resolved", "outcome": "bad", "resolved_on": DAY} for c in ledger["claims"]]
        assert trust.claim_events(bad_events, "builder") == []


if __name__ == "__main__":
    test_quotes()
    test_take_guards()
    test_extraction_stages_items_without_missing_snapshots()
    test_publication()
    test_caps_and_audit_paths()
    print("takes tests: OK")

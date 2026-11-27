"""Offline fixture for builder takes, from saved extraction output to the public drop and ledger."""
import datetime as dt
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest import mock

import yaml

from scanner import extract, fetch, gate, quotes, run, trust, util

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
        path.write_text("x " * 100000 + f"{SPEAKER}: {EXCERPT}")
        assert not quotes.matches_item(EXCERPT, item | {"summary": "x " * 750 + EXCERPT}, pages)
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER)
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
        assert not quotes.date_on_item("2026-11-18", {"snapshot": str(pages / "gone.txt"), "summary": "November 18, 2026"}, pages)
        assert quotes.https_link("https://a.example/p") and not quotes.https_link("http://a.example/p") and not quotes.https_link("https://a@b.example/")
        path.write_text(f"{EXCERPT} " + "filler " * 30 + SPEAKER)
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER)      # name too far after the quote
        path.write_text(f"\u201c{EXCERPT}\u201d said {SPEAKER}.")
        assert quotes.matches_item(EXCERPT, item, pages, SPEAKER)
        assert quotes.is_source_person("Armin Ronacher", "Armin Ronacher") and not quotes.is_source_person("Bob Troll", "Armin Ronacher")


def test_transcript_turns():
    host, guest = "Dwarkesh Patel", "Paul Christiano"
    transcript = (f"By {host}, the host.\n{host}: Why?\n"
                  f"{guest}: {EXCERPT}\n"
                  "The next task is a practical example of how our team might split the coding\n"
                  "agent's work. Each person would be responsible for one separate piece of it.\n"
                  "Production Notes\nUse a separate fixture.\n"
                  "A separate fixture makes it easier to check the output before sharing it.\n"
                  f"{host}: Thanks.\n{guest}: Sure.\n{host}: Anything else?\n{guest}: That's all.\n"
                  + f"{host}: Follow-up?\n{guest}: Another answer.\n" * 7)
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "transcript.txt"
        path.write_text(transcript)
        item = {"snapshot": str(path), "transcript": True}
        assert quotes.matches_item(EXCERPT, item, pages, guest)
        assert not quotes.matches_item(EXCERPT, item, pages, host)
        assert not quotes.matches_item(EXCERPT, item, pages, host, byline_ok=True)
        assert quotes.matches_item(EXCERPT, {"snapshot": str(path)}, pages, host)
        assert not quotes.matches_item(f"Why? {guest}: {EXCERPT}", item, pages, host)
        assert not quotes.matches_item("Split the coding agent's work.", item, pages, guest)
        assert not quotes.matches_item("Use a separate fixture.", item, pages, guest)
        assert not quotes.matches_item("Check the output before sharing it.", item, pages, guest)

        transcript = ("Nathan Labenz (00:01:02)\n" + "filler " * 450 + "We\u2019re doing \ufb01ne.\n"
                      "Chris: Start here.\n[1:02:03] Nathan Labenz (host): Keep going.\n"
                      "Benson 12:34\nKeep it simple.\nNathan Labenz 12:34: One last point.\n"
                      "Chris: Got it.\nChris: Thanks.\n"
                      "Chris Benson Jones: Hello.\nChris Benson Jones: Listening.\nChris Benson Jones: Bye.\n"
                      "Chris Benson: How do you know?\nChris Benson: All right.\nChris Benson: Thanks.\n"
                      "Nathan Labenz: A follow-up.\nChris Benson Jones: Go ahead.\nChris Benson: I agree.\n"
                      "Nathan Labenz: Final question.\nChris Benson Jones: All set.\nChris Benson: Goodbye.\n"
                      + "Nathan Labenz: A recap.\nChris Benson Jones: A response.\n" * 4)
        path.write_text(transcript, encoding="utf-8")
        boundaries = quotes.turn_labels(transcript)
        assert [label for _, label in boundaries.values()] == [
            "nathan labenz", None, "nathan labenz", None, "keep it simple.", "nathan labenz", None, None,
            "chris benson jones", "chris benson jones", "chris benson jones", "chris benson", "chris benson", "chris benson",
            "nathan labenz", "chris benson jones", "chris benson", "nathan labenz", "chris benson jones", "chris benson",
        ] + ["nathan labenz", "chris benson jones"] * 4
        assert quotes.has_turns(boundaries)
        assert quotes.matches_item("We're doing fine.", item, pages, "Nathan Labenz")
        assert quotes.matches_item("Keep going.", item, pages, "Nathan Labenz")
        assert quotes.matches_item("One last point.", item, pages, "Nathan Labenz")
        assert not quotes.matches_item("Listening.", item, pages, "Chris Benson Jones")
        assert not quotes.matches_item("Listening.", item, pages, "Chris Benson")
        assert quotes.matches_item("How do you know?", item, pages, "Chris Benson")
        assert not quotes.matches_item("How do you know?", item, pages, "Chris")
        assert not quotes.matches_item("We're doing fine.", item, pages, "Chris Benson")
        assert not quotes.matches_item("Start here.", item, pages, "Chris Benson")
        assert not quotes.matches_item("Keep it simple.", item, pages, "Chris Benson")
        without_overlap = (transcript.replace("Chris Benson: Goodbye.", "Drew Benson: Goodbye.")
                           + "\nNathan Labenz: A recap.\nNathan Labenz: Goodbye.")
        path.write_text(without_overlap, encoding="utf-8")
        assert quotes.matches_item("Listening.", item, pages, "Chris Benson Jones")
        boundaries = quotes.turn_labels("Then I said: no\nA B C D E F G: no\n" + "A" * 59 + ": no\n" + "A" * 60 + ": no")
        assert boundaries == {0: (12, "then i said"), 1: (14, None), 2: (60, None)}


def test_transcript_turn_threshold():
    host, guest = "Ada Lane", "Bob Reed"
    assert not quotes.has_turns(quotes.turn_labels(f"{host}: Hi.\n" * 12))
    # The switch requirement alone also rejects a single name, so check the name floor separately.
    assert not quotes._enough_creditable_labels({"ada lane": 20})
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "episode.txt"
        item = {"snapshot": str(path), "transcript": True, "source": host}
        lines = {"A": f"{host}: A follow-up.\n", "B": f"{guest}: An answer.\n"}
        for order, expected in (
            ("AB" * 9 + "A", False),        # 19 labels, 18 switches
            ("AB" * 10, True),              # 20 labels, 19 switches
            ("AABB" * 5, False),            # 20 labels, 9 switches
            ("AABB" * 4 + "AABA", True),    # 20 labels, 10 switches
            ("BAB" * 5 + "B" * 5, True),   # exactly five host labels
            ("ABA" * 5 + "A" * 5, True),   # exactly five guest labels
        ):
            text = "".join(lines[label] for label in order).replace("A follow-up.", EXCERPT, 1)
            path.write_text(text)
            assert quotes.has_turns(quotes.turn_labels(text)) is expected, order
            assert quotes.matches_item(EXCERPT, item, pages, host) is expected, order
        path.write_text((lines["A"] + lines["B"] + f"Cam Ray: {EXCERPT}\n") * 4 + (lines["A"] + lines["B"]) * 6)
        assert not quotes.matches_item(EXCERPT, item, pages, "Cam Ray")

    assert quotes.turn_labels(f"{host} (impersonation): Wait for review.")[0][1] is None


def test_wrong_transcript_credit():
    host, guest = "Alessio Fanelli", "Dylan Patel"
    base = "".join(f"{host}: Question {n}.\n{guest}: Answer {n}.\n" for n in range(10))
    tail = f"\n{host}: Follow-up?\n{guest}: Goodbye."
    excerpt = "Open weights will win."
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "episode.txt"
        item = {"snapshot": str(path), "transcript": True, "source": host}
        boundaries = (
            ("lowercase handle after host", host, "swyx: "),
            ("lowercase handle after guest", guest, "swyx: "),
            ("hyphenated name", guest, "Jean-Luc Picard: "),
            ("apostrophe in name", guest, "Sam O'Brien: "),
            ("guest note", guest, "Sam Lee (guest): "),
            ("leading timestamp", guest, "[01:02:03] Sam Altman: "),
            ("trailing timestamp", guest, "Sam Altman (00:01:02)\n"),
            ("bare name", guest, "Sam Altman\n"),
            ("title", guest, "Dr. X: "),
            ("digits", guest, "Speaker 1: "),
            ("saved non-breaking space", guest, "Sam\u00a0Altman: "),
        )
        for case, speaker, label in boundaries:
            page = base + f"{speaker}: Sure.\n{label}{excerpt}" + tail
            path.write_text(page, encoding="utf-8")
            assert not quotes.matches_item(excerpt, item, pages, speaker), case

        for label, speaker in (("Paul", "Paul Graham"), ("Patel", guest), ("Alessio", host), ("Sam Altman", "Sam Altman")):
            page = base + f"{label}: {excerpt}" + tail
            path.write_text(page, encoding="utf-8")
            assert not quotes.matches_item(excerpt, item, pages, speaker), label

        path.write_text(base + f"{guest}: {excerpt}" + tail)
        assert quotes.matches_item(excerpt, item, pages, "dylan patel")
        assert not quotes.matches_item(f"Patel: {excerpt}", item, pages, guest)

        path.write_text(base + f"{guest}: {excerpt}\nThanks for reading Latent Space! Subscribe for free to receive new posts.")
        assert not quotes.matches_item(excerpt, item, pages, guest)
        assert not quotes.matches_item("Subscribe for free to receive new posts.", item, pages, guest)

        for nbsp in ("&nbsp;", "\u00a0"):
            html = (f"<p>{guest}: Sure.</p><p>Sam{nbsp}Altman: {excerpt}</p>"
                    f"<p>{host}: Follow-up?</p>")
            text = util.html_to_text(html, keep_lines=True)
            assert f"Sam Altman: {excerpt}" in text
            assert util.html_to_text(html) == f"{guest}: Sure. Sam Altman: {excerpt} {host}: Follow-up?"
            path.write_text(base + text + f"\n{guest}: Goodbye.", encoding="utf-8")
            assert not quotes.matches_item(excerpt, item, pages, guest), nbsp


def test_transcript_reviewer_probes():
    host, guest = "Dwarkesh Patel", "Paul Christiano"
    excerpt = "Pause all algorithmic progress until the weights are secured from theft"
    discussion = "We spent the rest of the episode discussing a different question about the labs."
    pair = f"<p>{host}</p><p>{discussion}</p><p>{guest}</p><p>{discussion}</p>"
    base, tail = pair * 10 + f"<p>{host}</p><p>{discussion}</p>", pair
    quote = f"<p>{excerpt}, and there is more to say about that point.</p>"
    subscribe = "Subscribe for free to receive new posts and support my work"
    footer = base + quote + f"<p>{guest}</p><p>{discussion} {subscribe}.</p><p>Appears in episode</p><p>{host}</p>"
    essay = (f"<h3>{guest}</h3><p>The author writes that {excerpt.lower()} and many listeners agreed.</p>"
             f"<h3>{host}</h3><p>{discussion}</p>") * 6
    guest_list = pair * 4 + f"<p>{host}</p><p>We had guests like</p><p>{guest}</p>" + quote + f"<p>{host}</p><p>{discussion}</p>"
    probes = (
        ("inline bold", base + f"<p><b>Sam Lee</b> {excerpt}.</p>" + tail, excerpt, host),
        ("inline strong", base + f"<p><strong>Sam Lee</strong> {excerpt}.</p>" + tail, excerpt, host),
        ("inline spans", base + f"<p><span>{guest.upper()}</span><span>{excerpt}.</span></p>" + tail, excerpt, host),
        ("table cells", base + f"<table><tr><td>{guest}</td><td>{excerpt}.</td></tr></table>" + tail, excerpt, host),
        ("long guest title", base + f"<p>{guest}, head of the Alignment Research Center (guest)</p>" + quote + tail, excerpt, host),
        ("listener question", base + f"<p>{host} (reading a listener question)</p>" + quote + tail, excerpt, host),
        ("quoting another person", base + f"<p>{host} (quoting Sam Altman): {excerpt}.</p>" + tail, excerpt, host),
        ("long label note", base + f"<p>{host} (reading a listener question about the economics of new chips)</p>" + quote + tail, excerpt, host),
        ("footer byline", footer, subscribe, guest),
        ("penultimate speech before bare byline", footer, excerpt, host),
        ("essay headings", essay, excerpt, guest),
        ("guest list", guest_list, excerpt, guest),
    )
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "episode.txt"
        item = {"snapshot": str(path), "transcript": True}
        for case, html, text, speaker in probes:
            path.write_text(util.html_to_text(html, keep_lines=True), encoding="utf-8")
            assert not quotes.matches_item(text, item, pages, speaker), case

        for role in ("GuEsT", "HOST", "Co-Host", "Interviewer", "MODERATOR"):
            path.write_text(util.html_to_text(base + f"<p>[01:02] {guest} ({role})</p>" + quote + tail, keep_lines=True))
            assert quotes.matches_item(excerpt, item, pages, guest), role
        path.write_text(util.html_to_text(base + f"<p>{guest}son said {excerpt} before {guest} joins us.</p>" + tail, keep_lines=True))
        assert quotes.matches_item(excerpt, item, pages, host)
        assert not quotes.matches_item(f"{excerpt} before {guest}", item, pages, host)


def test_blog_and_turnless_transcript():
    guest, guest_quote = "Bob Troll", "Try a smaller example."
    own_quote = "Check the output before sharing it."
    page = (f"By {SPEAKER}, the author.\n" + "filler " * 75 + "\n"
            "Release Notes\nRecent Work\nRelease Notes\nExample Projects\nMore Reading\n"
            f"{EXCERPT}\n" + "filler " * 75 + "\n"
            f"As {guest} wrote, {guest_quote}\nAs {SPEAKER} wrote, {own_quote}")
    boundaries = quotes.turn_labels(page)
    assert len(boundaries) == 9 and not quotes.has_turns(boundaries)
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "page.txt"
        path.write_text(page)
        item = {"source": SPEAKER, "snapshot": str(path)}
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER)
        assert quotes.matches_item(EXCERPT, item, pages, SPEAKER, byline_ok=True)
        assert quotes.matches_item(guest_quote, item, pages, guest)
        item["transcript"] = True
        assert not quotes.matches_item(guest_quote, item, pages, guest)
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER, byline_ok=True)
        assert not quotes.matches_item(own_quote, item, pages, SPEAKER)
        turnless = "Ada Lane: Any tips?\nBob Reed: Use small models."
        path.write_text(turnless)
        item["source"] = "Ada Lane"
        assert not quotes.matches_item("Use small models.", item, pages, "Ada Lane", byline_ok=True)
        summary = {"source": "Ada Lane", "summary": util.html_to_text(turnless, 1500)}
        assert not quotes.matches_item("Use small models.", summary, pages, "Ada Lane")
        assert not quotes.matches_item("Use small models.", summary | {"transcript": True}, pages, "Ada Lane")


def test_label_paragraph_then_blank_line():
    """Substack-style transcripts put the speaker name in its own paragraph, then a blank line, then the speech."""
    host, guest = "Dwarkesh Patel", "Paul Christiano"
    excerpt = "For the very short term, the main worry on my mind is automation of AI development."
    html = ""
    for n in range(20):
        speaker = host if n % 2 == 0 else guest
        body = excerpt if n == 3 else f"This is the long answer for turn {n}, " + "with more words " * 10
        html += f"<p>{speaker}</p><p>{body}</p><p>{'A second paragraph in the same turn, ' * 3}</p>"
    text = util.html_to_text(html, 200_000, keep_lines=True)
    assert "\n\n" in text                                       # blank lines between label and speech
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "t.txt"
        path.write_text(text, encoding="utf-8")
        item = {"snapshot": str(path), "transcript": True, "source": "Podcast"}
        assert quotes.matches_item(excerpt, item, pages, guest)
        assert not quotes.matches_item(excerpt, item, pages, host)
        # without the blank-line rule every speech line would sit after an unlabelled boundary and nothing would pass
        assert quotes.matches_item("A second paragraph in the same turn,", item, pages, host)


def test_multi_turn_transcript():
    host, guest = "Dwarkesh Patel", "Paul Christiano"
    excerpt = "A couple things are on my mind, especially over the last few weeks."
    footer = "Subscribe for free to receive new posts."
    heading = "Discussion about this video"
    html = "<p>Home</p><p>Recent Episodes</p><p>You've reached the episode page.</p>"
    for n in range(40):
        speaker = host if n % 2 == 0 else guest
        body = excerpt if n == 21 else f"This is the discussion for turn {n}."
        html += f"<p><strong>{speaker}</strong><br>{body} " + "filler " * 700 + "</p>"
    transcript = util.html_to_text(html + f"<p>{footer}<br>{heading}<br>Related Posts</p>", 200_000, keep_lines=True)
    assert 190_000 < len(transcript) <= 200_000 and transcript.endswith(f"{heading}\nRelated Posts")
    assert quotes.has_turns(quotes.turn_labels(transcript))
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        path = pages / "dw.txt"
        path.write_text(transcript, encoding="utf-8")
        item = {"snapshot": str(path), "transcript": True, "source": host}
        assert quotes.matches_item(excerpt, item, pages, guest)
        assert not quotes.matches_item(excerpt, item, pages, host)
        assert not quotes.matches_item("You've reached the episode page.", item, pages, host)
        assert not quotes.matches_item("This is the discussion for turn 39.", item, pages, guest)
        assert not quotes.matches_item("This is the discussion for turn 38.", item, pages, host)
        assert not quotes.matches_item(heading, item, pages, guest)
        assert not quotes.matches_item(footer, item, pages, guest)


def test_snapshot_limits():
    html = "<p> Hello\t world <br />  next </p><p>Again</p>"
    assert util.html_to_text(html) == util.html_to_text(html, keep_lines=False) == "Hello world next Again"
    assert util.html_to_text(html, keep_lines=True) == "Hello world\nnext\n\nAgain"
    for tag in ("td", "th", "figcaption", "hr", "pre", "table", "ul", "ol", "dl"):
        inner = f"<{tag}>Middle</{tag}>" if tag != "hr" else "<hr/>"
        html = f"Before{inner}After"
        expected = "Before Middle After" if tag != "hr" else "Before After"
        assert util.html_to_text(html, keep_lines=False).encode() == expected.encode(), tag
        assert util.html_to_text(html, keep_lines=True) == expected.replace(" ", "\n"), tag
    for tag in ("b", "strong"):
        html = f"<p>Before <{tag}>Sam Lee</{tag}> After</p>"
        assert util.html_to_text(html, keep_lines=False).encode() == b"Before Sam Lee After", tag
        assert util.html_to_text(html, keep_lines=True) == "Before Sam Lee\nAfter", tag
    blog = "<p>First</p>" * 500 + "<p>" + "filler " * 2982 + f"Paul Christiano: {EXCERPT}</p><p>" + "filler " * 150
    transcript = ("<p>Dwarkesh Patel: Why?</p><p>Paul Christiano: " + "filler " * 3500
                  + f"{EXCERPT}</p><p>Dwarkesh Patel: Thanks.</p><p>Paul Christiano: Sure.</p>"
                  "<p>Dwarkesh Patel: Anything else?</p><p>Paul Christiano: That's all.</p>"
                  + "<p>Dwarkesh Patel: Follow-up?</p><p>Paul Christiano: Another answer.</p>" * 7)
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp), ("default", "extended", "custom"))
        reg = yaml.safe_load((repo / "sources.yaml").read_text())
        for src in reg["sources"]:
            src["register"] = dict.fromkeys(("route", "basis", "republish", "limit", "stop_rule", "opt_out"), "fixture")
            src["feeds"] = [{"type": "rss", "url": f"https://{src['id']}.example/feed"}]
        reg["sources"][1]["snapshot_chars"] = 200000
        reg["sources"][1]["transcript"] = True
        reg["sources"][2]["snapshot_chars"] = 80
        (repo / "sources.yaml").write_text(yaml.safe_dump(reg))

        def feed_item(src, _feed, _since):
            return [{"source_id": src["id"], "kind": "rss", "title": "Transcript",
                     "url": f"https://{src['id']}.example/episode"}]

        with mock.patch.object(fetch, "feed_items", side_effect=feed_item), \
             mock.patch.object(util, "get", side_effect=lambda url: mock.Mock(
                 status_code=200, text=transcript if "extended.example" in url else blog)), \
             mock.patch.object(util, "add_page"), mock.patch.object(util, "page_count", return_value=0):
            (default, extended, custom), _ = fetch.run(repo, work, DAY, 36)
        assert "transcript" not in default and "transcript" not in extended
        assert len(Path(default["snapshot"]).read_text()) == 24000
        assert Path(default["snapshot"]).read_bytes() == util.html_to_text(blog, 24_000).encode()
        assert Path(custom["snapshot"]).read_bytes() == util.html_to_text(blog, 80).encode()
        assert EXCERPT not in util.html_to_text(blog, 24_000, keep_lines=True)
        assert not quotes.matches_item(EXCERPT, custom, raw / "pages")
        assert not quotes.matches_item(EXCERPT, custom, raw / "pages", "Paul Christiano")
        assert quotes.matches_item(EXCERPT, default, raw / "pages")
        assert quotes.matches_item(EXCERPT, default, raw / "pages", "Paul Christiano")
        assert Path(extended["snapshot"]).read_text() == util.html_to_text(transcript, 200_000, keep_lines=True)
        transcript_item = extended | {"transcript": True}
        assert quotes.matches_item(EXCERPT, transcript_item, raw / "pages", "Paul Christiano")
        assert not quotes.matches_item(EXCERPT, transcript_item, raw / "pages", "Dwarkesh Patel")


def test_skip_extract_transcript_registry():
    with tempfile.TemporaryDirectory() as tmp:
        repo, work, raw = fixture(Path(tmp))
        registry = repo / "sources.yaml"
        reg = yaml.safe_load(registry.read_text())
        reg["sources"][0] |= {"transcript": True, "snapshot_chars": 200000}
        registry.write_text(yaml.safe_dump(reg))
        path = raw / "pages" / "episode.txt"
        guest = "Bob Troll"
        path.write_text("".join(f"{SPEAKER}: Question {n}?\n{guest}: Answer {n}.\n" for n in range(8))
                        + f"{SPEAKER}: One more?\n{guest}: {EXCERPT}\n{SPEAKER}: Thanks.\n{guest}: Goodbye.")
        url = "https://builder.example/episode"
        claims = [take(url, "Give the agent an example.")]
        items = [{"source_id": "builder", "url": url, "snapshot": str(path), "published": "2026-11-18"}]
        drop, *_ = publish(repo, work, raw, claims, items)
        assert not drop
        assert f"builder: quote not found: {take_id(claims[0])}" in (raw / "run.log").read_text()
        rewritten = yaml.safe_load(registry.read_text())
        source = rewritten["sources"][0]
        assert source["transcript"] is True and source["snapshot_chars"] == 200000

        source["transcript"] = False
        registry.write_text(yaml.safe_dump(rewritten))
        drop, *_ = publish(repo, work, raw, claims, [items[0] | {"transcript": True}])
        assert [d["id"] for d in drop] == [take_id(claims[0])]


def test_summary_quotes():
    summary = {"source": SPEAKER, "summary": f"Bob Troll: {EXCERPT}"}
    with tempfile.TemporaryDirectory() as tmp:
        pages = Path(tmp)
        assert quotes.matches_item(EXCERPT, summary, pages)
        assert not quotes.matches_item(EXCERPT, summary, pages, "Bob Troll")
        own = summary | {"summary": f"{SPEAKER}: {EXCERPT}"}
        assert quotes.matches_item(EXCERPT, own, pages, SPEAKER)
        for label in ("Bob", "Élodie René", "J. R. Smith", "Ann Bea Cat Dee Eve Fay"):
            other = own | {"summary": f"{SPEAKER}: {EXCERPT} {label}: Why?"}
            assert not quotes.matches_item(EXCERPT, other, pages, SPEAKER), label
        assert not quotes.matches_item(EXCERPT, own | {"transcript": True}, pages, SPEAKER)
        path = pages / "page.txt"
        path.write_text("The page has other material.")
        with_page = own | {"snapshot": str(path)}
        assert quotes.matches_item(EXCERPT, with_page, pages, SPEAKER)
        assert not quotes.matches_item(EXCERPT, with_page | {"transcript": True}, pages, SPEAKER)
        turns = (f"{SPEAKER} (reading notes): {EXCERPT}\nBob Troll: Why?\n{SPEAKER} (reading notes): It helps.\n"
                 f"Bob Troll: How?\n{SPEAKER} (reading notes): Try it.\nBob Troll: Thanks.")
        assert not quotes.matches_item(EXCERPT, own | {"summary": turns}, pages, SPEAKER)

        desc = turns.replace("\n", "<br>")
        rss = ("<rss version='2.0'><channel><item><link>https://builder.example/p</link>"
               "<pubDate>Wed, 25 Nov 2026 12:00:00 GMT</pubDate>"
               f"<description><![CDATA[{desc}]]></description></item></channel></rss>")
        with mock.patch.object(util, "get", return_value=mock.Mock(status_code=200, content=rss.encode())):
            (item,) = fetch.feed_items({"id": "builder", "name": SPEAKER},
                                       {"type": "rss", "url": "https://builder.example/feed"},
                                       dt.datetime(2026, 11, 25, tzinfo=dt.timezone.utc))
        assert item["summary"] == turns.replace("\n", " ")
        assert quotes.matches_item(EXCERPT, item, pages)
        assert not quotes.matches_item(EXCERPT, item, pages, SPEAKER)


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
        bad_verification = take(url, "Verification is malformed", verification=[{"status": "attributed"}])
        heading = take(url, "Line one\n## Fake heading [link](https://evil.example)")
        drop, *_ = publish(repo, work, raw, [bad_row, bad_verification, heading], items)
        log = (raw / "run.log").read_text()
        assert "malformed extractor row" in log and len(drop) == 1
        assert "take without eligible source, attributed status" in log
        assert drop[0]["id"] == take_id(heading)
        md = (repo / "latest.md").read_text()
        assert "\n## Fake heading" not in md and "\\[link\\]" in md
        assert any("status at publication" in x for x in gate.check(repo)) is False
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
        good = take(url, "Start with an example when prompting a coding agent", also_seen_in=None)
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
        assert opinion["also_seen_in"] == []
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
        reg = yaml.safe_load((repo / "sources.yaml").read_text())
        for src in reg["sources"]:
            src["name"] = f"{SPEAKER} ({src['id']})"
        (repo / "sources.yaml").write_text(yaml.safe_dump(reg))
        claims, items = [], []
        for src in reg["sources"]:
            sid = src["id"]
            for n in range(3):
                url = f"https://builder.example/{sid}/{n}"
                claims.append(take(url, f"Tip {n} from {sid}", sid=sid))
                items.append({"source_id": sid, "source": src["name"], "url": url,
                              "published": "2026-11-18", "summary": f"{SPEAKER}: {EXCERPT}"})
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
    test_transcript_turns()
    test_transcript_turn_threshold()
    test_wrong_transcript_credit()
    test_transcript_reviewer_probes()
    test_blog_and_turnless_transcript()
    test_label_paragraph_then_blank_line()
    test_multi_turn_transcript()
    test_snapshot_limits()
    test_skip_extract_transcript_registry()
    test_summary_quotes()
    test_take_guards()
    test_extraction_stages_items_without_missing_snapshots()
    test_publication()
    test_caps_and_audit_paths()
    print("takes tests: OK")

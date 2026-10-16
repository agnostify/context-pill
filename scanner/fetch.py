"""Ingestion: RSS/Atom feeds, sitemap new-URL diffs (incl. raycfu.com /guides, filtered to guides listed on /guides),
and (only once approved) the YouTube Data API. Every web request goes through util.get (robots, rate limits, stop rules).
Writes WORK/raw/<date>/items.json and page snapshots. Never fetches X/Twitter or Instagram hosts."""
import json, re, os, datetime as dt, hashlib, sys, urllib.parse
import requests
from pathlib import Path
import feedparser, yaml
from . import util

PAGE_CAP_PER_SITE = 20
ITEM_MEMORY_DAYS = 45          # item URLs already ingested in the last 45 days are not ingested again


def _when(e):
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return dt.datetime(*e[k][:6], tzinfo=dt.timezone.utc)
    return None


def _blocked(url):
    return util.blocked(url)


def youtube_items(src, feed, since):
    """YouTube's robots.txt disallows /feeds/videos.xml, so we don't use channel RSS. Official route:
    YouTube Data API v3 playlistItems.list on the channel's uploads playlist (1 quota unit per call; default 10,000/day).
    Needs CP_YOUTUBE_API_APPROVED=1 (set only after the maintainer approves this route) and YOUTUBE_API_KEY in the environment."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if os.environ.get("CP_YOUTUBE_API_APPROVED") != "1" or not key:
        raise LookupError("skipped: YouTube Data API route not approved/configured (channel RSS is disallowed by youtube.com/robots.txt)")
    pl = "UU" + feed["channel_id"][2:]
    r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", timeout=25,
                     headers={"x-goog-api-key": key, "User-Agent": util.UA},
                     params={"part": "snippet,contentDetails", "playlistId": pl, "maxResults": 10})
    if r.status_code in (403, 429):
        raise util.Stop(f"YouTube API {r.status_code}")
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        sn = it["snippet"]; vid = it["contentDetails"]["videoId"]
        w = dt.datetime.fromisoformat((it["contentDetails"].get("videoPublishedAt") or sn["publishedAt"]).replace("Z", "+00:00"))
        if w < since:
            continue
        out.append({"source_id": src["id"], "source": src["name"], "feed": "youtube-data-api:" + pl, "kind": "youtube",
                    "title": sn.get("title", ""), "url": f"https://www.youtube.com/watch?v={vid}", "published": w.isoformat(),
                    "summary": (sn.get("description") or "")[:1500]})
    return out


def feed_items(src, feed, since):
    if feed["type"] == "youtube":
        return youtube_items(src, feed, since)
    r = util.get(feed["url"])
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    f = feedparser.parse(r.content)
    out = []
    for e in f.entries:
        w = _when(e)
        if not w or w < since:
            continue
        link = e.get("link", "")
        if _blocked(link):
            continue
        desc = e.get("summary", "") or ""
        out.append({"source_id": src["id"], "source": src["name"], "feed": feed["url"], "kind": feed["type"],
                    "title": e.get("title", "").strip(), "url": link, "published": w.isoformat(),
                    "summary": util.html_to_text(desc, 1500)})
    return out


def sitemap_items(src, feed, since, seen):
    r = util.get(feed["url"])
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    locs = re.findall(r"<url>\s*<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", r.text)
    filt = re.compile(feed.get("filter", "."))
    key = feed["url"]
    known = set(seen.get(key, []))
    first_run = key not in seen
    out = []
    for loc, lastmod in locs:
        loc = loc.strip()
        if not filt.search(loc):
            continue
        new = loc not in known
        recent = False
        if feed.get("use_lastmod") and lastmod:
            try:
                recent = dt.datetime.fromisoformat(lastmod.replace("Z", "+00:00")) >= since
            except ValueError:
                pass
        if (new and not first_run) or recent:
            out.append({"source_id": src["id"], "source": src["name"], "feed": key, "kind": feed["type"],
                        "title": "", "url": loc, "published": lastmod or None, "summary": ""})
        known.add(loc)
    seen[key] = sorted(known)
    if feed.get("listing_url") and out:
        # only guides that are actually listed on the public /guides page count (unlisted pages are skipped)
        lr = util.get(feed["listing_url"])
        listed = set(re.findall(r'href="([^"#?]+)"', lr.text)) if lr.status_code < 400 else set()
        listed = {urllib.parse.urljoin(feed["listing_url"], h).rstrip("/") for h in listed}
        out = [o for o in out if o["url"].rstrip("/") in listed]
    return out, first_run, len(known)


def snapshot(item, pages_dir, counts):
    host = re.sub(r"^https?://([^/]+).*", r"\1", item["url"])
    if item["kind"] == "youtube" or _blocked(item["url"]) or util.page_count(host) >= PAGE_CAP_PER_SITE:
        return
    util.add_page(host)                       # persisted: at most 20 page snapshots per site per day across runs
    try:
        r = util.get(item["url"])
        if r.status_code >= 400:
            item["snapshot_error"] = f"HTTP {r.status_code}"; return
        text = util.html_to_text(r.text, 24000)
        if not item["title"]:
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text)
            item["title"] = util.html_to_text(m.group(1), 200) if m else item["url"]
        fn = hashlib.sha1(item["url"].encode()).hexdigest()[:12] + ".txt"
        (pages_dir / fn).write_text(text)
        item["snapshot"] = str(pages_dir / fn)
    except Exception as ex:
        item["snapshot_error"] = f"{type(ex).__name__}: {ex}"[:200]


def run(repo, work, date, hours):
    sources = yaml.safe_load((repo / "sources.yaml").read_text())["sources"]
    raw = work / "raw" / date; pages = raw / "pages"; pages.mkdir(parents=True, exist_ok=True)
    seen_p = work / "state" / "seen-urls.json"; seen_p.parent.mkdir(parents=True, exist_ok=True)
    seen = json.loads(seen_p.read_text()) if seen_p.exists() else {}
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    util.init_state(work, date)
    items, report = [], []
    for s in sources:
        if (s.get("trust") or {}).get("tier") == "drop":
            report.append({"source": s["id"], "status": "skipped: trust tier is drop"}); continue
        if not s.get("enabled"):
            report.append({"source": s["id"], "status": "disabled", "why": s.get("disabled_reason")}); continue
        reg = s.get("register") or {}
        if not all(reg.get(k) for k in ("route", "basis", "republish", "limit", "stop_rule", "opt_out")):
            report.append({"source": s["id"], "status": "skipped: register incomplete"}); continue
        for f in s["feeds"]:
            if f["type"] != "youtube" and util.feed_fetched_today(f["url"]):
                report.append({"source": s["id"], "feed": f["url"], "status": "skipped: already fetched today (rerun with --skip-extract)"}); continue
            if f["type"] != "youtube":
                util.mark_feed_fetched(f["url"])
            try:
                if f["type"] in ("rss", "youtube"):
                    got = feed_items(s, f, since); note = ""
                else:
                    got, first, n = sitemap_items(s, f, since, seen)
                    note = f"baseline {n} URLs" if first else f"{n} known URLs"
                items += got
                report.append({"source": s["id"], "feed": f["url"], "status": "ok", "items": len(got), "note": note})
            except util.Stop as ex:
                report.append({"source": s["id"], "feed": f.get("url"), "status": f"stopped for today: {ex}"})
            except LookupError as ex:
                report.append({"source": s["id"], "feed": f.get("url"), "status": str(ex)})
            except Exception as ex:
                report.append({"source": s["id"], "feed": f["url"], "status": f"error: {type(ex).__name__}: {str(ex)[:150]}"})
    # dedupe by URL, within this run and against items ingested on earlier days (overlapping 36 h windows)
    mem = seen.setdefault("_items", {})
    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=ITEM_MEMORY_DAYS)).isoformat()
    for u in [u for u, d in mem.items() if d < cutoff]:
        del mem[u]
    uniq, repeats = {}, 0
    for i in items:
        u = i["url"].rstrip("/")
        if mem.get(u, date) < date:
            repeats += 1; continue
        uniq.setdefault(u, i)
    items = list(uniq.values())
    for u in uniq:
        mem.setdefault(u, date)
    report.append({"source": "_dedupe", "status": "ok", "note": f"{repeats} items skipped as already ingested on an earlier day"})
    counts = {}
    for i in items:
        snapshot(i, pages, counts)
    # feeds skipped as already fetched today: keep the items an earlier run of the same day ingested, so a rerun never
    # overwrites the day's items with fewer
    old_p = raw / "items.json"
    if old_p.exists() and any("already fetched today" in r["status"] for r in report):
        have = {i["url"].rstrip("/") for i in items}
        kept = [i for i in json.loads(old_p.read_text()) if i["url"].rstrip("/") not in have]
        items += kept
        report.append({"source": "_rerun", "status": "info", "note": f"{len(kept)} items reused from an earlier run today"})
    seen_p.write_text(json.dumps(seen, indent=0))
    (raw / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
    (raw / "fetch-report.json").write_text(json.dumps(report, indent=1))
    ok = sum(1 for r in report if r["status"] == "ok")
    print(f"fetch: {len(items)} items from {ok} feeds ok; {sum(1 for r in report if r['status'].startswith('error'))} errors")
    return items, report

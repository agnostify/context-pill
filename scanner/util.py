"""HTTP helpers: one checked fetch path for every scanner request (ingestion and evidence checks).
- robots.txt checked for every URL, including each redirect hop (RFC 9309 handling, conservative on errors).
- At most 1 request every 2 s per host; a per-host daily request budget, persisted across runs on the same day.
- A 403/429 stops that host for the rest of the day (persisted), for every later step.
- Hosts on the blocklist (X/Twitter, Instagram and their link shorteners/mirrors) are never requested.
No credentials are read here."""
import time, urllib.robotparser, urllib.parse, re, html, json, datetime as dt
from pathlib import Path
import requests

UA = "ContextPillScanner/0.1 (+https://github.com/agnostify/context-pill; takedown: see TAKEDOWN.md)"
MIN_GAP = 2.0                 # seconds between requests to the same host
HOST_DAILY_BUDGET = 30        # requests per host per day (1-3 feeds + <=20 page snapshots + evidence checks)
STOP_CODES = {403, 429}
MAX_REDIRECTS = 5
BLOCKED_DOMAINS = ("x.com", "twitter.com", "t.co", "instagram.com", "instagr.am", "fxtwitter.com", "vxtwitter.com",
                   "fixupx.com", "nitter.net", "threads.net")

_last, _robots = {}, {}
_state = {"path": None, "date": None, "stopped": {}, "count": {}, "feeds": [], "pages": {}}


class Stop(Exception):
    """The host answered 403/429 (or is stopped/over budget today): no more requests to it today."""


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")


def blocked(url):
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in BLOCKED_DOMAINS)


def init_state(work, date):
    """Load today's per-host stop list, request counts, fetched feeds and page-snapshot counts (WORK/state/hosts-<date>.json)."""
    p = Path(work) / "state" / f"hosts-{date}.json"; p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text()) if p.exists() else {}
    _state.update(path=p, date=date, stopped=d.get("stopped", {}), count=d.get("count", {}),
                  feeds=d.get("feeds", []), pages=d.get("pages", {}))


def _save():
    if _state["path"]:
        _state["path"].write_text(json.dumps({k: _state[k] for k in ("stopped", "count", "feeds", "pages")}, indent=1))


def feed_fetched_today(url):
    return url in _state.get("feeds", [])


def mark_feed_fetched(url):
    _state.setdefault("feeds", []).append(url); _save()


def page_count(host):
    return _state.get("pages", {}).get(host, 0)


def add_page(host):
    _state.setdefault("pages", {})[host] = page_count(host) + 1; _save()


def stopped_hosts():
    return dict(_state["stopped"])


def _stop(host, why):
    _state["stopped"][host] = why; _save()
    raise Stop(f"{why} from {host}")


def _robots_for(url):
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        allow_all = disallow_all = False
        try:
            r = _robots_request(base + "/robots.txt")
            if r is None:
                disallow_all = True       # redirected to a blocked/stopped/over-budget host, or too many hops
            elif r.status_code in (401, 403, 429) or r.status_code >= 500:
                disallow_all = True       # conservative: treat as "do not crawl" (stricter than RFC 9309 for 401/403)
            elif r.status_code >= 400:
                allow_all = True          # 404/410 etc.: no robots file
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            disallow_all = True           # unreachable robots.txt: don't crawl
        if allow_all:
            rp.parse([])
        elif disallow_all:
            rp.parse(["User-agent: *", "Disallow: /"])
        _robots[base] = rp
    return _robots[base]


def _robots_request(url):
    """Fetch robots.txt following redirects by hand: every hop is blocklist-checked, rate-limited, counted against
    the host budget, and skipped if the host is stopped today. Returns None if any hop is not allowed."""
    for _ in range(MAX_REDIRECTS + 1):
        host = host_of(url)
        if blocked(url) or host in _state["stopped"] or _state["count"].get(host, 0) >= HOST_DAILY_BUDGET:
            return None
        gap = time.time() - _last.get(host, 0)
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        _last[host] = time.time()
        _state["count"][host] = _state["count"].get(host, 0) + 1; _save()
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15, allow_redirects=False)
        if r.status_code in STOP_CODES:
            _state["stopped"][host] = f"{r.status_code} on robots.txt"; _save()
            return r
        if r.is_redirect and r.headers.get("location"):
            url = urllib.parse.urljoin(url, r.headers["location"]); continue
        return r
    return None


def robots_ok(url):
    return _robots_for(url).can_fetch(UA, url)


def get(url, timeout=25, **kw):
    """The only way the scanner requests a page. Follows redirects manually so each hop is checked."""
    kw.pop("allow_redirects", None); kw.pop("check_robots", None)
    for _ in range(MAX_REDIRECTS + 1):
        if blocked(url):
            raise PermissionError(f"blocked host: {host_of(url)}")
        host = host_of(url)
        if host in _state["stopped"]:
            raise Stop(f"{host} stopped for today ({_state['stopped'][host]})")
        if _state["count"].get(host, 0) >= HOST_DAILY_BUDGET:
            raise Stop(f"{host} over daily request budget")
        if not robots_ok(url):
            raise PermissionError(f"robots.txt disallows {url}")
        if host in _state["stopped"]:                     # re-check after the robots.txt request(s)
            raise Stop(f"{host} stopped for today ({_state['stopped'][host]})")
        if _state["count"].get(host, 0) >= HOST_DAILY_BUDGET:
            raise Stop(f"{host} over daily request budget")
        gap = time.time() - _last.get(host, 0)
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        _last[host] = time.time()
        _state["count"][host] = _state["count"].get(host, 0) + 1; _save()
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=False, **kw)
        if r.status_code in STOP_CODES:
            _stop(host, str(r.status_code))
        if r.is_redirect and r.headers.get("location"):
            url = urllib.parse.urljoin(url, r.headers["location"]); continue
        return r
    raise RuntimeError("too many redirects")


def url_alive(url):
    """Evidence check: does the cited page exist? Returns (ok, status). ok=None means inconclusive
    (robots disallow, host stopped/over budget, network error); only a definite HTTP 4xx/5xx counts as dead."""
    try:
        r = get(url, timeout=20)
        return r.status_code < 400, r.status_code
    except (Stop, PermissionError) as e:
        return None, str(e)
    except Exception as e:
        return None, type(e).__name__


def html_to_text(s, limit=20000):
    s = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()[:limit]

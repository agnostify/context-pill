"""One-time generator for sources.yaml (kept for transparency; the scanner rewrites the trust blocks daily).
Every enabled source carries its source-access register entry (METHOD.md section 1). No X handles here, ever."""
import yaml, pathlib

FEED_BASIS = "Public RSS/Atom feed the publisher offers for syndication; robots.txt checked at fetch time"
YT_BASIS = "YouTube Data API v3 playlistItems.list on the uploads playlist (official API, API key from env, 1 unit/call). Channel RSS is NOT used: youtube.com/robots.txt disallows /feeds/videos.xml (checked Oct 16, 2026). Video content only via Gemini public-URL input when a key is configured"
REPUB = "Our own one-to-two sentence summary, a link, attribution, at most one quote of <=25 words"
FEED_LIMIT = "1 feed fetch/day; <=20 page fetches/day/site; <=1 request per 2 s"
STOP = "Stop the host for the day on 403/429; stop on a robots or terms change, or on opt-out"
OPT = "TAKEDOWN.md (GitHub issue or email); honored within 24 h"

def reg(route, basis=FEED_BASIS, terms="Not separately reviewed; feed published for syndication. We republish summaries only."):
    return {"route": route, "basis": basis, "terms": terms, "republish": REPUB, "limit": FEED_LIMIT, "stop_rule": STOP, "opt_out": OPT}

def yt(cid): return {"type": "youtube", "channel_id": cid, "url": f"https://www.youtube.com/channel/{cid}"}
def rss(u): return {"type": "rss", "url": u}

S = []
def add(id, name, home, feeds, kind, seed=False, criteria=None, register=None, notes=None, enabled=True):
    register = register or reg("rss/atom")
    if any(f["type"] == "youtube" for f in feeds):
        only_yt = all(f["type"] == "youtube" for f in feeds)
        register = dict(register, limit=("" if only_yt else register["limit"] + "; ") + "YouTube: 1 Data API call per channel per day")
    if any(f["type"] == "youtube" for f in feeds) and "YouTube" not in register["route"]:
        register = dict(register, route=register["route"] + " + YouTube Data API (not active yet)",
                        basis=register["basis"] + "; YouTube: " + YT_BASIS)
    S.append({"id": id, "name": name, "home_url": home, "kind": kind, "seed": seed, "enabled": enabled,
              "feeds": feeds, "research_criteria": criteria or [], "register": register,
              "notes": notes, "trust": None})

# --- Maintainer-assigned T1 seeds (all provisional, spot-checked to T2, until day 15) ---
add("theo", "Theo (t3.gg)", "https://t3.gg", [yt("UCbRP3c757lWg9M-U7TyEkXA")], "expert", seed=True,
    criteria=["Close to face value after day 14: confirm the tool/model/feature he names exists on its vendor's page or repo",
              "Benchmarks and 'best model' takes are labeled opinion or vendor claim",
              "Until Gemini video input is on, only the title/description is read; claims from the video body are not extracted"],
    register=reg("YouTube Data API (not active yet)", YT_BASIS, "YouTube API Services Terms (to be quoted before the key goes live): metadata and links only; no video download or re-hosting"))
add("dario", "Dario Amodei (essays)", "https://darioamodei.com",
    [{"type": "sitemap_new", "url": "https://darioamodei.com/sitemap.xml", "filter": "/post/|/essay/"}], "lab-leader", seed=True,
    criteria=["Face value for what he says Anthropic will do or believes; dated predictions are logged and scored at their deadline",
              "Capability or benchmark statements about Anthropic models are 'vendor claim'"],
    register=reg("sitemap new-URL diff", "Public sitemap; robots.txt lists it (Oct 16, 2026)", "Site shows a privacy policy; no terms page found in the sitemap. Summaries only."))
add("anthropic", "Anthropic", "https://www.anthropic.com",
    [{"type": "sitemap_new", "url": "https://www.anthropic.com/sitemap.xml", "filter": "/news/|/engineering/|/research/", "use_lastmod": True},
     rss("https://github.com/anthropics/claude-code/releases.atom"), yt("UCrDwWp7EBBv4NwvScIpBDOA")], "lab", seed=True,
    criteria=["Primary for whether its own release exists", "Benchmark and 'best model' claims stay 'vendor claim' at every tier"],
    register=reg("sitemap new-URL diff + GitHub releases Atom + YouTube Data API (not active yet)", "Public sitemap and GitHub Atom feed, robots.txt checked at fetch; YouTube via the Data API (see YT basis)"))
add("openai", "OpenAI (and Sam Altman's blog)", "https://openai.com",
    [rss("https://openai.com/news/rss.xml"), rss("https://github.com/openai/codex/releases.atom"), rss("https://blog.samaltman.com/posts.atom"), yt("UCXZCJLdBC09xxGZ6gcdrc6A")], "lab", seed=True,
    criteria=["Primary for whether its own release exists", "Benchmark and 'best model' claims stay 'vendor claim'",
              "Sam Altman blog predictions are logged as dated predictions"])
add("deepmind", "Google DeepMind / Google AI", "https://deepmind.google",
    [rss("https://deepmind.google/blog/rss.xml"), rss("https://blog.google/technology/ai/rss/"), rss("https://github.com/google-gemini/gemini-cli/releases.atom"), yt("UCP7jMXSY2xbc3KCAE0MHQ-A")], "lab", seed=True,
    criteria=["Primary for whether its own release exists", "Benchmark and 'best model' claims stay 'vendor claim'"])
add("karpathy", "Andrej Karpathy", "https://karpathy.ai",
    [yt("UCXUPKJO5MZQN11PqgIvyuvQ"), rss("https://karpathy.bearblog.dev/feed/")], "expert", seed=True,
    criteria=["Techniques: check the linked repo/code exists and runs as described before 'try this'", "Rare poster: dormant, not dropped, after 60 quiet days"],
    notes="Seed status per the approved plan (Karpathy and Simon Willison as T1 seeds).")
add("simonw", "Simon Willison", "https://simonwillison.net",
    [rss("https://simonwillison.net/atom/everything/")], "expert", seed=True,
    criteria=["Tool notes: confirm the tool/version exists on its repo or PyPI/npm; tools stay docs-verified until a sandbox smoke test runs",
              "Link-blog items quoting others are attributed to the original author, not Simon"])

# --- Earn-your-way sources: T3, verify everything ---
add("ray", "Ray Fu (RayCFU)", "https://www.raycfu.com",
    [{"type": "raycfu_guides", "url": "https://www.raycfu.com/sitemap.xml", "filter": "/guides/", "listing_url": "https://www.raycfu.com/guides"}, yt("UCICk1RFsC2NpDSlvPR427VQ")], "creator",
    criteria=["Every guide and video researched before inclusion", "Each named model/tool/feature checked against the vendor's own page",
              "Trading, money-making and growth-hack claims are not repeated as facts; scraping, detection-evasion or DM/engagement automation guides are listed as 'not recommended: terms risk' with no steps",
              "Only free guides listed on the public /guides page; keyword-gated guides only when the maintainer supplies the keyword; paid playbooks skipped",
              "Instagram is never fetched; reels enter only when the maintainer pastes the caption or keyword (manual lane), otherwise 'verification blocked'"],
    register=reg("sitemap /guides/ diff + YouTube Data API (not active yet)", "robots.txt: 'User-Agent: * Allow: /' (saved Oct 16, 2026); YouTube: " + YT_BASIS,
                 "/terms and /terms-of-service return 404 (Oct 16, 2026). Allowed crawling is not a license: summaries, links, <=25-word quotes only. Ray can opt out via TAKEDOWN.md."))
add("latentspace", "Latent Space (swyx)", "https://www.latent.space", [rss("https://www.latent.space/feed")], "expert",
    criteria=["Every claim checked against a primary source", "Podcast guest claims attributed to the guest"])
add("mollick", "Ethan Mollick (One Useful Thing)", "https://www.oneusefulthing.org", [rss("https://www.oneusefulthing.org/feed")], "expert",
    criteria=["Research results checked against the cited paper", "Opinion logged as unscorable"])
add("huggingface", "Hugging Face Blog", "https://huggingface.co/blog", [rss("https://huggingface.co/blog/feed.xml")], "vendor",
    criteria=["Primary for its own releases; third-party model claims checked against the model card"])
add("cursor", "Cursor Changelog", "https://cursor.com/changelog", [rss("https://cursor.com/changelog/rss.xml")], "vendor",
    criteria=["Primary for its own release existing; performance claims are 'vendor claim'"])
add("github-changelog", "GitHub Changelog", "https://github.blog/changelog/", [rss("https://github.blog/changelog/feed/")], "vendor",
    criteria=["Primary for its own release existing; AI-related entries only"])
add("fireship", "Fireship", "https://www.youtube.com/@Fireship", [yt("UCsBjURrPoezykLs9EqgamOA")], "creator",
    criteria=["Every claim checked against the vendor page; jokes and hype are unscorable"], register=reg("YouTube Data API (not active yet)", YT_BASIS, "YouTube API Services Terms (to be quoted before the key goes live): metadata and links only"))
add("aiexplained", "AI Explained", "https://www.youtube.com/@aiexplained-official", [yt("UCNJ1Ymd5yFuUPtn21xtRbbw")], "creator",
    criteria=["Every claim checked against the vendor page or paper"], register=reg("YouTube Data API (not active yet)", YT_BASIS, "YouTube API Services Terms (to be quoted before the key goes live): metadata and links only"))
add("interconnects", "Interconnects (Nathan Lambert)", "https://www.interconnects.ai", [rss("https://www.interconnects.ai/feed")], "expert",
    criteria=["Research and open-model claims checked against the model card or paper"])
add("importai", "Import AI (Jack Clark)", "https://importai.substack.com", [rss("https://importai.substack.com/feed")], "expert",
    criteria=["Paper results checked against the paper; commentary unscorable"])

# --- Disabled until a register entry is complete (METHOD.md section 1) ---
for id, name, why in [
    ("x-discovery", "X (private discovery signal)", "Disabled until X approves this use case in writing. Nothing from X is ever published here."),
    ("instagram", "Instagram", "Never fetched (login wall, automated-collection terms). Manual lane only: the maintainer pastes a caption or guide keyword."),
    ("arxiv", "arXiv API", "Disabled until registered (spaced-request etiquette, per-paper licenses)."),
    ("hf-api", "Hugging Face API", "Disabled until registered."),
    ("hn-algolia", "Hacker News (Algolia)", "Disabled until registered."),
    ("github-search", "GitHub REST search / Trending", "Disabled until registered."),
    ("reddit", "Reddit (Arctic Shift)", "Disabled until registered."),
]:
    S.append({"id": id, "name": name, "enabled": False, "disabled_reason": why})

if __name__ == "__main__":
    import sys
    out = pathlib.Path(__file__).resolve().parent.parent / "sources.yaml"
    if out.exists() and "--force" not in sys.argv:
        sys.exit("sources.yaml exists and holds live trust blocks; re-seeding would wipe them. Use --force only before the first run.")
    out.write_text("# Context Pill source registry. Hand-edit names, feeds and criteria; the scanner rewrites the trust blocks.\n"
                   "# No X handles in this public file. Caps: ~40 sources total.\n" + yaml.safe_dump({"sources": S}, sort_keys=False, width=140, allow_unicode=True))
    print(out, len(S))

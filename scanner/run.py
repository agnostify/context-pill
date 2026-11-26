"""Daily orchestrator: resolve due claims -> update trust (drop/demote/recover) -> fetch -> (Gemini, from Oct 23 if keyed)
-> extract+verify -> publication checks -> T1 full audit sample -> render -> gate.
Usage: python -m scanner.run [--date YYYY-MM-DD] [--hours 36] [--skip-extract]
Repo (public) = this checkout. Work dir (private, never pushed) = $CP_WORK or ../context-pill-work."""
import argparse, json, os, hashlib, datetime as dt
from collections import Counter
from pathlib import Path
import yaml
from . import fetch, extract, trust, render, gate, util, repeats, notes, quotes

REPO = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("CP_WORK", REPO.parent / "context-pill-work"))
START_DATE = dt.date(2026, 10, 16)          # day 1 of the scanner (provisional period = days 1-14)
CHECKED = ("verified", "docs-verified", "vendor-claim", "face-value-T1")
SCORABLE_CATS = ("release", "tool", "automation", "technique", "research", "prediction")
TAKE_KINDS = ("expert", "lab-leader", "creator", "lab")
TAKE_PER_SOURCE, TAKE_PER_DAY = 2, 8
AUDIT_RATE = 10                              # 1 in 10 T1 items (deterministic by item id), at least 1 per day if any


def _h(s):
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


THIN_ITEMS = 10           # fewer new items than this across all feeds is a thin day
CONCENTRATION = 0.6       # one source behind more than 60% of a drop of 3+ items
QUIET_DAYS = 7            # an enabled feed source with no items in 7 days may have gone stale


def health(date, items_read, drop, srcs):
    """Flags for the review step (never blocks the drop): thin day, one source dominating, sources gone quiet."""
    flags = []
    if items_read < THIN_ITEMS:
        flags.append(f"thin day: {items_read} new items read (under {THIN_ITEMS})")
    by = {}
    for d in drop:
        by[d["source"]["name"]] = by.get(d["source"]["name"], 0) + 1
    if len(drop) >= 3 and by and max(by.values()) / len(drop) > CONCENTRATION:
        top = max(by, key=by.get)
        flags.append(f"source concentration: {by[top]} of {len(drop)} items from {top}")
    lo = (dt.date.fromisoformat(date) - dt.timedelta(days=QUIET_DAYS)).isoformat()
    quiet = sorted(s["name"] for s in srcs.values() if s.get("enabled") and s.get("feeds")
                   and not any(lo < k <= date and v for k, v in ((s.get("trust") or {}).get("items_seen_by_date") or {}).items()))
    if quiet:
        flags.append(f"quiet {QUIET_DAYS}+ days (check the feeds): {', '.join(quiet)}")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--hours", type=int, default=36)
    ap.add_argument("--skip-extract", action="store_true", help="reuse WORK/raw/<date>/claims.raw.json (and audit.result.json)")
    a = ap.parse_args()
    date = a.date; today = dt.date.fromisoformat(date); day_no = (today - START_DATE).days + 1
    raw = WORK / "raw" / date; raw.mkdir(parents=True, exist_ok=True)
    util.init_state(WORK, date)
    logf = raw / "run.log"
    def log(m):
        line = f"[{dt.datetime.now():%H:%M:%S}] {m}"; print(line)
        with open(logf, "a") as f: f.write(line + "\n")
    def changelog(m):
        line = f"- {date}: {m}\n"; cp = REPO / "CHANGELOG-sources.md"
        if line not in cp.read_text():                      # reruns don't duplicate lines
            with open(cp, "a") as f: f.write(line)
    def save_registry():
        (REPO / "sources.yaml").write_text("# Context Pill source registry. Hand-edit names, feeds and criteria; the scanner rewrites the trust blocks.\n"
                                          "# No X handles in this public file. Caps: ~40 sources total.\n" + yaml.safe_dump(reg, sort_keys=False, width=140, allow_unicode=True))

    reg = yaml.safe_load((REPO / "sources.yaml").read_text())
    srcs = {s["id"]: s for s in reg["sources"]}
    reg.setdefault("tightened", {})                          # tier -> date; cleared only by the monthly review (by hand)
    lp = REPO / "ledger" / "claims.json"
    ledger = json.loads(lp.read_text()) if lp.exists() else {"started": START_DATE.isoformat(), "claims": [], "audits": []}
    ledger.setdefault("audits", [])
    non_scorable_ids = {c["id"] for c in ledger["claims"] if c.get("scorable") is False or c.get("category") == "take"}
    log(f"start {date} (scanner day {day_no}); {len(ledger['claims'])} claims in ledger")
    if not a.skip_extract and (raw / "claims.raw.json").exists():
        # feeds are fetched once a day, so a second full run would see no items and overwrite the day's drop with nothing
        log("refusing: today's extraction already exists; rerun with --skip-extract (no outputs were changed)")
        return 3

    # 1. resolution of claims due today (14 days after publication, or prediction deadline + 14)
    due = [c for c in ledger["claims"] if c["state"] == "pending" and c.get("scorable") and c["id"] not in non_scorable_ids
           and c["recheck_on"] <= date]
    res = extract.resolve([{"id": c["id"], "claim": c["claim"], "evidence": c["evidence"], "category": c["category"]} for c in due], WORK, date, log)
    res, why = notes.split(res, "outcome", notes.OUTCOMES, keep=(notes.CHANGE_FLAG,))   # an invalid outcome is not judged; model text stays in WORK
    for c in due:
        r = res.get(c["id"])
        if not r: continue
        out = r["outcome"]
        if out == "inconclusive":
            c["inconclusive_count"] = c.get("inconclusive_count", 0) + 1
            if c["inconclusive_count"] >= 2: c["state"] = "closed-inconclusive"
            else: c["recheck_on"] = (today + dt.timedelta(days=7)).isoformat()
            continue
        if c["category"] in ("tool", "automation") and out == "good" and not c.get("smoke_test_log"):
            out = "docs-verified"                    # no recorded smoke test -> docs-verified credit at most
        urls = notes.pick_urls(r.get("source_urls"), c["evidence"])
        out, dwhy = notes.downgrade(out, c["claim"], urls, r.get(notes.FLAG))   # good only if every fact is on a cited page
        if dwhy:
            why.setdefault(c["id"], {})["downgraded"] = {"from": r["outcome"], "to": out, "why": dwhy}
            log(f"resolve: {c['id']} {r['outcome']} -> {out}: {dwhy}")
        c["state"] = "resolved"; c["outcome"] = out; c["resolved_on"] = date
        c["resolution_note"] = notes.resolution_note(out, urls)
        if out in ("bad", "partial") and r.get(notes.CHANGE_FLAG) is True:
            # the resolver thinks the source itself changed its numbers, version or method after publication. The outcome
            # stands (the model never spares a source); the maintainer confirms with scanner.supersede, which undoes it
            c["source_change_flag"] = date
            changelog(f"re-check of {c['id']} ({c['source_id']}) scored {out} and flagged a possible source change after publication "
                      f"(new version, index or method); review and, if confirmed, record with python -m scanner.supersede")
    notes.save_reasoning(raw / "resolve.reasoning.json", why)

    # 2. trust update; apply drop, audit demotion/recovery, and per-tier tightening (>1 audit error in the last 20)
    def update_tightening():
        for base in ("T1", "T2"):
            last = [x for x in ledger["audits"] if not x.get("removed") and x["item_id"] not in non_scorable_ids
                    and (x.get("tier") or "").split("-")[0] == base and x["verdict"] in ("pass", "fail")][-20:]
            if sum(x["verdict"] == "fail" for x in last) > 1 and base not in reg["tightened"]:
                reg["tightened"][base] = date
                changelog(f"{base} research criterion tightened one level (>1 audit error in the last 20) until the next monthly review")
        return set(reg["tightened"])
    tightened = update_tightening()
    def rescore(s):
        prev = (s.get("trust") or {}).get("tier")
        old = s.get("trust") or {}
        t = trust.score(s, trust.claim_events(ledger["claims"], s["id"]), today)
        if s.get("audit_demoted") and not t["audit_demoted"]:
            s["audit_demoted"] = False; changelog(f"{s['name']} recovered from audit demotion (15+ resolved since, LB10 >= 0.80)")
        t.pop("audit_demoted", None)
        t["research_criterion"] = trust.research_criterion(t["tier"], day_no, tightened)
        t["updated"] = date
        t["items_seen_by_date"] = old.get("items_seen_by_date", {})
        t["items_seen"] = sum(t["items_seen_by_date"].values())
        t["scorable_claims"] = trust.count_scorable(ledger["claims"], s["id"])
        s["trust"] = t
        if prev and prev != t["tier"]:
            changelog(f"{s['name']} tier {prev} -> {t['tier']} (mean {t['trust_mean']}, LB10 {t['lb10']}, {t['resolved_tips']} resolved)")
        if t["tier"] == "drop" and s.get("enabled"):
            s["enabled"] = False; s["disabled_reason"] = f"dropped by trust rule on {date}"
            changelog(f"{s['name']} disabled: trust mean below 0.50 after 15+ resolved tips")
    for s in srcs.values():
        if s.get("enabled"): rescore(s)
    save_registry()          # fetch and extraction read today's tiers, drops and criteria

    # 3. fetch + video notes + extraction/verification
    craw = raw / "claims.raw.json"
    if a.skip_extract and craw.exists():
        items = json.loads((raw / "items.json").read_text()); claims = json.loads(craw.read_text())
    else:
        items, _ = fetch.run(REPO, WORK, date, a.hours)
        extract.gemini_video_notes(items, WORK, date, log)
        (raw / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
        claims = extract.extract(items, REPO, WORK, date, log, day_no) if items else []
        craw.write_text(json.dumps(claims, indent=1, ensure_ascii=False))
    per_src = {}
    for i in items:
        per_src[i["source_id"]] = per_src.get(i["source_id"], 0) + 1
    for sid, n in per_src.items():
        s = srcs.get(sid)
        if s and s.get("trust"):
            s["trust"]["items_seen_by_date"][date] = n          # idempotent on reruns
            s["trust"]["items_seen"] = sum(s["trust"]["items_seen_by_date"].values())

    # 4. publication checks: allowed source, cross-day dedupe, evidence alive, quote rules, tool ceiling, ids, recheck dates
    earlier_items = {(c.get("item_url"), c.get("category") == "take") for c in ledger["claims"] if c["date"] < date}
    earlier_stories = repeats.earlier_stories(ledger, date, log=log)
    stories = {False: [], True: []}
    for story in earlier_stories:
        stories[story.get("take", False)].append(story)
    same_day = {False: repeats.SameDay(), True: repeats.SameDay()}
    item_by_source_url = {(i["source_id"], i.get("url")): i for i in items}
    removed = {t["id"] for t in ledger.get("removed", [])}         # scanner.remove tombstones survive rebuilds
    drop, rejected = [], []
    take_counts, take_ids = Counter(), set()
    maybe_repeats = []                                            # kept but flagged; the review step reads raw/<date>/repeat-flags.json
    for c in claims:
        sid, category, item_url = c.get("source_id"), c.get("category"), c.get("item_url")
        is_take = category == "take"
        key = f"{sid}|{item_url}|{c.get('claim')}"
        if is_take:
            key += f"|take|{c.get('speaker')}"
        cid = date + "-" + format(_h(key), "x")[:8]
        if is_take:
            non_scorable_ids.add(cid)
        s = srcs.get(sid)
        if not s or not s.get("enabled") or (s.get("trust") or {}).get("tier") == "drop":
            rejected.append(f"{c.get('source_id')}: source not enabled"); continue
        if not is_take and category not in SCORABLE_CATS:
            rejected.append(f"{sid}: category {category!r}"); continue
        v = c.get("verification") or {}
        if is_take:
            if s.get("kind") not in TAKE_KINDS or v.get("status") != "attributed" or not gate.valid_attribution(c):
                rejected.append(f"{sid}: take without eligible source, attributed status, named speaker or original publication date"); continue
        elif v.get("status") == "attributed":
            rejected.append(f"{sid}: attributed status is only for takes"); continue
        excerpt, excerpt_url = c.get("excerpt"), c.get("excerpt_url")
        if is_take or excerpt is not None:
            item = item_by_source_url.get((sid, excerpt_url)) if notes.usable_url(excerpt_url) else None
            if (is_take and excerpt_url != item_url) or not quotes.matches_item(excerpt, item, raw / "pages", c.get("speaker") if is_take else None):
                if is_take:
                    rejected.append(f"{sid}: quote not found: {cid}"); continue
                c["excerpt"] = c["excerpt_url"] = None
        if is_take and cid not in removed:
            if cid in take_ids:
                rejected.append(f"{sid}: same-day duplicate take: {cid}"); continue
            if take_counts[sid] >= TAKE_PER_SOURCE or len(take_ids) >= TAKE_PER_DAY:
                rejected.append(f"{sid}: take cap reached: {cid}"); continue
        if item_url and (item_url, is_take) in earlier_items:
            rejected.append(f"{c['source_id']}: item already covered on an earlier day"); continue
        c["evidence"] = ([{"url": excerpt_url, "kind": "primary"}] if is_take else
                         [e for e in c.get("evidence", []) if e.get("url") and not util.blocked(e["url"])])
        if cid not in removed:                       # a tombstoned claim is dropped below and must not block another one
            what = f"{cid} ({c.get('item_url')}) {(c.get('claim') or '')[:80]!r}"
            rep = repeats.find_repeat(c, stories[is_take])
            if rep and rep[2]:
                rejected.append(f"{c['source_id']}: repeat story: {what}: {rep[1]}"); continue
            dup = same_day[is_take].check(c)
            if dup and dup[1]:
                rejected.append(f"{c['source_id']}: same-day duplicate: {what}: {dup[0]}"); continue
            for m in ([rep[1]] if rep else []) + ([dup[0]] if dup else []):
                maybe_repeats.append({"id": cid, "item_url": c.get("item_url"), "claim": c.get("claim"), "match": m})
                log(f"possible repeat (kept, for review): {what}: {m}")
            same_day[is_take].add(c)
            if is_take:
                take_counts[sid] += 1
                take_ids.add(cid)
        if is_take:
            v["method"] = gate.TAKE_METHOD
        else:
            alive = [util.url_alive(e["url"])[0] for e in c["evidence"]]
            if not c["evidence"] or any(x is False for x in alive):
                if v.get("status") in CHECKED:
                    v["status"] = "unverified"; v["method"] = (v.get("method", "") + "; evidence link failed our check").strip("; ")
            if category in ("tool", "automation") and v.get("status") == "verified" and not c.get("smoke_test_log"):
                v["status"] = "docs-verified"; v["method"] = v.get("method", "") + " (capped: no sandbox smoke test)"
            if v.get("status") == "face-value-T1" and (day_no <= trust.PROVISIONAL_DAYS or not s["trust"]["tier"].startswith("T1") or "T1" in tightened
                                                       or category in ("tool", "automation")):
                v["status"] = "unverified"; v["method"] = v.get("method", "") + " (face value not allowed for this source today)"
        t = s["trust"]
        # METHOD.md: every specific, checkable claim (release, tool, technique, research number, dated prediction) is scorable
        c["scorable"] = category != "take"
        deadline = c.get("prediction_deadline") if c["scorable"] else None
        base = dt.date.fromisoformat(deadline) if deadline else today
        drop.append({
            "id": cid, "date": date, "category": c["category"], "claim": c["claim"], "why_it_matters": c.get("why_it_matters"),
            "try_this": c.get("try_this") if is_take or v.get("status") in CHECKED or c.get("try_this") == "not recommended: terms risk" else None,
            "source": {"id": s["id"], "name": s["name"], "home_url": s["home_url"], "tier": t["tier"], "trust_mean": t["trust_mean"],
                       "lb10": t["lb10"], "seed": bool(s.get("seed"))},
            "also_seen_in": [srcs[x]["name"] for x in c.get("also_seen_in", []) if x in srcs and x != s["id"]],
            "item_url": c.get("item_url"), "evidence": c["evidence"],
            "verification": {"status": v.get("status", "unverified"), "method": v.get("method", ""), "checked_at": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
                             "provisional": day_no <= trust.PROVISIONAL_DAYS, "t1_audit_sample": False, "audit_verdict": None, "audit_note": None},
            "excerpt": c.get("excerpt"), "excerpt_url": c.get("excerpt_url"),
            "scorable": c["scorable"], "prediction_deadline": deadline,
            "state": "pending", "recheck_on": (base + dt.timedelta(days=trust.RECHECK_DAYS)).isoformat() if c["scorable"] else None}
            | ({"speaker": c["speaker"], "said_on": c["said_on"]} if is_take else {}))
    rejected += [f"{d['source']['id']}: {d['id']} was removed by the maintainer" for d in drop if d["id"] in removed]
    drop = [d for d in drop if d["id"] not in removed]
    for r in rejected:
        log(f"rejected at publication: {r}")
    (raw / "repeat-flags.json").write_text(json.dumps(maybe_repeats, indent=1, ensure_ascii=False))

    # 5. T1 full-audit sample (runs from day 1): deterministic 1-in-10 by id, at least one per day when T1 items exist.
    # Sampling uses each source's tier as of the start of the day, so a same-day rerun after a demotion audits the same items.
    def start_tier(d):
        sx = srcs[d["source"]["id"]]
        return sx.get("audit_demoted_from") if sx.get("audit_demoted_on") == date else d["source"]["tier"]
    t1 = [d for d in drop if d["scorable"] and (start_tier(d) or "").startswith("T1") and d["verification"]["status"] in CHECKED]
    sample = [d for d in t1 if _h(d["id"]) % AUDIT_RATE == 0] or sorted(t1, key=lambda d: _h(d["id"]))[:1]
    ares_p = raw / "audit.result.json"
    # audit.result.json keeps only the verdict, source_urls and the fields below (superseded_by is set by the maintainer; cause too,
    # or by the scanner with cause_by "auditor" when the auditor says the fail was our summary's)
    AKEEP = ("cause", "cause_by", "superseded_by", "source_id", "tier", "evidence_url")
    araw = json.loads(ares_p.read_text()) if (a.skip_extract and ares_p.exists()) else {}
    ares, why = notes.split(araw, "verdict", notes.VERDICTS, AKEEP)
    ares = {k: r for k, r in ares.items() if k not in non_scorable_ids}
    notes.save_reasoning(raw / "audit.reasoning.json", why)
    # an entry with an invalid verdict is re-audited, but its maintainer fields are kept (and written back with the new verdict)
    # (an auditor-set cause is not the maintainer's: the re-audit decides it again; any other cause is the maintainer's)
    MKEEP = ("cause", "superseded_by")
    def mfields(r):
        return {f: r[f] for f in MKEEP if r.get(f) and not (f == "cause" and notes.auditor_set_cause(r))}
    held = {k: mfields(r) for k, r in (araw.items() if isinstance(araw, dict) else ())
            if isinstance(k, str) and isinstance(r, dict) and k not in ares and k not in non_scorable_ids and mfields(r)}
    # a claim rewritten after a failed audit gets a new id; the maintainer links it ("superseded_by") and it is always re-audited
    replaces = {r["superseded_by"]: k for k, r in (held | ares).items() if r.get("superseded_by")}
    sample += [d for d in drop if d["scorable"] and d["id"] in replaces and d not in sample]
    sample_tier = {d["id"]: start_tier(d) for d in sample}             # recorded before any demotion below
    todo = [d for d in sample if d["id"] not in ares]
    if todo:
        aout = extract.audit([{"id": d["id"], "claim": d["claim"], "evidence": d["evidence"], "source": d["source"]["name"]} for d in todo],
                             WORK, date, log)
        got, why = notes.split(aout, "verdict", notes.VERDICTS)
        for k, r in got.items():
            if k in {d["id"] for d in todo}:                                # never ids it wasn't asked about
                ares[k], bad = notes.with_audit_cause(r, aout.get(k), held.get(k))   # METHOD 3: a fail that was our summary's
                if bad is not None:
                    why.setdefault(k, {})["failure_cause_invalid"] = bad
        notes.save_reasoning(raw / "audit.reasoning.json", why)
        ares_p.write_text(json.dumps(held | ares, indent=1))
    log(f"audit: {sum(1 for d in sample if d['id'] in ares)} of {len(sample)} sampled items judged")
    ledger["audits"] = [x for x in ledger["audits"] if x["date"] != date]
    def demote(s):
        if s.get("audit_demoted"):
            return
        s["audit_demoted_from"] = (s.get("trust") or {}).get("tier")
        s["audit_demoted"] = True; s["audit_demoted_on"] = date
        rescore(s)
        for x in drop:                              # every item from the demoted source shows its new tier
            if x["source"]["id"] == s["id"]:
                x["source"]["tier"] = s["trust"]["tier"]
                if x["verification"]["status"] == "face-value-T1":   # taken on trust that no longer holds: withhold
                    x["verification"]["status"] = "unverified"; x["try_this"] = None
                    x["verification"]["method"] += "; withheld: source demoted by today's audit"
    # METHOD 3: a failure caused by our own summary is logged (and counts toward tightening) but does not demote the source
    def demotes(r):
        return r.get("verdict") == "fail" and r.get("cause") != "our summary"
    # failed audits of claims that are no longer in the drop (rewritten or withdrawn) stay in the public log
    drop_ids = {d["id"] for d in drop}
    for k, r in ares.items():
        if k in drop_ids or not k.startswith(date):
            continue
        rec = {"date": date, "item_id": k, "source_id": r.get("source_id"), "tier": r.get("tier"), "verdict": r["verdict"]}
        rec.update({f: r[f] for f in ("cause", "superseded_by") if r.get(f)})
        if k in removed:
            rec["removed"] = True
        ledger["audits"].append(rec)
        sname = srcs[r["source_id"]]["name"] if r.get("source_id") in srcs else r.get("source_id")
        note = notes.audit_note(r["verdict"], notes.pick_urls(r.get("source_urls"), [{"url": r.get("evidence_url")}]))
        changelog(f"audit of {k} ({sname}): {r['verdict']} ({note})"
                  + (f"; cause: {r['cause']}" if r.get("cause") else "") + (f"; replaced by {r['superseded_by']}" if r.get("superseded_by") else ""))
        if r["verdict"] == "fail":
            tightened = update_tightening()
            if demotes(r) and r.get("source_id") in srcs:
                demote(srcs[r["source_id"]])
    # any item with a recorded verdict today is treated as audited, even if it isn't in today's sample on a rerun
    audited = sample + [d for d in drop if d["scorable"] and d["id"] in ares and d not in sample]
    for d in audited:
        r = ares.get(d["id"]) or {"verdict": "not run"}
        note = notes.audit_note(r["verdict"], notes.pick_urls(r.get("source_urls"), d["evidence"]))
        v = d["verification"]; v["t1_audit_sample"] = True; v["audit_verdict"] = r["verdict"]; v["audit_note"] = note
        ledger["audits"].append({"date": date, "item_id": d["id"], "source_id": d["source"]["id"],
                                 "tier": sample_tier.get(d["id"]) or start_tier(d), "verdict": r["verdict"]}
                                | ({"cause": r["cause"]} if r.get("cause") else {}))
        if d["id"] in ares:                          # remembered so the record survives if the item is later rewritten or removed
            ares[d["id"]].setdefault("source_id", d["source"]["id"]); ares[d["id"]].setdefault("tier", ledger["audits"][-1]["tier"])
            if ev := notes.pick_url(None, d["evidence"]):      # the audit note's fallback URL once the item has left the drop
                ares[d["id"]]["evidence_url"] = ev
        if d["id"] in replaces:
            v["method"] += f"; an earlier draft of this summary ({replaces[d['id']]}) failed the full audit and was corrected, then re-audited"
        changelog(f"audit of {d['id']} ({d['source']['name']}): {r['verdict']} ({note})" + (f"; cause: {r['cause']}" if r.get("cause") else "")
                  + (f"; re-audit of the corrected {replaces[d['id']]}" if d["id"] in replaces else ""))
        if r["verdict"] == "fail":
            v["status"] = "unverified"; v["method"] += "; failed the full audit"; d["try_this"] = None
            tightened = update_tightening()
            if demotes(r):
                demote(srcs[d["source"]["id"]])

    if ares or held or ares_p.exists():
        ares_p.write_text(json.dumps(held | ares, indent=1))
    # 6. ledger (stable ids; reruns replace today's entries)
    corrected = {c["id"]: c for c in ledger["claims"] if c["date"] == date and c.get("state") in ("corrected", "superseded")}   # scanner.correct/supersede survive rebuilds
    for d in drop:
        if d["id"] in corrected:
            d["state"] = corrected[d["id"]]["state"]
    ledger["claims"] = [c for c in ledger["claims"] if c["date"] != date or (c["id"] in corrected and c["id"] not in {d["id"] for d in drop})]
    for d in drop:
        ledger["claims"].append({k: d[k] for k in ("id", "date", "category", "claim", "why_it_matters", "try_this", "evidence", "verification",
                                                   "excerpt", "excerpt_url", "item_url", "scorable", "prediction_deadline", "state", "recheck_on")}
                                | {k: d[k] for k in ("speaker", "said_on") if k in d}
                                | {"source_id": d["source"]["id"], "status_at_publication": d["verification"]["status"]})
        if d["id"] in corrected:
            ledger["claims"][-1].update({k: corrected[d["id"]][k] for k in ("correction", "reversed_on", "source_change", "outcome", "resolved_on",
                                                                            "resolution_note") if k in corrected[d["id"]]})
    trust.set_scorable_claims(srcs.values(), ledger["claims"])

    # 7. write public outputs
    lp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    save_registry()
    render.write(REPO, date, day_no, drop, srcs, len(items), len(per_src))
    flags = health(date, len(items), drop, srcs)
    (raw / "health.json").write_text(json.dumps(flags, indent=1, ensure_ascii=False))
    for f in flags:
        log(f"health (for review): {f}")
    problems = gate.check(REPO, require_private=os.environ.get("CP_REQUIRE_PRIVATE") == "1")
    log("gate: PASS" if not problems else "gate: FAIL\n  " + "\n  ".join(problems))
    log(f"done: {len(drop)} items in latest.md; stopped hosts today: {util.stopped_hosts() or 'none'}")
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())

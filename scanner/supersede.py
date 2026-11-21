"""Record that a claim was accurate when we published it, but the source later changed its own numbers, version or method
(METHOD.md: source changed). Example: a benchmark index re-scored every model in a new version after our check.
Usage: python -m scanner.supersede <claim-id> --changed-on YYYY-MM-DD --note "..." [--now "..."] [--date YYYY-MM-DD]
  --changed-on  the day the source changed (on or after the claim's date: it must have been accurate when published)
  --note        what changed, with dates, e.g. "Oct 23 launch · Oct 27 index update (v5)"
  --now         what the source shows now (defaults to the reason of an earlier correction, if any)
- The claim gets outcome "source-changed" and state "superseded". It is never scored: it gives the source no credit and no
  beta +1 (trust.WEIGHTS has no entry for it, like inconclusive), and it counts as neither held nor wrong.
- A claim that was pending, resolved (any outcome) or corrected can be superseded. An earlier outcome, correction and
  reversal are kept under source_change (outcome_before, state_before, correction_before) and removed from the claim, so a
  beta +1 from a "bad" re-check or a reversed credit is undone. The source's trust is recomputed from the ledger at once
  (as scanner.run does each day), sources.yaml is rewritten and latest.* re-rendered with the new numbers (the day's archive is left as published).
- The public resolution note keeps its fixed form ("Outcome: source-changed. Source: <url>") and URLs.
Only the maintainer records this, never the model: a re-check that suspects a change only flags it (source_change_flag)."""
import json, re, sys, datetime as dt
from pathlib import Path
import yaml
from . import trust, render, gate

REPO = Path(__file__).resolve().parent.parent
START_DATE = dt.date(2026, 10, 16)           # as in scanner.run


def _arg(argv, k):
    return argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else None


def main(argv):
    if not argv or argv[0].startswith("--") or not _arg(argv, "--changed-on") or not _arg(argv, "--note"):
        print(__doc__); return 2
    cid, note, now = argv[0], _arg(argv, "--note"), _arg(argv, "--now")
    changed_on = dt.date.fromisoformat(_arg(argv, "--changed-on"))
    today = dt.date.fromisoformat(_arg(argv, "--date")) if _arg(argv, "--date") else dt.date.today()
    lp = REPO / "ledger" / "claims.json"; ledger = json.loads(lp.read_text())
    c = next((c for c in ledger["claims"] if c["id"] == cid), None)
    if not c:
        print(f"no claim {cid}"); return 1
    if c.get("state") == "superseded":
        print(f"{cid} is already superseded ({c['source_change'].get('changed_on')}); nothing done"); return 1
    if c.get("state") not in ("pending", "resolved", "corrected"):
        print(f"{cid} is {c.get('state')}: only pending, resolved or corrected claims can be superseded"); return 1
    if changed_on < dt.date.fromisoformat(c["date"]) or changed_on > today:
        print(f"--changed-on {changed_on} must be between the claim's date {c['date']} and {today}"); return 1
    sc = {"recorded_on": today.isoformat(), "changed_on": changed_on.isoformat(), "note": note,
          "now_shows": now or (c.get("correction") or {}).get("reason"), "state_before": c.get("state"), "outcome_before": c.get("outcome")}
    for k in ("correction", "reversed_on", "source_change_flag"):
        if k in c:
            sc[k + "_before"] = c.pop(k)
    c["source_change"] = {k: v for k, v in sc.items() if v is not None}
    c["state"] = "superseded"; c["outcome"] = "source-changed"
    c["resolved_on"] = c.get("resolved_on") or today.isoformat()
    if c.get("resolution_note"):
        c["resolution_note"] = re.sub(r"^Outcome: [a-z-]+\.", "Outcome: source-changed.", c["resolution_note"])
    else:
        c["resolution_note"] = "Outcome: source-changed."
    lp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))

    # trust comes only from the ledger's scored outcomes: rescoring drops this claim's credit or beta +1
    reg = yaml.safe_load((REPO / "sources.yaml").read_text())
    srcs = {s["id"]: s for s in reg["sources"] if "id" in s}
    s = srcs.get(c["source_id"]); effect = "source not in sources.yaml: no trust change"
    if s is not None:
        old = s.get("trust") or {}
        ev = [{"date": x["resolved_on"], "outcome": x["outcome"], "reversed_on": x.get("reversed_on")} for x in ledger["claims"]
              if x["source_id"] == s["id"] and x["state"] in ("resolved", "corrected") and x.get("outcome") in trust.WEIGHTS]
        t = trust.score(s, ev, today); t.pop("audit_demoted", None)
        t["research_criterion"] = trust.research_criterion(t["tier"], (today - START_DATE).days + 1, set(reg.get("tightened") or {}))
        t["updated"] = today.isoformat()
        t.update({k: old[k] for k in ("items_seen_by_date", "items_seen", "scorable_claims") if k in old})
        s["trust"] = t
        # the drop rule as in scanner.run (which only rescores enabled sources, so a drop this claim caused is lifted here)
        if t["tier"] == "drop" and s.get("enabled"):
            s["enabled"] = False; s["disabled_reason"] = f"dropped by trust rule on {today}"
        elif t["tier"] != "drop" and not s.get("enabled") and str(s.get("disabled_reason", "")).startswith("dropped by trust rule"):
            s["enabled"] = True; s.pop("disabled_reason")
        effect = (f"not scored; trust recomputed: mean {old.get('trust_mean')} -> {t['trust_mean']}, LB10 {old.get('lb10')} -> {t['lb10']}, "
                  f"tier {old.get('tier')} -> {t['tier']}" + ("" if s.get("enabled") else ", source disabled by the drop rule"))
        (REPO / "sources.yaml").write_text(
            "# Context Pill source registry. Hand-edit names, feeds and criteria; the scanner rewrites the trust blocks.\n"
            "# No X handles in this public file. Caps: ~40 sources total.\n" + yaml.safe_dump(reg, sort_keys=False, width=140, allow_unicode=True))
    with open(REPO / "CHANGELOG-sources.md", "a") as f:
        f.write(f"- {today}: source changed on {cid} ({c['source_id']}): accurate when published on {c['date']}; the source changed it on "
                f"{changed_on} ({note}). Outcome {sc['outcome_before'] or 'none'} -> source-changed, state {sc['state_before']} -> superseded ({effect})\n")
    lj = REPO / "latest.json"
    if s is not None and lj.exists():          # latest.* show each source's trust: re-render with the new numbers
        meta = json.loads(lj.read_text()); date = meta["date"]
        arch = REPO / "daily" / date[:4] / date[5:7] / f"{date}.json"
        if arch.exists():
            meta = json.loads(arch.read_text())
            for d in meta["items"]:
                if d["source"]["id"] == s["id"]:
                    d["source"].update(tier=s["trust"]["tier"], trust_mean=s["trust"]["trust_mean"], lb10=s["trust"]["lb10"])
            render.write(REPO, date, meta["scanner_day"], meta["items"], srcs, meta.get("items_read", 0), meta.get("sources_with_items", 0),
                         write_archive=False)
    pr = gate.check(REPO)
    print(f"recorded: {effect}; gate: {'PASS' if not pr else 'FAIL ' + '; '.join(pr)}")
    return 0 if not pr else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

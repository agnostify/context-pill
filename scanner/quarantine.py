"""Undo resolutions a review blocked, so the claims are resolved again later (never hand-edit outcomes).
Usage: python -m scanner.quarantine YYYY-MM-DD <claim-id> [<claim-id> ...] --reason "..."
For each claim resolved on that date: state back to pending, outcome/resolved_on/resolution_note cleared, recheck_on set to
date + 7 days. Trust is recomputed from the ledger for the affected sources (as scanner.run does), today's CHANGELOG-sources.md
lines naming those claims are removed, the day's archive and latest.* are re-rendered with the new trust, and the reason and
ids are recorded in WORK raw/<date>/quarantine.json. Claims not resolved on that date are left alone. Re-running is a no-op.
Exits non-zero if the gate fails."""
import json, os, sys, datetime as dt
from pathlib import Path
import yaml
from . import render, gate, trust

REPO = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("CP_WORK", REPO.parent / "context-pill-work"))
START_DATE = dt.date(2026, 10, 16)           # as in scanner.run
REQUEUE_DAYS = 7


def main(argv):
    if "--reason" not in argv or argv.index("--reason") + 1 >= len(argv):
        print(__doc__); return 2
    i = argv.index("--reason"); reason = argv[i + 1]
    args = [a for a in argv[:i] + argv[i + 2:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__); return 2
    date, ids = args[0], set(args[1:])
    today = dt.date.fromisoformat(date); day_no = (today - START_DATE).days + 1
    lp = REPO / "ledger" / "claims.json"; ledger = json.loads(lp.read_text())
    reset, skipped = [], []
    for c in ledger["claims"]:
        if c["id"] not in ids:
            continue
        if c.get("state") != "resolved" or c.get("resolved_on") != date:
            skipped.append(f"{c['id']} ({c.get('state')}, resolved_on {c.get('resolved_on')})"); continue
        c["state"] = "pending"
        for k in ("outcome", "resolved_on", "resolution_note"):
            c.pop(k, None)
        c["recheck_on"] = (today + dt.timedelta(days=REQUEUE_DAYS)).isoformat()
        reset.append(c)
    missing = sorted(ids - {c["id"] for c in ledger["claims"]})
    for m in skipped + [f"{i} (not in ledger)" for i in missing]:
        print(f"left alone: {m}")
    if reset:
        lp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
        # trust comes only from the ledger's resolution events, so rescoring the sources undoes the removed credit
        reg = yaml.safe_load((REPO / "sources.yaml").read_text())
        srcs = {s["id"]: s for s in reg["sources"] if "id" in s}
        tightened = set(reg.get("tightened") or {})
        for sid in sorted({c["source_id"] for c in reset} & set(srcs)):
            s = srcs[sid]; old = s.get("trust") or {}
            ev = [{"date": c["resolved_on"], "outcome": c["outcome"], "reversed_on": c.get("reversed_on")} for c in ledger["claims"]
                  if c["source_id"] == sid and c["state"] in ("resolved", "corrected") and c.get("outcome") in trust.WEIGHTS]
            t = trust.score(s, ev, today); t.pop("audit_demoted", None)
            t["research_criterion"] = trust.research_criterion(t["tier"], day_no, tightened)
            t["updated"] = date
            t.update({k: old[k] for k in ("items_seen_by_date", "items_seen", "scorable_claims") if k in old})
            s["trust"] = t
            print(f"{s['name']}: trust {old.get('trust_mean')} -> {t['trust_mean']}, tier {old.get('tier')} -> {t['tier']}")
        (REPO / "sources.yaml").write_text(
            "# Context Pill source registry. Hand-edit names, feeds and criteria; the scanner rewrites the trust blocks.\n"
            "# No X handles in this public file. Caps: ~40 sources total.\n" + yaml.safe_dump(reg, sort_keys=False, width=140, allow_unicode=True))
        cp = REPO / "CHANGELOG-sources.md"
        rids = {c["id"] for c in reset}
        lines = cp.read_text().splitlines(keepends=True)
        cp.write_text("".join(l for l in lines if not (l.startswith(f"- {date}:") and any(i in l for i in rids))))
        arch = REPO / "daily" / date[:4] / date[5:7] / f"{date}.json"
        if arch.exists():                        # the day's items show their source's trust: re-render with the new numbers
            meta = json.loads(arch.read_text())
            for d in meta["items"]:
                t = (srcs.get(d["source"]["id"]) or {}).get("trust")
                if d["source"]["id"] in {c["source_id"] for c in reset} and t:
                    d["source"].update(tier=t["tier"], trust_mean=t["trust_mean"], lb10=t["lb10"])
            latest_is_this = json.loads((REPO / "latest.json").read_text()).get("date") == date if (REPO / "latest.json").exists() else False
            render.write(REPO, date, meta["scanner_day"], meta["items"], srcs, meta.get("items_read", 0), meta.get("sources_with_items", 0),
                         write_latest=latest_is_this)
        qp = WORK / "raw" / date / "quarantine.json"; qp.parent.mkdir(parents=True, exist_ok=True)
        log = json.loads(qp.read_text()) if qp.exists() else []
        log.append({"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "reason": reason, "ids": sorted(rids),
                    "recheck_on": reset[0]["recheck_on"]})
        qp.write_text(json.dumps(log, indent=1, ensure_ascii=False))
    pr = gate.check(REPO)
    print(f"quarantined {len(reset)} claim(s); gate: {'PASS' if not pr else 'FAIL ' + '; '.join(pr)}")
    return 0 if not pr else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Remove published items everywhere (review BLOCK fixes and takedowns): latest.*, the dated archive (.md and .json),
weekly files, and the ledger. Usage: python -m scanner.remove YYYY-MM-DD <item-id> [<item-id> ...] [--reason "..."]
       python -m scanner.remove YYYY-MM-DD --all [--reason "..."]   (a held day: removes every item, works on an empty day)
Re-renders the affected day (and latest.* if it is the latest day), reports weekly files that still mention an id,
and exits non-zero if anything remains or the gate fails."""
import json, sys, datetime as dt
from pathlib import Path
import yaml
from . import render, gate

REPO = Path(__file__).resolve().parent.parent


def main(argv):
    reason = argv[argv.index("--reason") + 1] if "--reason" in argv else "removed after review"
    args = [a for a in argv if not a.startswith("--") and a != reason]
    if len(args) < 2 and not (args and "--all" in argv):
        print(__doc__); return 2
    date = args[0]
    arch = REPO / "daily" / date[:4] / date[5:7] / f"{date}.json"
    if not arch.exists():
        print(f"no archive for {date}; nothing to remove"); return 0
    meta = json.loads(arch.read_text())
    ids = {d["id"] for d in meta["items"]} if "--all" in argv else set(args[1:])
    keep = [d for d in meta["items"] if d["id"] not in ids]
    srcs = {s["id"]: s for s in yaml.safe_load((REPO / "sources.yaml").read_text())["sources"] if "id" in s}
    lp = REPO / "ledger" / "claims.json"
    ledger = json.loads(lp.read_text())
    ledger["claims"] = [c for c in ledger["claims"] if c["id"] not in ids]
    tomb = ledger.setdefault("removed", [])      # tombstones: a same-day rebuild (run --skip-extract) never republishes these
    tomb += [{"id": i, "date": date, "reason": reason} for i in sorted(ids) if i not in {t["id"] for t in tomb}]
    for x in ledger.get("audits", []):            # keep audit history, but mark it so tightening ignores removed items
        if x["item_id"] in ids:
            x["removed"] = True
    lp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    latest_is_this = json.loads((REPO / "latest.json").read_text()).get("date") == date
    render.write(REPO, date, meta["scanner_day"], keep, srcs, meta.get("items_read", 0), meta.get("sources_with_items", 0),
                 write_latest=latest_is_this)
    with open(REPO / "CHANGELOG-sources.md", "a") as f:
        f.write(f"- {dt.date.today()}: removed {len(ids)} item(s) from {date} ({reason})\n")
    left = [str(p.relative_to(REPO)) for p in [*REPO.glob("latest.*"), *(REPO / "daily").rglob("*.*"), *(REPO / "weekly").rglob("*.*")]
            if p.is_file() and any(i in p.read_text(errors="ignore") for i in ids)]
    if any(c["id"] in ids for c in json.loads(lp.read_text())["claims"]):
        left.append("ledger/claims.json")
    pr = gate.check(REPO)
    print(f"removed {len(meta['items']) - len(keep)}; still mentioned in: {left or 'nothing'}; gate: {'PASS' if not pr else 'FAIL ' + '; '.join(pr)}")
    if left:
        print("edit those files by hand (weekly briefs are prose), then re-run the gate")
    return 0 if not (pr or left) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Record that a published claim was wrong (METHOD.md: corrections).
Usage: python -m scanner.correct <claim-id> --reason "..." [--date YYYY-MM-DD]
- The claim's state always becomes "corrected".
- Pending claim: closed, never scored. Resolved within the last 90 days: its credit is reversed and the source gets beta +1
  dated today. Resolved earlier: no trust change.
The claim is also labeled corrected in the ledger; remove it from published files with scanner.remove if needed."""
import json, sys, datetime as dt
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main(argv):
    if not argv or argv[0].startswith("--") or "--reason" not in argv:
        print(__doc__); return 2
    cid = argv[0]; reason = argv[argv.index("--reason") + 1]
    today = dt.date.fromisoformat(argv[argv.index("--date") + 1]) if "--date" in argv else dt.date.today()
    lp = REPO / "ledger" / "claims.json"; ledger = json.loads(lp.read_text())
    c = next((c for c in ledger["claims"] if c["id"] == cid), None)
    if not c:
        print(f"no claim {cid}"); return 1
    if c.get("state") == "corrected":
        print(f"{cid} is already corrected ({c.get('correction', {}).get('on')}); nothing done"); return 1
    prev = c.get("state")
    c["correction"] = {"on": today.isoformat(), "reason": reason, "state_before": prev}
    c["state"] = "corrected"                       # never "pending" again, so it can't earn credit at resolution
    if prev == "pending":
        effect = "pending claim closed as corrected: it will never be scored"
    elif prev == "resolved" and c.get("resolved_on") and (today - dt.date.fromisoformat(c["resolved_on"])).days <= 90:
        c["reversed_on"] = today.isoformat(); effect = "trust credit reversed, beta +1"
    else:
        effect = "no trust change (resolved more than 90 days ago, or not scored)"
    with open(REPO / "CHANGELOG-sources.md", "a") as f:
        f.write(f"- {today}: correction on {cid} ({c['source_id']}): {reason} ({effect})\n")
    lp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    print("recorded"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Pre-publication gate (run after every scan and before every push). Returns a list of problems; empty = PASS.
Checks every file in the repo (no exemptions): no secrets, nothing from X (no X/Twitter links or post IDs),
no maintainer-private text. Private patterns live OUTSIDE the repo ($CP_PRIVATE_PATTERNS, default
$CP_WORK/private-patterns.txt), so the public code never contains them. Also validates latest.json and every
daily/weekly archive JSON against schema/drop.schema.json, quote length/attribution, and the tool-status ceiling."""
import json, os, re
from pathlib import Path

ALLOWED_TOP = {"README.md", "llms.txt", "latest.md", "latest.json", "breaking.json", "breaking.md", "daily", "weekly", "scanner", "sources.yaml", "schema", "METHOD.md",
               "CHANGELOG-sources.md", "TAKEDOWN.md", "skill", "LICENSE", "LICENSE-CONTENT", "ledger", "feedback", ".gitignore", ".git",
               ".claude-plugin", "plugin", "mcp"}
SECRET = [r"sk-[A-Za-z0-9_-]{20,}", r"sk-ant-[A-Za-z0-9_-]{10,}", r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"AIza[0-9A-Za-z_-]{35}",
          r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}", r"xox[abpr]-[A-Za-z0-9-]{10,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
          r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"]
XPAT = [r"https?://([a-z0-9-]+\.)*(x|twitter)\.com/[^\s)\"']+", r"https?://t\.co/\w+", r"/status(es)?/\d{10,}"]
TOOL_CATS = ("tool", "automation")
CHECKED = ("verified", "docs-verified", "vendor-claim", "face-value-T1")


def private_patterns():
    """Returns (patterns, problem). Patterns: one regex per line, '#' comments allowed. File is never committed."""
    p = os.environ.get("CP_PRIVATE_PATTERNS") or str(Path(os.environ.get("CP_WORK", Path(__file__).resolve().parents[2] / "context-pill-work")) / "private-patterns.txt")
    try:
        pats = [l.strip() for l in Path(p).read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
        return pats, None if pats else "private-patterns file is empty"
    except OSError:
        return [], "private-patterns file not found (set CP_PRIVATE_PATTERNS); private-text check could not run"


def scan_text(rel, t, private):
    out = []
    for pat in SECRET:
        if re.search(pat, t):
            out.append(f"possible secret in {rel} (pattern #{SECRET.index(pat)})")
    for pat in XPAT:
        if re.search(pat, t):
            out.append(f"X link or post ID in {rel}")
    for i, pat in enumerate(private):
        if re.search(pat, t):
            out.append(f"private text in {rel} (private pattern #{i + 1})")
    return out


# --- minimal JSON Schema subset validator (type, required, enum, properties, items, $ref, pattern, maxLength, minimum)
_T = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}


def _validate(x, sch, root, path, out):
    if "$ref" in sch:
        node = root
        for k in sch["$ref"].lstrip("#/").split("/"):
            node = node[k]
        return _validate(x, node, root, path, out)
    if "enum" in sch and x not in sch["enum"]:
        out.append(f"{path}: {x!r} not in allowed values"); return
    if "type" in sch:
        ts = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(isinstance(x, _T[t]) and not (t in ("integer", "number") and isinstance(x, bool)) for t in ts):
            out.append(f"{path}: wrong type"); return
    if isinstance(x, dict):
        for k in sch.get("required", []):
            if k not in x:
                out.append(f"{path}: missing {k}")
        for k, sub in sch.get("properties", {}).items():
            if k in x:
                _validate(x[k], sub, root, f"{path}.{k}", out)
    if isinstance(x, list) and "items" in sch:
        for i, v in enumerate(x):
            _validate(v, sch["items"], root, f"{path}[{i}]", out)
    if isinstance(x, str):
        if "pattern" in sch and not re.search(sch["pattern"], x):
            out.append(f"{path}: does not match pattern")
        if "maxLength" in sch and len(x) > sch["maxLength"]:
            out.append(f"{path}: too long")
    if isinstance(x, (int, float)) and not isinstance(x, bool) and "minimum" in sch and x < sch["minimum"]:
        out.append(f"{path}: below minimum")


def check_item(d, where):
    out = []
    pid = f"{where}:{d.get('id')}"
    src = d.get("source") or {}
    if not src.get("home_url") or not src.get("name"):
        out.append(f"{pid}: missing attribution")
    v = d.get("verification") or {}
    if v.get("status") in CHECKED and not d.get("evidence"):
        out.append(f"{pid}: checked status without evidence")
    if d.get("category") in TOOL_CATS and v.get("status") == "verified" and not d.get("smoke_test_log"):
        out.append(f"{pid}: tool/automation marked verified without a smoke-test log (ceiling is docs-verified)")
    if v.get("status") not in CHECKED and d.get("try_this") and d["try_this"] != "not recommended: terms risk":
        out.append(f"{pid}: actionable try-this step on an unverified/blocked item")
    if v.get("status") == "face-value-T1" and (d.get("category") in TOOL_CATS or v.get("provisional") or not str(src.get("tier", "")).startswith("T1")):
        out.append(f"{pid}: face-value-T1 on a tool/automation, a provisional item or a non-T1 source")
    if d.get("excerpt"):
        if len(d["excerpt"].split()) > 25:
            out.append(f"{pid}: quote over 25 words")
        if not d.get("excerpt_url"):
            out.append(f"{pid}: quote without a source URL")
    return out


def check_drop(path, schema, rel):
    out = []
    try:
        doc = json.loads(path.read_text())
    except Exception as e:
        return [f"{rel}: unreadable JSON ({type(e).__name__})"]
    errs = []
    _validate(doc, schema, schema, rel, errs)
    out += errs[:20]
    for d in doc.get("items", []):
        out += check_item(d, rel)
    return out


def check(repo, require_private=False):
    repo = Path(repo); problems = []
    private, perr = private_patterns()
    if perr and require_private:
        problems.append(perr)
    for p in repo.iterdir():
        if p.name not in ALLOWED_TOP and not p.name.startswith(".venv") and p.name != "__pycache__":
            problems.append(f"unexpected path at repo root: {p.name}")
    for f in repo.rglob("*"):
        if not f.is_file() or "/.git/" in str(f) or "__pycache__" in str(f) or "/.venv" in str(f):
            continue
        try:
            t = f.read_text(errors="ignore")
        except Exception:
            continue
        problems += scan_text(f.relative_to(repo), t, private)
    schema = json.loads((repo / "schema" / "drop.schema.json").read_text())
    for f in [repo / "latest.json", *sorted((repo / "daily").rglob("*.json")), *sorted((repo / "weekly").rglob("*.json"))]:
        if f.exists():
            problems += check_drop(f, schema, f.relative_to(repo))
    bschema = json.loads((repo / "schema" / "breaking.schema.json").read_text())
    for rel, get in (("breaking.json", lambda d: d), ("latest.json", lambda d: d.get("breaking"))):
        f = repo / rel
        if f.exists():
            try:
                b = get(json.loads(f.read_text()))
            except Exception as e:
                problems.append(f"{rel}: unreadable JSON ({type(e).__name__})"); continue
            if b is not None:
                errs = []
                _validate(b, bschema, bschema, f"{rel}:breaking", errs)
                problems += errs[:20]
    lp =repo / "ledger" / "claims.json"
    if lp.exists():
        for c in json.loads(lp.read_text()).get("claims", []):
            if c.get("excerpt") and (len(c["excerpt"].split()) > 25 or not c.get("excerpt_url")):
                problems.append(f"ledger:{c['id']}: quote over 25 words or without URL")
    return problems


if __name__ == "__main__":
    import sys
    pr = check(Path(__file__).resolve().parent.parent, require_private="--require-private" in sys.argv)
    print("gate: PASS" if not pr else "gate: FAIL\n" + "\n".join(pr)); sys.exit(1 if pr else 0)

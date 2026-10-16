"""Pre-push check over everything that would leave the machine. Prints file names and pattern numbers, never matched values.
Usage: python -m scanner.prepush   (exit 0 = OK to push)
- origin must be exactly agnostify/context-pill with no credentials in the URL.
- Daily pushes need origin/main to exist (the first push is a separate, maintainer-approved step: --first-push).
- Every outgoing commit (rev-list origin/main..HEAD, merges included; full history on the first push) has its FULL tree
  scanned: every blob, including scanner/gate.py and scanner/prepush.py, against the secret, X and private patterns,
  and every path must sit under an allowed top-level name.
- The working-tree gate passes with the private-patterns file present, and the tree is clean.
Any git error blocks the push."""
import re, subprocess, sys
from pathlib import Path
from . import gate

REPO = Path(__file__).resolve().parent.parent
EXPECTED = re.compile(r"^(git@github\.com:|https://github\.com/)agnostify/context-pill(\.git)?$")


class GitError(Exception):
    pass


def git(*a, ok_codes=(0,)):
    r = subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True)
    if r.returncode not in ok_codes:
        raise GitError(f"git {a[0]} failed ({r.returncode})")
    return r


def main(argv):
    problems = []
    try:
        url = git("remote", "get-url", "origin").stdout.strip()
        if not EXPECTED.match(url):
            problems.append("remote 'origin' is not exactly agnostify/context-pill without credentials (value not shown)")
        has_main = git("rev-parse", "--verify", "-q", "origin/main", ok_codes=(0, 1)).returncode == 0
        if not has_main and "--first-push" not in argv:
            problems.append("origin/main does not exist: the first push needs the maintainer-approved --first-push path")
        commits = git("rev-list", "origin/main..HEAD" if has_main else "HEAD").stdout.split()
        private, perr = gate.private_patterns()
        if perr:
            problems.append(perr)
        seen_blobs = set()
        for c in commits:
            for line in git("ls-tree", "-r", "--full-tree", c).stdout.splitlines():
                meta, path = line.split("\t", 1)
                _, kind, sha = meta.split()
                if path.split("/")[0] not in gate.ALLOWED_TOP:
                    problems.append(f"outgoing path not allowed: {path}")
                if kind != "blob" or sha in seen_blobs:
                    continue
                seen_blobs.add(sha)
                text = git("cat-file", "-p", sha).stdout
                problems += [p.replace(" in ", " in outgoing ", 1) for p in gate.scan_text(path, text, private)]
        problems += gate.check(REPO, require_private=True)
        if git("status", "--porcelain").stdout.strip():
            problems.append("working tree not clean (commit or discard before pushing)")
    except GitError as e:
        problems.append(f"{e}: blocking push")
    print("prepush: OK" if not problems else "prepush: BLOCK\n" + "\n".join(sorted(set(problems))))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

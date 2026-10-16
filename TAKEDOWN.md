# Removal and correction requests

If you are a publisher, author or creator and want something changed or removed, we will act **within 24 hours** of receiving the request.

**How to ask**
- Open a GitHub issue titled `takedown: <what>` on this repository, or
- Email **sully@agnostify.com** with the subject `Context Pill takedown`.

Tell us the item id (looks like `2026-10-16-1a2b3c4d`) or the URL, and what you want: correct, remove, or stop tracking your source entirely.

**What we do**
1. Fix or remove the item from `latest.*`, `daily/`, `weekly/` and `ledger/` within 24 hours.
2. If you ask for the content to be gone from history too, we rewrite the git history to remove it and force-push, and note the removal (not the content) in `CHANGELOG-sources.md`.
3. If you ask us to stop tracking you, your source is set to `enabled: false` in `sources.yaml` with the reason "opt-out", and the scanner stops fetching it the same day.

**What we publish in the first place:** our own short summaries, links, attribution, and at most one quote of 25 words or fewer per item. No full transcripts, re-hosted video, images, or personal data beyond public names. Nothing from X (Twitter) is published here.

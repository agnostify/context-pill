# Source changes

Every add, drop, tier change and audit result is logged here.

- 2026-10-16: Scanner day 1. 17 sources enabled, 7 routes disabled (X, Instagram, arXiv API, Hugging Face API, HN Algolia, GitHub search/trending, Reddit). Every source starts at Beta(2,2).
- 2026-10-16: Maintainer-assigned T1 seeds (provisional until day 15): Theo (t3.gg), Dario Amodei, Anthropic, OpenAI (incl. Sam Altman's blog), Google DeepMind / Google AI, Andrej Karpathy, Simon Willison. Everyone else starts at T3. Ray Fu starts at T3 (every guide and video researched before inclusion).
- 2026-10-16: YouTube channel RSS not used: youtube.com/robots.txt disallows /feeds/videos.xml. YouTube sources wait for approval of the official YouTube Data API route and a key. Until then, no YouTube channel is read: Theo, Anthropic, OpenAI, Google DeepMind, Karpathy, Ray Fu, Fireship and AI Explained.
- 2026-10-16: raycfu.com /guides baseline recorded (103 guide URLs in the sitemap). New guides appear from the next run.
- 2026-10-16: audit of 2026-10-16-57503e5e (Anthropic): fail (arXiv abstract: improvements in seven of eight tasks, not eight. Blog confirms closed loop and 12% more qubits / 17x fewer gates.); cause: our summary; replaced by 2026-10-16-77948875
- 2026-10-16: audit of 2026-10-16-7bbecbe6 (Anthropic): pass (Anthropic page confirms lrand48 seeded with Unix timestamps (May 1997–April 1998), not a hash, and published reproduction code.)
- 2026-10-16: audit of 2026-10-16-77948875 (Anthropic): pass (Anthropic post confirms closed loop, eight tasks, 12% more qubits/17x fewer gates; arXiv abstract confirms significant reductions on seven of eight.); re-audit of the corrected 2026-10-16-57503e5e
- 2026-10-16: Anthropic was not demoted for the failed audit above: the failure was in our first draft of the quantum-circuits summary (it said "eight" without noting that the arXiv abstract reports reductions on seven of the eight), not in Anthropic's own statements. The corrected item was re-audited and passed (METHOD.md section 3).
- 2026-10-16: process note: day 1 included several scanner runs while the build was finished, and the one-fetch-per-feed-per-day guard took effect partway through the day, so some feeds were fetched more than once today. The 30-requests-per-host daily budget held throughout (it stopped Hugging Face and the GitHub changelog for the day).

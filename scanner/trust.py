"""Per-source trust: Beta-Binomial with recency decay (METHOD.md section 2).

- Prior Beta(2, 2). Evidence above the prior decays by LAMBDA per day (90-day half-life).
- Trust = posterior mean. Promotion uses LB10 (10th-percentile lower bound). Drop uses the mean.
- Tiers: T1 LB10 >= 0.80 with >= 15 resolved tips (or a maintainer-assigned seed);
         T2 0.65 <= LB10 < 0.80 with >= 15; T3 otherwise. Drop: mean < 0.50 after >= 15.
- Low-volume rule: < 6 resolved in the last 30 days -> LB10 on undecayed counts from the last 12 months.
Stdlib only."""
import math, datetime as dt

PRIOR_A, PRIOR_B = 2.0, 2.0
HALF_LIFE_DAYS = 90
LAMBDA = 0.5 ** (1 / HALF_LIFE_DAYS)           # 0.99233...
RECHECK_DAYS = 14
T1_LB, T2_LB, DROP_MEAN, MIN_RESOLVED = 0.80, 0.65, 0.50, 15
PROVISIONAL_DAYS = 14                            # no face-value publishing before day 15

# outcome -> (alpha increment, beta increment)
WEIGHTS = {"good": (1.0, 0.0), "docs-verified": (0.5, 0.0), "partial": (0.5, 0.5), "bad": (0.0, 1.0)}


def _betacf(a, b, x):
    # continued fraction for the regularized incomplete beta (Numerical Recipes)
    MAXIT, EPS, FPMIN = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > FPMIN else FPMIN)
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > FPMIN else FPMIN)
        c = 1 + aa / c; c = c if abs(c) > FPMIN else FPMIN
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > FPMIN else FPMIN)
        c = 1 + aa / c; c = c if abs(c) > FPMIN else FPMIN
        de = d * c; h *= de
        if abs(de - 1) < EPS:
            break
    return h


def betainc(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def beta_ppf(q, a, b):
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if betainc(a, b, mid) < q: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def _d(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def posterior(events, today, decay=True, since=None):
    """events: [{date, outcome, reversed_on?: date}] (resolved, non-inconclusive). Returns (alpha, beta).
    A claim found wrong within 90 days of resolving (scanner/correct.py) loses its original credit entirely
    (equivalent to reversing it at its current decayed weight) and adds beta +1 dated at the correction day."""
    a, b = PRIOR_A, PRIOR_B
    today = _d(today)
    for e in events:
        d = _d(e["date"])
        if since and d < since:
            continue
        if e.get("reversed_on"):
            rd = _d(e["reversed_on"])
            if since and rd < since:
                continue
            b += (LAMBDA ** max(0, (today - rd).days)) if decay else 1.0
            continue
        w = LAMBDA ** max(0, (today - d).days) if decay else 1.0
        da, db = WEIGHTS[e["outcome"]]
        a += w * da; b += w * db
    return a, b


def score(source, events, today):
    """Returns the trust record written into sources.yaml for one source."""
    today = _d(today)
    resolved = [e for e in events if e["outcome"] in WEIGHTS]
    n = len(resolved)
    a, b = posterior(resolved, today)
    mean = a / (a + b)
    recent = [e for e in resolved if (today - _d(e["date"])).days <= 30]
    if len(recent) < 6:                 # low-volume rule
        la, lb = posterior(resolved, today, decay=False, since=today - dt.timedelta(days=365))
        lb10, basis = beta_ppf(0.10, la, lb), "undecayed-12mo (low volume)"
    else:
        lb10, basis = beta_ppf(0.10, a, b), "decayed"
    seed = bool(source.get("seed"))
    audit_fail = bool(source.get("audit_demoted"))
    if audit_fail and source.get("audit_demoted_on"):
        # recovery: an audit-demoted source earns T1 back with >= 15 resolved tips after the demotion and LB10 >= 0.80
        after = [e for e in resolved if _d(e["date"]) > _d(source["audit_demoted_on"])]
        if len(after) >= MIN_RESOLVED:
            ra, rb = posterior(after, today)
            if beta_ppf(0.10, ra, rb) >= T1_LB:
                audit_fail = False
    # precedence: drop rule > audit demotion > earned tier > seed
    if n >= MIN_RESOLVED and mean < DROP_MEAN:
        tier = "drop"
    elif audit_fail and n < MIN_RESOLVED:
        tier = "T2-provisional"
    elif n >= MIN_RESOLVED and lb10 >= T1_LB and not audit_fail:
        tier = "T1"
    elif n >= MIN_RESOLVED and lb10 >= T2_LB:
        tier = "T2"
    elif n >= MIN_RESOLVED:
        tier = "T3"
    elif seed and not audit_fail:
        tier = "T1-seed"
    else:
        tier = "T3"
    return {"alpha": round(a, 3), "beta": round(b, 3), "trust_mean": round(mean, 3), "lb10": round(lb10, 3),
            "lb10_basis": basis, "resolved_tips": n, "tier": tier, "audit_demoted": audit_fail}


CRITERIA = {"T1": "face value + primary link exists; random full audit of at least 10% of items",
            "T2": "main claim checked against one primary or independent source",
            "T3": "every claim checked against a primary source; tools capped at docs-verified until the sandbox exists; unverified items left out or listed as not recommended"}
TIGHTEN = {"T1": "T2", "T2": "T3", "T3": "T3"}


def research_criterion(tier, day_number, tightened=()):
    """What check an item gets at publication (METHOD.md tiers). Before day 15 everything gets >= T2.
    `tightened`: base tiers whose audits found > 1 error in the last 20; their check moves up one level."""
    if tier == "drop":
        return "skip (dropped)"
    base = tier.split("-")[0]
    level = base
    if day_number <= PROVISIONAL_DAYS and level == "T1":
        level = "T2"
    if base in tightened:
        level = TIGHTEN[level]
    note = []
    if day_number <= PROVISIONAL_DAYS and base == "T1":
        note.append("provisional: first 14 days")
    if base in tightened:
        note.append("tightened: >1 audit error in last 20")
    text = CRITERIA[level] + (f" ({'; '.join(note)})" if note else "")
    if base == "T1" and level != "T1":
        text += "; plus the random full audit"
    return text

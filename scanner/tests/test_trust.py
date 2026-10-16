"""Checks the worked examples in METHOD.md. Run: python -m scanner.tests.test_trust"""
import datetime as dt
from scanner import trust as T

def approx(x, y, tol=0.01):
    assert abs(x - y) <= tol, (x, y)

# 15/15 good, no decay -> Beta(17, 2), LB10 ~ 0.80
approx(T.beta_ppf(0.10, 17, 2), 0.80, 0.01)
# 100 tips at 98% good -> Beta(100, 4): mean 0.962, LB10 0.936
approx(100 / 104, 0.962, 0.001); approx(T.beta_ppf(0.10, 100, 4), 0.936, 0.005)
# 7 good, 13 bad -> Beta(9, 15), mean 0.375 -> drop
today = dt.date(2026, 10, 16)
ev = [{"date": today, "outcome": "good"}] * 7 + [{"date": today, "outcome": "bad"}] * 13
r = T.score({"name": "x"}, ev, today); assert r["tier"] == "drop", r
# a seed with no resolved tips stays T1-seed; a new source starts T3
assert T.score({"seed": True}, [], today)["tier"] == "T1-seed"
assert T.score({}, [], today)["tier"] == "T3"
approx(T.LAMBDA, 0.9923, 0.0001)
# a correction adds beta +1 at the correction date and removes the original credit
ev = [{"date": today - dt.timedelta(days=30), "outcome": "good", "reversed_on": today}]
a, b = T.posterior(ev, today); approx(a, 2.0, 1e-9); approx(b, 3.0, 1e-9)
# audit demotion: blocks T1 until 15 good resolutions after the demotion date, then recovers
demo = {"audit_demoted": True, "audit_demoted_on": (today - dt.timedelta(days=40)).isoformat()}
old = [{"date": today - dt.timedelta(days=50), "outcome": "good"}] * 20
assert T.score(demo, old, today)["tier"] == "T2", T.score(demo, old, today)
new = [{"date": today - dt.timedelta(days=5), "outcome": "good"}] * 20
assert T.score(demo, old + new, today)["tier"] == "T1", T.score(demo, old + new, today)
print("trust tests: OK")

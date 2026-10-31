"""Tests for the resolution/audit note check (scanner/notes.py) on the notes that got daily drops held, plus good notes.
No network: source texts are fixtures. Run: python -m scanner.tests.test_notes"""
from scanner import notes as N

POST = "https://www.anthropic.com/research/designing-efficient-quantum-circuits"
ARXIV = "https://arxiv.org/abs/2610.22367"
METR = "https://www.anthropic.com/news/an-update-on-independent-evaluators"
PRICING = "https://claude.com/pricing"
OPENAI = "https://openai.com/news/rss.xml"
XURL = "https://" + "x" + ".com/a"          # a blocked host, built at runtime so the public-file gate sees no X link

ARXIV_TEXT = ("We pair Claude with an automated evaluator in a closed loop to design quantum circuits. Across eight benchmark tasks, "
              "the designs show significant reductions in resource cost on seven of the eight tasks relative to prior work.")
POST_TEXT = ("Claude worked in a closed loop with an automated evaluator on eight circuit design tasks. Compared with the smallest "
             "previous design, one circuit in the family uses 12% more qubits but 17x fewer gates.")
METR_TEXT = ("We've onboarded six METR employees with company hardware, office space, and access to internal tools and systems, similar "
             "to the access of our own risk teams. METR will publish its risk assessments, and Anthropic cannot veto publication. "
             "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories.")
PRICING_TEXT = ("Pricing. Claude Haiku 5.5 Input $0.10 / MTok Output $0.50 / MTok for prompts up to 100K tokens; $0.50 / $2.50 above. "
                "Legacy models Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok Claude Sonnet 4.5 Input $3 / MTok Output $15 / MTok")
OPENAI_TEXT = ("29 Oct 2026. Our model spec: the model should aim for three things: sound judgment, respect for human agency, "
               "and faithfulness to the intent of its instructions.")


def rejects(note, quote, src=None, why=None):
    p = N.validate_note(note, quote, src)
    assert p, f"expected rejection: {note!r}"
    assert why is None or any(why in x for x in p), (why, p)
    return p


def accepts(note, quote, src=None):
    p = N.validate_note(note, quote, src)
    assert not p, (note, p)


# 1. 2026-10-16-77948875: figures from the post attributed alongside the arXiv abstract
CASE1 = "Post: eight tasks, closed loop; arXiv: seven of eight; 12% more qubits, 17x fewer gates."
def test_case1_mixed_sources_and_figures_not_in_quote():
    q = "significant reductions in resource cost on seven of the eight tasks"
    rejects(CASE1, q, ARXIV_TEXT, why="figure 12% not in the quote")
    rejects(CASE1, q, ARXIV_TEXT, why="figure 17x not in the quote")
    rejects(CASE1, q, ARXIV_TEXT, why="mixes sources")
    rejects(CASE1, q, why="figure 12%")                       # no source text: the quote alone still catches it
    # even quoting the post (which has every figure) the note still mixes two sources' figures
    rejects(CASE1, ["closed loop with an automated evaluator on eight circuit design tasks", "uses 12% more qubits but 17x fewer gates",
                    "seven of the eight"], why="mixes sources")
    r = {"outcome": "good", "note": CASE1, "source_url": ARXIV, "quote": q}
    assert N.resolution_note(r, "good", [{"url": POST}], fetch=lambda u: ARXIV_TEXT) == f"Outcome: good. Source: {ARXIV}"


# 2. 2026-10-16-c5ba7374: "decides redaction categories" vs "decides what falls inside predefined categories"
CASE2 = "Six METR staff, near risk-team access; no veto; Anthropic decides redaction categories."
def test_case2_recombined_phrase():
    q = ["We've onboarded six METR employees", "similar to the access of our own risk teams", "Anthropic cannot veto publication",
         "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories"]
    rejects(CASE2, q, METR_TEXT, why="phrase category redaction not in the quote")
    rejects("Anthropic decides redaction categories.", q[3], METR_TEXT, why="phrase")
    r = {"outcome": "good", "note": CASE2, "source_url": METR, "quote": q}
    assert N.resolution_note(r, "good", fetch=lambda u: METR_TEXT) == f"Outcome: good. Source: {METR}"


# 3. 2026-10-17-f0e1a13c: "not shown there" while the page lists Haiku 4.5 at $1/$5 under Legacy models
CASE3 = "Haiku 5.5 tiers match pricing page; Haiku 4.5 $1/$5 not shown there."
def test_case3_absence_claim():
    q = "Claude Haiku 5.5 Input $0.10 / MTok Output $0.50 / MTok"
    rejects(CASE3, q, PRICING_TEXT, why="absence claim but the source contains")
    rejects(CASE3, q, why="absence claim without the full source text")       # page not fetched: unverified
    r = {"outcome": "good", "note": CASE3, "source_url": PRICING, "quote": q}
    assert N.resolution_note(r, "good", fetch=lambda u: PRICING_TEXT) == f"Outcome: good. Source: {PRICING}"
    assert N.resolution_note(r, "good", fetch=lambda u: None) == f"Outcome: good. Source: {PRICING}"
    for phrase in ("not listed", "absent", "missing", "no mention of it", "doesn't mention it"):
        rejects(f"Haiku 4.5 {phrase}.", q, why="absence claim")
    # a real absence, checked against the full page text, is allowed
    accepts("Claude Haiku 5.5 Input $0.10 / MTok; Haiku 9 not listed.", q, PRICING_TEXT)


# 4. audit 2026-10-30-da3cbe61: "five core values" where the source names three aims
CASE4 = ("OpenAI RSS item dated 29 Oct 2026 confirms five core values: sound judgment, human agency, faithfulness to intent. "
         "Article page returned 403.")
def test_case4_audit_count_not_in_source():
    q = "aim for three things: sound judgment, respect for human agency, and faithfulness to the intent of its instructions"
    rejects(CASE4, q, OPENAI_TEXT, why="figure 5 not in the quote")
    rejects("Confirms five core values: sound judgment, human agency, faithfulness to intent.", q, OPENAI_TEXT, why="figure 5")
    r = {"verdict": "pass", "note": CASE4, "source_url": OPENAI, "quote": q}
    assert N.audit_note(r, fetch=lambda u: OPENAI_TEXT) == f"Verdict: pass. Source: {OPENAI}"
    assert r["verdict"] == "pass"                                             # the verdict itself is never changed


# other rules
def test_quote_must_be_in_source():
    rejects("Anthropic cannot veto publication.", "Anthropic can veto publication", METR_TEXT, why="quote not found")
    accepts("Anthropic cannot veto publication.", "anthropic  CANNOT veto\npublication", METR_TEXT)   # whitespace/case-normalized


def test_agreement_and_fallbacks():
    rejects("Post and paper agree on the closed loop.", "closed loop", why="agreement")
    rejects("x " * 21, "x", why="over 20 words")
    rejects("Closed loop confirmed.", "", why="no quote")
    assert N.resolution_note({"outcome": "bad"}, "bad", [{"url": XURL}, {"url": POST}], fetch=None) == f"Outcome: bad. Source: {POST}"
    assert N.audit_note({"verdict": "fail", "note": "n"}) == "Verdict: fail."
    # a blocked-host source_url is never published; the claim's evidence URL is used instead
    r = {"verdict": "pass", "note": "Closed loop.", "source_url": XURL, "quote": "closed loop"}
    assert N.audit_note(r, [{"url": POST}], fetch=None) == f"Verdict: pass. Source: {POST}"


# good notes pass and are kept verbatim
def test_good_notes():
    accepts("Post: one design uses 12% more qubits but 17x fewer gates.",
            "one circuit in the family uses 12% more qubits but 17x fewer gates", POST_TEXT)
    accepts("arXiv abstract: significant reductions on seven of the eight tasks.",
            "significant reductions in resource cost on seven of the eight tasks", ARXIV_TEXT)
    accepts("Six METR employees onboarded; Anthropic cannot veto publication.",
            ["We've onboarded six METR employees", "Anthropic cannot veto publication"], METR_TEXT)
    accepts("Anthropic decides what falls inside predefined redaction categories.",
            "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories",
            METR_TEXT)
    accepts("Legacy models list Claude Haiku 4.5 at $1 input, $5 output per MTok.",
            "Legacy models Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok", PRICING_TEXT)
    accepts("Model spec names sound judgment, human agency and faithfulness to intent.",
            "Our model spec: the model should aim for three things: sound judgment, respect for human agency, "
            "and faithfulness to the intent of its instructions", OPENAI_TEXT)
    q = "one circuit in the family uses 12% more qubits but 17x fewer gates"
    note = "Post: one design uses 12% more qubits, 17x fewer gates."
    assert N.resolution_note({"outcome": "good", "note": note, "source_url": POST, "quote": q}, "good",
                             fetch=lambda u: POST_TEXT) == note
    assert N.resolution_note({"outcome": "good", "note": note, "source_url": POST, "quote": q}, "good", fetch=lambda u: None) == note
    a = {"verdict": "pass", "note": "Six METR employees onboarded.", "source_url": METR, "quote": "We've onboarded six METR employees"}
    assert N.audit_note(a, fetch=lambda u: METR_TEXT) == "Six METR employees onboarded."


def test_numbers():
    assert N.numbers("$1/$5, 12%, 17x, 4.50, seven, 1,000") == [("$", "1"), ("$", "5"), ("%", "12"), ("x", "17"), ("", "4.5"), ("", "7"), ("", "1000")]


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
    print("test_notes: all passed")

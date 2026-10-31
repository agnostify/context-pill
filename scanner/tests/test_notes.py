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
    rejects(CASE1, q, ARXIV_TEXT, why="contiguous span")
    rejects(CASE1, q, why="figures without the full source text")
    # even quoting the post (which has every figure) the note joins several spans
    rejects(CASE1, ["closed loop with an automated evaluator on eight circuit design tasks", "uses 12% more qubits but 17x fewer gates",
                    "seven of the eight"], POST_TEXT + " " + ARXIV_TEXT, why="contiguous span")
    r = {"outcome": "good", "note": CASE1, "source_url": ARXIV, "quote": q}
    assert N.resolution_note(r, "good", [{"url": POST}], fetch=lambda u: ARXIV_TEXT) == f"Outcome: good. Source: {ARXIV}"


# 2. 2026-10-16-c5ba7374: "decides redaction categories" vs "decides what falls inside predefined categories"
CASE2 = "Six METR staff, near risk-team access; no veto; Anthropic decides redaction categories."
def test_case2_recombined_phrase():
    q = ["We've onboarded six METR employees", "similar to the access of our own risk teams", "Anthropic cannot veto publication",
         "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories"]
    rejects(CASE2, q, METR_TEXT, why="contiguous span")
    rejects("Anthropic decides redaction categories.", q[3], METR_TEXT, why="contiguous span")
    r = {"outcome": "good", "note": CASE2, "source_url": METR, "quote": q}
    assert N.resolution_note(r, "good", fetch=lambda u: METR_TEXT) == f"Outcome: good. Source: {METR}"


# 3. 2026-10-17-f0e1a13c: "not shown there" while the page lists Haiku 4.5 at $1/$5 under Legacy models
CASE3 = "Haiku 5.5 tiers match pricing page; Haiku 4.5 $1/$5 not shown there."
def test_case3_absence_claim():
    q = "Claude Haiku 5.5 Input $0.10 / MTok Output $0.50 / MTok"
    rejects(CASE3, q, PRICING_TEXT, why="absence claim but the source contains")
    rejects(CASE3, q, PRICING_TEXT, why="contiguous span")                   # the rest is a paraphrase too
    rejects(CASE3, q, why="absence claim without the full source text")       # page not fetched: unverified
    r = {"outcome": "good", "note": CASE3, "source_url": PRICING, "quote": q}
    assert N.resolution_note(r, "good", fetch=lambda u: PRICING_TEXT) == f"Outcome: good. Source: {PRICING}"
    # page not fetched: the fallback cites the claim's evidence URL, not the unchecked source_url
    assert N.resolution_note(r, "good", [{"url": POST}], fetch=lambda u: None) == f"Outcome: good. Source: {POST}"
    assert N.resolution_note(r, "good", fetch=lambda u: None) == "Outcome: good."
    for phrase in ("not listed", "absent", "missing", "no mention of it", "doesn't mention it"):
        rejects(f"Haiku 4.5 {phrase}.", q, why="absence claim")
    # a real absence, checked against the full page text, is allowed
    accepts("Claude Haiku 5.5 Input $0.10 / MTok; Mythos 9 not listed.", q, PRICING_TEXT)


# 4. audit 2026-10-30-da3cbe61: "five core values" where the source names three aims
CASE4 = ("OpenAI RSS item dated 29 Oct 2026 confirms five core values: sound judgment, human agency, faithfulness to intent. "
         "Article page returned 403.")
def test_case4_audit_count_not_in_source():
    q = "aim for three things: sound judgment, respect for human agency, and faithfulness to the intent of its instructions"
    rejects(CASE4, q, OPENAI_TEXT, why="contiguous span")
    rejects("Confirms five core values: sound judgment, human agency, faithfulness to intent.", q, OPENAI_TEXT, why="contiguous span")
    r = {"verdict": "pass", "note": CASE4, "source_url": OPENAI, "quote": q}
    assert N.audit_note(r, fetch=lambda u: OPENAI_TEXT) == f"Verdict: pass. Source: {OPENAI}"
    assert r["verdict"] == "pass"                                             # the verdict itself is never changed


# other rules
def test_quote_must_be_in_source():
    rejects("Anthropic cannot veto publication.", "Anthropic can veto publication", METR_TEXT, why="quote not found")
    accepts("Anthropic cannot veto publication.", "anthropic  CANNOT veto\npublication", METR_TEXT)   # whitespace/case-normalized


def test_agreement_and_fallbacks():
    rejects("Post and paper agree on the closed loop.", "closed loop", why="contiguous span")
    rejects("x " * 21, "x", why="over 20 words")
    rejects("Closed loop confirmed.", "", why="no quote")
    assert N.resolution_note({"outcome": "bad"}, "bad", [{"url": XURL}, {"url": POST}], fetch=None) == f"Outcome: bad. Source: {POST}"
    assert N.audit_note({"verdict": "fail", "note": "n"}) == "Verdict: fail."
    # a blocked-host source_url is never published; the claim's evidence URL is used instead
    r = {"verdict": "pass", "note": "Closed loop.", "source_url": XURL, "quote": "closed loop"}
    assert N.audit_note(r, [{"url": POST}], fetch=None) == f"Verdict: pass. Source: {POST}"


# good notes pass and are kept verbatim
def test_good_notes():
    accepts("One circuit in the family uses 12% more qubits but 17x fewer gates.",
            "one circuit in the family uses 12% more qubits but 17x fewer gates", POST_TEXT)
    accepts("Significant reductions in resource cost on seven of the eight tasks.",
            "significant reductions in resource cost on seven of the eight tasks", ARXIV_TEXT)
    accepts("Anthropic decides what falls inside those categories.",
            "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories",
            METR_TEXT)
    accepts("Legacy models: Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok.",
            "Legacy models Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok", PRICING_TEXT)
    accepts("Sound judgment, respect for human agency, and faithfulness to the intent of its instructions.",
            "Our model spec: the model should aim for three things: sound judgment, respect for human agency, "
            "and faithfulness to the intent of its instructions", OPENAI_TEXT)
    q = "one circuit in the family uses 12% more qubits but 17x fewer gates"
    note = "One circuit in the family uses 12% more qubits but 17x fewer gates."
    assert N.resolution_note({"outcome": "good", "note": note, "source_url": POST, "quote": q}, "good",
                             fetch=lambda u: POST_TEXT) == note
    # page not fetched: a note with figures falls back
    assert N.resolution_note({"outcome": "good", "note": note, "source_url": POST, "quote": q}, "good",
                             fetch=lambda u: None) == "Outcome: good."
    a = {"verdict": "pass", "note": "Onboarded six METR employees.", "source_url": METR, "quote": "We've onboarded six METR employees"}
    assert N.audit_note(a, fetch=lambda u: METR_TEXT) == "Onboarded six METR employees."


# the whole note is ONE span: no source label, no joining spans, no paraphrase
def test_one_contiguous_span():
    rejects("Post: one circuit in the family uses 12% more qubits but 17x fewer gates.",
            "one circuit in the family uses 12% more qubits but 17x fewer gates", POST_TEXT, why="contiguous span")
    rejects("Onboarded six METR employees; Anthropic cannot veto publication.",
            ["We've onboarded six METR employees", "Anthropic cannot veto publication"], METR_TEXT, why="contiguous span")
    rejects("Anthropic decides what falls inside predefined redaction categories.",
            "Anthropic may require redactions within predefined categories, and Anthropic decides what falls inside those categories",
            METR_TEXT, why="contiguous span")
    # Sonnet's prices attached to Haiku: both pieces are in the quote, in order, but not as one span
    text = "Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok; Claude Sonnet 4.5 Input $3 / MTok Output $15 / MTok"
    rejects("Claude Haiku 4.5, Input $3 / MTok, Output $15 / MTok.", text, text, why="contiguous span")
    accepts("Claude Haiku 4.5, Input $1 / MTok, Output $5 / MTok.", text, text)
    accepts("Claude Sonnet 4.5 Input $3 / MTok Output $15 / MTok.", text, text)
    q = "Available on iOS, not on Android"
    rejects("Available on Android, not on iOS.", q, q, why="contiguous span")
    accepts("Available on iOS, not on Android.", q, q)
    rejects("On Android.", q, q, why="contiguous span")                        # span right after "not"
    q = "Anthropic acquired Bun"
    rejects("Bun acquired Anthropic.", q, q, why="contiguous span")
    accepts("Anthropic acquired Bun.", q, q)


# figures stay in the quote's order and with the words around them
def test_figure_order_and_labels():
    q = "Input $1; output $5."
    rejects("Input $5; output $1.", q, q, why="contiguous span")              # reversed input/output
    rejects("Output $5; input $1.", q, q, why="contiguous span")              # right pairs, wrong order
    rejects("$5 input, $1 output.", q, q, why="contiguous span")
    rejects("Input: $5.", q, q, why="contiguous span")
    accepts("Input $1; output $5.", q, q)
    accepts("Input: $1 / MTok, Output: $5 / MTok.", "Input: $1 / MTok, Output: $5 / MTok", "Input: $1 / MTok, Output: $5 / MTok")
    rejects("Input: $1, output: $5.", "Input: $1 / MTok, Output: $5 / MTok", "Input: $1 / MTok, Output: $5 / MTok",
            why="contiguous span")                                            # dropping words breaks the span
    rejects("Input $1; output $5.", ["Output $5", "Input $1"], "Input $1 Output $5", why="contiguous span")
    rejects("Haiku 4.5 at $1 input, $5 output.", "Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok", PRICING_TEXT,
            why="contiguous span")
    rejects("Haiku 4.5 input $5.", "Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok", PRICING_TEXT, why="contiguous span")


# page not fetched: only plain, figure-free notes supported by the quote pass
def test_failed_fetch():
    q = "one circuit in the family uses 12% more qubits but 17x fewer gates"
    rejects("One circuit uses 12% more qubits.", q, why="figures without the full source text")
    rejects("Six METR employees onboarded.", "We've onboarded six METR employees", why="figures without")      # number word
    rejects("A single circuit uses fewer gates.", q, why="figures without")
    rejects("Haiku 4.5 omitted? Mythos not listed.", "Claude Haiku 4.5", why="absence claim without")
    rejects("Anthropic cannot veto publication.", "Anthropic cannot veto publication", why="negation without")
    accepts("Worked in a closed loop with an automated evaluator.", "Claude worked in a closed loop with an automated evaluator")
    rejects("Anthropic picks the evaluators.", "Anthropic picks evaluators", why="contiguous span")
    a = {"verdict": "pass", "note": "Closed loop.", "source_url": POST, "quote": "a closed loop with an automated evaluator"}
    assert N.audit_note(a, [{"url": METR}], fetch=lambda u: None) == "Closed loop."
    a["note"] = "Anthropic cannot veto publication."; a["quote"] = "Anthropic cannot veto publication"
    assert N.audit_note(a, [{"url": POST}], fetch=lambda u: None) == f"Verdict: pass. Source: {POST}"
    a["source_url"] = METR
    a["note"] = "Onboarded six METR employees."; a["quote"] = "We've onboarded six METR employees"
    assert N.audit_note(a, [{"url": POST}], fetch=lambda u: None) == f"Verdict: pass. Source: {POST}"
    assert N.audit_note(a, [{"url": POST}], fetch=None) == f"Verdict: pass. Source: {POST}"
    assert N.audit_note(a, [{"url": POST}], fetch=lambda u: METR_TEXT) == "Onboarded six METR employees."
    a["note"] = "Onboarded seven METR employees."                     # fetched but rejected: source_url is cited
    assert N.audit_note(a, [{"url": POST}], fetch=lambda u: METR_TEXT) == f"Verdict: pass. Source: {METR}"
    def boom(u): raise RuntimeError("network")
    assert N.audit_note(a, [{"url": POST}], fetch=boom) == f"Verdict: pass. Source: {POST}"


# absence: any subject term on the page rejects it; more phrasings count
def test_absence_any_term():
    q = "Claude Haiku 5.5 Input $0.10 / MTok"
    rejects("Legacy Haiku 4.5 pricing not listed.", q, PRICING_TEXT, why="absence claim but the source contains")
    rejects("Haiku 4.5 no longer listed.", q, PRICING_TEXT, why="absence claim but")
    rejects("Mythos 4.5 not listed.", q, PRICING_TEXT, why="absence claim but")                # 4.5 is on the page
    for phrase in ("omits Haiku 4.5", "lacks Haiku 4.5", "removed Haiku 4.5", "does not include Haiku 4.5"):
        rejects(f"Pricing page {phrase}.", q, PRICING_TEXT, why="absence claim but")
        rejects(f"Pricing page {phrase}.", q, why="absence claim without")
    rejects("Haiku 4.5 not included.", q, why="absence claim without")
    accepts("Mythos 9 no longer listed.", q, PRICING_TEXT)
    accepts("Page omits Mythos 9.", q, PRICING_TEXT)


# malformed model output falls back instead of raising
def test_robustness():
    for note, quote in ((42, "closed loop"), (["Closed loop."], "closed loop"), ({"a": 1}, "closed loop"),
                        ("Closed loop.", 7), ("Closed loop.", ["closed loop", None]), ("Closed loop.", ["closed loop", 3]),
                        ("Closed loop.", {"q": "closed loop"}), ("Closed loop.", [["closed loop"]])):
        assert N.validate_note(note, quote, POST_TEXT), (note, quote)
        r = {"outcome": "good", "note": note, "source_url": POST, "quote": quote}
        assert N.resolution_note(r, "good", [{"url": ARXIV}], fetch=lambda u: POST_TEXT) == f"Outcome: good. Source: {POST}"
        assert N.resolution_note(r, "good", [{"url": ARXIV}], fetch=lambda u: None) == f"Outcome: good. Source: {ARXIV}"
    assert N.validate_note("Closed loop.", "closed loop", 123) == []          # non-string page text = not fetched
    assert N.resolution_note(None, "good", [None, {"url": 5}, {"url": POST}], fetch=lambda u: 9) == f"Outcome: good. Source: {POST}"
    assert N.audit_note("junk", fetch=None) == "Verdict: None."
    assert N.quote_parts(["a", 1]) == [] and N.quote_parts(None) == []


# any figure: the quote is on the fetched page and the note is one span of it
def test_figure_clauses_verbatim():
    q = "Input $1; output $5."
    rejects("$1 output; $5 input.", q, q, why="contiguous span")              # same order, labels swapped
    rejects("Output $5.", q, "Input $9; output $7.", why="quote not found")   # quote not on the page
    rejects("Output $5.", q, why="figures without the full source text")
    accepts("Output $5.", q, q)
    accepts("Input $1; output $5.", q, q)
    legacy = "Claude Haiku 4.5 Input $1 / MTok Output $5 / MTok Claude Sonnet 4.5 Input $3 / MTok Output $15 / MTok"
    for note in ("Claude Haiku 4.5 Input $3 / MTok.", "Haiku 4.5: Input $3 / MTok.", "Claude Haiku 4.5 Output $15 / MTok.",
                 "Claude Haiku 4.5 Input $3 / MTok Output $15 / MTok."):
        rejects(note, legacy, PRICING_TEXT, why="contiguous span")                # Sonnet's figures moved to Haiku
    accepts("Claude Sonnet 4.5 Input $3 / MTok Output $15 / MTok.", legacy, PRICING_TEXT)
    accepts("Eight benchmark tasks.", ARXIV_TEXT.split(". ")[1], ARXIV_TEXT)
    for note in ("Eight design tasks.", "Reductions on 7 of the eight tasks.", "A 4.5 model.", "Seven tasks improved."):
        rejects(note, ARXIV_TEXT.split(". ")[1], ARXIV_TEXT, why="contiguous span")    # number word, digit, decimal
    rejects("Uses twelve percent more qubits.", "uses 12% more qubits", POST_TEXT)
    rejects("17× fewer gates, 12% more qubits.", "uses 12% more qubits but 17x fewer gates", POST_TEXT, why="contiguous span")
    accepts("Uses 12 % more qubits but 17× fewer gates.", "uses 12% more qubits but 17x fewer gates", POST_TEXT)


# a negation can't be added, dropped or swapped
def test_negation():
    rejects("Haiku is not available.", "Haiku is available", "Haiku is available.")
    rejects("Haiku is not available.", "Haiku is available")
    rejects("Anthropic can veto publication.", "Anthropic cannot veto publication", METR_TEXT, why="contiguous span")
    rejects("Anthropic cannot veto publication.", "Anthropic can veto publication", "Anthropic can veto publication.",
            why="contiguous span")
    rejects("Anthropic never vetoes publication.", "Anthropic cannot veto publication", METR_TEXT, why="contiguous span")
    rejects("Anthropic can't veto publication.", "Anthropic cannot veto publication", METR_TEXT, why="contiguous span")
    rejects("METR publishes without redactions.", "METR will publish its risk assessments", METR_TEXT, why="contiguous span")
    rejects("Anthropic doesn't decide.", "Anthropic decides what falls inside those categories", METR_TEXT, why="contiguous span")
    rejects("Anthropic cannot veto METR publication.", "Anthropic cannot veto publication", METR_TEXT,
            why="contiguous span")                                            # one extra word is enough
    rejects("Veto publication.", "Anthropic cannot veto publication", METR_TEXT, why="contiguous span")   # negation dropped
    accepts("Anthropic cannot veto publication.", "Anthropic cannot veto publication", METR_TEXT)
    accepts("Anthropic can't veto publication.", "Anthropic can't veto publication", "Anthropic can't veto publication.")


# more absence phrasings; each still needs the fetched page with none of its subject on it
def test_absence_phrases():
    q = "Claude Haiku 5.5 Input $0.10 / MTok"
    for phrase in ("is unavailable", "is not available", "retired", "discontinued", "dropped", "deprecated", "no longer offered",
                   "no longer", "removed", "omitted"):
        rejects(f"Haiku 4.5 {phrase}.", q, PRICING_TEXT, why="absence claim but the source contains")
        rejects(f"Haiku 4.5 {phrase}.", q, why="absence claim without")
        accepts(f"Mythos {phrase}.", q, PRICING_TEXT)
    for phrase in ("omits", "lacks"):
        rejects(f"Pricing page {phrase} Haiku 4.5.", q, PRICING_TEXT, why="absence claim but")
        accepts(f"Page {phrase} Mythos.", q, PRICING_TEXT)
        rejects(f"Pricing page {phrase} Mythos.", q, why="absence claim without")


def test_numbers():
    assert N.numbers("$1/$5, 12%, 17x, 4.50, seven, 1,000") == [("$", "1"), ("$", "5"), ("%", "12"), ("x", "17"), ("", "4.5"), ("", "7"), ("", "1000")]


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
    print("test_notes: all passed")

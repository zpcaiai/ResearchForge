# ResearchForge — commercial packaging and pricing

**Status: PROPOSAL. Not a decision.**

Everything here is either a **RECOMMENDATION** (my judgement, argued, overridable) or a
**FOUNDER DECISION** (a call I am explicitly not making for you). Numbers that come from
this repository's own measurements are cited to the file that measured them. Numbers that
come from my judgement rather than from data are marked **[GUESS]** and are collected again
in §8 so you can see all of them in one place. There is no market data in this document,
because I have none: no competitor price, no win rate, no willingness-to-pay survey. Where a
decision needs a number I do not have, §8 names the number and says how to get it.

Read §5 first if you only read one section. The most expensive finding in this document is
not about price.

---

## 1. What is actually being sold

ResearchForge is **self-hosted software with an offline-verified licence key**. The customer
installs it on their own machine or cluster, supplies their own model credentials
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`), and runs it against their own compute. The licence
is an Ed25519-signed JSON file verified against a public key compiled into the build
(`packages/cli/src/license.ts`, `packages/cli/src/pubkey.ts`). There is no phone-home, no
telemetry, and no licence server in the customer's path — deliberately, because in the
environments this is aimed at, outbound network access is a compliance question, and a
six-hour run that dies at hour five because a licence server was restarting is worse than a
run that was never licensed.

That property is the single cleanest thing to sell. "Your papers, your unpublished data and
your API keys never leave your machine" is not a feature claim that needs benchmarking; it
is an architectural fact a security reviewer can verify by reading the code. For a lab whose
data is under an IRB protocol or a corporate group whose research is pre-publication, it is
frequently the difference between a tool they can adopt and one their institution forbids.

What the customer gets, mechanically: a 16-state research pipeline
(`packages/cli/src/state.ts`) driven by 32 implemented skills, in which the state machine
refuses to advance unless the *artifacts* of the previous stage exist — not unless the
previous skill reported success. Reproduction of the source paper runs before ideation, and
`IDEAS_READY` is structurally unreachable except through `REPRO_LEVEL_ESTABLISHED`. Literature
coverage is measured, and under `UNKNOWN_COVERAGE` no candidate may be certified
`NOVEL_ENOUGH` by rule rather than by judgement. Every number in a draft is traced to the
run that produced it or marked `FABRICATED`. Figures are verified by reading the numbers back
off the rendered artists. The release gate refuses.

### What it does not do — state this in the first paragraph of every sales conversation

- **It does not supply models.** BYOK. The customer's contract with the model provider
  governs cost, availability, output ownership and data retention. We are not in that path
  and take no margin on it.
- **It does not supply compute.** No GPU is shipped, rented or brokered. `execution.py`
  assumes the common deployment has no GPU and that untrusted code execution is disabled by
  default; the sandbox is rlimit-bounded subprocess isolation, not a cluster scheduler.
- **It does not supply datasets.** No mirrors, no licences, no access brokerage.
- **It does not write the method under test.** `codebase-scaffolder` generates the harness —
  CLI, seeding, invalid-condition checks, result emission — and deliberately refuses to
  generate the algorithm being measured, because a generator that writes the thing it also
  measures produces evidence about itself. The worked example makes the researcher supply
  `impl/`. Any pitch that implies "it writes the code" will be falsified in the customer's
  first real run.
- **It does not guarantee a publishable paper, and it never marks output submission-ready.**
  `release-gate` hard-codes `submission_ready: False` with a stated reason
  (`artifacts_out.py`). See §6.
- **It cannot reproduce 40% of source papers, and it never will.** This repo's own 20-paper
  study (`docs/study/FINDINGS.md`) found that **8 of 20 sampled papers (40%, CI [22%, 61%])
  cannot begin reproduction for reasons intrinsic to the paper**: 20% published no code at
  all, and another 20% published code with no dependency declaration anywhere in the tree.
  That figure is independent of our environment. The hard upper bound on P(RL≥1) is
  therefore **60%, CI [39%, 78%]**.

The product's answer to that 40% is not to pretend otherwise. It is the degradation path: the
achieved reproduction level sets the comparison mode, and at `CM_NONE` the run keeps going and
produces a *non-empty but narrower* portfolio — reproducibility studies, negative-result
reports, evaluation-methodology work — using the failure taxonomy it just generated as the
evidence. That is honest research, and for some buyers it is the more valuable output. It is
also, unambiguously, **not** what a buyer who wants "beat the SOTA baseline" was hoping for.

**RECOMMENDATION:** put the 40% number in the marketing copy, above the fold, as a stated
limit. A buyer who discovers it in week two feels defrauded. A buyer who was told it in week
zero and bought anyway has self-selected into the segment that values the degradation path,
which is the segment that will renew.

---

## 2. Who buys this, and what they are buying instead

The honest differentiator is **the gates** — reproduction before ideation, measured coverage
with named blind spots, claim-to-run traceability, and a release gate that refuses. It is not
"AI writes your paper", which is both false here and already commoditised elsewhere.

| Segment | Alternative today | What is specifically better here | Willingness to pay |
|---|---|---|---|
| **A. Individual PhD student / postdoc** | Their own time, plus a consumer LLM subscription and ad-hoc prompting | The gates stop the failure mode they cannot self-detect: an idea whose novelty was assessed against a literature search that silently returned nothing, or a delta estimated against a baseline nobody ran. Reproduction-first is exactly the step a lone student skips. | Very low. Pays personally, out of a stipend. **[GUESS]** |
| **B. University lab (PI + 5–15 people)** | A first-year student's time, spent on reproducing a baseline instead of on their own work | Compresses the reproduction-and-triage phase that every new student burns a term on, and produces a *record* of it (failure taxonomy, coverage report, decision log) that outlives the student. The PI's real problem is not idea generation; it is that the lab's institutional memory walks out of the door every three years. `finding-memory` and the artifact store address that directly. | Low-to-moderate. Pays from a grant with a software/equipment line. **[GUESS]** |
| **C. Industrial research group** | An in-house agent framework, or an existing general agent framework wired up by an engineer | Two things they cannot get from a general framework: (i) a refusal architecture — the contract makes it physically impossible for a skill to write an artifact it does not own, so "the pipeline reported success but produced nothing" cannot happen silently; (ii) self-hosted with no egress, which is the only form their security review will approve for pre-publication work. Their alternative costs an engineer's quarter to build and never gets the integrity auditor. | Moderate-to-high. Pays from an R&D budget where the licence is small next to compute. **[GUESS]** |
| **D. Research-tooling reseller / core facility / CRO** | Nothing comparable that is self-hostable; they currently sell people-hours | Wants to run it on behalf of third parties. This is the only segment that needs terms the licence schema does not currently express (see §3, redistribution). Highest revenue per contract, highest legal complexity, and the worst fit for a product whose quality has not been benchmarked. | High per deal, but premature. **[GUESS]** |

**RECOMMENDATION:** sell to B and C first. A is the acquisition channel, not a revenue line —
students become postdocs who become PIs who buy, and a student who has to pay will simply not
use it. D should be deferred until §5's blockers are closed, because a reseller's customers
discover your defects at N× the rate your direct customers do, and the reputational damage
lands on you without the direct relationship that lets you fix it.

**FOUNDER DECISION:** whether to pursue D at all. It is the fastest path to a large first
contract and the fastest path to a public failure.

---

## 3. Tiering — mapping onto the gates that exist

The code already defines the shape. `license.ts` has three editions
(`community | team | site`) and exactly four paid features:

| Feature key | What it gates (verbatim from `PAID_FEATURES`) | Pipeline states |
|---|---|---|
| `experiment-engine` | sandboxed multi-branch experiment execution | `BLUEPRINT_READY` → `EXPERIMENTING` → `EVIDENCE_LOCKED` |
| `manuscript` | evidence-bound manuscript drafting and citation audit | `WRITING`, `REVIEWING` |
| `deck` | native editable defense deck generation | `DEFENSE_READY` |
| `release-gate` | release packaging with full provenance verification | `RELEASED` |

Community gets `ingest, literature, reproduction, innovation, human-gate` — states `INGESTED`
through `DIRECTION_SELECTED`. The cut lands exactly at the human selection gate: **everything
up to and including the human's decision is free; everything that executes on that decision is
paid.**

### Is each gate a viable paywall?

A gate is viable if working around it is meaningfully harder than paying. Judged one at a time:

**`experiment-engine` — the strongest gate, and the one to build the price on.** What sits
behind it is not "running a script": it is worktree branching, bounded repair with a hard cap,
rlimit-bounded sandboxed execution, an append-only ledger, provenance binding every metric to
code SHA + config + seed + environment, and — critically — evaluator isolation, where the
hidden tests live outside the agent's view. A determined engineer *can* replace this with a
shell script and a spreadsheet. What they cannot cheaply replace is the property that the
downstream integrity auditor recomputes every reported statistic from that ledger. Break the
ledger and you lose the audit, which is the thing being bought. **Viable.**

**`manuscript` — the weakest gate, and the one most likely to be worked around.** A user who
has `findings`, `evidence_graph` and `stats_audit` in hand — all community-tier artifacts —
can paste them into any chat assistant and get prose. What they do not get is the
claim-to-run binding and the citation audit that distinguishes a real-but-irrelevant citation
(`NOT_SUPPORTED`) from a supporting one, and a number with no run behind it (`FABRICATED`)
from a real one. That is genuinely valuable and genuinely hard to reproduce by hand — but it
is *invisible* value, and invisible value is what customers refuse to pay for. **Viable only
if the audit output is made loudly visible.**

**`deck` — a nice-to-have, not a paywall.** Native PPTX with real text frames instead of
flat images is a real engineering achievement and a small purchasing consideration. Nobody
buys a research runtime for the slides. It belongs in the paid bundle because it costs
nothing to include, not because it sells anything.

**`release-gate` — the commercially strangest gate, and the most interesting.** The customer
is paying for software whose headline behaviour is to tell them *no*. Framed as "packaging",
it sounds like a formality. Framed correctly, it is the compliance artifact: a signed
`release_manifest.json` carrying the AI-participation disclosure, the achieved comparison
mode, the provenance chain, and an explicit `submission_ready: False` with a stated reason.
For segment C and for any institution with a research-integrity office, *that document is the
product* — it is what lets them say, on the record, that the lab has a mechanised control on
undisclosed generative-model involvement.

This creates a genuine conflict. The release gate is simultaneously (a) the highest-value
compliance deliverable and (b) the mechanism that stops an undisclosed AI manuscript reaching
a venue. Paywalling (b) means the unpaid user's path of least resistance is to write their own
disclosure, or not to.

**RECOMMENDATION:** split the feature in two. A *refusal-only* release gate — it can say no,
and it emits the disclosure text — belongs in community, on ethics grounds. The *packaging and
provenance bundle* — the signed manifest, the reproducibility commands, the artifact bundle —
stays paid. **This is not built.** Today `release-gate` is one feature key, and splitting it
means a code change and a second key (e.g. `release-package`). Adding a key is safe: the
verifier re-serialises whatever it parsed, so existing signed licences continue to verify
unchanged.

**FOUNDER DECISION:** whether the ethics argument outweighs losing the single most
institution-legible paid deliverable from the paid bundle. I lean toward the split. You may
reasonably decide that an institution that will not pay for the compliance artifact is not
going to run a disciplined process anyway.

### Is community too generous?

Community gives away ingestion, literature, reproduction, the innovation engine and the human
gate. That is a lot — arguably the intellectually hardest parts of the system.

**RECOMMENDATION: keep it. It is correct, for three independent reasons.**

*First, it is the evaluation surface.* Nobody buys a research runtime on a demo. They buy it
after feeding it three of their own papers and watching what happens. If the free tier stops
before reproduction, the buyer never sees the one thing that differentiates the product — that
`IDEAS_READY` is unreachable except through `REPRO_LEVEL_ESTABLISHED` — and the trial teaches
them nothing. A gate that blocks evaluation kills adoption, and the gate is *specifically*
what is being sold.

*Second, community is where the measured defects live.* Per `docs/PDF_INGEST_QUALITY.md`, the
ingestion layer mis-attributes 73% of claim sections on real PDFs, indexes 0 of 9 figures on
IEEE-style papers, and 1 in 18 real PDFs hard-crashes the pipeline. Charging for that today
would be selling a known defect. Giving it away, with the defects documented, is both honest
and strategically sound: it converts your worst-quality subsystem from a refund liability into
a funnel.

*Third, the paid tier is where the customer's own money is already committed.* By the time a
user reaches `EXPERIMENTING` they have chosen a direction and are about to spend real GPU
hours and real API tokens. That is the moment of maximum commitment and the correct moment to
ask for money.

**FOUNDER DECISION:** whether community should be time-limited or project-limited (e.g. three
projects, then a licence). I recommend against — a limit that bites during evaluation is a
limit that ends the evaluation — but it is a defensible way to convert freeloading labs.

### What the licence schema cannot express

`License` is `{ licensee, edition, expires, features, issued }`. There is **no seat count, no
host binding, and no redistribution flag**. So "team, up to 10 named users" is a contractual
term with no technical enforcement, and a reseller arrangement (segment D) has no expression
in the artefact at all. Adding `seats` / `hosts` / `redistribution` fields is backward
compatible for verification (the verifier re-serialises the parsed object, so old licence
files still verify), but every issuing path and the issuance ledger have to change.

**RECOMMENDATION:** add the fields before the first `site` or reseller contract. Do not add
them before the first `team` contract; a named-user count in the EULA is sufficient at that
scale and the field is worthless without §5's enforcement work anyway.

---

## 4. Price points and the reasoning behind them

**All four numbers below are [GUESS].** They are anchored on three reasoning chains — the cost
of the buyer's alternative, the size of the licence relative to the buyer's own compute spend,
and the size of the purchase relative to the approval threshold that triggers procurement.
None is anchored on an observed comparable, because I have no comparable. Treat them as
starting hypotheses to test against the interviews in §8, not as a price list.

### The reasoning chains

**Chain 1 — fraction of the alternative.** For segment B the alternative is a first-year
student spending a term reproducing a baseline. The right price is a defensible fraction of
the fully-loaded cost of that time. I do not know that cost in your target geography and will
not invent it (§8, number 1). What I can say is the *shape*: a tool that plausibly saves
weeks-not-months of one junior person's time supports a low single-digit percentage of a
person-year, not a double-digit one, because the saving is probabilistic and the buyer knows it.

**Chain 2 — size relative to compute spend.** This one decides whether the purchase is easy.
A licence that is small next to the monthly GPU and API bill gets waved through as a rounding
error on an existing budget line. A licence that dominates that bill has to be justified on
its own merits, against a product with no benchmark (§5). So the licence must be priced as a
*fraction of the customer's existing research-infrastructure spend*, and the segments split
sharply on this:

| Segment | Rough monthly compute + API spend **[GUESS]** | A $6k/yr licence is… | Verdict |
|---|---|---|---|
| A. Individual student | $0–200 (often free cluster + small API spend) | 2.5×–∞ their spend | Impossible. Must be free. |
| B. University lab | $1k–10k | 5%–50% | Buyable at the low end of the licence range; painful at the high end |
| C. Industrial group | $20k–200k+ | <3% | Trivially approvable |

**Chain 3 — the procurement threshold.** Below some amount a PI signs and expenses it; above
it, a purchase order, a security review and a legal review appear, and the cycle goes from days
to two quarters. I do not know that threshold at your target institutions (§8, number 2), but
it is the single most important number in this document for revenue *velocity*, and it is
cheap to find out. Price the entry tier deliberately under it.

### Recommended prices

| Tier | Edition key | Features | Recommended list | Range | Term |
|---|---|---|---|---|---|
| **Community** | `community` | ingest, literature, reproduction, innovation, human-gate | **$0** | — | perpetual |
| **Academic single seat** | `team` (1 named user) | + all four paid features | **$0** for enrolled students/postdocs; **$400/yr** otherwise | $0–800 | annual |
| **Lab / Team** | `team` (≤10 named users) | + all four paid features | **$6,000/yr** | $3,000–12,000 | annual |
| **Commercial team** | `team` (≤10 named users) | same features, commercial-use terms | **$24,000/yr** | $15,000–36,000 | annual |
| **Site** | `site` | institution-wide, air-gap support, named support contact, priority on the fix list | **$35,000/yr** | $25,000–60,000 | annual |

Reasoning, tier by tier:

**Academic single seat at $0 for students.** Chain 2 says it plainly: a student's licence would
cost several times their entire monthly compute spend. There is no price at which this segment
converts, and pricing it at $200 just converts a future advocate into a non-user. The $400
non-student figure exists so that the *edition* is not free-forever by definition, which
matters when an industrial user tries to buy the cheapest thing on the list.

**Lab at $6,000/yr.** This is chain 1 and chain 3 meeting. It is a plausible single-digit
percentage of one junior person-year **[GUESS]**, it is $500/month against a lab compute bill
of $1k–10k/month, and it is — I believe, but do not know — under the threshold at which a PI
must open a procurement case. The range is wide because the answer changes completely
depending on §8 numbers 1 and 2. If the threshold turns out to be $5,000, price at $4,800 and
do not argue with it.

**Commercial team at 4× academic.** Academic/commercial differentiation is standard and
uncontroversial, and chain 2 says the industrial buyer will not notice. I have chosen 4×
rather than 2× or 10× on judgement alone **[GUESS]**; the honest justification is that a
commercial customer costs more to support, carries more legal exposure, and derives revenue
from the output. If you cannot defend the multiple in a sales call, lower it — an
indefensible multiple costs more trust than it earns margin.

**Site at $35,000/yr.** What justifies the jump from $24k is not more software; it is the
support envelope: a named contact, air-gapped installation support, and — the part that
actually sells — **influence over the priority of the fix list**. An institution buying this
is buying the ability to say "our ICML-style PDFs must parse correctly" and have that be
scheduled. That is a genuine, deliverable commitment, and it is the only thing at this price
point that a customer can verify you kept.

### Perpetual vs annual

`License.expires` supports `null` for perpetual. **RECOMMENDATION: do not sell perpetual yet.**
The fix list in `PDF_INGEST_QUALITY.md` §5 is twelve items, none implemented, and the
dependency work in `FINDINGS.md` §4 is not started. A perpetual licence sold today is sold on
a product the buyer will discover is materially incomplete, with no contractual mechanism to
keep them on the version where it is fixed.

The exception is genuinely air-gapped sites, where annual re-issuance is operationally painful
and perpetual-plus-maintenance-window is the norm. **FOUNDER DECISION:** whether to offer
perpetual + 12-month maintenance at roughly 2.5–3× the annual price **[GUESS]** for air-gapped
`site` customers only.

---

## 5. What will actually block a sale

Being adversarial with the product. Ordered by when the buyer hits it.

### Day one: there is no paywall

This is the biggest commercial risk in this document, and it is not about pricing.

**The licence gate does not gate anything.** `verify()` computes a `restricted[]` list
correctly, and `cli.ts` lines 89–90 *print* it. Nothing else reads it. `orchestrator.ts`
contains zero references to the licence. `state.ts` contains zero references to the licence.
The `PIPELINE` walks straight from `DIRECTION_SELECTED` into `EXPERIMENTING`, `WRITING`,
`DEFENSE_READY` and `RELEASED` without consulting the licence once. A community user today
gets every paid feature, plus a line of dimmed text telling them what they are not supposed to
have.

It is worse than that. The Python side does the actual work and is invoked as a subprocess per
skill; `python/researchforge/runner.py` is a standalone `argparse` CLI with **zero** licence
awareness. Even if the TypeScript orchestrator were fixed, `researchforge-py run --skill
manuscript-builder` bypasses it entirely.

And `pubkey.ts` still contains the sentinel `__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__`. Until
a release build substitutes a real key, *no build in existence can verify any licence*, so
every install is community — which is the correct failure direction, and is also why nobody
has noticed that the gate is decorative.

The mitigation is not "make the gate uncrackable" — this is self-hosted software the customer
can read and edit, so a determined customer will always win. The mitigation is to make the
gate *real enough to be an honest statement of what was sold*, and to put the commercial weight
on things a licence key cannot be forged around: signed builds, updates, the support envelope,
the fix-list priority, and indemnity. Those are what actually sustain a self-hosted business.

**Answer:** enforce the four features in the orchestrator *and* in `runner.py`, substitute the
public key at build time, and ship a signed build. Until that is done, you cannot honestly
invoice anyone for a feature, because they already have it.

### Week one: ingestion breaks on their own papers

The buyer will feed it five of their own PDFs. From `PDF_INGEST_QUALITY.md`, measured on 18
real papers:

- **1 of 18 (5.6%) hard-crashes** on unpaired UTF-16 surrogates, and reports as a generic
  internal crash indistinguishable from a runtime bug.
- On ICML/PMLR and ICLR-style papers — which is most of the target market — **exactly two
  sections are ever detected**, because `HEAD_RE` demands whitespace after the section number
  and those styles write `1. Introduction`. One character of regex.
- Consequently **189 of 258 claims (73%) carry a wrong or unknown section label**. Twelve of
  twelve claims in one paper are filed as living in the Abstract; one of them does.
- **28% of extracted "claims" are appendix or bibliography bookkeeping**, including raw LaTeX
  algebra with a page footer welded into the middle of it.
- Titles are exactly right on **9 of 18**, while 8 of 18 carry the correct title in PDF
  metadata the pipeline never reads.
- IEEE-style figures and tables are indexed at **0 of 9**, with no warning.

None of that is fatal on its own. **This is:** for all 17 non-crashing PDFs,
`layout_warnings.md` says `- none: text extraction produced anchored, ordered content`. On a
paper where the section map is 2-of-11 and the figure index is 0-of-5, the artifact whose job
is to describe layout risk affirmatively states there is none.

That is not a quality defect, it is a **trust** defect, and it is precisely the failure mode
this product claims to exist to prevent. A buyer who finds it — and a careful buyer will find
it in week one — will reasonably conclude that the integrity claims elsewhere in the system
are equally decorative. They will be wrong (the Python suite is 207 tests mostly asserting
refusals), but you will not get to make that argument.

**Answer:** ship fixes 1, 2, 3, 4 and 5 from `PDF_INGEST_QUALITY.md` §5 before charging. The
author's own estimates total roughly a day and a half of work, and fix 4 — make
`layout_warnings.md` refuse to say "none" unless something was actually checked — converts
every remaining defect from silent to visible. A visible defect in a beta is survivable. A
silent one in a product about honesty is not.

### Week one to three: their own baseline will not reproduce

The buyer's first real test is their own paper or a competitor's. Per `FINDINGS.md`, 40% of
papers cannot begin at all, and `DEPENDENCY_UNRESOLVABLE` accounts for 45% of observed
failures — dominated by pinned PyTorch/CUDA wheels that are not on PyPI.

There are two honest halves to the answer:

*The engineerable half.* The study's clearest output is an engineering priority, and it is
robust to the study's own instrument bias because it asks about the *shape* of failure, not
the rate: a historical wheel-index snapshot resolved against the repo's last-commit date, a
multi-version Python interpreter pool, a conda/mamba backend, and the PyTorch-specific index.
Those four attack 45% of the failure surface and **none of them requires a GPU**. This is the
highest-return unbuilt work in the repository, and the study is what identified it.

*The unengineerable half.* `NO_CODE` + `CONFIG_AMBIGUOUS` is 40% and no amount of engineering
touches it. The answer there is the `CM_NONE` degradation path — which the study incidentally
confirmed is not an optional nicety but the only route for four papers in ten.

**Answer:** build the dependency time machine before selling, and sell the degradation path
honestly rather than hiding it. Say to the buyer, in the first meeting: "four papers in ten
will not reproduce, and here is exactly what the tool does instead."

### Week two to four: "is the innovation any good?"

**There is no answer to this question today, and that should worry you more than the ingestion
defects.** `retrospective-benchmark-builder` exists and behaves correctly — it *refuses* to
build a benchmark without a supplied corpus, and refuses to gate a promotion on a benchmark
whose contamination floor is unmeasured. But no corpus exists. So the innovation engine, which
is the intellectual core of the product and the thing the customer is emotionally buying, has
never been measured against anything.

A sophisticated buyer — which is every buyer in segment C — will ask "how do you know the
ideas are good?" and the truthful answer is "we don't; we know they are grounded in a
reproduction that actually ran, and we know the system refuses to certify novelty it cannot
support." That is a real and defensible answer about *process*. It is not an answer about
*quality*, and you should not let a sales conversation blur the two.

**Answer:** assemble the corpus and publish a first number, even a bad one. The machinery is
built and waiting for input (§8, number 3). A measured recall@k of 0.4 with a stated
contamination floor is infinitely more sellable than no number, because it makes you the
vendor who measured. And note the ordering the implementation plan already insists on: build
the benchmark *before* tuning the innovation engine, or you cannot distinguish improvement
from drift.

### Throughout: the offline-mode trap

`--model offline` stamps everything `synthetic: true` and the release gate refuses it. That is
correct behaviour and a good demo of the gate. But it means the bundled worked example has
**never produced a non-synthetic release**, and as far as this repository shows, no end-to-end
run with a real model key has been certified (implementation plan Batch 13 requires three
fixture papers spanning RL3, RL1 and RL0; there is one, and it is offline). Before charging,
someone must run the full pipeline with a real key and see what the release gate says. It may
refuse for reasons nobody has seen yet.

---

## 6. Legal and ethical exposure

### Why `ASSISTED_DRAFT` is commercially correct, not merely cautious

`release-gate` sets `status` to `ASSISTED_DRAFT` or `BLOCKED`, hard-codes
`submission_ready: False`, and emits `why_not_submission_ready`: the disclosure must be carried
into the venue's LLM-use declaration, and a named human author must verify the claims.

Four reasons this is the commercially right default, in descending order of how much money it
saves you:

**It puts the accountable party where authorship norms already put them.** Venues hold *named
human authors* responsible for a manuscript's content. A tool that declared output
"submission-ready" would be asserting a judgement it has no standing to make and the customer
cannot delegate. The default aligns the product's claim with the accountability structure the
customer is actually operating in, which is why a research-integrity office can approve it.

**It converts an unbounded promise into a bounded deliverable.** "Produces a publishable paper"
is unfalsifiable at sale and indefensible at renewal. "Produces an assisted draft with a
provenance manifest and a disclosure block, every quantitative claim traced to a run or marked
fabricated" is a specification you can put in a contract and be measured against. Bounded
deliverables are what make enterprise contracts signable.

**Venue policies vary and change faster than the software.** Disclosure requirements differ by
venue and are revised between cycles. Any product that certified readiness against them would
be asserting knowledge of a moving target it does not track, and would be wrong on some
customer's venue on some cycle. Refusing to certify is not caution; it is declining to make a
claim about facts you do not have.

**The tail risk is asymmetric and lands on you.** One customer retraction traceable to an
undisclosed generated manuscript costs more reputation than the entire first year of revenue.
The default makes it structurally hard for a customer to reach a venue without a disclosure,
and — more importantly — makes it *documented* that they were told.

**RECOMMENDATION:** do not merely keep this default. Sell it. It is the single most credible
thing in the product for an institutional buyer, and it is the reason §3 recommends putting a
refusal-only release gate in the free tier.

### Upstream repository and dataset licences

`result-reproducer` already writes a `baseline_license_risk` artifact, which is the right
instinct and more than most tools do. It does not resolve the exposure:

- **Reproducing a baseline means cloning and running someone else's repository.** Many research
  repos are unlicensed (no licence = all rights reserved, not public domain), and others carry
  GPL, CC-BY-NC or research-only terms. A commercial customer deriving from a non-commercial
  baseline is a licence violation the tool facilitated.
- **Benchmark datasets frequently carry research-only or no-redistribution terms.** Segment C
  running a standard benchmark for commercial R&D is a real and common exposure.
- **Model provider terms are the customer's**, which is good for you: BYOK means the customer's
  own agreement governs output ownership, retention and training-on-output. Do not accidentally
  take that on by offering to supply keys.
- **Sensitive data.** Self-hosting means data never leaves the customer's machine — that is the
  security story and it is true. It is *not* a HIPAA/GDPR/IRB compliance claim, and you must
  not let it be heard as one. You have no BAA, no DPA and no audit.

**RECOMMENDATION:** allocate this contractually and explicitly. The customer warrants that they
have the right to use every paper, repository and dataset they point the tool at; that they are
responsible for compliance with venue disclosure policies; and that they will not treat any
output as verified without a named human author's review. You warrant that the software does
what the docs say and nothing about the correctness of research conclusions. Cap liability at
fees paid. **FOUNDER DECISION:** whether to offer any IP indemnity at the `site` tier — it is
sometimes the thing that closes an institutional deal, and it is a real liability against a
product with the defect profile in §5. My inclination is no, this year.

### One thing to add that does not exist

`baseline_license_risk` should be promoted into a release-gate **blocker** when the detected
upstream licence is incompatible with the customer's declared use, rather than remaining an
informational artifact. That is a small change with a large sales effect: it is the kind of
control an institutional buyer's legal team asks about by name. Not built.

---

## 7. What to do before charging anyone

Ordered. Items marked **[not built]** are work, not decisions.

1. **Make the gate real.** Enforce `restricted[]` in `orchestrator.ts` *and* in
   `python/researchforge/runner.py`; substitute the public key at build time; ship a signed
   build; test that a community licence actually cannot reach `EXPERIMENTING`. Until this is
   done you cannot invoice for a feature the customer already has. **[not built]**
2. **Fix the false all-clear and the four ingest defects around it** — items 1, 2, 3, 4 and 5
   of `PDF_INGEST_QUALITY.md` §5. Roughly a day and a half by the author's own estimates.
   Item 4 is the one that matters most: never say "none" unless something was checked.
   **[not built]**
3. **Run one full pipeline with a real model key** and see what the release gate does when the
   prose is not synthetic. Nobody has. **[not built]**
4. **Build the dependency time machine** — historical wheel index, multi-version interpreter
   pool, conda backend, PyTorch index. Attacks 45% of reproduction failures, needs no GPU.
   **[not built]**
5. **Assemble the retrospective benchmark corpus and publish one number** for innovation
   quality, with its contamination floor. The machinery refuses to run without it, correctly.
   **[not built]**
6. **Do the pricing interviews in §8** before quoting anyone. Five to ten PIs and three
   industrial research leads is enough to replace four of the six guesses below.
7. **Write the EULA and the support envelope.** Warranty disclaimer, liability cap at fees
   paid, customer warranties on input rights and venue disclosure, and a written definition of
   what `site` support actually promises. **[not built]**
8. **Sign three design partners at $0** — one segment B, one segment C, one that wants the
   `CM_NONE` degradation path specifically — in exchange for a written case study and
   permission to quote a number. Do this *while* 1–5 are in progress, not after.
9. **Then set list prices**, using the interview data rather than §4's guesses.

Selling before item 1 is selling something you cannot deliver. Selling before items 2 and 3 is
selling something whose first week will contradict the pitch. Items 4 and 5 can trail the first
paid contract if the design partners know what they are getting.

---

## 8. Every number I guessed, and how to replace it

I have no market data. The following are judgement calls, listed so they can be attacked
individually rather than inherited silently.

| # | The guess | Where it appears | How to replace it |
|---|---|---|---|
| 1 | Fully-loaded annual cost of a junior researcher in the target geography, and what fraction of it a tool like this defensibly captures | Chain 1, all of §4 | Ask five PIs directly what a first-year student costs their grant, and what they have previously paid for research software. Public salary scales give the floor; the overhead multiplier is institution-specific |
| 2 | The procurement threshold — the amount above which a PI cannot simply expense a purchase | Chain 3, the $6,000 lab price | One question to any PI or department administrator at three target institutions. Cheapest and highest-leverage number here |
| 3 | Whether the innovation engine produces good ideas | §5, the whole value proposition | Build the corpus of seed/follow-up paper pairs with explicit relations, run `retrospective-benchmark-builder`, report recall@k with a measured contamination floor |
| 4 | Monthly compute + API spend per segment (the §4 table) | Chain 2, which segments can afford which tier | Ask design partners for their actual monthly cloud and API bill. They will tell you; it is not sensitive |
| 5 | The 4× academic-to-commercial multiple | Commercial tier price | Judgement; test by quoting it to two industrial prospects and watching whether they argue |
| 6 | 2.5–3× annual for perpetual + maintenance | Perpetual option | Standard-shaped, unverified. Only matters if an air-gapped `site` deal appears |

Two further things I could not determine from the repository, which are not guesses but gaps:

- **Whether any of the 32 skills has ever been run at scale by someone who is not the author.**
  Everything in this document about defect rates comes from the author's own measurement of the
  author's own system, which is admirable and unusually honest, and is still a single observer.
- **What the support load looks like.** A self-hosted product with BYOK, a Python subprocess
  boundary and a 12-item known-defect list will generate support tickets at a rate nobody has
  observed. If that rate is high, the $6,000 lab tier may be underwater on support alone. The
  design-partner phase is what measures it, which is another reason to run it before pricing.

---

## Summary of the recommendation

Give away everything through the human gate, because that is where the differentiator is
visible and where the measured defects live. Charge at the moment of commitment, when the
customer is about to spend their own compute. Price the lab tier under whatever the procurement
threshold turns out to be, and price the commercial tier as a rounding error on an R&D compute
budget. Sell the refusal — the release gate's `submission_ready: False` is the most credible
asset in the product for an institutional buyer.

And before any of that: make the paywall exist, because right now it does not.

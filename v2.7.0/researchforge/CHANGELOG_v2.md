# v2.0.0 — commercial build (2026-08-11)

## Licence issuing and enforcement

- `tools/license/{keygen,issue,server}.mjs` — Ed25519 keypair generation, licence signing with a
  self-check that refuses to emit an unverifiable licence, and a minimal issuing service with an
  admin token, rate limiting and an append-only issuance ledger that never records the key.
- **The paywall now actually gates.** It previously computed the restricted feature list correctly
  and then printed it while running every gated stage anyway. Enforcement is now on both sides of
  the language boundary, because `researchforge.runner` is invokable directly.
- A licence cannot be made unbypassable in self-hosted software. The design goal is therefore that
  bypass is a deliberate, *recorded* act: `RESEARCHFORGE_ALLOW_UNLICENSED=1` works, and writes itself
  into provenance and the release manifest.
- Fixed a latent bug that would have broken every real licence: the verifier called
  `crypto.verify("sha256", ...)`, and OpenSSL rejects a named digest for Ed25519 rather than
  ignoring it. Every test passed because they all verified against a null key.

## Measured ingestion quality on real PDFs, and three fixes

18 real paper PDFs from 14 GitHub repos. Measured: 73% of extracted claims carried a wrong section
label, 28% were mined from past the References heading, and 1 in 18 PDFs was un-ingestable.

Fixed:
1. **Lone surrogates no longer crash ingestion.** pypdf emits unpaired U+D835 for math-italic
   glyphs; the artifact store died on `.encode()` and a real paper looked like a runtime bug.
   Now transcoded lossily *and the loss is named*, because a silent recovery would hide that the
   formulae on those pages are junk.
2. **`HEAD_RE` now matches `1. Introduction` and roman numerals.** It previously required whitespace
   after the section number, so ICML/PMLR style was invisible: 7 of 18 papers detected exactly two
   sections, and every body claim was filed under "Abstract".
3. **`layout_warnings.md` can no longer claim a clean bill of health.** It used to print
   "none: text extraction produced anchored, ordered content" whenever the warning list happened to
   be empty — an affirmative all-clear for checks that were never run. In a system whose entire pitch
   is refusing to report work it did not do, this was the worst defect in the codebase. It now
   separates what was checked from what was not.

Everything else found is documented in `docs/PDF_INGEST_QUALITY.md` with its measured cost, and
pinned by tests.

## Acceptance harness

`acceptance/` — `RUBRIC.md` (eight dimensions, every threshold traced to a runtime constant or a
stated property), `grade.py` (consumes the existing audits rather than re-deriving them, because two
thresholds means the lower one wins), `run_acceptance.sh` (fails loudly if no API key or GPU rather
than degrading to the offline path).

`NOT_MEASURED` is a distinct outcome from `PASS`, and it blocks acceptance. Grading a project whose
artifacts carry `synthetic: true` is refused outright.

## Commercial proposal

`docs/COMMERCIAL.md` — what is actually being sold, four buyer segments and what each is buying
instead of, tiers mapped onto the feature gates that exist, price points with the reasoning chain,
what will block a sale, and legal exposure. Every guess is marked as one, with how to replace it.

## Corrected: the A′ arm refuted this project's own recommendation

`docs/study/APRIME_FINDINGS.md`. v1.1.0 recommended a "dependency time machine" — a date-pinned wheel
index and a multi-version interpreter pool — as the highest-value engineering investment. Both were
built and tested on the same 20 papers. The date snapshot helped one paper; the interpreter pool
helped none. The apparent gain from switching resolvers evaporated under a real install, because a
dry-run does not execute `setup.py`.

The real blocker is a CUDA build toolchain: `flash-attn` and its class import torch at build time and
then compile. `result-reproducer`'s remediation table has been corrected to say so, and to say
explicitly that the date-pinned index was tested and did not work.

## Step 0, executed

`docs/study/step0_results.json`. 30 seeds per metric on the worked example: **8 of 12 metrics need a
tolerance wider than the 2% floor, one of them 67× wider.** A fixed ±5% tolerance would classify
those metrics' own noise as a failed reproduction. The floor is now documented as a floor and nothing
more.

## Still not done

- **No acceptance run has been performed.** No API key, no GPU here. The harness exists; the run does not.
- **B arm not executed.** The protocol's human-ceiling comparison needs a human; the agent-vs-human gap is unmeasured.
- The innovation engine has never been scored against a benchmark. `retrospective-benchmark-builder`
  correctly refuses without a corpus, and no corpus exists.
- `pubkey.ts` still holds the substitution sentinel: no shipped build can verify a licence until the
  release process substitutes a real public key.

---

# v2.1.0 — the falsifiability mechanism, made real (2026-08-12)

The acceptance grader built in v2.0.0 immediately failed the stock run on two structural
defects. Both were real, both reproduced on any run, and both are now fixed. A grader that
finds nothing on its first outing is a grader nobody should trust.

## 1. Void conditions were prose, so nothing was ever checked

Every `invalid_condition` the blueprint compiler emitted carried a `detect` field written for a
human — "count completed runs per condition in the experiment ledger". No runtime can evaluate
that. Not one condition had ever fired or cleared on any run, which made every experiment the
system produced formally **unfalsifiable** while its spec said otherwise in good English.

`researchforge/invalid_conditions.py` gives each condition a machine-evaluable `check`:
`min_completed_runs_per_condition`, `field_stable_across_runs`, `metric_names_stable`,
`configs_match_except`, `artifact_field_present`, `text_present_in_artifact`. The prose stays
for the reader; the predicate is what binds.

`experiment-runner` evaluates them after the runs and **excludes a void experiment's runs before
ranking**, rather than annotating the ranking afterwards. A void run is not a negative result —
it is nothing, and nothing may be concluded from it.

A condition whose kind is unknown reports `UNCHECKED`, never satisfied. An experiment none of
whose conditions could be evaluated is reported as not falsifiable, which is a distinct and
worse state than void.

Each ledger entry now stamps the evaluator digest, because `EVALUATOR_CHANGED_MID_RUN` was
otherwise permanently `UNCHECKED` — which reads like "fine" and means "we never looked".

## 2. Ablations measured something other than the claim they tested

Ablation specs declared `primary` and `seed_dispersion` while the experiments they ablate
declared domain metrics. Disjoint sets: the contrast an ablation exists to make could never be
constructed from the ledger. The ablation was structurally incapable of answering the only
question it was created to answer.

Ablations now inherit the parent experiment's metrics and add dispersion on top. The worked
example's ablation was rewritten accordingly — and the corrected contract immediately rejected
the old implementation for returning undeclared metrics, which is the gate working.

## 3. A number extractor that silently dropped exponents

Found while diagnosing why a slide number would not bind. `5.3462760410685855e-16` — a
dispersion of essentially zero — was parsed as `5.34628` and rendered on a defense slide as five
and a third. **Wrong by sixteen orders of magnitude.** The binder caught it as "unbound", so the
gate held, but the diagnosis pointed at the wrong thing.

`NUMBER_RE` now matches scientific notation as one quantity, exponent included, while still
keeping digits inside identifiers (`E-001`, `v1.2`) out.

## 4. Two false positives in the claim auditor

Sample sizes (`n=7 runs`) and recorded run scalars (wall-clock, seed) were marked FABRICATED
because the indexes exposed only metric values. Both are recorded facts traceable to a run id.
An auditor that cries wolf on sample sizes is one nobody reads. The worked example now audits
11 of 11 claims as SUPPORTED, and the release is still refused — for the synthetic draft, which
is correct.

## 5. A scoping error I made and then reverted

I first limited `BASELINE_NOT_ESTABLISHED` to comparative specs, reasoning that a diagnostic
experiment at CM_NONE compares against our own implementation and needs no external pin. A test
disagreed and the test was right: an internal reference has to hold still too, or a change in
our own code is credited to the mechanism. The condition applies to every spec; only the *check*
differs — external revision pin for comparative specs, entry-point digest stability for internal ones.

## Verified

300 tests (276 Python + 24 TypeScript). The worked example traverses all 16 states, all three
experiments are non-void and falsifiable with zero unchecked conditions on the two primaries,
11 of 11 claims SUPPORTED, and the release gate still refuses the synthetic draft.

# v2.2.0 — the comparison has a third arm (2026-08-12)

Ablations and baselines now measure against the current strongest method, not only against the
paper the project started from.

## 1. Nothing ever asked who is currently best

`result-reproducer` located the *source paper's* repository and stopped there. Every downstream
comparison was therefore "did we beat what they beat", and every ablation isolated a mechanism
inside a method whose competitiveness nobody had established. Both are real measurements. Neither
answers the question a reviewer asks, and an ablation anchored to a non-competitive full method is
a strawman with extra steps.

- `result-reproducer` now consumes `benchmark_matrix` and `literature_candidates` and writes
  `baseline_assets.sota`: candidates drawn from `--set sota_methods='[...]'`, from the benchmark
  table, and from titles that assert the frontier. `established` stays **False** until a candidate
  has been run here and graded RL3+. A number scraped from a benchmark table is a *reported*
  number; placing our measured result beside someone else's best-case reported one, under their
  hardware and their tuning budget, is the most common way a comparison becomes fiction.
- `research-blueprint-compiler` adds a third condition to every comparative spec when a candidate
  exists, states in the success metric whether that arm is measured or reported, budgets for it
  (`conditions: 3`), and emits `SOTA_NOT_ESTABLISHED` as a machine-checkable invalid condition.
- Ablation specs carry `anchored_to`, including a `strawman_risk` naming which of the two weaknesses
  applies: no sota arm at all, or one declared and never run.
- When no candidate is found the compiler says so and says what it costs: no claim of
  competitiveness can come out of that plan however the runs turn out.
- `claim-citation-auditor` blocks "state-of-the-art" / "outperforms all prior" claims about *this
  work* unless a `sota` arm completed with metrics in the ledger. Reported numbers do not satisfy
  it. Sentences describing the literature are not graded, because a check that misfires in related
  work is a check that gets turned off.

## 2. The runner only ever ran one arm

Found while wiring the above. `experiment-runner` invoked the generated entry point with no
`--arm` at all, so it always executed the default — the candidate. A spec could declare a baseline
and report `conditions: 2`, and the ledger would contain two runs of the same condition.

Every comparison built on such a ledger was the candidate against itself. It could not fail: the
means matched because they were the same runs.

- The runner now executes every arm the spec declares, the generated entry point derives its
  `--arm` choices from the spec rather than a hard-coded pair, and ledger rows carry `arm`.
- A spec that designs more conditions than it names runnable arms now says which ones will never be
  measured, instead of leaving "small effect" and "never ran" indistinguishable downstream.
- `research-blueprint-compiler`'s diagnostic spec said `conditions: 1` while declaring a baseline
  the runner would be asked to execute. The budget was short by half.

## 3. Four places that pooled a method with its own control

Once the arms actually ran, every consumer that grouped by experiment id started averaging the
candidate together with the baseline it was being compared against. The result is arithmetically
valid, describes no condition that was ever run, and passes every integrity check downstream
because it is a real mean of real runs.

- `experiment-runner._rank` scores a branch by its **candidate** arm and reports the others beside
  it, with `contrasts` giving candidate-minus-control per metric. No significance is claimed there;
  `integrity-auditor` owns that question.
- `data-analyst` groups by `(experiment, arm, metric)`. Grouping by arm alone — the first fix, and
  wrong — would have merged E-001's baseline with E-ABL-001's baseline into one group called
  "baseline".
- `integrity-auditor` identifies one control **per experiment**, refuses to pair arms across
  experiments, and no longer fills the multiple-comparison family with contrasts nobody designed.
- `claim-evidence-graph` emits one own-work claim per (experiment, arm, metric). It previously
  emitted "In E-001, failure_mode_incidence measured 0.2131 (n=14 runs)" — 7 seeds of the method
  and 7 of its control, averaged. `figure-factory` caught it: the number matched nothing in the
  analysis. That refusal is the one that made this whole class visible.

# v2.3.0 — the innovation engine has a benchmark, and the benchmark has a floor (2026-08-12)

`retrospective-benchmark-builder` had been correctly refusing to run since v1: no corpus existed,
and it will not invent one. The corpus now exists. Its most important output is not the benchmark.

## The floor is 0.75

Asked to name — from memory, with no corpus, no retrieval and no tools — the papers that beat each
seed on its own benchmark between 2020 and 2022, `claude-opus-5` named **3 of 3** ImageNet gold
directions and **1 of 2** CoNLL 2003 directions. The probe ran in a separate context from the one
that built the corpus, so the answers could not leak from the session that knew them.

**Any recall@10 at or below 0.75 on this benchmark measures the model's reading, not its judgment.**
Without that number the same score would have been reported as a capability, which is the failure
this skill was written to prevent. The floor is a lower bound: it is scored by title containment,
which misses paraphrase and misses recognition that never surfaces as a title.

## Leaderboards, not citations

Forward-citation traversal is the obvious source and the wrong one. A citation edge says one paper
mentioned another; deciding which mentions are *material* is a judgment, and a judgment made by the
model that is about to be scored is not evidence.

`tools/benchmark/build_retro_corpus.py` uses the archived Papers With Code evaluation tables
instead. Per benchmark it takes the record holder as of a cut date as the seed, and the
**record-holder chain** after it as the gold directions: each paper that set a new best, in date
order, deduplicated by arXiv id. The relation `replaces` is then a recorded fact — a published
number on the same benchmark exceeding the previous record — and no text was read to decide it.

What it costs, stated in the shipped report rather than discovered later: the chain contains only
work that improved a headline number. It excludes work that diagnosed the seed, refuted it, changed
the problem, or was tried and lost. A system scoring 0 here has not been shown to be bad at
research; it has been shown not to predict leaderboard succession.

## The skips are findings

Five benchmarks were queried; three were dropped by the rule. Nothing in the Papers With Code
record beat the 2019 leader on **SQuAD1.1 dev** or **MultiNLI** during 2020–2022, and WMT2014 En-De
produced exactly one record-setting successor. Those benchmarks were saturated. A suite that
dropped them quietly would have hidden that, so `benchmarks_skipped` carries each one with its
reason and the builder prints them.

That leaves **2 seeds and 5 gold directions** — small, and labelled as such. The one non-mechanical
step is the benchmark list itself, a convenience enumeration of five widely used benchmarks; it is
recorded in `selection_rule`, and it biases toward heavily-worked benchmarks whose successors are
famous, which *raises* contamination rather than lowering it.

Shipped in `benchmarks/retro-v1/`: the corpus, the raw leaderboard rows as retrieved, the paper
metadata, the frozen benchmark, the run config and the report. A test rebuilds the shipped corpus
from the shipped inputs and asserts byte equality, so the corpus is provably derived rather than
written.

## Still not done

- **Scoring the engine against it** needs a real model provider. The offline stub emits structural
  placeholders and scoring placeholders yields a number, not a measurement. The bar, when someone
  runs it, is not zero — it is 0.75.
- **No blind human rubric.** recall@k is a regression signal between versions. It is not evidence
  that the output is worth anything, and the two do not substitute for each other.
- **No acceptance run** (no API key, no GPU here) and **no B arm** (needs a human).

# v2.4.0 — the benchmark can now be scored against (2026-08-12)

v2.3.0 shipped a frozen benchmark and a contamination floor. Nothing could score a system against
it: `research-eval-harness` computed recall@k only if handed adjudicated matches, and nothing
produced adjudicated matches. That is the missing half.

## 1. `tools/benchmark/score_directions.py`

Two phases, because matching a generated direction to a gold one is a judgment and the benchmark's
own scoring contract forbids every shortcut — not string overlap, not an embedding threshold, not a
title match. Two directions can share every content word and mean different things ("scale the
teacher" vs "scale the student"); two can share none and mean the same.

- `--emit-packet` writes the **full cross product** of the top-k generated directions against every
  gold direction, with both texts and no verdicts. Deliberately not a shortlist: a shortlisting
  function would be the real matcher while the adjudicator believed they were doing the matching.
- The rubric ships inside the packet, including the rule that does most of the work — a generated
  direction *strictly more general* than the gold one is `NO_MATCH`.
- An unfilled pair is `UNADJUDICATED`. It is excluded from numerator and denominator, and the run
  is reported `INCOMPLETE` rather than as a low score. Counting undecided as wrong is how an
  unreviewed run comes to look measured.
- `--score` reports one of four verdicts. `AT_OR_BELOW_FLOOR` names the only thing that matters
  about a number on this benchmark: **the reference point is 0.75, not zero.** A system at or below
  the floor produces the same number a system that had merely read the literature produces.
- A model judge is allowed and is *recorded as one*, with the caveat attached: a model adjudicating
  whether an output matches literature it has also read has the contamination problem one level up.
- `--emit-harness-inputs` converts the filled packet into what `research-eval-harness` consumes. A
  test asserts both paths compute the same recall from one adjudication — two formats for one fact
  is how a project ends up with two numbers and no way to say which is right.

## 2. `tools/benchmark/blind_rubric.py`

recall@k answers one question: does the system name the directions the field took? It cannot say
whether they are any good, and the gap is not academic — the gold set is leaderboard succession, so
a system that only ever proposes "scale it up" scores well while proposing nothing worth reading.

The blind rubric is the other half, and the instrument is the whole thing:

- Five criteria, each defined by what a **1 and a 5** look like. "Rate novelty" collects the rater's
  mood.
- Arms must be **balanced** — a rater who notices the imbalance can guess the majority arm and beat
  chance without reading — and above a minimum size, below which the comparison is a coin flip
  dressed as a rubric.
- **Tells are stripped and reported**: model-voice phrasing, citation markers, numbered-plan
  formatting, emoji, markdown leads. One "as an AI" and the rater is no longer blind for the rest
  of the session.
- The order is shuffled by a **recorded seed**, so a disputed packet can be rebuilt and shown to
  have had the order it claims. The key is a separate file that raters never see.
- Analysis **refuses** in three cases: the packet no longer hashes to its key (edited after the
  fact, so the arm labels attach to text nobody rated); raters guessed the arm above chance (the
  ratings are then ratings of the arm); or the blind check was never run at all — which is not the
  same as it having held.
- A blank score is `not rated`, never a 1.

## What is still missing, and what is no longer missing

Scoring the innovation engine needed a real model provider **and** a scorer. It now needs only the
provider. The blind rubric needed an instrument **and** a panel. It now needs only the panel.

Still absent for the same reasons as before: the acceptance run (no API key, no GPU here) and the
B arm of the reproduction study (needs a human).

# v2.5.0 — the B arm is built, and the power analysis says it was specified wrong (2026-08-12)

The reproduction study's B arm — the human-ceiling comparison that gives the agent's 15% a
denominator — has been outstanding since v1.1.0 with the note "needs a human". It needed an
instrument too. `docs/study/b-arm/` is that instrument, and building it surfaced a defect in the
protocol itself.

## The finding: n=8 cannot detect any gap anyone expects

`analyse.py` computes the minimum detectable difference before it will report a gap:

| per-arm n | smallest difference detectable at 80% power |
|---:|---:|
| **8 (the protocol)** | **0.70** |
| 16 | 0.50 |
| 25 | 0.40 |
| 44 | 0.30 |
| 99 | 0.20 |

`REPRO_STUDY_PROTOCOL.md` §3 called n=8 "a cost compromise" giving "a rough estimate of the
ceiling". It does not give a rough estimate. On a simulated fill it produced a human rate of 0.375
with a 95% interval from **0.14 to 0.69** — wide enough to contain both "the agent is fine" and
"the agent is useless" — and the analysis correctly returned `NOT_DETECTABLE` on a
thirty-seven-point observed gap.

Run the B arm as specified and the most likely outcome is a week of a skilled engineer's time
spent learning that the sample was too small to tell. **Budget n≈25**, or run 8 knowing you are
collecting failure-code distributions and qualitative notes rather than a ceiling.

`docs/study/FINDINGS.md` has been corrected accordingly. The old row said "not executed"; it now
says the design as written is not usable and what to do instead.

## The packet: blinding that can be audited

- The eight papers are **drawn, not chosen** — a seeded shuffle whose seed is in the packet.
  Picking by hand selects for papers whose difficulty someone already has an opinion about, and
  that opinion comes from the A-arm results this arm must be blind to.
- Every A-arm outcome is withheld — level, rationale, failure codes, timings, notes — and the list
  of what was withheld ships in the packet, because withholding that cannot be audited is
  indistinguishable from forgetting.
- The builder **refuses** if the A-arm records carry a field it does not classify as carried or
  withheld. A field added later becomes an error rather than a leak.
- Each worksheet asks `saw_a_arm_material` after the fact, and says plainly why a truthful "yes"
  costs nothing: it moves the worksheet into a separate sample rather than wasting it.

## The analysis: four refusals

1. **Different papers, no gap.** The returned packet must hash to its key.
2. **A run past its timebox executed a different design.** Reported separately, never merged.
3. **An unattempted paper is `NOT_RUN`, not a failure.** Counting it as RL0 makes the human arm
   look worse and the agent look better — the exact direction of error this study exists to avoid.
4. **RL1 is not a reproduction.** Success is RL3+. "It ran without erroring" and "right direction
   at reduced scale" are progress; folding them into a success rate is how this kind of study
   reports a number several times too high.

## Still needing something this environment does not have

- **The acceptance run**: an API key and a GPU. `acceptance/run_acceptance.sh` already collects
  every missing precondition and names them together, and refuses `--model offline` outright.
- **The B arm itself**: a skilled engineer, ideally twenty-five of the twenty papers' worth of
  them.
- **Scoring the innovation engine**: a real model provider. The scorer exists; the bar is 0.75.
- **The blind rubric**: a panel. The instrument exists.

# v2.6.0 — an adversarial audit of everything shipped in v2.3–v2.5 (2026-08-12)

Two independent audits, each told to find defects that produce **wrong numbers or false
confidence** rather than style problems. Twenty-three were confirmed by executing the code. Every
test suite was green throughout: none of these raised, and most made a number look *better*.

One of them invalidates a claim published one release ago. That one first.

## The correction: the B-arm power analysis used the wrong test

v2.5.0 reported that the reproduction study's B arm at n=8 "cannot detect anything smaller than a
70-point difference" and needed n≈25 for 0.40, n=99 for 0.20. Those figures came from the
two-independent-sample formula. **Both arms attempt the same papers.** That is a paired design; the
correct instrument is McNemar on the discordant pairs, and using the independent formula on a
positively correlated design inflates the sample requirement — by **2.7× at a 20-point
difference**.

Corrected, at a 25% discordance rate: n=8 resolves about **0.42**, not 0.70, and 0.20 needs **47**
pairs, not 99. The instrument now runs an **exact** McNemar test rather than comparing an observed
difference against a power threshold — the old affirmative branch would emit `DIFFERENCE_OBSERVED`
on four discordant pairs, where the exact test gives p=0.125.

n=8 is still thin — five discordant pairs all one way is p=0.0625, which does not reject at 0.05 —
but "still thin" is a different claim from "cannot detect anything anyone expects", and the earlier
one was overstated. `docs/study/FINDINGS.md` and the B-arm README carry the correction.

## The scorer counted only the questions the system answered

`score()` built its average from the adjudication packet's items, and a seed the system produced
nothing for contributes no items. **Answering one seed out of five and getting it right reported
recall 1.0 and `ABOVE_FLOOR`** — while the packet builder's own stdout said "it scores 0 by
absence, not by error". It did not. The packet now carries the full gold denominator.

Four more in the same file:

- **Two numbers from one adjudication.** `score()` macro-averaged over seeds; the harness
  micro-averages over gold directions. On unequal gold counts they disagreed — 0.5 versus 0.2 — and
  `to_harness_inputs`' docstring promised they could not. The headline is now micro, matching the
  harness, with the macro reported beside it.
- **A score exactly at the floor cleared it.** `round(2/3, 6)` is strictly greater than `2/3`, so a
  system sitting precisely on the contamination floor was handed the capability claim the module
  exists to withhold. Compared unrounded.
- **`list("use a better teacher")`** produced twenty single-character "directions" and a full
  adjudication packet of nonsense for a human to rate. A seed whose value is not a list is refused.
- **A duplicate gold id shrank the denominator** while still emitting a pair to adjudicate: 1 of 3
  real directions reported as 1 of 2. Refused at packet build.
- The harness input file was written *before* the packet was validated, so a run the tool then
  refused to score left a file on disk that says nothing about the refusal.

## The blind rubric's blind check could not fire

`blind_held = accuracy < 0.65` is one-sided, and the guess had to be the literal string `machine`
or `human`. So raters who identified **every** arm correctly and wrote the labels inverted scored
0.00 and were reported as *"at or near chance; the blind held"* — as were raters who wrote "AI",
"model" or "system". The packet never told them which strings were legal. Now two-sided on
`|accuracy − 0.5|`, unrecognised labels are refused rather than graded as misses, and the
instructions name the two legal strings.

Worse, and more structural:

- **The packet was its own key.** `entry_id = sha(arm, index, text)[:10]` over data the packet
  carries — 800 hashes recovered the full arm assignment for a 20-entry packet. Now salted, with
  the salt only in the key; pass it back to rebuild a disputed packet byte-identically.
- **Nothing bound the key's rows to anything.** Flipping every `arm` reversed the reported
  difference and the output still displayed a hash that appeared to attest to it. The key now
  carries its own hash.
- **The shuffle correlated arm with position.** The hand-rolled LCG's low two bits are a counter
  (`state ≡ seed + n mod 4`), so the last swaps were decided by seed parity. A rater guessing purely
  from slot number scored **0.579** — above chance, under the old threshold, reported as held.
  Mersenne Twister now, seeded and still reproducible.
- **`seed_id` was rater-visible** and non-null only for dict-shaped directions, so it identified the
  arm perfectly whenever machine output was structured and human output was typed as prose.
- Blank entries, out-of-range scores and `"n/a"` are handled: blank is refused at build, out of
  range blocks reporting, and `"n/a"` is not-rated rather than a crash that loses the session.

## The corpus builder lost data three ways, all silently

- **The seed tie-break kept the *later* paper.** The key's second term was `True` for every
  pre-cutoff row, so it was constant and ties fell through to file order — picking T5 (1911) over
  the earlier T5-XXL (1910) at an exact 92.0 tie on the shipped MultiNLI table.
- **Same-month record setters were collapsed.** arXiv identifiers carry only `YYYY-MM`; sorting
  `(date, −value)` processed the better paper first, after which the other no longer beat the
  standing record and disappeared. ImageNet has eleven such months in the evaluation window.
  Fixing it **recovered two gold directions** (FixEfficientNet-L2 and LUKE): the corpus went from 5
  to 7, and the order-unknown ambiguity is now stamped rather than resolved invisibly.
- **Dropped rows were invisible.** A row linked to a non-arXiv venue carries no date and leaves the
  pool. If it held the record, the recorded seed is not the record holder and every `replaces`
  claim is wrong while looking identical. Five are dropped across the shipped tables and are now
  listed.
- Latent: `max()` and `>` hardcoded higher-is-better. A WER spec would have seeded on the worst
  method and called every later, worse number a record. `direction` is now a required spec field.

## The B-arm packet leaked two ways

- **An unanswered question read as "no".** Both `saw_a_arm_material` and `wall_clock_hours_used`
  ship as `null`, and `if w.get(...)` treats `null` as falsy. An engineer who filled in only the
  level produced a clean 8/8 ceiling that had passed neither refusal.
- **The unclassified-field tripwire saw only top-level keys.** A carried field whose value is a
  dict was copied wholesale — and `notes` is already a dict in these records. The day someone turns
  `github_repo` into a struct, the whole A-arm outcome rides inside it.
- The draw used the same broken LCG: measured over 200,000 seeds, the last record in the file was
  drawn **half as often** as the first ten. A draw whose bias is a function of file order is still
  a selection, just on something nobody chose.

## Benchmark v2

Corpus `20bd6243a0369fcd`, frozen at `1b40232d45fcc9d4195cbc203d2e436e`, 2 seeds and **7** gold
directions. The contamination floor was re-probed against the enlarged gold set, in fresh contexts
again: **0.71** (3 of 4 ImageNet, 2 of 3 CoNLL). The bar for any system scored against it moves
with it.

379 Python tests, 26 TypeScript. Every defect above has a regression test that fails against the
old code.

# v2.7.0 — the same audit, turned on the older code (2026-08-12)

v2.6.0 audited the tools written in v2.3–v2.5. This one audits what the multi-arm change of v2.2
touched: the runner, the analysis plane, the evidence graph and the manuscript gate. Nine defects,
all reproduced by executing the code, all in a green suite.

The first one is the worst thing this project has shipped.

## 1. Declaring a state-of-the-art candidate deleted every measurement

`SOTA_NOT_ESTABLISHED` was emitted as an **invalid_condition**, and a fired invalid condition means
the experiment is VOID. Its check read `baseline_assets.sota_established` — a field **no runtime
path ever sets True**. `result-reproducer` hard-codes `established: False`, and nothing promotes it.

So: name a SOTA candidate, and the primary experiment could never clear the condition. Three arms,
six completed runs *including a measured state-of-the-art arm at 0.95*, and the runner reported

```
warning: E-001 is VOID: SOTA_NOT_ESTABLISHED
best_candidate: {"selected": null,
                 "reason": "no run produced a measurement, so no branch can be ordered above another."}
```

Six runs measured. The feature added to make comparisons mean more destroyed the comparison, and
the refusal text asserted the opposite of what had happened. The design comment three lines above
it said *"is not void — it is just narrow"*; the code did the other thing.

Fixed by making the distinction real. Specs now carry `narrowing_conditions` beside
`invalid_conditions`: a fired narrowing condition limits what the result may be claimed to show and
leaves the measurement intact. `SOTA_ARM_NOT_MEASURED` checks the **ledger** — a new
`ledger_arm_completed` check kind — so a state-of-the-art arm that actually ran actually clears it.
The frontier claim itself is still hard-blocked by `claim-citation-auditor`, which is where that
enforcement belongs.

## 2. `SEEDS_TOO_FEW` got weaker as an arm got worse

The check counted completed runs per arm — over the arms that *appeared in the ledger*. An arm with
one completed run FIRED; the same arm with **zero** produced no group key and CLEARED. Combined
with defect 8 below, an arm starved to nothing escaped the check entirely. Expected conditions now
come from the spec.

## 3. The cross-experiment guard was void for arm-less rows

`_branch` returned a bare experiment id when a row carried no arm — and the runner's own docstring
says such rows exist, while the ledger is append-only, so one project holds both shapes. For those,
`_experiment_of` returned `""`, so the guard compared `"" != ""` and **passed every pair**,
generating exactly the cross-experiment contrasts it exists to block and inflating the
multiple-comparison family with them. Rows without an arm now read as `candidate`, matching the
documented default.

## 4. An ambiguous control was discarded in silence

An experiment with two control-named arms gets no control — correct — but nothing said so.
`NO_DECLARED_CONTROL` only fires when the control set is *globally* empty, so one clean experiment
elsewhere suppressed it. A real +0.30 effect was filed as "a difference, not an improvement" with
the reading *"without a declared control"* — when a control had been found and thrown away — while
a +0.005 effect elsewhere drove the recommendation. Now an `AMBIGUOUS_CONTROL` finding at HIGH.

## 5. "No metric declares a direction" when the direction was declared

A branch is scored by its candidate arm. If the candidate was starved and only controls measured,
`_rank` fell through to the direction-missing branch and told the operator to add a `direction`
that was already there — sending them to fix the wrong thing and hiding the real cause. Now named:
*"N branch(es) measured something but not their candidate arm."*

## 6. A contrast with no direction reads as an improvement

`_contrasts` reported `difference: +0.70` against the state of the art for a candidate that was
**85% worse** — the metric was `val_loss`, direction `minimize`, and the direction was available in
the same function's caller and simply never passed in. `ranked_branches` is a published artifact.
Each contrast now carries `direction` and an explicit `candidate_is: better | worse | undetermined`.

## 7. The timebox ate the strongest arm without saying so

Arms run `baseline, candidate, sota`, so the timebox is spent last-first and takes the comparison
arm — a defensible priority. `TIMEBOX_EXHAUSTED` was the only NOT_RUN reason with no warning, and
the lost contrast vanished from `contrasts` with no trace. A real run: sota measured nothing, the
winner was declared from a single seed, and the skill's warnings said nothing. Now the exhaustion
is reported with its per-arm counts, and an uncomputed contrast is emitted with `absent_because`
rather than omitted — absent is not zero and is not negative.

## 8. Own-work claims traced to nothing, and unbound their own figures

`_own_claims` listed `run_id`, which `_base_entry` sets to the *orchestration* run for every row —
so a claim promising "a number can be traced to the runs that produced it" listed the same id three
times. Now `attempt_id`. Separately, the edge's `branch` was `E-001:candidate` while the analysis
group for the same arm-less rows was `E-001`, and `_edge_result` returned only the most specific
id: the figure was refused as `figure_data_missing` **for a claim that was fully supported**. The
binder now sees every identifier the edge names, with the numeric read-back check unchanged.

## 9. The frontier gate missed the common phrasings and blocked correct text

Three false negatives, each a real state-of-the-art claim that passed with zero blockers:

| draft | why it passed |
|---|---|
| `ForgeNet outperforms all prior methods on WMT14.` | no self-reference pattern matches a system's own name |
| `Our approach is simple. It outperforms all prior methods.` | the self-reference is in the previous sentence |
| `We report the highest accuracy ever recorded on this benchmark.` | the pattern had no plain-words branch |

And a false positive class: the sentence splitter's lookahead admitted only uppercase letters and
brackets, so **a sentence beginning with a digit never started a new sentence** — routine in results
prose. `"Our method improves over the baseline. 12 recent systems report state-of-the-art numbers."`
merged into one "claim" and became a hard submission blocker on correct text.

Fixed: the splitter admits digits (with abbreviation lookbehinds, so `e.g. 3 datasets` stays
whole); self-reference carries forward within a paragraph across a continuation pronoun; an
unattributed frontier claim is read as this paper's own, because a sentence claiming the frontier
and citing no one is not describing the literature; and a frontier phrase *mentioned* rather than
asserted — `compared to the state of the art`, `e.g. state-of-the-art transformers` — no longer
counts.

389 Python tests, 26 TypeScript. Every defect has a regression test that fails against the old code.

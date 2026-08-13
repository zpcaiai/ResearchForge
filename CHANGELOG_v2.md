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

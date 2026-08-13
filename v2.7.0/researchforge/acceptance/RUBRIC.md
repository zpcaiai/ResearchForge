# Acceptance rubric v1.0.0

The question this rubric answers: **did a full ResearchForge run, on a real model key and real
hardware, produce acceptable research?**

It is written so a human and `grade.py` reach the same verdict on the same project. Every threshold
below is stated as a number, an outcome, and a reason. "80% seems right" is not a reason; a threshold
whose only justification is that it sounds strict is a threshold that will be argued down the first
time it fails.

## How a verdict is reached

Eight dimensions. Each gets exactly one of four outcomes:

| outcome | meaning |
|---|---|
| `PASS` | measured, and it met the threshold |
| `FAIL` | measured, and it did not |
| `NOT_APPLICABLE` | the project legitimately contains nothing of this kind (no figures were planned; the comparison mode requires no disclosure) |
| `NOT_MEASURED` | the grader could not see enough of the project to decide |

> **`NOT_MEASURED` is not a pass.** The overall verdict is `ACCEPTED` only when zero dimensions are
> `FAIL` **and** the `NOT_MEASURED` list is empty. This is the one rule in the rubric that exists to
> constrain the *grader* rather than the project: a harness that scores a missing artifact as a pass
> is the exact failure ResearchForge is built against, reappearing one layer up. The remaining
> verdicts are `REJECTED` (something failed), `INCOMPLETE` (nothing failed, something was invisible)
> and `REFUSED_SYNTHETIC`.

Within a dimension, one failing check fails the dimension. Dimensions are not averaged and there is
no total score. A weighted mean would let a strong result in one dimension pay for a fabricated claim
in another, and nothing in this system is for sale at that price.

## What the grader does not decide

`grade.py` **consumes** these verdicts and never recomputes them:

| verdict | owner |
|---|---|
| is a claim fabricated / unsupported | `claim-citation-auditor` → `integrity_gate`, `claim_audit` |
| do the statistics hold | `integrity-auditor` → `stats_audit.evidence_lock` |
| does the manuscript survive review | `review-simulator` → `review_report` |
| may an artifact ship | `release-gate` → `release_manifest` |

Re-deriving any of them would create two thresholds for one question, and the lower of two thresholds
is the one that actually governs — which means the careful one upstream would quietly stop mattering.
What the grader adds is the class of quantity no upstream skill computes, because every upstream check
is per-item and acceptance is per-project: **coverage and ratio**. The gate refuses one fabricated
claim; it has nothing to say about a manuscript in which 40% of claims are merely partially supported.
The figure factory refuses one figure bound to an unknown claim; it has nothing to say about a claim
with no figure at all.

## Refusal: synthetic projects are not graded

If any scanned artifact carries `synthetic: true`, `_synthetic: true` or `is_synthetic: true` — the
same markers `release-gate` refuses on — the grader emits `REFUSED_SYNTHETIC` and no dimension is
scored. `--model offline` exists to exercise the machinery and stamps everything it touches. A score
attached to that output would look like a research result, and the number would outlive the caveat
printed next to it.

---

## 1. Experiment engineering

*Do the generated experiments run, is every metric declared, are the seeds enough for the claim, and
do the void conditions get checked while the run is happening rather than after it?*

| check | threshold | rationale |
|---|---|---|
| `E1.1_every_spec_ran` | every `ExperimentSpec` produced ≥1 `COMPLETED` run carrying numeric metrics | A spec that never ran is a plan. The manuscript cannot cite a plan, so a spec with zero measured runs is either dead weight in the blueprint or a missing result — both are defects, and there is no fraction of specs that may be plans. |
| `E1.2_completion_rate` | completed runs / planned runs ≥ **0.66** | Not chosen here. `integrity-auditor` already prices a failure rate at `max_failure_rate = 0.34` (`analysis.py`), above which it raises a reporting-hygiene finding. Using the project's own number keeps one threshold for one question. Above a third failures, the surviving runs are a filtered sample rather than the experiment that was designed. |
| `E1.3_metrics_declared` | **0** metrics recorded that the spec does not declare | A metric that first appears after the run is a metric chosen with the data in view. There is no acceptable fraction of that: one post-hoc metric converts a test into a search. `experiment-runner` already rejects undeclared metrics at the boundary (`INVALID_METRICS`), so a nonzero count here means the ledger was written by something other than the runner. |
| `E1.4_seed_adequacy` | **≥3** completed seeds for `comparative` and `ablation` specs; **≥2** for `diagnostic` and `evaluation` | 3 is `MIN_SEEDS_FOR_COMPARATIVE` (`planning.py`) and `MIN_SEEDS_FOR_INFERENCE` (`analysis.py`): below it a between-condition difference has no estimable dispersion and the claim is a single draw. 2 is where `analysis._summary` starts reporting an interval at all — at n=1 it refuses, because one observation carries no dispersion information. **Completed** seeds, not declared seeds: a seed that was planned and never ran is not a sample. |
| `E1.5a_invalid_conditions_declared` | every spec declares ≥1 `invalid_conditions` entry | An experiment that cannot come back void is not an experiment, it is a plan to produce numbers. A void run and a negative run are different objects and a system that cannot distinguish them reports its voids as findings. |
| `E1.5b_invalid_conditions_machine_evaluable` | every spec carries ≥1 condition shaped `{metric, op, value}` | `rf_runtime.check_invalid_conditions` returns a prose condition as **unchecked**, never as satisfied. A condition it cannot evaluate is documentation, not a runtime guard — and it will not fire on the run it was written to void. This check is the difference between "we wrote down when this result is void" and "the run knew". |
| `E1.5c_invalid_conditions_checked_at_runtime` | every executed entry point calls `check_invalid_conditions` with `INVALID_CONDITIONS` | Verified by reading the entry-point source recorded in each ledger entry's provenance. If the entry points are not on disk this is `NOT_MEASURED`, never a pass: whether the guard ran is then unknown. |

> **Known consequence.** The shipped `research-blueprint-compiler` emits `invalid_conditions` as prose
> objects (`{code, condition, why, detect}`). A stock run therefore **fails `E1.5b`** until the
> operator supplies at least one machine-evaluable condition per spec. That is the correct outcome and
> not a bug in the rubric: the conditions as shipped are checked by a human at analysis time, and the
> acceptance question is whether the *run* was guarded. See "What a stock run fails today" below.

## 2. Baselines

*Is there something to compare against, is it pinned, and does the comparison mode permit the
comparison that was actually made?*

| check | threshold | rationale |
|---|---|---|
| `E2.1_baseline_condition_exists` | every spec names a `baseline` with a `kind` | Without a referent a number is a measurement of nothing in particular. "Better" has no meaning; only "different" does. |
| `E2.2_baseline_pinned_or_declared_unpinned` | every external baseline is `established: true` **or** carries `BASELINE_NOT_ESTABLISHED` in its `invalid_conditions` | An unpinned baseline is acceptable research practice when it is declared. An unpinned baseline nobody wrote down is not: it can change between conditions, and every difference then belongs to the baseline rather than to the candidate. The disjunction is the point — this rubric does not require a pinned baseline, it requires that the project knows which of the two it has. Internal baselines (`own_full_method`, `metric_under_test`, `internal_reference_condition`) are exempt because there is no external revision to pin. |
| `E2.3_comparison_permitted_by_mode` | **0** specs whose baseline kind is outside the mode's vocabulary; **0** comparative specs under `CM_NONE` | The comparison mode is derived from what was actually reproduced (RL0–RL4), and it is the only thing that licenses a comparison. `CM_MEASURED`→`locally_measured`, `CM_RELATIVE`→`locally_measured_reduced_scale`, `CM_REPORTED`→`reported_by_authors`, `CM_NONE`→internal only. A spec outside that map is comparing against something the project never established, whatever its prose says. |

## 3. Ablations

*Is every claimed mechanism actually isolated, is the compute matched, and is a null result reported
rather than re-run against a different metric?*

| check | threshold | rationale |
|---|---|---|
| `E3.1_isolation_test_per_mechanism` | coverage = **1.0** (every `mechanism_id` in `ablation_plan` maps to an ablation spec that produced ≥1 measured run) | An uncovered mechanism is an unfalsified mechanism. A fractional threshold — 0.8, say — would license claiming exactly the mechanisms nobody tested, since the paper does not distinguish between the covered and uncovered ones when it attributes the effect. Coverage is the quantity this grader exists to compute: `planning.py` writes one ablation per mechanism, and nothing downstream checks that the ablation ran. |
| `E3.2_compute_matched` | every ablation declares a `matched_compute` counterfactual **and** runs on the same seed budget as the non-ablation specs | Matched compute that exists only in the plan is not matched compute. The seed budget is the part of it the ledger can witness. Without it, "removing the mechanism degrades the metric" is explained equally well by the mechanism simply having cost more. |
| `E3.3a_ablation_shares_a_metric_with_its_primary_experiment` | every ablation spec shares **≥1** declared metric with the non-ablation specs | `analysis._group_values` groups the ledger by `(branch, metric)`. An ablation whose metric set is disjoint from the primary experiments' can therefore never be contrasted with the experiment it was written to isolate a mechanism for — no test can be constructed, and the isolation supports nothing. It is also the shape a retried null takes: flat on the metric that mattered, reported on a metric that appeared afterwards. Non-empty intersection, not equality, because an ablation may legitimately record extra diagnostics. |
| `E3.3b_null_results_reported` | every ablation whose test is not significant after correction appears in `findings` or `negative_findings` | A null result that is not written down is indistinguishable from one that was retried until it stopped being null. `finding-memory` already emits negative findings; what is measured here is that none of the ablations went missing between the audit and the findings. `NOT_MEASURED` if `stats_audit` or `negative_findings` is absent. |

## 4. Evidence support

*Does every quantitative claim trace to a run, and how much of the argument is fully backed?*

| check | threshold | rationale |
|---|---|---|
| `E4.1_zero_fabricated` | **0** claims with verdict `FABRICATED` | Consumed from `integrity_gate.counts`. One is disqualifying. There is no rate at which a number with no run behind it is acceptable, because the reader has no way to tell which number it was. |
| `E4.2_support_ratio` | SUPPORTED / graded claims ≥ **1 − α**, where α is `stats_audit.alpha` (default 0.05, so **95%**) | This is the ratio nothing upstream computes, and it exists because of a real gap: the integrity gate blocks `FABRICATED`, `NOT_SUPPORTED` and `SCOPE_MISMATCH` individually, but lets `PARTIALLY_SUPPORTED` through in unlimited quantity — the gate returns `PASS_WITH_CONDITIONS` and the run continues. A manuscript can therefore pass every existing check with every claim overstated. The floor is taken from the project's own declared error tolerance rather than invented: α is the rate at which this project has already agreed to be wrong about a comparison, and a claim broader than its evidence is the prose form of the same error. Tightening α tightens this automatically, which is the property a made-up constant would not have. |
| `E4.3_quantitative_claims_traced` | every claim marked `quantitative` has ≥1 `number_check` and all of them `matched` | Consumed from `claim_audit` records, not re-adjudicated. Overlaps with `E4.1` by construction and is kept separate because it is measurable when `integrity_gate` is absent but `claim_audit` is not. `NOT_APPLICABLE` when the manuscript states no quantitative claim. |

## 5. Statistical validity

| check | threshold | rationale |
|---|---|---|
| `E5.1_evidence_lock_clear` | `stats_audit.evidence_lock.blocked == false` | Consumed. `BLOCKER` and `HIGH` findings prevent evidence lock for the claims they affect; a claim whose statistics did not survive that audit cannot be written as a result, and the grader does not get a second opinion on it. |
| `E5.2_effect_sizes_with_intervals` | every test with a p-value carries `effect_size.ci95`, or an explicit refusal to compute one | A point estimate with no interval is the format in which noise gets published. `analysis._hedges_g` computes the interval, so a missing one means the field was dropped between the audit and the report. An explicit `refused` counts as satisfied — refusing to compute is a stated position; omitting silently is not. |
| `E5.3_correction_named` | when family size > 1, `multiple_comparison_correction.method` is present and not `"none"` | Named, not merely applied: a correction that is not named cannot be checked, and "we corrected for multiple comparisons" is compatible with anything. At α=0.05 a family of 5 has a ~23% chance of at least one false positive uncorrected. When the family is 1 the audit says so explicitly, which passes — applying a correction to a single test would be theatre. |
| `E5.4_no_claim_on_fewer_than_min_seeds` | **0** tests reporting a result on fewer than `stats_audit.min_seeds_required` (default 3) seeds per arm without a refusal | Below 3 there is no power to be wrong with, and printing a p-value beside it launders that. The audit already refuses these; a nonzero count means something downstream reported one anyway. |

## 6. Figures

| check | threshold | rationale |
|---|---|---|
| `E6.1_vector_not_raster` | every shipped figure parses as `<svg>` and contains no `<image>` element | The `.svg` extension is not the property that matters; what is inside the file is. An embedded raster is how a bitmap ships inside a file everyone reads as a vector, and it is exactly what a reviewer cannot zoom into or a copy-editor correct. |
| `E6.2_figure_claim_binding` | binding coverage = **1.0** against `manuscript_spine` claims | A figure that argues for nothing in the paper is a picture, and the reader will still read it as evidence. `figure-factory` refuses to *draw* an unbound figure; nothing checks the shipped set against the spine afterwards, and coverage over the shipped set is what this grader adds. |
| `E6.3_figure_numbers_match_analysis` | **0** bound elements whose drawn values differ from `analysis_results` beyond 1e-9 + 1e-6·scale | The same tolerance `analysis._close` uses, so the figure check and the statistics check agree on what "equal" means. A figure is believed faster than a sentence and checked later; elements carrying no comparable statistic are counted as neither pass nor fail and are reported as such. |

## 7. Writing and argumentation

| check | threshold | rationale |
|---|---|---|
| `E7.1_paragraph_claim_coverage` | **1.0** of paragraphs name a claim id, and `every_paragraph_bound_to_a_claim` is `true` | `manuscript-builder` drops orphan paragraphs before rendering, so a shortfall here means the manifest disagrees with itself — which is worse than an orphan, because it means the invariant is being reported rather than held. |
| `E7.2_spine_claims_are_the_drafts_claims` | the two sets are equal (`MC-DISCLOSURE` excepted; it is a required sentence, not a claim) | A spine claim with no paragraph is an argument the paper promised and did not make. A paragraph claim outside the spine was never audited, because the auditor grades against the spine. Set equality, not overlap. |
| `E7.3_disclosure_present` | when the mode requires it, `draft_manifest.disclosure.missing_sections` is empty | Under a degraded comparison mode the disclosure is not a courtesy; it is the sentence that makes every other number in the section readable. `NOT_APPLICABLE` when the mode requires none. |
| `E7.4_no_forbidden_claim_pattern` | **0** of the mode's `forbidden_claim_patterns` appear in the rendered draft | The builder filters forbidden language out of the paragraphs it was handed; nothing re-reads the rendered file afterwards, and the rendered file is what ships. This is a scan of bytes, not a second judgement about what a claim means, so it does not duplicate the auditor. |

## 8. Reproducibility of the run itself

| check | threshold | rationale |
|---|---|---|
| `E8.1_provenance_for_every_released_artifact` | every artifact marked `released` has `provenance_complete: true`, and at least one artifact was released | Consumed from `release_manifest`. The count matters as well as the flag: a manifest can be "complete" because it released nothing, and a refused release is a correct outcome for a run but is not an accepted one. |
| `E8.2_environment_captured` | `environment.lock` contains ≥1 pinned (`==`) line **and** `sandbox_manifest.untrusted_code_execution_allowed` is `true` | A `pip freeze` with no `==` lines is not a lock. Untrusted execution disabled means nothing ran in a recorded environment — under that condition `experiment-runner` records every planned run as `NOT_RUN`, so numbers present alongside it did not come from this pipeline. |
| `E8.3_second_run_reaches_the_same_state` | a second project built from the same inputs has the same final state, the same per-(experiment, seed) status and metrics, and the same release status | Every interval in the analysis is computed from dispersion across seeds. If the seeds do not reproduce, that dispersion is measuring the machine rather than the method, and every confidence interval in the manuscript is a measurement of the wrong thing. Compared on a fingerprint rather than a directory digest, because timestamps, run ids and absolute paths differ between two *correct* runs. **`NOT_MEASURED` unless a second run is supplied** (`--second-run`); `run_acceptance.sh` performs it. Determinism is never assumed from a single run. |

---

## What a stock run fails today

Run against the bundled worked example (with its synthetic markers stripped, purely to reach the
dimensions), this rubric rejects on three findings. They are recorded here so that nobody has to
rediscover whether they are rubric bugs — they are not, and each has an operator fix.

1. **`E1.5b` — every `invalid_condition` the compiler emits is prose.** `rf_runtime` returns a prose
   condition as *unchecked*, so no run is guarded at runtime by any of them. Fix: supply at least one
   `{metric, op, value}` condition per spec.
2. **`E3.3a` — the ablation specs declare `primary` / `seed_dispersion` while the diagnostic and
   evaluation specs declare domain metrics.** The metric sets are disjoint, so `analysis._group_values`
   can never place them in a comparable group and no contrast between the full method and the ablated
   one is computable. Fix: give the ablation spec the primary experiment's metric names.
3. **`E8.1` — the release gate refused, so nothing was released.** For the worked example this is the
   documented and correct outcome (its prose came from the offline model). On a real key it is the
   thing acceptance is asking about.

A harness that passed the example out of the box would be measuring nothing.

## Threshold provenance

Every number above is either taken from the runtime or derived from a stated property. None was
picked for feel.

| threshold | value | source |
|---|---|---|
| minimum seeds, inferential claim | 3 | `planning.MIN_SEEDS_FOR_COMPARATIVE`, `analysis.MIN_SEEDS_FOR_INFERENCE` |
| minimum seeds, descriptive claim | 2 | `analysis._summary` refuses an interval below n=2 |
| run completion floor | 0.66 | `1 − max_failure_rate`, `max_failure_rate = 0.34` in `integrity-auditor` |
| claim support ratio floor | 1 − α (0.95 by default) | `stats_audit.alpha`, defaulting to `analysis.DEFAULT_ALPHA` |
| numeric agreement tolerance | 1e-9 + 1e-6·scale | `analysis.TOL_ABS`, `analysis.TOL_REL` |
| coverage thresholds (ablation, figure, paragraph) | 1.0 | stated per check: a fractional coverage threshold licenses claiming precisely the uncovered items |
| fabrication, undeclared metrics, invented metrics, forbidden patterns | 0 | stated per check: one instance is the whole failure |

`grade.py` re-reads the first, second and fourth of these from `researchforge` when the package is
importable and **refuses to grade** on any disagreement, rather than scoring against a stale copy.

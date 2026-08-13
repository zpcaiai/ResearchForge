---
name: retrospective-benchmark-builder
description: Use when you need to measure whether ResearchForge produces good research directions. Builds a benchmark from older seed papers paired with the follow-up work actually published afterwards. Trigger before tuning the innovation engine.
version: 0.4.0
stage: 12-meta
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [retrospective-benchmark-builder]
---

# retrospective-benchmark-builder

## Objective

Give the system a way to be wrong measurably.

**Unchanged from v0.2.0** (`retrospective-benchmark-builder`).

Kept whole, on the offline maintenance entry point.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `citation_graph`
- `coverage_report`
- `provider_registry`
- `external: domain scope and venue list`
- `external: seed time window and evaluation window`

## Outputs

- `retro_benchmark`
- `retro_benchmark_report`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `literature-provider-manager`

## Procedure

1. Select seed papers from a **closed** time window T (for example, papers published in a single year) within the declared domain. Record the exact selection rule; convenience sampling of famous papers produces a benchmark that measures fame, not judgment.
2. For each seed, assemble the **follow-up set**: works published in the window after T that cite the seed and materially extend, replace, diagnose or refute it. Distinguish genuine follow-ups from incidental citations — a paper that cites the seed in its related-work paragraph is not a follow-up.
3. Reduce each follow-up to a **direction descriptor** in the same vocabulary the innovation engine emits: problem delta, method delta, mechanism, and the experiment that demonstrated it. Scoring compares directions to directions; comparing a generated idea to a full paper is not a comparable pairing.
4. Enforce **leakage control**, which is the part most easily got wrong. The model under test very likely saw the follow-up papers during pretraining, so a high score may measure recall rather than reasoning. Mitigate and report:
   - prefer seed windows close to, or after, the model's knowledge cutoff;
   - hold out a subset of follow-ups that postdate the cutoff and report scores on it separately;
   - probe directly by asking the model to name known follow-ups to the seed, and record that recall as the contamination floor against which the system's score must be read.
   A benchmark distributed without a stated contamination floor is not usable.
5. Define the scoring function. Recommended primary metric is **recall@k of matched directions**: of the real follow-up directions, how many appear among the system's top-k. Matching is semantic and must be adjudicated by a rubric, not string overlap.
6. Record the **known ceiling**. Not every good direction was published, and not everything published was good; a system scoring below 1.0 is not necessarily wrong. Treat recall@k as a comparative signal between system versions, never as an absolute measure of research quality.
7. Pair the retrospective metric with a **blind human rubric** on a smaller sample: domain experts score generated directions against human-authored ones without knowing which is which. The retrospective benchmark catches regressions cheaply; the blind rubric is what establishes that the output is worth anything at all. Neither substitutes for the other.
8. Where forward-citation traversal is unavailable — it usually is; citation APIs are rate-limited
   or unreachable, and citation *intent* is rarely exposed — build the gold set from published
   leaderboards instead. `tools/benchmark/build_retro_corpus.py` takes the record-holder chain on a
   benchmark after a cut date: each paper that set a new best, in date order. The relation is then a
   recorded fact rather than an adjudication, which removes the step most vulnerable to being made
   by the same model that is about to be scored. State the cost in the report: the chain excludes
   work that diagnosed, refuted or reframed the seed, and every direction that was tried and lost.
9. Publish construction method, seed list, follow-up adjudication decisions, contamination floor and known limits in `retro_benchmark_report`. A benchmark whose construction is not inspectable will be over-trusted.

## Hard gates

- A benchmark without a stated contamination floor may not be used to gate skill promotion.
- Seeds and follow-ups are frozen and versioned before any system run against them; adjusting the benchmark after seeing results invalidates it.
- The held-out post-cutoff subset is never used for tuning, only for reporting.
- Recall@k may not be reported as a measure of research quality without the accompanying blind-rubric result.

## Verification / tests

- Seed with a well-known follow-up is matched by the adjudication rubric; a merely-citing paper is not.
- Contamination probe fixture: a model that can recite the follow-up produces a high contamination floor, and the report marks the score uninterpretable.
- Benchmark-freeze fixture: post-hoc modification of the seed set is rejected.
- Two system versions with a known quality difference are ordered correctly by recall@k.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

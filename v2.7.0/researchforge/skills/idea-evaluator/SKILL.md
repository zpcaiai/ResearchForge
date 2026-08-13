---
name: idea-evaluator
description: Use when candidate ideas need evidenced novelty verdicts with their closest prior work named, and honest cost, dependency-risk and data-availability scores with uncertainty kept separate. Trigger for every candidate before ranking.
version: 0.3.0
stage: 04-innovation
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [novelty-verifier, feasibility-estimator]
---

# idea-evaluator

## Objective

Establish, per candidate, whether it is actually new and whether it can actually be done — as two separately reported judgments.

**Consolidates v0.2.0 skills** `novelty-verifier`, `feasibility-estimator`.

Novelty and feasibility were always computed together and consumed together by exactly one skill. Merging keeps them adjacent while preserving the thing that matters: they remain two scores, never collapsed into one.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `baseline_assets`
- `citation_graph`
- `comparison_mode`
- `coverage_report`
- `idea_portfolio`
- `provider_registry`
- `source_repro_report`
- `external: resource envelope (compute, time, budget)`

## Outputs

- `closest_prior_work`
- `feasibility_report`
- `novelty_report`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `idea-portfolio-generator`
- `literature-provider-manager`
- `reproduction-fallback-planner`
- `result-reproducer`

## Procedure

### A. Novelty verification and closest prior work

1. Translate the idea into multiple terminology families and mechanism descriptions.
2. Search title/abstract/full-text/code when available for near-duplicates.
3. Identify the closest prior work and write a delta table: same/different problem, mechanism, data, evaluation, outcome.
4. Assign novelty status: NOVEL_ENOUGH, INCREMENTAL, DUPLICATE_RISK, UNKNOWN_COVERAGE.
5. Recommend reframing or merger when overlap is high.

### B. Feasibility, uncertainty and resource planning

1. Estimate required code changes, dependency risk, dataset access, GPU/CPU/memory/storage and wall-clock classes.
2. Identify unknowns that need a cheap probe.
3. Score feasibility and uncertainty separately.
4. Propose a minimal viable experiment and a full validation experiment.


## Hard gates

- NOVEL_ENOUGH requires at least one closest-prior-work comparison, not just zero search hits.
- Coverage limits must propagate to idea-ranker.
- High uncertainty cannot be hidden inside a high feasibility score.
- Ideas requiring unavailable proprietary data must be flagged before selection.

## Verification / tests

- Paraphrased duplicate fixture is detected by mechanism search.
- Unavailable dataset fixture lowers feasibility and emits blocker.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `resource_plan` — Compute/data/time plan per idea (`ideas/resource_plan.yaml`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

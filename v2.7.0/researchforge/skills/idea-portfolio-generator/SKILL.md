---
name: idea-portfolio-generator
description: Use when seed mining is complete and multiple candidate directions must be generated under the constraints the reproduction level imposes, including variants recombined from existing candidates and prior findings. Trigger before any single idea is committed to.
version: 0.3.0
stage: 04-innovation
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [idea-portfolio-generator, genetic-idea-mutator]
---

# idea-portfolio-generator

## Objective

Generate a materially diverse portfolio of candidate directions, each with a falsifiable hypothesis, an exact delta, a minimum experiment and explicit kill criteria.

**Consolidates v0.2.0 skills** `idea-portfolio-generator`, `genetic-idea-mutator`.

Mutation is a generation strategy, not a separate stage. It read the same seeds, wrote the same candidate objects, and existed mainly to be run again after the first batch clustered too tightly.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `analogy_candidates`
- `comparison_mode`
- `gap_ledger`
- `idea_mode_constraints`
- `source_repro_report`
- `weakness_map`
- `external: code exemplars for mutation`
- `external: resource envelope (compute, time, budget)`

## Outputs

- `idea_lineage_graph`
- `idea_portfolio`
- `mutant_candidates`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `idea-seed-miner`
- `reproduction-fallback-planner`
- `result-reproducer`

## Procedure

### A. Candidate generation across innovation modes

1. Generate candidates across at least four innovation modes: extend/generalize, replace/simplify, combine/transfer, explain/diagnose, benchmark/evaluate, or systemize.
2. For each idea define hypothesis, exact delta vs baseline, minimum experiment, success metric, novelty rationale, compute/data needs and kill criteria.
3. Deduplicate semantically equivalent ideas.
4. Ensure portfolio includes at least one low-cost high-information probe and one higher-upside direction when resources allow.

### B. Recombination and mutation of existing candidates

1. Choose mutation operators: replace component, recombine mechanisms, simplify, scale, alter objective, alter representation, or transfer code pattern.
2. Generate bounded mutants with stated delta from parents.
3. Reject mutants that violate blueprint hard constraints or duplicate prior failures.
4. Send survivors through feasibility + experiment spec generation before execution.


## Hard gates

- No vague verbs such as 'improve' without a measurable mechanism and experiment.
- Each idea has explicit kill criteria.
- Mutation does not bypass novelty verification for a paper-level claim.
- Lineage is mandatory.

## Verification / tests

- Portfolio fixture contains materially diverse mechanism classes.
- Each candidate validates against IdeaCandidate schema.
- Duplicate mutant is filtered via semantic fingerprint.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

---
name: idea-ranker
description: Use when candidates have novelty and feasibility evidence and must be ordered for human decision. Trigger before the selection gate; hard user constraints are filters here, not preferences.
version: 0.3.0
stage: 04-innovation
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [idea-ranker]
---

# idea-ranker

## Objective

Order candidate directions across multiple criteria and name the tradeoff that decided the ordering.

**Unchanged from v0.2.0** (`idea-ranker`).

Kept whole. Ranking is a pure function over evaluations, independently testable, and the one place where a subtle scoring error changes the entire project's direction.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `comparison_mode`
- `coverage_report`
- `feasibility_report`
- `idea_portfolio`
- `novelty_report`
- `external: user priorities and weightings`

## Outputs

- `idea_pareto_front`
- `ranked_ideas`
- `ranking_rationale`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `idea-evaluator`
- `idea-portfolio-generator`
- `literature-provider-manager`
- `reproduction-fallback-planner`

## Procedure

1. Score novelty, scientific value, falsifiability, feasibility, compute cost, expected effect size, venue fit, differentiation and reproducibility.
2. Calibrate uncertainty and penalize unknown novelty coverage or fragile baselines.
3. Compute a Pareto front and top-N recommendations.
4. Explain decisive tradeoffs and identify which evidence would most change the ranking.

## Hard gates

- Do not collapse all criteria into an unexplained 1–10 score.
- User-specified hard constraints are filters, not soft preferences.

## Verification / tests

- Changing compute budget changes ranking for compute-heavy idea fixture.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

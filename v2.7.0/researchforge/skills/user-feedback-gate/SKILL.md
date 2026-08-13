---
name: user-feedback-gate
description: Use when ranked directions are ready and the human must choose, merge, constrain or reject before expensive work begins. Trigger in guided mode always; in auto mode, record an autonomous decision artifact instead.
version: 0.3.0
stage: 04-innovation
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [user-feedback-gate]
---

# user-feedback-gate

## Objective

Stop the machine at the one decision a human should make, and accept merges and constraints without losing the candidates not chosen.

**Unchanged from v0.2.0** (`user-feedback-gate`).

Kept whole. This is the highest-value skill in the package and the reason the system is not just another autonomous idea generator.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `comparison_mode`
- `idea_pareto_front`
- `ranked_ideas`
- `ranking_rationale`
- `external: user feedback on ranked ideas`

## Outputs

- `decision_log`
- `selected_direction`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `idea-ranker`
- `reproduction-fallback-planner`

## Procedure

1. Present 3–5 highest-value directions with concise tradeoffs and minimum experiment.
2. Accept commands such as choose 2, combine 2+4, keep mechanism but change dataset, lower GPU budget, or generate alternatives around candidate 3.
3. Apply feedback as structured constraints and rerun only affected rank/feasibility/novelty steps.
4. Record rationale and preserve rejected ideas for future branches.

## Hard gates

- In guided mode no experiment execution before explicit selection/approval.
- Never reinterpret a user's rejection as approval.

## Verification / tests

- Merge-two-directions fixture yields one selected direction with traceable parents.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

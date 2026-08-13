---
name: evaluator-builder
description: Use when scoring must be defined and decisive tests kept outside the agent's reach. Trigger before experiments run, and re-trigger whenever the metric definition changes.
version: 0.3.0
stage: 05-planning
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [metric-and-hidden-evaluator-builder]
---

# evaluator-builder

## Objective

Define what the score means and keep the tests that decide it isolated from whatever is being scored.

**Unchanged from v0.2.0** (`metric-and-hidden-evaluator-builder`).

Kept whole and separate on purpose. This is a security boundary, not a planning step; merging it into the blueprint would put the grader inside the thing it grades.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `external: known failure modes and gaming patterns`
- `external: research objective`
- `external: submission/answer schema`

## Outputs

- `evaluator_code`
- `evaluator_spec`
- `hidden_tests`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- None; this skill consumes only external inputs.

## Procedure

1. Define raw score semantics and validity rules.
2. Create public examples but keep decisive hidden tests isolated.
3. Probe obvious leakage/tampering/tolerance hacks.
4. Version evaluator independently of agent code.

## Hard gates

- Agent context cannot access hidden tests.
- Evaluator change invalidates comparability unless results are rerun.

## Verification / tests

- Score-tampering fixture is invalid.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

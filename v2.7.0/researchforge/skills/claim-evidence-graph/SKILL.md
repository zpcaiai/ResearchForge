---
name: claim-evidence-graph
description: Use when claims must be bound to the evidence supporting them — first for the source paper, later for your own manuscript. Trigger after parsing, and again whenever new experimental evidence lands.
version: 0.4.0
stage: 02-evidence
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [paper-claim-evidence-graph]
---

# claim-evidence-graph

## Objective

Maintain the evidence backbone: every claim, in the source paper and in your own writing, connected to what actually supports it.

**Unchanged from v0.2.0** (`paper-claim-evidence-graph`).

Kept whole. This is the truth backbone of the architecture and the store outlives every stage that writes to it.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `paper_model`
- `resolved_references`
- `feedback: experiment_ledger`
- `feedback: findings`

## Outputs

- `claim_registry`
- `evidence_graph`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `paper-model-builder`

## Procedure

1. Split paper contributions and results into atomic claims.
2. Classify claims: conceptual, empirical, comparative, causal, theoretical, negative, limitation.
3. Attach source-paper evidence and reference citations with locator anchors.
4. Later, join generated experiments by stable experiment_result_id rather than free-text copying.
5. Track confidence, conflicts, unsupported claims and stale evidence.

## Hard gates

- No edge without provenance.
- Conflicting evidence remains explicit; never average contradictions away.

## Verification / tests

- Graph detects unsupported quantitative claim fixture.
- Changing an experiment result invalidates dependent manuscript claims.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

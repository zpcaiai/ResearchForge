---
name: paper-model-builder
description: Use immediately after ingestion to turn normalized text into a structured PaperModel and to isolate the paper's contributions into independently attackable atoms. Trigger before literature expansion or weakness mining.
version: 0.3.0
stage: 01-intake
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [paper-structure-parser, contribution-decomposer]
---

# paper-model-builder

## Objective

Build the structured representation of the source paper, down to the level at which its individual contributions can be attacked separately.

**Consolidates v0.2.0 skills** `paper-structure-parser`, `contribution-decomposer`.

Parsing structure and decomposing contributions were never independently useful. The section map exists so contributions can be located; the contribution atoms exist so weaknesses can be mined. Nothing consumed one without the other.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `locator_map`
- `normalized_text`

## Outputs

- `contribution_atoms`
- `figure_table_index`
- `method_dependency_graph`
- `paper_model`
- `section_map`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `paper-ingest`

## Procedure

### A. Structural parse

1. Identify rhetorical sections even when headings are non-standard.
2. Extract problem statement, method components, datasets, baselines, metrics, ablations, limitations and future-work claims.
3. Represent method as a component/data-flow graph with explicit dependencies.
4. Link every extracted item back to source locators.
5. Mark inferred fields separately from explicit author statements.

### B. Contribution decomposition

1. Separate problem novelty, method novelty, data novelty, system novelty, theory novelty and evaluation novelty.
2. Map each contribution atom to assumptions, dependencies and supporting evidence.
3. Identify which atoms are essential versus packaging/presentation.
4. Expose attack surfaces: bottleneck, missing generality, expensive component, weak evidence, untested regime.


## Hard gates

- Every nontrivial extracted claim must have a source locator.
- Inferences cannot be labeled as author claims.
- Do not call the whole paper one contribution.
- Every targetable atom must cite source evidence.

## Verification / tests

- Parser fixture with renamed sections still maps method/results correctly.
- Schema validation passes with zero orphan locator references.
- Fixture produces multiple atomic contributions from one abstract-level contribution list.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

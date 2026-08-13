---
name: release-gate
description: Use when the project claims to be finished. Trigger to verify every gate, package the deliverables, and refuse release while any critical gate remains open.
version: 0.3.0
stage: 11-release
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [release-gate-exporter]
---

# release-gate

## Objective

Refuse to ship a project whose claims outrun its evidence, and package what does ship with the provenance to check it.

**Unchanged from v0.2.0** (`release-gate-exporter`).

Kept whole. This is the last place a fabricated number can be caught.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `artifact_manifest`
- `bibliography`
- `citation_audit`
- `claim_audit`
- `comparison_mode`
- `defense_deck`
- `finding_memory_graph`
- `findings`
- `integrity_gate`
- `manuscript_draft`
- `provenance_log`
- `review_report`
- `source_repro_report`
- `stats_audit`
- `submission_blockers`
- `feedback: progress_state`

## Outputs

- `release_bundle`
- `release_manifest`
- `release_report`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `claim-citation-auditor`
- `deck-factory`
- `experiment-runner`
- `finding-memory`
- `integrity-auditor`
- `manuscript-builder`
- `reproduction-fallback-planner`
- `result-reproducer`
- `review-simulator`

## Procedure

1. Verify required artifacts and their checksums/provenance.
2. Run baseline/candidate smoke tests and deterministic package validation.
3. Check unresolved novelty/citation/statistical/review blockers.
4. Build export bundle: code, configs, paper source/PDF, references, figures/SVG, PPTX, review and evidence reports.
5. Record limitations, unresolved risks and exact commands for reproduction.

## Hard gates

- No 'complete' status with unresolved BLOCKER/HIGH integrity findings.
- Release report must distinguish generated skill package validation from scientific result validation.

## Verification / tests

- Missing provenance for a headline result blocks release.
- Bundle validation script verifies all declared files/checksums.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

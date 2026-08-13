---
name: claim-citation-auditor
description: Use when a draft exists and every claim must be checked against its cited source or experiment record, and as the final pre-submission verdict on whether anything unsupported survived. Trigger before any submission or release; citation existence is not citation support.
version: 0.4.0
stage: 08-writing
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [claim-citation-auditor, paper-quality-integrity-gate]
---

# claim-citation-auditor

## Objective

Verify that each claim is supported by what it points at, and refuse to pass a manuscript in which any high-severity finding is unresolved.

**Consolidates v0.2.0 skills** `claim-citation-auditor`, `paper-quality-integrity-gate`.

The integrity gate was reading the citation auditor's output and re-deciding the same question. One skill, one verdict, with the gate as its final step — because two skills meant two thresholds and the lower one wins.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `comparison_mode`
- `draft_manifest`
- `evidence_graph`
- `experiment_ledger`
- `experiment_specs`
- `manuscript_draft`
- `resolved_references`
- `source_repro_report`
- `stats_audit`
- `feedback: response_to_reviewers`
- `feedback: review_triage`
- `feedback: revision_experiment_plan`
- `feedback: revision_matrix`

## Outputs

- `citation_audit`
- `claim_audit`
- `integrity_gate`
- `submission_blockers`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `claim-evidence-graph`
- `experiment-runner`
- `integrity-auditor`
- `manuscript-builder`
- `reproduction-fallback-planner`
- `research-blueprint-compiler`
- `result-reproducer`

## Procedure

### A. Claim extraction and support verification

1. Extract atomic claims from the draft.
2. Resolve citations and verify each cited source supports the intended proposition, not merely the topic.
3. Verify quantitative claims against experiment ledger and raw artifacts.
4. Flag unsupported, overstated, anchorless, fabricated or scope-mismatched claims.
5. Block finalization until high-severity findings are fixed or claims removed/narrowed.
6. Grade **frontier claims** separately from comparative ones. "Our method improves over the
   baseline of [X]" is narrow and admissible on a two-arm ledger. "Our method outperforms all prior
   approaches" is a claim about a population, and it requires a `sota` arm that completed with
   metrics in the ledger. A reported number does not satisfy it: the claim has to be rewritten, not
   annotated. Sentences that describe the literature rather than this work are not graded, because
   a check that misfires in related work is a check that gets turned off.

### B. Pre-submission verdict

1. Check argument continuity from spine to manuscript.
2. Run citation and statistical audits.
3. Verify figure/table/text consistency and LaTeX/package health.
4. Verify reproducibility commands and AI-use disclosures where required.


## Hard gates

- Citation existence is necessary but not sufficient; support must be checked.
- Planned experiments cannot be written as completed.
- Critical audit failures block submission packaging.
- No state-of-the-art claim about this work survives without a measured state-of-the-art arm.

## Verification / tests

- Real-but-irrelevant citation fixture is marked NOT_SUPPORTED.
- Broken figure reference and unsupported claim fixture both block.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

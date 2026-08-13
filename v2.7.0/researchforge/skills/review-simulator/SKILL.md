---
name: review-simulator
description: Use when a draft needs adversarial pre-submission review against a target venue, and when reviews — simulated or real — must be triaged into a prioritized revision and response plan. Trigger after the citation audit passes.
version: 0.3.0
stage: 08-writing
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [journal-fit-reviewer, rebuttal-and-revision-planner]
---

# review-simulator

## Objective

Attack the manuscript the way a reviewer will, then turn what survives into a plan rather than a list of complaints.

**Consolidates v0.2.0 skills** `journal-fit-reviewer`, `rebuttal-and-revision-planner`.

Generating a review and acting on one are the same competence pointed in two directions, and they were the only consumers of each other's output.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `citation_audit`
- `evidence_graph`
- `experiment_ledger`
- `integrity_gate`
- `manuscript_draft`
- `manuscript_spine`
- `external: resource envelope (compute, time, budget)`
- `external: target venue`

## Outputs

- `response_to_reviewers`
- `review_report`
- `review_triage`
- `revision_experiment_plan`
- `revision_matrix`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `claim-citation-auditor`
- `claim-evidence-graph`
- `experiment-runner`
- `manuscript-builder`

## Procedure

### A. Adversarial review against the venue

1. Create journal/venue-fit, methods, experiments, novelty and devil's-advocate review perspectives.
2. Score with explicit rubric and confidence; separate fatal issues from presentation issues.
3. Map each concern to a manuscript location and required evidence or edit.
4. Preserve disagreement among reviewers instead of forcing consensus.
5. Recommend accept-like, major revision, or resubmit only with reasons.

### B. Concern triage, revision matrix and response

1. Atomize comments and infer underlying concern with uncertainty flag.
2. Classify severity and decide PROMISING/BORDERLINE/LOW_RETURN for rebuttal.
3. Plan P0–P3 experiments/analyses before drafting persuasive text.
4. When results arrive, validate them and draft Direct Answer → Evidence → Revision responses.
5. Track every promised manuscript change to a concrete location.


## Hard gates

- Reviewer must not request impossible evidence without flagging resource mismatch.
- No review concern can directly rewrite experimental facts.
- Never claim planned work as completed.
- Negative new results are disclosed when material.

## Verification / tests

- Known methodological flaw fixture appears as high-priority concern.
- Low-return fixture produces resubmission plan rather than aggressive rebuttal.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `revision_backlog` — Prioritized revision items (`review/revision_backlog.md`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

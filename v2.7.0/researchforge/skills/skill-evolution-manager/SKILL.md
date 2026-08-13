---
name: skill-evolution-manager
description: Use when accumulated run traces suggest a skill should change, or when the package's own structure and contracts need auditing. Trigger offline only; a candidate edit is promoted only if it improves held-out performance without regressing any guardrail.
version: 0.3.0
stage: 12-meta
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [skill-package-auditor, skill-evolution-manager]
---

# skill-evolution-manager

## Objective

Change the system's own skills only when a held-out evaluation says the change is an improvement.

**Consolidates v0.2.0 skills** `skill-package-auditor`, `skill-evolution-manager`.

Auditing the package and proposing edits to it are the same loop; the audit existed to feed the evolution manager and had no other consumer.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `eval_failure_taxonomy`
- `eval_runs`
- `eval_scorecard`
- `external: held-out evaluation suite`
- `external: skill catalog source`
- `external: skill version under evaluation`
- `external: skills directory path`

## Outputs

- none

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `research-eval-harness`

## Procedure

### A. Package and contract audit

1. Validate frontmatter and required sections.
2. Check dependency names and cycles.
3. Detect suspiciously identical placeholder skills.
4. Classify each artifact as workflow spec, executable script, adapter or implementation code.
5. Fail if documentation claims runtime implementation that package does not contain.

### B. Bounded edit, held-out evaluation, promotion

1. Mine recurring failure patterns and useful human corrections.
2. Propose bounded add/delete/replace changes to one skill at a time.
3. Replay training examples and evaluate on held-out tasks.
4. Reject changes that regress any hard guardrail or fail minimum improvement threshold.
5. Promote as a new version with changelog and rollback pointer.


## Hard gates

- Specification-only package must say so explicitly.
- No online self-edit of active skill.
- Held-out improvement and guardrail pass are required for promotion.

## Verification / tests

- Template-clone fixture is flagged as low-specificity.
- Overfit skill that improves train but harms held-out is rejected.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `skill_audit_machine_report` — Machine-readable package audit (`evals/machine_report.json`)
- `skill_audit_report` — Human-readable package audit (`evals/skill_audit_report.md`)
- `skill_eval_comparison` — Held-out comparison of skill versions (`evals/skill_eval_comparison.json`)
- `skill_patch` — Proposed bounded skill edit (`evals/skill_patch.diff`)
- `skill_promotion_decision` — Promote/reject with rollback pointer (`evals/promotion_decision.json`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

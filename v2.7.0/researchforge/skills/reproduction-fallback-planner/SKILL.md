---
name: reproduction-fallback-planner
description: Use immediately after result-reproducer, before ideation. Converts the achieved reproduction level into the comparative claims and innovation modes that remain admissible. Trigger whenever RL is established or re-established.
version: 0.3.0
stage: 03-reproduction
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [reproduction-fallback-planner]
---

# reproduction-fallback-planner

## Objective

Turn 'we could not fully reproduce the paper' from a terminal state into a constraint.

**Unchanged from v0.2.0** (`reproduction-fallback-planner`).

Kept deliberately separate from the reproducer. Measurement and policy must not live in the same skill, or the thing that decides what counts as success is also the thing that reports it.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `baseline_license_risk`
- `repro_failure_taxonomy`
- `source_repro_metrics`
- `source_repro_report`
- `external: resource envelope`
- `external: user risk tolerance and target venue`

## Outputs

- `comparison_mode`
- `fallback_decision_log`
- `idea_mode_constraints`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `result-reproducer`

## Procedure

1. Read the achieved RL and derive the **comparison mode**, which is the binding constraint on every later quantitative claim:
2. Derive `idea_mode_constraints` — which of the innovation modes in `idea-portfolio-generator` remain admissible:
3. Consult `repro_failure_taxonomy` for **recoverable** causes and propose targeted remediation before accepting the degraded mode: `CHECKPOINT_MISSING` may be resolvable by contacting authors or locating a mirror; `HARDWARE_UNAVAILABLE` may be resolvable by renting; `DATA_ACCESS_GATED` may be resolvable by application. Each remediation carries an explicit cost and latency estimate, and the user decides. Do not silently accept RL1 when a two-hour action would reach RL3.
4. Where the source artifact is unusable but an independent reimplementation or a strong third-party implementation exists, propose it as a **substitute baseline** with an explicit comparability caveat. A substitute baseline never inherits the original's RL; it must be graded on its own.
5. Record every degradation decision in `fallback_decision_log` with the RL, the taxonomy codes that caused it, the modes closed off, the remediation options considered and rejected, and — in guided mode — the human approval. In auto mode, record an autonomous decision artifact.
6. Propagate `comparison_mode` as a **hard constraint object**, not a suggestion. `idea-portfolio-generator`, `feasibility-estimator`, `experiment-spec-author`, `claim-citation-auditor` and `release-gate-exporter` all read it and all enforce it.

## Hard gates

- A comparison mode may never be upgraded without a new reproduction attempt producing a higher RL; agents may not argue their way to `CM_MEASURED`.
- Under `CM_REPORTED`, any manuscript comparing against published numbers without the non-reproduction disclosure is blocked at the release gate.
- Under `CM_NONE`, any quantitative comparative claim reaching the manuscript is a release blocker, not a warning.
- Degrading to a lower comparison mode requires an explicit decision record; silent degradation is forbidden.
- A substitute baseline must be disclosed in the manuscript wherever it is used for comparison.

## Verification / tests

- RL0 fixture yields `CM_NONE` and an idea portfolio containing only diagnostic/evaluation directions — and, critically, a **non-empty** portfolio.
- RL1 fixture produces a manuscript that fails the release gate when the non-reproduction disclosure is removed.
- RL2 fixture blocks a claim that compares a local reduced-scale number against the paper's published absolute number.
- Recoverable-cause fixture (`CHECKPOINT_MISSING` with a known mirror) surfaces the remediation instead of degrading.
- Upgrade fixture: attempting to set `CM_MEASURED` without a new RL3+ report is rejected.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

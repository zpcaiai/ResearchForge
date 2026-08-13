---
name: integrity-auditor
description: Use before any result is allowed to become a claim, and when repeated trials must be aggregated into an honest keep-or-kill decision. Trigger on every analysis output.
version: 0.4.0
stage: 07-analysis
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [statistical-integrity-auditor, result-meta-analyzer]
---

# integrity-auditor

## Objective

Check that the statistics support what is about to be claimed, and aggregate repeated runs without letting the best one speak for the rest.

**Consolidates v0.2.0 skills** `statistical-integrity-auditor`, `result-meta-analyzer`.

Meta-analysis is where selective reporting actually happens, so the skill that aggregates repeated trials and the skill that detects cherry-picking belong in the same place with the same view of the raw runs.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `analysis_code`
- `analysis_results`
- `experiment_ledger`
- `experiment_specs`
- `external: keep/kill decision criteria`

## Outputs

- `meta_analysis`
- `meta_analysis_decision`
- `stats_audit`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `data-analyst`
- `experiment-runner`
- `research-blueprint-compiler`

## Procedure

### A. Statistical validity audit

1. Verify test assumptions and independence structure.
2. Check sample sizes/seeds, uncertainty intervals, effect sizes and multiple testing where relevant.
3. Compare metric code and reported values to raw results.
4. Detect selective reporting, leakage, p-hacking-like post-hoc promotion and mismatch between plots/tables/text.
5. Classify findings as BLOCKER/HIGH/MEDIUM/LOW.

### B. Aggregation across repeated trials

1. Group comparable runs by design and identify heterogeneity.
2. Aggregate effect estimates with uncertainty; avoid mixing incompatible metrics.
3. Analyze consistency across seeds/datasets/branches and failure rate.
4. Report positive, null and negative evidence.
5. Recommend continue, pivot, narrow claim or stop.


## Hard gates

- BLOCKER/HIGH unresolved findings prevent evidence lock for affected claims.
- Best-run-only summaries are disallowed when repeated trials exist.

## Verification / tests

- Mismatched table vs raw metric fixture is caught.
- Multiple-comparison fixture triggers correction requirement.
- One outlier win among failed trials does not produce GO recommendation.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `stats_required_fixes` — BLOCKER/HIGH items to resolve (`analysis/required_fixes.md`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

---
name: data-analyst
description: Use when raw data must be profiled, cleaned, split and leakage-checked, and when experiment results must become computed answers, diagnostic plots and an analyst-grade narrative. Trigger before any result is interpreted.
version: 0.4.0
stage: 07-analysis
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [data-prep-agent, data-analysis-agent]
---

# data-analyst

## Objective

Prepare the data honestly, then analyse it, keeping every transformation on the record.

**Consolidates v0.2.0 skills** `data-prep-agent`, `data-analysis-agent`.

Preparation and analysis shared the leakage question, which is the only question either of them gets seriously wrong. Splitting them let a leak be introduced in one skill and go unnoticed in the other.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `best_candidate`
- `experiment_ledger`
- `experiment_specs`
- `experiment_tree`
- `ranked_branches`
- `external: analysis question`
- `external: raw dataset`

## Outputs

- `analysis_code`
- `analysis_plots`
- `analysis_report`
- `analysis_results`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `experiment-runner`
- `research-blueprint-compiler`

## Procedure

### A. Profiling, cleaning, splitting, leakage checks

1. Profile schema, missingness, duplicates, outliers, units and split leakage.
2. Propose transformations with reason and reversibility.
3. Execute transformations and validate postconditions after each step.
4. Freeze train/val/test semantics and checksum outputs.
5. Emit warnings for potentially outcome-leaking operations.

### B. Analysis, plots and narrative

1. Read experiment design before touching metrics.
2. Separate exploratory from confirmatory analyses.
3. Compute declared metrics and appropriate uncertainty summaries.
4. Inspect subgroup/seed/ablation behavior and potential failure cases.
5. Generate figures from code with traceable inputs.


## Hard gates

- No target leakage.
- Raw data is never destructively overwritten.
- Post-hoc metrics are labeled exploratory.
- No cherry-picking of best seeds without full distribution.

## Verification / tests

- Leakage fixture is detected.
- Transformation pipeline reproduces same checksum.
- Analysis fixture reports all seeds and flags selective subset attempt.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `data_profile` — Schema, distribution and quality profile (`analysis/data_profile.json`)
- `data_transform_log` — Every transformation applied (`analysis/transform_log.jsonl`)
- `prepared_data` — Cleaned, split, leakage-checked data (`analysis/prepared/`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

---
name: finding-memory
description: Use when evidence across the run must be distilled into durable findings that later stages and later projects can read, including the failures and null results worth keeping. Trigger at every evidence lock point, and before any failed branch is discarded.
version: 0.3.0
stage: 07-analysis
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [finding-memory-manager, negative-result-curator]
---

# finding-memory

## Objective

Keep what was learned, including — especially — what did not work and where the method stops working.

**Consolidates v0.2.0 skills** `finding-memory-manager`, `negative-result-curator`.

Negative results are findings with a tag, not a separate store. Separating them was how negative results got curated into a file nobody read.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `analysis_code`
- `analysis_report`
- `assumption_tests`
- `baseline_deviation_log`
- `baseline_license_risk`
- `baseline_metrics`
- `baseline_repo_rankings`
- `benchmark_matrix`
- `branch_map`
- `checkpoint_metadata`
- `citation_gap_candidates`
- `citation_resolution_report`
- `claim_registry`
- `closest_prior_work`
- `contribution_atoms`
- `decision_log`
- `experiment_ledger`
- `experiment_tree`
- `fallback_decision_log`
- `figure_table_index`
- `gap_evidence`
- `landscape_report`
- `layout_warnings`
- `library_hits`
- `library_note_links`
- `literature_retrieval_log`
- `literature_search_plan`
- `locator_map`
- `meta_analysis`
- `meta_analysis_decision`
- `paper_assets`
- `paper_source_file`
- `quest_repo`
- `quota_ledger`
- `repro_failure_taxonomy`
- `reproduction_report`
- `section_map`
- `source_manifest`
- `source_repro_metrics`
- `source_repro_plan`
- `system_matrix`
- `visual_notes`
- `feedback: deck_manifest`
- `feedback: figure_generation_trace`
- `feedback: manuscript_pdf`
- `feedback: response_to_reviewers`
- `feedback: review_triage`
- `feedback: revision_experiment_plan`
- `feedback: revision_matrix`
- `feedback: speaker_notes`

## Outputs

- `boundary_conditions`
- `finding_memory_graph`
- `findings`
- `negative_findings`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `claim-evidence-graph`
- `data-analyst`
- `experiment-runner`
- `idea-evaluator`
- `idea-seed-miner`
- `integrity-auditor`
- `literature-provider-manager`
- `literature-search`
- `paper-ingest`
- `paper-model-builder`
- `project-repo-manager`
- `reproduction-fallback-planner`
- `result-reproducer`
- `user-feedback-gate`

## Procedure

### A. Finding distillation and memory graph

1. Distill each finding into context, evidence, confidence, scope and source IDs.
2. Link findings to parent idea/experiment and related failures.
3. Tag reusable engineering lessons separately from scientific conclusions.
4. Retrieve only relevant memory for current task and show provenance.
5. Expire or supersede stale findings when new evidence conflicts.

### B. Negative results and boundary conditions

1. Distinguish implementation failure from scientific null.
2. Summarize conditions under which the idea failed.
3. Update kill criteria, finding memory and manuscript limitations when relevant.
4. Suggest informative pivots rather than hiding the result.


## Hard gates

- Memory is advisory; it cannot substitute for current experiment evidence.
- Conflicting memories remain versioned.
- Negative evidence cannot be deleted merely because it hurts the narrative.

## Verification / tests

- Superseded finding is not returned as current truth.
- Repeated dead-end branch is suppressed by memory.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

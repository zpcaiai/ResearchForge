---
name: researchforge-orchestrator
description: Use when the user supplies a paper and wants the full paper-to-innovation-to-manuscript run. Owns the state machine, enforces reproduction before ideation, supervises budget and pace, and stops at the human selection gate in guided mode.
version: 0.3.0
stage: 00-runtime
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [researchforge-orchestrator, research-progress-controller]
---

# researchforge-orchestrator

## Objective

Drive the research lifecycle as a gated state machine that can be killed and resumed without losing or inventing evidence.

**Consolidates v0.2.0 skills** `researchforge-orchestrator`, `research-progress-controller`.

Budget supervision, stall detection and resume semantics are the state machine's own mechanics. As a separate skill they described a controller with no authority over the thing it was controlling.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `comparison_mode`
- `evidence_graph`
- `idea_portfolio`
- `paper_model`
- `ranked_ideas`
- `release_bundle`
- `release_manifest`
- `release_report`
- `research_blueprint`
- `selected_direction`
- `external: budget and cost policy`
- `external: live runtime state feed`
- `external: paper locator (URL/PDF/HTML/DOI/arXiv/local path)`
- `external: resource envelope (compute, time, budget)`
- `external: run mode (guided|auto|analysis-only)`
- `external: user feedback on ranked ideas`

## Outputs

- `progress_state`
- `research_state`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `claim-evidence-graph`
- `idea-portfolio-generator`
- `idea-ranker`
- `paper-model-builder`
- `release-gate`
- `reproduction-fallback-planner`
- `research-blueprint-compiler`
- `user-feedback-gate`

## Procedure

### A. State machine and stage gating

1. Normalize the request and create a durable project/quest repository via project-repo-manager.
2. Run paper-ingest → paper-structure-parser → contribution-decomposer → claim-evidence-graph.
3. Expand evidence with literature-search, citation-neighborhood-miner and baseline-repo-finder.
4. Generate a candidate innovation portfolio using assumption-weakness-miner, novelty-gap-miner, cross-domain-analogy-miner and idea-portfolio-generator.
5. Run novelty-verifier, feasibility-estimator and idea-ranker. In guided mode, stop at user-feedback-gate and accept combine/reject/constraint edits without losing prior candidates.
6. Compile the selected direction into a research blueprint; reproduce or establish a baseline before novel experiments are allowed to claim gains.
7. Run experiment-spec-author, sandbox-provisioner, codebase-scaffolder and experiment-tree-search. Persist every attempt in experiment-ledger and finding-memory-manager.
8. Lock validated evidence, then run manuscript-spine-builder → paper-drafter → claim-citation-auditor → journal-fit-reviewer; iterate only on evidence-backed revisions.
9. Generate figures and defense deck; run release-gate-exporter. Never mark the project complete if critical gates remain open.

### B. Budget, pace, alerts and resume

1. Track stage/node status, cost, wall-clock, GPU usage and pending human gates.
2. Detect stalled loops and repeated failures.
3. Checkpoint durable state before cancellation/takeover.
4. Resume from last valid artifact rather than restarting everything.


## Hard gates

- No selected idea may enter experiment execution without novelty + feasibility evidence.
- No experimental claim may enter the manuscript without an experiment_result_id and provenance record.
- No citation may support a claim unless claim-citation-auditor records a resolvable source and support judgment.
- In guided mode, an explicit user selection/merge/approval is required after ranking.
- Budget overrun blocks new expensive jobs.

## Verification / tests

- E2E dry run from one paper fixture reaches HUMAN_SELECTION_REQUIRED in guided mode.
- Resume test: killing after any stage preserves state and restarts idempotently.
- Negative test: fabricated experiment metrics are rejected by release gate.
- Kill-and-resume fixture continues from checkpoint.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `progress_alerts` — Budget/stall alerts (`.researchforge/alerts.jsonl`)
- `resume_token` — Idempotent restart pointer (`.researchforge/resume_token.json`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

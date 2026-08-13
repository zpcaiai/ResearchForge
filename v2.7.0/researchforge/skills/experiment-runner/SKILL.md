---
name: experiment-runner
description: Use when experimental branches must be explored under a budget, every run recorded, and every artifact given lineage. Trigger after scaffolding; a number that never entered the ledger cannot enter the manuscript.
version: 0.5.0
stage: 06-execution
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [experiment-tree-search, experiment-ledger, artifact-provenance]
---

# experiment-runner

## Objective

Execute the search over experimental branches, and make every resulting number traceable to the code, config, data, seed and environment that produced it.

**Consolidates v0.2.0 skills** `experiment-tree-search`, `experiment-ledger`, `artifact-provenance`.

The ledger and the provenance log were two views of one append-only event stream, and the tree search was the only thing writing to them. Three skills, one write path — which is exactly how a metric ends up in a paper without a matching ledger entry.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `acceptance_criteria`
- `baseline_assets`
- `blueprint_dag`
- `code_tests`
- `code_worktree`
- `debug_terminal_status`
- `environment_lock`
- `evaluator_code`
- `evaluator_spec`
- `experiment_specs`
- `failure_diagnosis`
- `hidden_tests`
- `idea_lineage_graph`
- `implementation_plan`
- `mutant_candidates`
- `repair_commits`
- `research_blueprint`
- `sandbox_container_config`
- `sandbox_manifest`
- `external: artifact creation and edit events`
- `external: runtime execution events`

## Outputs

- `artifact_manifest`
- `best_candidate`
- `experiment_ledger`
- `experiment_tree`
- `provenance_log`
- `ranked_branches`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `codebase-scaffolder`
- `evaluator-builder`
- `idea-portfolio-generator`
- `research-blueprint-compiler`
- `result-reproducer`
- `sandbox-provisioner`

## Procedure

### A. Branch search and pruning

1. Initialize several diverse branches/seeds when uncertainty is high.
2. Execute cheap probes first and score nodes using experiment evidence, not model confidence.
3. Expand promising nodes, debug bounded failures and prune dominated/invalid branches.
4. Preserve failed nodes with reasons to prevent repeat waste.
5. Stop on budget, target achievement, statistical futility or exhausted high-value branches.

### B. Run ledger

1. Assign immutable run_id before execution.
2. Execute **every arm the spec declares** (`baseline`, `candidate`, `sota`), once per seed, and
   record `arm` on the row. Invoking the entry point without naming an arm runs the default one
   twice and calls one of them a baseline; every comparison built on that ledger is the candidate
   against itself, and it cannot fail.
3. Record git SHA, config hash, dataset/version, seed, arm, command, environment and timestamps.
4. Attach stdout/stderr/logs/raw results/artifacts by checksum.
5. Record terminal status and evaluator result.
6. Provide query views by idea, branch, arm, metric, failure class and artifact.

### C. Artifact lineage

1. Assign stable artifact IDs and checksums.
2. Record producer skill/tool/model, source artifact IDs, git SHA, run ID and timestamps.
3. Version edits instead of losing history.
4. Expose lineage queries from paper claim → figure/table → analysis → raw experiment.


## Hard gates

- No unlimited self-debug loops.
- Search score must come from declared evaluator/metrics.
- Never overwrite historical run records.
- A manuscript-visible metric must resolve to one or more run IDs.
- Aggregate within one arm, never across arms. The mean of a method and the control it is compared
  against is arithmetically valid, describes no condition that was run, and passes every
  downstream integrity check because it is a real average of real runs.
- A condition the spec designs but names no runnable arm for is reported as unmeasurable, so that
  "the effect was small" and "the arm never ran" do not read identically downstream.
- Arms execute in a fixed order and the timebox is therefore spent last-first, taking the
  comparison arm. That priority is deliberate; the silence was not. Report the exhaustion with its
  per-arm counts, and emit an uncomputed contrast with the reason it is absent — an absent contrast
  must not read as a flat one.
- A contrast carries its metric's direction. A signed difference alone reads as an improvement to
  every human and every downstream agent, and on a minimize metric that is backwards.
- Release-visible artifacts require provenance entries.
- Manual edits are recorded as human-authored events, not hidden.

## Verification / tests

- Failed branch is not repeatedly retried after terminal classification.
- Budget exhaustion stops new node creation.
- Tampering with artifact triggers checksum mismatch.
- Duplicate retry receives a new run_id but links parent_run_id.
- Lineage query resolves a paper number back to raw result fixture.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `run_index` — Queryable index over the ledger (`run_index.sqlite`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

---
name: result-reproducer
description: Use when any published result must be reproduced and graded — the source paper's own results before ideation, a comparison baseline after a direction is selected, or a substitute baseline. Trigger on state EVIDENCE_EXPANDED for the source paper; nothing downstream may assume a working code base until this runs.
version: 0.4.0
stage: 03-reproduction
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [baseline-repo-finder, source-result-reproducer, baseline-reproduction-auditor]
---

# result-reproducer

## Objective

Locate the artifacts behind a published result, attempt reproduction inside a time box, and grade the outcome RL0-RL4 against numbers actually read from the paper.

**Consolidates v0.2.0 skills** `baseline-repo-finder`, `source-result-reproducer`, `baseline-reproduction-auditor`.

v0.2.0 had three skills doing one thing at three points in the pipeline: find the repo, reproduce the source paper, reproduce the comparison baseline. The mechanism is identical — the only difference is which paper you point it at, which the ReproductionLevel schema already carries as `target_kind`. Three copies of the hardest procedure in the system was three places for it to drift.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `benchmark_matrix`
- `contribution_atoms`
- `literature_candidates`
- `paper_model`
- `sandbox_manifest`
- `external: preferred research direction hint`
- `external: reproduction tolerance policy`
- `external: resource envelope (compute, time, budget)`
- `external: resource envelope (wall-clock cap, compute class, cost cap)`
- `feedback: comparison_mode`

## Outputs

- `baseline_assets`
- `baseline_deviation_log`
- `baseline_license_risk`
- `baseline_metrics`
- `baseline_repo_rankings`
- `repro_failure_taxonomy`
- `reproduction_report`
- `source_repro_metrics`
- `source_repro_plan`
- `source_repro_report`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `literature-search`
- `paper-model-builder`
- `sandbox-provisioner`

## Procedure

### A. Artifact discovery

1. Search paper links, author organizations, GitHub and benchmark pages for official artifacts.
2. Rank repos by author linkage, release tag, paper mention, reproducibility evidence and maintenance state.
3. Identify required datasets/checkpoints and expected licenses/access constraints.
4. Select a baseline asset set and pin commit/tag/checksums.

### B. Time-boxed tiered reproduction and RL grading

1. Extract from `paper_model` the paper's **headline claims** and the specific table/figure cells that carry them. Reproduce claims, not repositories: a repo whose demo script runs but whose Table 2 cannot be regenerated is RL1, not success.
2. Build the `source_repro_plan`: for each headline cell, record the required dataset, checkpoint, config, hardware class and estimated wall-clock. Rank by (claim importance) / (estimated cost). Declare an explicit time box; the default is 4 hours of agent wall-clock per paper before escalation.
3. Provision a pinned environment via the sandbox and capture `source_repro_env` **before** any dependency is resolved loosely. Record resolved versions, CUDA/driver, and image digest.
4. Execute in escalating tiers, stopping as soon as the time box is exhausted:
   - **RL1 CODE_RUNS** — environment builds and the entry point completes on a reduced sample without error.
   - **RL2 SMOKE_MATCH** — a reduced-scale run produces metrics whose direction and order of magnitude match the paper.
   - **RL3 HEADLINE_MATCH** — at least one headline cell reproduces within tolerance (default: relative deviation ≤5%, or inside the paper's own reported variance where given).
   - **RL4 TABLE_MATCH** — a majority of the cells in the primary results table reproduce within tolerance.
5. Grade honestly and record the achieved level in `source_repro_report`. **RL is assigned by measured comparison against the paper's reported numbers, never by the agent's confidence that the code "looks correct".** Absence of an error is RL1, not RL3.
6. Classify every shortfall into `repro_failure_taxonomy` using a fixed vocabulary: `NO_CODE`, `DEPENDENCY_UNRESOLVABLE`, `HARDWARE_UNAVAILABLE`, `DATA_UNAVAILABLE`, `DATA_ACCESS_GATED`, `CHECKPOINT_MISSING`, `UNDOCUMENTED_PREPROCESSING`, `CONFIG_AMBIGUOUS`, `NONDETERMINISM`, `METRIC_DEFINITION_MISMATCH`, `NUMBERS_DIVERGE`, `TIMEBOX_EXCEEDED`, `LICENSE_BLOCKED`. This vocabulary is the input to fleet-level diagnosis; free-text reasons are not acceptable.
7. Emit `source_repro_metrics` as paired records: `(claim_id, reported_value, measured_value, tolerance, verdict, run_id)`. Unpaired measurements are not evidence.
8. Hand the achieved RL to `reproduction-fallback-planner`. **This skill does not decide whether the project continues** — it only establishes ground truth.

### C. Comparison-baseline mode

1. Provision the documented environment in a sandbox and run the smallest valid smoke test.
2. Reproduce the target benchmark under pinned data/config/seeds where feasible.
3. Compare observed metrics to reported values with tolerance bands.
4. Classify differences as reproduced, near-reproduced, non-reproduced or incomparable and explain deviations.
5. Freeze baseline command/config/environment for later differential tests.

### D. Who is currently strongest on this task

The source paper's own baseline answers "did we beat what they beat". Nobody asks that. This step
finds the methods a reviewer would actually compare against, and grades the evidence for each.

1. Collect candidates from three sources, each tagged with where it came from: `sota_methods`
   supplied by the operator, `benchmark_matrix` rows written by `literature-search`, and
   `literature_candidates` whose titles assert the frontier.
2. Write them to `baseline_assets.sota` with `established: false` and `sota_established: false`.
3. A candidate becomes `established` only when it has been run **here** and graded RL3+. Until
   then its number is REPORTED — produced on someone else's hardware, data version and tuning
   budget — and is not comparable to a number measured here.
4. If no candidate is found, say so and say what it costs: no later claim of competitiveness can
   come out of this project however the runs turn out.

## Hard gates

- Never assume a similarly named repo is official.
- Pin immutable revisions before experiments.
- No RL above RL1 may be asserted without a numeric comparison to a value extracted from the source paper with a locator.
- Reduced-scale runs may support RL2 but never RL3 or RL4.
- The time box is enforced by the runtime, not by agent judgment; exceeding it yields `TIMEBOX_EXCEEDED` at the highest level actually reached.
- A reproduction attempt that silently changes the metric definition to obtain agreement is a failure, not an RL3.
- Environment capture precedes execution; a run whose environment was not captured cannot support any RL.
- Novel method comparison is blocked if baseline is non-reproduced unless the blueprint explicitly accepts a new baseline and explains why.
- Do not tune the baseline and candidate asymmetrically.
- A reported benchmark number is never `established`. Treating a table cell as a measured baseline
  is how a comparison becomes fiction while every number in it remains real.

## Verification / tests

- Fixture with official and unofficial forks ranks the author-linked repo first.
- Selected baseline has immutable commit SHA.
- Fixture with a deliberately broken dependency yields RL0 with `DEPENDENCY_UNRESOLVABLE`, not a crash.
- Fixture whose code runs but whose metric is 40% off the paper yields RL1 with `NUMBERS_DIVERGE`, never RL3.
- Fixture reproducing Table 1 within 2% yields RL3 with paired metric records.
- Time-box fixture halts and reports the highest level reached rather than running unbounded.
- Re-running the same fixture with the captured `source_repro_env` reproduces the same RL.
- Metric mismatch fixture blocks downstream comparative claim.
- Re-run from frozen config produces same result within declared tolerance.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `source_repro_env` — Exact environment in which reproduction was attempted (`reproduction/environment.lock`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

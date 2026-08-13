---
name: research-blueprint-compiler
description: Use when a direction has been selected and must become an executable plan: stages, budgets, acceptance criteria, falsifiable experiment specifications and the ablations that isolate the claimed mechanism. Trigger immediately after selection.
version: 0.5.0
stage: 05-planning
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [research-blueprint-compiler, experiment-spec-author, ablation-and-counterfactual-planner]
---

# research-blueprint-compiler

## Objective

Compile a selected direction into experiments that can actually be run and that can actually fail.

**Consolidates v0.2.0 skills** `research-blueprint-compiler`, `experiment-spec-author`, `ablation-and-counterfactual-planner`.

The blueprint, the experiment specs and the ablation plan were one act of planning split three ways. An experiment spec without its ablations cannot support a causal claim, so the two were never separable in practice.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `baseline_assets`
- `comparison_mode`
- `selected_direction`
- `source_repro_report`
- `external: candidate method description`
- `external: hypothesis under test`
- `external: resource envelope (compute, time, budget)`

## Outputs

- `acceptance_criteria`
- `blueprint_dag`
- `experiment_specs`
- `research_blueprint`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `reproduction-fallback-planner`
- `result-reproducer`
- `user-feedback-gate`

## Procedure

### A. Blueprint and acceptance criteria

1. Define research question, hypothesis, baseline, candidate method, datasets, metrics, statistical plan and expected artifacts.
2. Expand into DAG stages with dependencies and parallelizable experiments.
3. Assign providers/tools/skills to capabilities using tool-skill-router.
4. Define human gates, budget limits, safety constraints and kill/continue rules.
5. Version the blueprint and hash critical configs.

### B. Falsifiable experiment specifications

1. Define hypothesis and decision rule first.
2. Specify control/baseline, treatment variants, fixed factors and nuisance variables.
3. Pin dataset splits, preprocessing, seeds and metric code.
4. Declare required outputs: raw metrics, logs, plots, environment capture and result JSON.
5. Define invalid-run conditions and rerun policy.

### C. Ablations and counterfactual controls

1. Map each claimed mechanism to a removable/replaced component.
2. Design equal-budget comparisons where possible.
3. Add sensitivity checks for key hyperparameters/assumptions.
4. Prioritize ablations by claim importance.
5. Record what the ablated full method is anchored to (`anchored_to`). Removing a part from a
   method that loses to the current best by a wide margin measures the internals of an also-ran,
   while the paper reads as if it measured the internals of a contender.

### D. The state-of-the-art arm

1. When `baseline_assets.sota` names a candidate, every comparative spec gets a third condition and
   budgets for it: `conditions: 3`, `runs = 3 x seeds`.
2. The success metric states which it is — measured here, or reported by its authors and not
   measured here. The caveat travels with the criterion; a footnote gets lost.
3. Emit `SOTA_ARM_NOT_MEASURED` as a **narrowing** condition — a separate list from
   `invalid_conditions` — checking the ledger for a completed `sota` arm. A narrowing condition
   limits what the result may be claimed to show; it does not void the measurement. Filing it as an
   invalid condition meant declaring a state-of-the-art candidate marked the whole experiment void,
   deleting six completed runs including the measured state-of-the-art arm itself.
4. When no candidate exists, emit no arm and no condition — an unsatisfiable condition on every
   spec turns the mechanism into noise — and warn that no claim of competitiveness can follow.

## Hard gates

- Every success claim has a measurable acceptance criterion.
- Every expensive branch has a predeclared budget/stop rule.
- No experiment without a control or an explicit reason control is impossible.
- Metric implementation is shared between baseline and candidate when comparing them.
- A component cannot receive causal credit without an isolation test when feasible.
- A spec's `conditions` count must equal the number of arms the runner can invoke, or the plan
  budgets for an experiment other than the one that will run.
- A comparative spec may omit the state-of-the-art arm only when no candidate was identified, and
  the omission is stated in the blueprint rather than left silent.

## Verification / tests

- Blueprint schema validation.
- Cycle detection rejects invalid DAG.
- Experiment schema rejects missing seed/split for stochastic benchmark fixture.
- Headline mechanism has at least one discriminating ablation.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `ablation_plan` — Ablations and counterfactual controls (`experiments/ablation_plan.yaml`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

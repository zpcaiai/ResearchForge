---
name: research-eval-harness
description: Use when the ResearchForge system itself must be measured against a task suite. Trigger before and after any change to the innovation engine or to a skill.
version: 0.4.0
stage: 12-meta
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [research-eval-harness]
---

# research-eval-harness

## Objective

Run the system against a frozen task suite and produce comparable scores across versions.

**Unchanged from v0.2.0** (`research-eval-harness`).

Kept whole, on the offline maintenance entry point.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `evaluator_code`
- `retro_benchmark`
- `retro_benchmark_report`
- `external: evaluation task suite`
- `external: skill version under evaluation`

## Outputs

- `eval_failure_taxonomy`
- `eval_runs`
- `eval_scorecard`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `evaluator-builder`
- `retrospective-benchmark-builder`

## Procedure

1. Define task contract and submission schema.
2. Run each task in an isolated environment with hidden evaluator/held-out data separated from agent context.
3. Capture tool calls, file changes, outputs and resource cost.
4. Score correctness, process integrity, reproducibility, efficiency and failure classes.
5. Compare versions on held-out tasks and block regressions.

### Scoring against the retrospective benchmark

recall@k needs adjudicated matches, and this skill will not manufacture them. Prepare them with
`tools/benchmark/score_directions.py`: it emits the full cross product of the top-k generated
directions against every gold direction, a human or a recorded model judge fills in the verdicts,
and `--emit-harness-inputs` converts the filled packet into `system_directions` and
`match_adjudications`. An unadjudicated pair is excluded, never counted as a miss.

Report the recall beside the benchmark's contamination floor or not at all. The reference point is
not zero; it is the fraction of the gold set the model names when asked to recite it.

## Hard gates

- Agent cannot read private evaluator.
- Self-reported success never counts as score.

## Verification / tests

- Cheating fixture that reads evaluator path fails.
- Regression fixture blocks promotion despite better training score.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

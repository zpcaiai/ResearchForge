---
name: codebase-scaffolder
description: Use when experiment specs must become an actual code branch with tests, and when the resulting runs fail and need bounded diagnosis rather than blind retry. Trigger after specs exist and the sandbox is provisioned.
version: 0.3.0
stage: 06-execution
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [codebase-scaffolder, debug-and-repair]
---

# codebase-scaffolder

## Objective

Turn experiment specifications into running code, and keep it running without unbounded self-debugging.

**Consolidates v0.2.0 skills** `codebase-scaffolder`, `debug-and-repair`.

Scaffolding and repair are one loop. The debugger only ever read code the scaffolder wrote, and separating them made 'stop trying' a decision with no owner.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `baseline_assets`
- `experiment_specs`
- `research_blueprint`
- `feedback: experiment_ledger`

## Outputs

- `code_tests`
- `code_worktree`
- `debug_terminal_status`
- `failure_diagnosis`
- `implementation_plan`
- `repair_commits`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `research-blueprint-compiler`
- `result-reproducer`

## Procedure

### A. Scaffolding from specs

1. Create a dedicated branch/worktree from the pinned baseline.
2. Map proposed method changes to concrete modules/functions/configs.
3. Add feature flags so baseline and candidate can be executed from the same harness.
4. Implement unit/smoke tests for new components before large experiments.
5. Keep generated code, configs and docs aligned with experiment contracts.

### B. Bounded diagnosis and repair

1. Classify failure: environment, dependency, data, code bug, numerical issue, OOM, evaluator mismatch, invalid hypothesis.
2. Create minimal reproduction and inspect recent diffs.
3. Attempt bounded repairs that preserve experimental contract.
4. If repair requires changing metric/data/hypothesis, open a blueprint amendment instead of silently proceeding.
5. Record root cause and prevention note in finding memory.


## Hard gates

- Do not rewrite unrelated baseline code.
- Candidate path must be disable-able for baseline parity.
- Maximum repair attempts is explicit.
- Scientific-contract changes require new version and rerun of affected baseline.

## Verification / tests

- Baseline tests still pass.
- Candidate feature flag changes only intended execution path.
- Metric-changing repair triggers amendment-required status.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

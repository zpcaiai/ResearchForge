---
name: sandbox-provisioner
description: Use before any generated or untrusted code executes, and whenever an evaluator must stay invisible to the agent being evaluated. Trigger at run setup, not at first execution.
version: 0.3.0
stage: 00-runtime
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [sandbox-provisioner]
---

# sandbox-provisioner

## Objective

Provide a pinned, least-privilege execution environment, and keep the grader context out of the agent's reach.

**Unchanged from v0.2.0** (`sandbox-provisioner`).

Kept whole. It is provisioned in phase 0 — reproduction needs it long before any experiment spec exists — which is precisely why v0.2.0's edge from experiment specs into the sandbox was a genuine design error.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `external: sandbox security profile`

## Outputs

- `environment_lock`
- `sandbox_container_config`
- `sandbox_manifest`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- None; this skill consumes only external inputs.

## Procedure

1. Build or select a pinned container/venv image.
2. Mount only required project/data paths and provide least-privilege secrets.
3. Apply CPU/GPU/memory/time/process/network limits.
4. When evaluation could be gamed, put evaluator in a separate grader context invisible to the agent.
5. Capture package versions, system info and image digest.

## Hard gates

- Untrusted generated code cannot run on the host by default.
- Private evaluator and hidden tests are never mounted into the agent context.

## Verification / tests

- Sandbox escape fixture cannot read host secret path.
- Agent cannot read hidden evaluator fixture.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

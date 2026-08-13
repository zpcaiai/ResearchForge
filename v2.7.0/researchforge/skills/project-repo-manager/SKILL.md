---
name: project-repo-manager
description: Use at project start and at every milestone. Trigger to create the quest repository, pin sources, route ideas to branches and preserve failed paths.
version: 0.3.0
stage: 00-runtime
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [project-repo-manager]
---

# project-repo-manager

## Objective

One research quest, one Git repository — with competing directions on branches and failed paths kept until their findings are distilled.

**Unchanged from v0.2.0** (`project-repo-manager`).

Kept whole. Long-horizon projects live or die on their ability to resume, and the repository is where that lives.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `external: paper locator (URL/PDF/HTML/DOI/arXiv/local path)`
- `external: project intent statement`
- `external: workspace and retention policy`

## Outputs

- `branch_map`
- `checkpoint_metadata`
- `quest_repo`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- None; this skill consumes only external inputs.

## Procedure

1. Create/adopt a Git repo and initialize research metadata directories.
2. Pin the source paper and baseline revision.
3. Create branches/worktrees for selected idea and major experimental variants.
4. Commit meaningful milestones: baseline reproduced, experiment evidence locked, draft reviewed, deck finalized.
5. Never delete failed branches until findings are distilled and retention policy allows cleanup.

## Hard gates

- Generated agents do not force-push protected project history.
- Secrets/data artifacts follow .gitignore/data policy.

## Verification / tests

- Resume from checkpoint fixture restores correct branch and blueprint version.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

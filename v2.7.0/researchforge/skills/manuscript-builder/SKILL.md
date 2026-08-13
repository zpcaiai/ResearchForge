---
name: manuscript-builder
description: Use when evidence is locked and the paper must be built — first its central argument, then prose written against that argument and the evidence behind it. Trigger only after evidence lock.
version: 0.3.0
stage: 08-writing
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [manuscript-spine-builder, paper-drafter]
---

# manuscript-builder

## Objective

Build the paper's argument first and write to it, so that no sentence exists without a claim behind it and no claim without evidence.

**Consolidates v0.2.0 skills** `manuscript-spine-builder`, `paper-drafter`.

The spine exists solely to constrain the draft. Two skills meant the draft could be regenerated without regenerating the spine it was supposed to obey, which is precisely how evidence-free prose gets in.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `bibliography`
- `boundary_conditions`
- `comparison_mode`
- `evidence_graph`
- `findings`
- `meta_analysis`
- `negative_findings`
- `selected_direction`
- `external: target venue`
- `external: target venue template (LaTeX/Word style)`
- `feedback: selected_figure`

## Outputs

- `draft_manifest`
- `figure_plan`
- `manuscript_draft`
- `manuscript_pdf`
- `manuscript_spine`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `claim-evidence-graph`
- `finding-memory`
- `integrity-auditor`
- `reproduction-fallback-planner`
- `user-feedback-gate`

## Procedure

### A. Argument spine and section blueprint

1. State the paper's one-sentence contribution and why it matters.
2. Define reviewer-facing claim sequence: problem → gap → method → evidence → limits.
3. Assign each major claim to experiments, tables/figures and citations.
4. Plan introduction beats, method modules, result questions, limitations and discussion.
5. Identify any evidence gap that must be filled before prose generation.

### B. Evidence-bound drafting

1. Draft section-by-section from the spine, never from model memory alone for factual claims.
2. Use stable claim IDs and citation IDs during generation, then render bibliography syntax.
3. Report methods and experimental settings from provenance records.
4. Use calibrated language matching evidence strength.
5. Include limitations, negative findings and AI-use disclosure when required by venue/license/workflow.


## Hard gates

- No major claim without evidence assignment.
- Writing cannot hide negative results needed to delimit the claim.
- No invented experiment or citation.
- Numbers must be pulled from verified result artifacts by ID.

## Verification / tests

- Spine validator finds orphan major claim fixture.
- Draft with injected unsupported number is rejected by post-draft audit.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `section_blueprint` — Per-section argumentative role (`paper/section_blueprint.md`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

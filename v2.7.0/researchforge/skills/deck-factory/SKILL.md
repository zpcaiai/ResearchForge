---
name: deck-factory
description: Use when a defense or talk must be produced as native editable PowerPoint objects, with every quantitative element on every slide bound to a project artifact. Trigger once the manuscript's evidence is locked.
version: 0.3.0
stage: 10-slides
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [defense-ppt-storyline, paper-to-ppt-evidence-mapper, defense-ppt-generator]
---

# deck-factory

## Objective

Turn the locked evidence into a defense narrative and then into a real, editable deck in which every number can be traced back.

**Consolidates v0.2.0 skills** `defense-ppt-storyline`, `paper-to-ppt-evidence-mapper`, `defense-ppt-generator`.

Evidence mapping was a step between storyline and generation with no other consumer. Three skills made it possible to generate a deck that skipped the mapping entirely — which is the exact failure the mapping exists to prevent.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `comparison_mode`
- `draft_manifest`
- `editable_svg`
- `experiment_ledger`
- `manuscript_draft`
- `manuscript_spine`
- `meta_analysis`
- `selected_figure`
- `svg_element_map`
- `svg_validation_report`
- `external: PPTX template`
- `external: defense audience profile`
- `external: target talk duration`

## Outputs

- `deck_manifest`
- `defense_deck`
- `speaker_notes`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `experiment-runner`
- `figure-factory`
- `integrity-auditor`
- `manuscript-builder`
- `reproduction-fallback-planner`

## Procedure

### A. Narrative, slide spec and timing

1. Define defense thesis, audience assumptions and 3–5 takeaways.
2. Allocate time to motivation, gap, method, evidence, limitations, contributions and backup slides.
3. Map each slide to one message and source artifact.
4. Plan likely committee questions and backup evidence slides.
5. Keep slide rhythm and density explicit.

### B. Slide element to artifact binding

1. For each planned slide, choose the minimum evidence needed.
2. Link source figure/table/claim IDs and decide reuse vs redraw.
3. Prepare concise speaker-note provenance.
4. Flag unsupported or too-dense slides before generation.

### C. Native PPTX generation and render QA

1. Resolve theme/template and typography scale before slide generation.
2. Create each slide from structured spec using native PowerPoint objects where possible.
3. Use data-backed charts/tables and editable vector diagrams rather than flattened screenshots when feasible.
4. Add speaker notes and source/provenance notes.
5. Render/inspect every slide for overflow, alignment, legibility and narrative continuity.


## Hard gates

- No slide without a single primary message.
- All numerical slides reference verified experiment artifacts.
- Every evidence slide has source IDs.
- Do not produce a deck of full-slide images.
- Slide source references must resolve to project artifacts or citations.

## Verification / tests

- Total planned timing stays within talk duration tolerance.
- Orphan slide fixture is rejected.
- PPTX inspector confirms editable text objects on content slides.
- Rendered slide images pass overflow/legibility checks.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `deck_spec` — Structured per-slide specification (`slides/deck_spec.json`)
- `slide_evidence` — Slide element -> project artifact links (`slides/slide_evidence.json`)
- `slide_outline` — Defense narrative outline (`slides/slide_outline.md`)
- `speaker_timing` — Time budget per slide (`slides/speaker_timing.csv`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

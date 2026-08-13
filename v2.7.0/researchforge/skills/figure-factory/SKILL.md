---
name: figure-factory
description: Use when the argument determines which figures must exist, and each must become a publication-quality, semantically editable vector figure — whether drawn from data, reconstructed from an existing raster, or both. Trigger after the data behind each figure is audited.
version: 0.3.0
stage: 09-visuals
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [figure-storyboard, scientific-figure-generator, vector-figure-reconstructor, editable-svg-refiner]
---

# figure-factory

## Objective

Produce the figures the argument requires, as editable vector artifacts bound to the claims they support.

**Consolidates v0.2.0 skills** `figure-storyboard`, `scientific-figure-generator`, `vector-figure-reconstructor`, `editable-svg-refiner`.

Four skills for one pipeline: decide what the figure must say, draw it, and make it editable — where two of the four ('refine to editable SVG' and 'reconstruct a raster into vectors') were the same job reached from different inputs.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `analysis_plots`
- `analysis_results`
- `evidence_graph`
- `figure_plan`
- `manuscript_spine`
- `external: figure style reference`
- `external: raster figure or structured visual spec`
- `external: source raster figure`
- `external: target venue`

## Outputs

- `editable_svg`
- `figure_generation_trace`
- `selected_figure`
- `svg_element_map`
- `svg_validation_report`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `claim-evidence-graph`
- `data-analyst`
- `manuscript-builder`

## Procedure

### A. What each figure must say

1. Assign each figure one communication job: motivation, method overview, main result, ablation, failure case, qualitative example.
2. Define source data/claims and what must remain editable.
3. Choose plot vs schematic vs table and target dimensions.
4. Specify caption claim and accessibility/legibility constraints.
5. Prioritize figures by reviewer decision value.

### B. Figure generation

1. Retrieve relevant style/reference examples when allowed.
2. Plan semantics/layout before rendering.
3. For plots, generate code from exact data; for schematics, generate a structured visual spec.
4. Produce multiple candidates when ambiguity is high.
5. Run critic loop for semantic accuracy, legibility, venue size and consistency.

### C. Raster to vector reconstruction

1. Detect text, shapes, icons and connectors.
2. Recreate layout with editable elements.
3. Compare geometry and labels against source.
4. Expose uncertain elements for manual correction.

### D. Semantic labelling and editability validation

1. Segment or identify meaningful visual elements.
2. Construct a vector layout with stable IDs and real text whenever possible.
3. Reassemble icons/shapes/connectors in a common coordinate system.
4. Iteratively compare against semantic/layout constraints.
5. Export SVG plus a manifest mapping visual elements to manuscript concepts.


## Hard gates

- No decorative figure without a defined paper role.
- Data plots must identify exact result artifacts.
- Plots cannot invent or smooth away data without explicit transformation.
- Visual critic must check labels/units/legend against source.
- Do not claim pixel-perfect semantic reconstruction when labels are uncertain.
- Do not rasterize the whole diagram into a single embedded image and call it editable.
- Text must remain selectable/editable unless technically impossible and disclosed.

## Verification / tests

- Each main manuscript claim has at most appropriate and traceable visual support.
- Mismatched axis-unit fixture is rejected.
- Text remains editable in output fixture.
- SVG fixture contains separate editable text and connector elements.

## Internal artifacts

Produced and consumed entirely inside this skill. They no longer cross a skill boundary, so they are not part of the public artifact contract — but they are still written to disk and still carry provenance.

- `figure_candidates` — Generated figure variants (`figures/candidates/`)
- `figure_captions` — Draft captions tied to claims (`figures/captions_draft.md`)
- `figure_storyboard` — Message and form for each figure (`figures/figure_storyboard.json`)
- `reconstructed_svg` — Vector reconstruction of a raster figure (`figures/reconstructed/`)
- `svg_reconstruction_map` — Source region -> vector element mapping (`figures/reconstruction_map.json`)

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

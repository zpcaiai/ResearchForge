---
name: paper-ingest
description: Use as the first action on any new paper locator. Fetches the source, normalizes text, extracts assets and falls back to visual reading when layout defeats text extraction. Trigger before any parsing or reasoning about the paper's content.
version: 0.3.0
stage: 01-intake
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [paper-ingest, paper-html-visual-reader]
---

# paper-ingest

## Objective

Turn a paper locator into a normalized, anchored, machine-readable source bundle — including when the PDF fights back.

**Consolidates v0.2.0 skills** `paper-ingest`, `paper-html-visual-reader`.

Visual reading is the fallback branch of ingestion, not a separate capability. It fires on the same input, produces artifacts only ingestion's consumers read, and choosing between the two paths is a decision inside one act of reading the paper.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `external: paper locator (URL/PDF/HTML/DOI/arXiv/local path)`
- `external: rendered page images`
- `external: supplementary URLs or files`

## Outputs

- `layout_warnings`
- `locator_map`
- `normalized_text`
- `paper_assets`
- `paper_source_file`
- `source_manifest`
- `visual_notes`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- None; this skill consumes only external inputs.

## Procedure

### A. Fetch and normalize

1. Resolve the canonical paper identity and version; record title/authors/year/venue/DOI/arXiv when available.
2. Fetch or copy the source while preserving original bytes and checksum.
3. Extract text and structural anchors; for PDFs preserve page numbers and figure/table references; for HTML preserve heading/XPath-like anchors.
4. Download linked supplementary material only when explicitly accessible and relevant.
5. Emit a source manifest with provenance, fetch time, license/access notes and parser warnings.

### B. Visual fallback when text extraction is unreliable

1. Inspect pages containing key figures/tables/equations when parser confidence is low.
2. Capture semantic content, not OCR-only strings.
3. Reconcile visual findings with locator map and flag discrepancies.


## Hard gates

- Do not silently substitute a different paper version.
- If text extraction is materially incomplete, mark NEEDS_VISUAL_INSPECTION rather than guessing.
- Do not infer unreadable labels; mark uncertain.

## Verification / tests

- Same DOI ingested twice is deduplicated by canonical ID.
- PDF fixture preserves page anchors for at least abstract/method/results.
- Known figure-caption mismatch fixture is detected.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

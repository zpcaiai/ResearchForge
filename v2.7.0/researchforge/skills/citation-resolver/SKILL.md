---
name: citation-resolver
description: Use when reference strings, DOIs or arXiv ids must become canonical records with a renderable bibliography, and when the citation neighbourhood around a seed paper must be mapped. Trigger before any citation is allowed to support a claim.
version: 0.3.0
stage: 02-evidence
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [citation-resolver, citation-neighborhood-miner]
---

# citation-resolver

## Objective

Turn reference strings into resolvable, deduplicated records, and expand outward through the citation graph to find where the field is thin.

**Consolidates v0.2.0 skills** `citation-resolver`, `citation-neighborhood-miner`.

Neighbourhood mining is what you do with resolved references, using the same provider clients and the same identity resolution. Separating them duplicated the hardest part — deciding when two references are the same work.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `provider_registry`
- `external: personal library metadata`
- `external: raw reference strings or identifiers`
- `external: seed paper canonical identifier`

## Outputs

- `bibliography`
- `citation_clusters`
- `citation_gap_candidates`
- `citation_graph`
- `citation_resolution_report`
- `resolved_references`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `literature-provider-manager`

## Procedure

### A. Reference resolution and bibliography

1. Normalize titles/authors/years and attempt DOI/arXiv/OpenAlex/Semantic Scholar/Crossref resolution through configured providers.
2. Deduplicate preprint/published versions while preserving version lineage.
3. Store canonical IDs, landing URLs and citation metadata.
4. Flag unresolved, suspicious or title-mismatched records for manual review.

### B. Citation graph expansion and thin regions

1. Collect backward references, forward citations and highly co-cited/co-referenced works.
2. Cluster by problem, method, data and evaluation pattern.
3. Find missing comparisons, convergent ideas, abandoned approaches and recently revived methods.
4. Record why each cluster matters for novelty rather than only listing papers.


## Hard gates

- Never invent DOI, venue, pages or authors.
- Unresolved citations cannot be promoted to VERIFIED.
- Do not infer novelty from citation count alone.
- If forward-citation coverage is unavailable, mark coverage limits.

## Verification / tests

- Duplicate arXiv+journal fixture collapses to one work with two versions.
- Fabricated DOI fixture is flagged unresolved.
- Known competitor fixture appears in method-neighbor cluster.
- Coverage metadata distinguishes unavailable vs zero citations.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

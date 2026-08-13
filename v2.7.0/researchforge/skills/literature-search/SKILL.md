---
name: literature-search
description: Use when related work must be retrieved for a topic, claim or candidate idea, or when the user needs a comparative view of competing systems rather than a list of papers. Trigger after provider registration, never before.
version: 0.3.0
stage: 02-evidence
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [literature-search, paper-library-connector, research-landscape-builder]
---

# literature-search

## Objective

Retrieve the relevant literature, ground it in the user's own library where one exists, and render it as a comparative landscape rather than a pile of citations.

**Consolidates v0.2.0 skills** `literature-search`, `paper-library-connector`, `research-landscape-builder`.

Personal-library lookup is one more retrieval provider. Landscape building is a rendering of a completed search, not a separate investigation — it consumed search output and produced only documents.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `coverage_report`
- `paper_model`
- `provider_registry`
- `external: landscape topic`
- `external: library query`
- `external: literature search objective`
- `external: paper context for library lookup`
- `external: personal library connector configuration`
- `external: recency and time constraints`
- `external: tool and domain catalog source`

## Outputs

- `benchmark_matrix`
- `landscape_report`
- `library_hits`
- `library_note_links`
- `literature_candidates`
- `literature_retrieval_log`
- `literature_search_plan`
- `system_matrix`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `literature-provider-manager`
- `paper-model-builder`

## Procedure

### A. Query planning and retrieval

1. Generate query families from problem, method components, metrics, datasets and hidden assumptions.
2. Search configured scholarly sources and tool ecosystems; record exact query/provider/time.
3. Prioritize primary papers and authoritative artifacts; retain negative or contradictory evidence.
4. Deduplicate and rank by relevance, recency, methodological proximity and citation-neighborhood importance.
5. Hand selected records to citation-resolver and deep reading.

### B. Personal library grounding

1. Search library by title/author/topic and reuse existing PDFs/notes when authorized.
2. Separate bibliographic metadata from private annotations.
3. Resolve cited works through citation-resolver before manuscript use.

### C. Comparative landscape rendering

1. Collect representative systems and surveys.
2. Normalize capabilities and evaluation axes.
3. Identify capability gaps and crowded directions.
4. Feed landscape deltas into novelty-gap-miner.


## Hard gates

- Search coverage must include at least method-neighbor, task-neighbor and benchmark-neighbor queries before claiming novelty coverage.
- Private annotations are not quoted or exported unless explicitly requested.
- Landscape is descriptive evidence, not proof of novelty.

## Verification / tests

- Fixture returns distinct method/task/benchmark query families.
- Search log is replayable and provider failures are visible.
- Library hit missing canonical citation is routed to resolver.
- Matrix includes source paper plus nearest competitors.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

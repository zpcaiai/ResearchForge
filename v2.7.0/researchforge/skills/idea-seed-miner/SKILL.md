---
name: idea-seed-miner
description: Use when contributions are decomposed, the literature is mapped and coverage is measured, and you need innovation seeds grounded in evidence rather than in guessing. Runs three mining modes — weakness, gap and analogy. Trigger as the first stage of the innovation engine.
version: 0.3.0
stage: 04-innovation
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [assumption-weakness-miner, novelty-gap-miner, cross-domain-analogy-miner]
---

# idea-seed-miner

## Objective

Produce evidenced innovation seeds from three complementary angles: what the paper assumes, what the field has not covered, and what transfers from elsewhere.

**Consolidates v0.2.0 skills** `assumption-weakness-miner`, `novelty-gap-miner`, `cross-domain-analogy-miner`.

Three mining passes emitting the same kind of object into the same consumer. Keeping them apart made the portfolio generator depend on three skills to get one list, and let a run silently skip a mode without anything noticing. As modes of one skill, mode coverage becomes checkable.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `citation_clusters`
- `contribution_atoms`
- `coverage_report`
- `literature_candidates`
- `method_dependency_graph`
- `paper_model`
- `source_repro_report`
- `external: target venue`
- `external: tool and domain catalog source`

## Outputs

- `analogy_candidates`
- `assumption_tests`
- `gap_evidence`
- `gap_ledger`
- `weakness_map`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- `citation-resolver`
- `literature-provider-manager`
- `literature-search`
- `paper-model-builder`
- `result-reproducer`

## Procedure

### A. Mode A — assumptions, failure modes and blind spots

1. Enumerate assumptions about data distribution, compute, labels, sensors, stationarity, supervision, architecture, optimization and evaluation.
2. Separate author-admitted limitations from independently inferred risks.
3. For each assumption, propose a falsifiable stress test and likely scientific value if broken.
4. Prioritize weaknesses that can produce a new contribution rather than cosmetic fixes.

### B. Mode B — evidenced gaps in the literature

1. Build a comparison matrix of capabilities/assumptions/metrics/compute/data across source and competitors.
2. Detect uncovered cells, contradictory results, missing ablations, cost-quality frontiers and domain-transfer gaps.
3. Estimate whether each gap is already solved in another terminology or adjacent field.
4. Attach positive and negative evidence and a novelty-risk level.

### C. Mode C — cross-domain mechanism transfer

1. Represent the source method as abstract operations: estimation, control, retrieval, optimization, compression, causal adjustment, memory, search, etc.
2. Search for structurally similar mechanisms in adjacent domains.
3. For each transfer, state invariant, required adaptation, likely failure and testable hypothesis.
4. Reject analogies based only on surface vocabulary.


## Hard gates

- Weaknesses must be falsifiable or explicitly marked speculative.
- Do not convert mere implementation inconvenience into a scientific gap without evidence.
- A gap cannot be labeled novel until novelty-verifier runs.
- No 'nobody has done X' statement from search absence alone.
- Every analogy must identify a mechanism-level correspondence and a falsifiable benefit.

## Verification / tests

- At least one stress-test spec is generated per high-priority assumption.
- Synthetic literature matrix yields expected uncovered cell and contradiction.
- Keyword-only analogy fixture is rejected.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

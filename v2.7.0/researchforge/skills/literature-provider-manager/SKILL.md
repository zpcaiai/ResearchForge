---
name: literature-provider-manager
description: Use before any literature search, novelty check or citation resolution. Manages providers, quota and credentials, and measures how much of the relevant literature was actually reachable. Trigger whenever a skill is about to assert that no prior work exists.
version: 0.3.0
stage: 02-evidence
artifact_kind: workflow-skill
implementation_status: specification-ready
consolidates: [literature-provider-manager]
---

# literature-provider-manager

## Objective

Make retrieval coverage an explicit, measured quantity rather than a silent assumption.

**Unchanged from v0.2.0** (`literature-provider-manager`).

Kept whole and deliberately separate from search. Coverage is the guarantee behind every novelty claim; folding it into the searcher would let the searcher grade its own homework.

This skill is an **execution contract for an agent/coding runtime**, not proof that the corresponding application code already exists. When used to build the ResearchForge system, the agent must create/modify real files, run tests, capture evidence, and report incomplete items explicitly.

## Inputs

- `external: domain and recency scope`
- `external: provider credentials and contact email for polite pools`
- `external: run-level quota and cost budget`

## Outputs

- `coverage_report`
- `provider_registry`
- `quota_ledger`

## Depends on

*Derived from the artifact graph (`manifests/artifact-graph.json`); do not hand-edit.*

- None; this skill consumes only external inputs.

## Procedure

1. Register each available provider in `provider_registry` with its real operational envelope, not its marketing description: auth mode, documented rate limit, observed rate limit, daily/monthly quota, whether it exposes **full text** or only title/abstract, whether it exposes embeddings or only keyword match, citation-graph availability, licence terms on retrieved metadata, and typical latency. Record the observed limit separately from the documented one; they differ.
2. Probe each provider at registration with a small fixed query set and record actual behaviour. A provider that is configured but returning 429 is not available, and must not be counted toward coverage.
3. Budget quota across the run **before** spending it. Novelty verification is the highest-value consumer and must be allocated first; landscape building and neighbourhood expansion are elastic and yield first. Record every call in `quota_ledger` with provider, endpoint, cost, and the artifact it served.
4. Declare the **known blind spots** of the assembled provider set explicitly in `coverage_report`. At minimum, state whether the run had: full-text search, non-English coverage, preprint coverage, coverage of the last 90 days, code/artifact search, and venue-proceedings coverage. Each absent capability is a named blind spot, not a silent gap.
5. Compute a **coverage score** per search objective using measurable proxies rather than self-assessment:
   - *seeded-recall*: inject a small set of works known a priori to be relevant (e.g. the source paper's own references) and measure what fraction the search actually returned;
   - *saturation*: the rate at which new queries stop surfacing new works;
   - *provider agreement*: overlap between independent providers on the same query.
   Report the score with the method used, and never report a score derived only from the number of hits returned.
6. When coverage falls below the configured threshold, emit `UNKNOWN_COVERAGE` for the affected objective and propagate it. Downstream, `novelty-verifier` may not assert `NOVEL_ENOUGH` under `UNKNOWN_COVERAGE`, and `idea-ranker` applies its documented uncertainty penalty.
7. Degrade in a declared order when quota is exhausted: reduce neighbourhood depth, then reduce landscape breadth, then reduce recency window — and only then reduce novelty verification, which requires a human decision because it directly weakens the system's central guarantee.

## Hard gates

- No skill may assert absence of prior work while `coverage_report` marks that objective `UNKNOWN_COVERAGE`.
- A coverage score must state its measurement method; an unmethodded score is treated as zero.
- Quota exhaustion is surfaced as a first-class run condition, never silently swallowed as an empty result set.
- Credentials never enter artifacts, logs, prompts or the ledger.
- A provider probed as failing is excluded from coverage computation for that run.

## Verification / tests

- Seeded-recall fixture: with 10 known-relevant works injected and 3 retrievable, coverage score reflects 0.3, not the raw hit count.
- Quota-exhaustion fixture produces `UNKNOWN_COVERAGE` and blocks `NOVEL_ENOUGH` downstream rather than returning an empty, confident result.
- Title/abstract-only provider set is reported as lacking full-text coverage, and mechanism-level novelty search is flagged as degraded.
- 429-throttled provider is excluded from coverage and surfaced as an alert.
- Credential-leak fixture: no artifact under the project directory contains the token.

## Evidence contract

When this skill executes in a real project, persist an evidence record containing: skill version, input artifact IDs, output artifact IDs, commands/tool calls executed, exit status, test results, warnings, model/provider identity when relevant, git SHA when code changed, and human approvals when a gate required them.

## Failure behavior

Fail closed for integrity, provenance, evaluator isolation, citation support, or baseline-comparability problems. For recoverable execution errors, create a structured failure record rather than degrading the honesty of the report.

## Upstream inspiration (conceptual, not vendored text)

See `SOURCE_MAP.md` and `LICENSE_NOTES.md`. This package intentionally uses original workflow wording and references upstream projects by URL/role instead of copying their text.

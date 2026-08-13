# Implementation Plan — turn the Skills package into a real application

The purpose of this plan is to prevent a common failure mode: producing many SKILL.md files while implementing little or no runtime code. Each batch below must modify a real target repository and produce build/test evidence.

## Batch 01 — Project kernel and durable state
Implement project-repo-manager, researchforge-orchestrator state machine, artifact IDs, event/provenance ledger and resume semantics. DoD: kill/restart resumes at last valid stage; state migrations tested.

## Batch 02 — Paper ingestion and structural model
Implement URL/PDF/HTML ingestion, parser adapters, page/section locator map, paper model schema and visual inspection fallback. DoD: three fixture formats normalize to equivalent paper identity and anchored sections.

## Batch 03 — Literature providers, coverage measurement, citations and evidence graph
Implement the provider registry (auth, documented vs observed rate limits, quota ledger, full-text capability flags), coverage measurement (seeded recall, saturation, cross-provider agreement), named blind-spot reporting, then citation resolution/dedup, query expansion, citation-neighborhood graph and claim-evidence persistence.

DoD: fake/unresolved citation fixture is rejected; every evidence edge has provenance; **seeded-recall fixture with 10 known-relevant works and 3 retrievable reports coverage 0.3, not the raw hit count**; quota exhaustion produces `UNKNOWN_COVERAGE` and blocks a downstream `NOVEL_ENOUGH` rather than silently returning an empty result; no credential appears in any artifact.

## Batch 04 — Source reproduction, grading and the degradation path  **[moved before ideation]**
Implement code/data/checkpoint discovery, pinned checkout, environment capture, time-boxed tiered execution, RL0–RL4 grading against values read from the paper, the fixed failure taxonomy, and the fallback planner that maps RL to `comparison_mode` and `idea_mode_constraints`.

**Do Batch 04 before Batch 05.** Ideation that runs before reproduction produces feasibility estimates with no basis.

DoD: broken-dependency fixture yields RL0 + `DEPENDENCY_UNRESOLVABLE` without crashing; runs-but-diverges fixture yields RL1 + `NUMBERS_DIVERGE`, never RL3; reduced-scale run cannot produce RL3; time box halts and reports the level actually reached; **RL0 fixture produces a non-empty idea portfolio containing only diagnostic/evaluation directions**; attempting to set `CM_MEASURED` without a fresh RL3+ report is rejected.

### Batch 04-pre — the 20-paper reproduction study  **[do this first, no runtime code]**
Before writing Batch 04, run the study in `REPRO_STUDY_PROTOCOL.md` on 20 real papers and measure the actual RL distribution. That distribution determines whether the gate design above is viable. **If RL≥3 is achieved on fewer than 30% of papers, stop and redesign the degradation path before writing runtime code.**

## Batch 05 — Innovation engine
Implement contribution/assumption/gap/analogy mining, portfolio generation, novelty verification, feasibility scoring and Pareto ranking. DoD: system outputs at least 5 structured ideas on a fixture paper and detects a seeded near-duplicate idea.

## Batch 06 — Human feedback gate
Implement guided idea selection UI/API and structured combine/constrain/reject semantics. DoD: user can combine idea 2+4 and the system recomputes only affected artifacts.

## Batch 07 — Blueprint and evaluation environment
Implement blueprint compiler, experiment schemas, metric/hidden evaluator builder and sandbox/grader isolation. DoD: agent container cannot read hidden evaluator; invalid score tampering is rejected.

## Batch 08 — Code/worktree experiment engine
Implement codebase scaffolding, worktrees, experiment tree search, bounded debugging, ledger and budget controller. DoD: parallel branches run, failed branches persist, cost/time budget halts expansion.

## Batch 09 — Data analysis and integrity
Implement data prep, analysis, stats audit, meta-analysis, negative-result curation and findings memory. DoD: leakage and table/raw mismatch fixtures are detected; repeated runs aggregate honestly.

## Batch 10 — Manuscript and citation integrity
Implement manuscript spine, evidence-bound drafting, bibliography rendering, claim-citation audit, reviewer simulation and rebuttal/revision matrix. DoD: real-but-irrelevant citation and fabricated metric fixtures block finalization.

## Batch 11 — Figure and defense PPT factory
Implement figure storyboard, plot/schematic adapters, editable SVG refinement, slide evidence mapping, native PPTX generation and render QA. DoD: deck contains native text/shapes/charts, not flat slide images; every quantitative slide links to verified evidence.

## Batch 12 — Tool/model routing, the retrospective benchmark, and skill evolution
Implement compact tool discovery, domain skill locking, provider routing, the retrospective benchmark builder, research-eval-harness and offline SkillOpt-style skill evolution.

Build the retrospective benchmark **before** tuning anything in the innovation engine — otherwise there is no way to tell improvement from drift.

DoD: held-out regression prevents skill promotion; provider fallback preserves data policy; benchmark reports a contamination floor and is rejected as a promotion gate without one; two system versions with a known quality difference are ordered correctly by recall@k; post-hoc modification of a frozen benchmark is rejected.

## Batch 13 — Release gate and E2E certification
Implement final integrity gate, reproducibility commands, artifact packaging and end-to-end golden projects. DoD: **three** fixture papers spanning RL3, RL1 and RL0 each reach a coherent terminal state — the RL0 case producing a diagnostic/evaluation contribution rather than halting — with ranked ideas, a selected branch, a real code run, an evidence-backed draft and an editable deck; all artifacts have provenance; the manuscript's disclosure text matches the achieved comparison mode. A single fixture paper is not an end-to-end test, it is a demo.

## Mandatory evidence per batch

- changed file list and git SHA;
- build/lint/unit/integration commands and exit codes;
- key test artifacts/logs;
- screenshots/rendered artifacts where UI/PDF/PPT is involved;
- explicit incomplete items;
- no checklist item may be marked complete from documentation-only changes.

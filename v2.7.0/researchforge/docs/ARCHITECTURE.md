# ResearchForge — Paper → Innovation → Code → Paper → Defense PPT

## Goal

Given a paper URL, PDF or HTML, ResearchForge should automatically understand the paper, expand and verify the literature, discover several high-value innovation directions, allow a user to choose/merge/constrain them (or run auto mode), then execute a reproducible research project that outputs code, experiment evidence, a full paper with verified citations, publication figures, and a natively editable defense PPTX.

## Non-negotiable design choices

1. **Portfolio before commitment.** Generate and rank multiple directions; do not run the first plausible idea.
2. **Reproduction before ideation.** The source paper's own results are reproduced *before* any innovation direction is generated. An idea whose feasibility and delta were estimated against a code base that does not run is not an estimate. This is also the cheapest kill signal in the pipeline.
2b. **Reproduction failure degrades the project, it does not halt it.** The achieved reproduction level (RL0–RL4) determines which comparative claims and which innovation modes remain admissible. At RL0 the system pivots to diagnostic and evaluation contributions rather than stopping.
3. **Evidence graph as truth backbone.** Manuscript claims, figures and slides point to source citations or experiment result IDs.
4. **One research quest = one Git repo.** Branch/worktree routes preserve competing ideas and failed paths.
5. **Sandboxed code execution.** Generated code runs isolated; private evaluator/hidden tests stay outside agent view.
6. **Durable run ledger.** Every metric is traceable to code SHA, config, data, seed and environment.
7. **Human gate at innovation selection.** Guided mode requires explicit selection; auto mode still records an autonomous decision artifact.
8. **Claim-bounded writing.** Paper generation starts only after evidence is locked; citations are checked for support, not only existence.
9. **Editable artifacts.** Scientific diagrams prefer SVG; defense deck prefers native PPT objects.
10. **Offline skill evolution.** Skill changes use held-out evaluation and version promotion; no live self-rewriting in production.

## Runtime layers

- **Control plane:** orchestrator, research-progress-controller, project-repo-manager, budgets, human gates.
- **Knowledge/evidence plane:** paper model, citation resolver, literature/citation graph, claim-evidence graph, finding memory.
- **Innovation plane:** weakness/gap/analogy mining, portfolio generation, novelty/feasibility/ranking, user feedback.
- **Execution plane:** blueprint, experiment specs, sandbox, code/worktrees, tree search/mutation, debugger, ledger.
- **Evaluation plane:** hidden evaluator, research-eval-harness, data/statistical audits, meta-analysis.
- **Publication plane:** manuscript spine, drafting, citation audit, review/rebuttal, figures, PPT, release gate.
- **Provider/tool plane:** model-provider-router, tool-skill-router, domain-skill-loader, tool composition.

## State machine

```text
INGESTED
  -> MODELED
  -> EVIDENCE_EXPANDED
  -> SOURCE_REPRO_ATTEMPTED        # reproduce the SOURCE paper, time-boxed
  -> REPRO_LEVEL_ESTABLISHED       # RL0-RL4 + comparison mode + admissible idea modes
  -> IDEAS_READY
  -> HUMAN_SELECTION_REQUIRED (guided)
  -> DIRECTION_SELECTED
  -> BLUEPRINT_READY
  -> BASELINE_ESTABLISHED
  -> EXPERIMENTING
  -> EVIDENCE_LOCKED
  -> WRITING
  -> REVIEWING
  -> DEFENSE_READY
  -> RELEASED
```

Transitions are gated; a later stage must not synthesize missing evidence to bypass an earlier failure.

### Reproduction level and what it permits

`REPRO_LEVEL_ESTABLISHED` is the most consequential state in the machine, because it sets the ceiling on every quantitative claim the project may later make.

| RL | achieved | comparison_mode | admissible innovation modes |
|---|---|---|---|
| RL4 | majority of primary table reproduced within tolerance | `CM_MEASURED` | all |
| RL3 | ≥1 headline cell reproduced within tolerance | `CM_MEASURED` | all |
| RL2 | reduced-scale smoke match (direction + magnitude) | `CM_RELATIVE` | all, deltas stated relatively |
| RL1 | code runs, numbers do not match | `CM_REPORTED` | diagnose, evaluate, systemize, guarded transfer |
| RL0 | not reproduced | `CM_NONE` | explain/diagnose and benchmark/evaluate only |

RL0 is deliberately **not** a terminal state. A field in which a large fraction of artifacts do not reproduce is not a field in which research is impossible — it is one in which reproducibility studies, negative results and evaluation-methodology work are themselves the contribution, and `repro_failure_taxonomy` is their evidence.

### Retrieval coverage is measured, not assumed

Every novelty claim is a claim about absence. `literature-provider-manager` measures what the search could actually reach — using seeded recall, saturation and cross-provider agreement — and publishes named blind spots (no full text, no non-English, no last-90-days, no code search). Under `UNKNOWN_COVERAGE`, `novelty-verifier` may not assert `NOVEL_ENOUGH`. "We did not find prior work" and "we could not look" are never allowed to look the same.

### The system evaluates itself retrospectively

`retrospective-benchmark-builder` pairs seed papers from a closed past window with the follow-up work actually published afterwards, and scores the innovation engine by recall@k against directions that really happened. It carries a mandatory contamination floor, because the model under test has likely read the follow-ups. The retrospective metric catches regressions cheaply; a blind expert rubric on a smaller sample is what establishes the output is worth anything. Neither substitutes for the other.

## Suggested project layout produced by the future runtime

```text
research-project/
  .researchforge/
    research_state.json
    research_blueprint.yaml
    decisions.jsonl
    provenance.jsonl
    experiment_ledger.jsonl
    findings.jsonl
  source/
  literature/
  reproduction/
  baseline/
  code/
  experiments/
  analysis/
  evidence/
  paper/
  figures/
  slides/
  review/
  release/
```

## The artifact contract

Skills do not communicate through prose. `manifests/artifact-graph.json` is the single source of truth:

- every artifact has **exactly one** producing skill;
- every skill input is either an artifact id, an `external:` input, or a declared `feedback:` read;
- `depends_on` is **derived** from the graph and never hand-edited;
- the build graph is acyclic. Genuine runtime loops (tree search reading accumulated findings, the integrity gate informing the reviewer) are declared as `feedback:` edges, which are validated to name real artifacts but excluded from build ordering.

`tests/validate_package.py` enforces all of the above and reports what it does not check.

## Guided interaction

At the idea gate, present 3–5 directions with: novelty status, closest prior work, mechanism delta, minimum experiment, expected value, feasibility, compute cost, kill criteria, and biggest uncertainty. The user can choose one, merge several, change constraints or request more candidates; only the affected downstream artifacts should be recomputed.

## One-click UX target

A future CLI/API can expose:

```bash
researchforge run <paper-url-or-file> --mode guided
researchforge run <paper-url-or-file> --mode auto --budget-usd 100 --gpu-hours 20
researchforge resume <project-dir>
researchforge export <project-dir>
```

This ZIP is a **skills/specification package**, not that runtime implementation. `IMPLEMENTATION_PLAN.md` defines the recommended coding order.

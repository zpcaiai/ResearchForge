# ResearchForge

**v2.7.0.** Licence issuing and enforcement, measured PDF ingestion quality, an acceptance harness, a state-of-the-art comparison arm, and the innovation engine's benchmark — corpus, contamination floor, scorer and blind-rubric instrument — plus the B-arm packet. v2.6.0 and v2.7.0 are adversarial audits: 32 confirmed defects across the new tools and the older planes, including one where declaring a state-of-the-art candidate deleted every measurement in the experiment. See `CHANGELOG_v2.md`.

Self-hosted research runtime. One paper in; a ranked portfolio of research directions, a human
decision, and — as the remaining batches land — code, experiments, a manuscript and a defense deck out.

```bash
researchforge run paper.pdf --project ./my-project
researchforge select --project ./my-project
researchforge status --project ./my-project
researchforge doctor
```

## Measuring the innovation engine

`benchmarks/retro-v1/` holds the retrospective benchmark: seed papers paired with the work that
actually beat them afterwards, derived from archived Papers With Code leaderboards. Read its
README before reading any score from it — the contamination floor is **0.71**, and a system at or
below that number has demonstrated memory, not judgment. `benchmarks/retro-v1/SCORING.md` is how to
score a system against it; `tools/benchmark/blind_rubric.py` is the other half, the one recall@k
cannot supply.

## What is actually built

This repository is honest about its own completeness, because the failure it is designed against is
a system that reports work it did not do.

**All 32 skills are implemented. There are no stubs.** A full run traverses all 16 states, and on
the bundled fixture it executes 21 real experiments, computes real statistics, renders real vector
figures, builds a real native PowerPoint deck, and is then **refused by the release gate** — which is
the correct outcome for a run whose prose came from the offline model.

| plane | what actually runs |
|---|---|
| intake | repo + git init, sandbox provisioning, PDF/HTML ingestion with anchored locators, paper model, contribution atoms |
| evidence | provider registry with real rate-limit envelopes, quota ledger, measured coverage with named blind spots, search, citation resolution, claim/evidence graph |
| reproduction | clone, dependency detection, environment capture, RL0–RL4 grading, fixed failure taxonomy, comparison-mode degradation |
| innovation | three seed-mining modes, portfolio under mode constraints, novelty + feasibility, Pareto ranking, human gate |
| planning | blueprint whose *shape* follows the comparison mode, falsifiable specs with invalid conditions, ablations, an isolated grader with hidden tests |
| execution | code generation, bounded repair with a hard cap, sandboxed runs under rlimits, append-only ledger, provenance |
| analysis | leakage checks before any statistic, Hedges' g with intervals, Holm–Bonferroni, fabrication detection against the raw ledger, findings including negative results |
| writing | argument spine before prose, per-paragraph claim binding, claim/citation audit, reviewer simulation |
| artifacts | SVG figures verified against the analysis by reading numbers back off the rendered artists, native PPTX, release gate |

The gates are the product. Reproduction runs before ideation; coverage is measured before novelty is
claimed; a number in the draft is traced to the run that produced it or marked fabricated; a figure
that disagrees with its own data is refused; nothing synthetic can be released.

## The three things this design is actually about

**Reproduction runs before ideation.** `IDEAS_READY` is unreachable except through
`REPRO_LEVEL_ESTABLISHED`. An idea whose feasibility and delta were estimated against a code base
nobody tried to run is not an estimate.

**A failed reproduction narrows the project instead of ending it.** The achieved level (RL0–RL4)
sets the comparison mode, and the comparison mode sets which innovation modes remain open:

| RL | comparison mode | admissible innovation modes |
|---|---|---|
| RL3–RL4 | `CM_MEASURED` | all six |
| RL2 | `CM_RELATIVE` | all six, deltas stated relatively |
| RL1 | `CM_REPORTED` | diagnose, evaluate, systemize, guarded transfer |
| RL0 | `CM_NONE` | explain/diagnose and benchmark/evaluate |

At RL0 the run keeps going and produces a non-empty portfolio — of reproducibility studies,
negative-result reports and evaluation-methodology work. The failure taxonomy it just generated is
the evidence for them.

**Coverage is measured, so "we did not find prior work" cannot be confused with "we could not
look."** Under `UNKNOWN_COVERAGE` no candidate may be certified `NOVEL_ENOUGH` — by rule, not by
judgment.

## Architecture

```
manifests/artifact-graph.json     the contract: 128 artifacts, one producer each
        │
        ├─ tools/codegen ──► packages/contracts/src/generated.ts   (TypeScript)
        └─ tools/codegen ──► python/researchforge/generated.py     (Python)

packages/cli        TypeScript: state machine, gating, human interface, licensing
python/researchforge  Python: skills, artifact store, providers, provenance
```

The TypeScript layer exists to own orchestration and the human gate. Its cross-language boundary is
the one place a contract violation would otherwise go unnoticed, so the boundary is *generated from
the contract* rather than described by it. Both sides fail on the same violation for the same reason.

Each skill runs as its own subprocess. Skills execute untrusted generated code and can hang or die;
process isolation makes that a normal observable outcome rather than a corrupted shared runtime, and
makes every invocation reproducible from its request JSON alone.

## The artifact store is where the contract stops being a document

A skill physically cannot write an artifact it does not own, or read one it did not declare.

```
ContractViolation: skill 'idea-ranker' tried to write 'paper_model',
which is produced by 'paper-model-builder'. Every artifact has exactly one
producer; two writers means no one owns whether it is correct.
```

Schemas are enforced on write, not on read, so a malformed artifact never reaches a consumer.

## BYOK

No key ships with this. Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; set
`RESEARCHFORGE_CONTACT_EMAIL` so scholarly APIs apply polite-pool limits rather than throttling you.

`--model offline` runs the whole pipeline without a model. Every artifact it touches is stamped
`synthetic: true`, every result reports `[SYNTHETIC]`, and the release gate treats it as a blocker.
It exists to exercise the machinery. It is not a demo mode and it is not research.

## Worked example

One command, from a paper to a refused release, with no GPU and no model key:

```bash
./examples/attention-length-generalization/run.sh /tmp/example
```

It traverses all 16 states, runs 21 real experiments, and is refused by the release gate because the
prose came from the offline model. See that directory's README for why you have to write `impl/`.

## Install

```bash
npm install && npm run build          # TypeScript
pip install -e python                 # Python core
node packages/cli/dist/cli.js doctor  # check the wiring
```

## Test

```bash
npm test        # 13 contract, ordering and licensing tests
npm run test:py # 207 behavioural tests, most of them asserting a refusal
```

The Python suite is mostly tests that something is correctly *refused*: an abstract page is reported
as insufficient; zero search results do not become evidence of novelty; a comparative experiment is
not compiled under CM_NONE; no metric is invented when isolation is unavailable; a leak is a blocker;
runs scored by different evaluator versions are refused for aggregation; a real-but-irrelevant
citation is NOT_SUPPORTED; a number with no run behind it is FABRICATED; a slide number bound to no
artifact blocks the deck; a skill edit that improves train but regresses held-out is rejected.

## Licensing

Self-hosted, offline-verified, Ed25519. No phone-home: in these environments egress is a compliance
question, and a six-hour run must not die because a licence server was unreachable.

The gate is real and enforced on **both** sides of the language boundary — `python/researchforge/
licensing.py` as well as the orchestrator — because `researchforge.runner` is invokable directly and
a gate that lives only upstream gates nothing. It cannot be made unbypassable in self-hosted
software, and pretending otherwise produces hostile DRM that punishes honest users. What it does
instead: refuse by default, and make an override an explicit, *recorded* act that surfaces in the
run's provenance and its release manifest.

Issuing lives in `tools/license/` — `keygen.mjs`, `issue.mjs` (self-checks every licence before
emitting it), and `server.mjs` (admin-token gated, rate-limited, append-only issuance ledger, never
logs the key). Run it where the customer cannot reach it.

Community includes ingestion, literature, reproduction, the innovation engine and the human gate —
everything up to the decision a person should make. `docs/COMMERCIAL.md` argues why that is the right
free tier and proposes prices; it is a proposal, and it marks every place it had to guess.

## Measured quality, including where it is bad

- `docs/study/FINDINGS.md` — 20 real papers probed. **40% cannot begin reproduction at all** (no code,
  or no environment spec anywhere in the tree). The frozen decision rule was NOT triggered, and the
  document explains why claiming otherwise would have been the exact error the study exists to prevent.
- `docs/study/APRIME_FINDINGS.md` — the "dependency time machine" this project recommended was built
  and tested on the same 20 papers. **It produced zero real lift.** The recommendation was wrong and
  has been corrected in the code.
- `docs/PDF_INGEST_QUALITY.md` — 18 real PDFs. Three of the defects it found are now fixed; the rest
  are listed with their measured cost.
- `acceptance/` — the rubric and grader for the full run on real keys and a GPU. That run has not
  been performed; nothing here claims it has.

## Where to go next

`IMPLEMENTATION_PLAN.md` in the skills package. Do **Batch 04-pre** first: the 20-paper reproduction
study in `REPRO_STUDY_PROTOCOL.md`. Its RL distribution decides whether the rest of the plan is
viable, and it costs two weeks against a six-month execution plane.

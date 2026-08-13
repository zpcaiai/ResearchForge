# retro-v1 — the innovation engine's benchmark corpus

Version `1` · corpus sha256 `e108af6f0a2e384e` · frozen content hash `2e460ff77a211446722bdead1ab82b7c`

This is the corpus `retrospective-benchmark-builder` refused to run without. It exists so the
innovation engine can be **wrong measurably**: given a paper, does the system propose the
directions the field actually took next?

## The one number that matters

**Contamination floor: 0.75.**

Asked to recite, from memory alone, the papers that beat each seed on its own benchmark between
2020 and 2022, the model under test (`claude-opus-5`) named **3 of 3** ImageNet gold directions and
**1 of 2** CoNLL directions. It was given only the seed title, the benchmark and the window — no
corpus, no retrieval, no tools — and the probe was run in a separate context so the answers could
not leak from the session that built the corpus.

A recall@10 at or below 0.75 on this benchmark is therefore **not evidence of research judgment**.
It is evidence that the model read the literature. That is the entire reason this file exists;
without the floor the same number would have been reported as a capability.

The floor is a lower bound. It is scored by title containment, which misses paraphrase and misses
recognition that never surfaces as a title. The error direction is the dangerous one: an
undercounted floor makes memorization look like reasoning.

## How the gold set was built

Source: the archived **Papers With Code evaluation tables** (`hf://datasets/pwc-archive/files`,
via the mirror `felixleungsc/paperswithcode-data-evaluation-tables`), read through the Hugging Face
datasets-server with a server-side `WHERE` per benchmark.

- **Seed** — per benchmark, the paper holding the record on the declared metric as of
  **2019-12-31**.
- **Follow-ups** — the *record-holder chain* in **2020-01-01 … 2022-12-31**: each paper that set a
  new best after the seed, in date order, deduplicated by arXiv id. At least two required.
- **Relation** — `replaces`, and it is a recorded fact rather than a judgment: a published number
  on the same benchmark exceeding the previous record. No text was read to decide it.

Why leaderboards and not forward citations: a citation edge says one paper mentioned another, and
deciding which mentions are *material* is a judgment. A judgment made by the same model that is
about to be scored is not evidence. A leaderboard row is a published claim by a third party,
verifiable by anyone, needing no adjudication from us.

## What this benchmark cannot tell you

The record-holder chain is a **narrow** slice of "what came next". By construction it contains only
work that improved a headline number on an existing benchmark. It excludes work that diagnosed the
seed, work that refuted it, work that changed the problem, and every direction that was tried and
did not win. A system scoring 0 here has not been shown to be bad at research — it has been shown
not to predict leaderboard succession.

Three further limits, all of them live:

1. **Two seeds, five gold directions.** Small. Three of the five benchmarks queried were dropped by
   the rule, and the drops are themselves findings: nothing in the Papers With Code record beat the
   2019 leader on SQuAD1.1 dev or MultiNLI during 2020–2022, and WMT2014 En-De produced only one
   record-setting successor. Those benchmarks were saturated, and a benchmark suite that quietly
   dropped them would have hidden that.
2. **The benchmark list is a convenience enumeration.** Five widely used benchmarks, chosen by the
   operator rather than sampled from the full table. This is the one non-mechanical step in the
   construction, and it biases toward heavily-worked benchmarks whose successors are famous — which
   *raises* contamination rather than lowering it. It is recorded in `selection_rule` so no reader
   has to infer it.
3. **Every gold direction predates the model cutoff.** The held-out post-cutoff subset is empty.
   There is no part of this benchmark that the model could not have read.

## Reproducing it

```
python3 tools/benchmark/build_retro_corpus.py \
    --spec benchmarks/retro-v1/spec.json \
    --meta benchmarks/retro-v1/paper_metadata.json \
    --out  benchmarks/retro-v1/corpus.json

cat benchmarks/retro-v1/run_config.json | python3 -m researchforge.runner run \
    --skill retrospective-benchmark-builder --project <project> --offline --model offline
```

`raw/` holds the leaderboard rows exactly as retrieved, so the chain computation can be re-derived
without network access. `paper_metadata.json` carries each paper's title, publication date and a
one-line mechanism; the mechanism lines are compressions of the fetched abstracts, and each records
the URI the abstract came from. They are the only part of this corpus written rather than recorded.

## Scoring a system against it

Not done here, and it needs one thing this environment does not have: a real model provider. The
innovation engine's offline stub produces structural placeholders, and scoring placeholders would
produce a number rather than a measurement.

When you do run it: the number to beat is **not zero, it is 0.75**, and beating it by a little is
not beating it at all.

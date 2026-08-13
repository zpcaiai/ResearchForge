# retro-v1 — the innovation engine's benchmark corpus

Version `2` · corpus sha256 `20bd6243a0369fcd` · frozen content hash `1b40232d45fcc9d4195cbc203d2e436e`

This is the corpus `retrospective-benchmark-builder` refused to run without. It exists so the
innovation engine can be **wrong measurably**: given a paper, does the system propose the
directions the field actually took next?

## The one number that matters

**Contamination floor: 0.71.**

Asked to recite, from memory alone, the papers that beat each seed on its own benchmark between
2020 and 2022, the model under test (`claude-opus-5`) named **3 of 4** ImageNet gold directions and
**2 of 3** CoNLL directions. It was given only the seed title, the benchmark and the window — no
corpus, no retrieval, no tools — and the probe was run in a separate context so the answers could
not leak from the session that built the corpus.

A recall@10 at or below 0.71 on this benchmark is therefore **not evidence of research judgment**.
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
  new best after the seed, in date order, deduplicated by arXiv id. At least two required. Within
  one month the order is unknown — arXiv identifiers carry only `YYYY-MM` — so rows are processed
  ascending by value and the ambiguity is stamped on the affected follow-ups rather than resolved
  invisibly. Processing best-first, as the first version did, silently deleted the smaller of two
  same-month record setters and cost this corpus two gold directions.
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

1. **Two seeds, seven gold directions.** Small. Three of the five benchmarks queried were dropped by
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

See `SCORING.md`. Two commands and one judgment:

```
python3 tools/benchmark/score_directions.py --benchmark benchmarks/retro-v1/retro_benchmark.jsonl \
    --directions <project>/ideas/idea_portfolio.json --k 10 --emit-packet packet.json
# fill in verdicts, then
python3 tools/benchmark/score_directions.py --packet packet.json --judge-kind human \
    --judge-id <who> --out scorecard.json
```

The run itself has not been done here: it needs a real model provider. The innovation engine's
offline stub produces structural placeholders, and scoring placeholders produces a number rather
than a measurement.

When you do run it: the number to beat is **not zero, it is 0.71**, and beating it by a little is
not beating it at all.

## The other half

`tools/benchmark/blind_rubric.py` builds and analyses the blind panel: experts rate generated
directions against human-authored ones without knowing which is which. It strips the tells, fixes
the order with a recorded seed, keeps the key in a separate file, and refuses to report a
comparison whose raters could identify the arms. It needs a panel; everything else is done.

## Corrections made to v1

Version 2 is not a re-run; it is a fix. Three defects in the builder were found by an adversarial
audit and each of them made the corpus **smaller or wrong**, never larger:

- **The seed tie-break kept the later paper.** `max(pre, key=(value, date < seed_end))` — the second
  term is `True` for every pre-cutoff row, so it was constant and the tie fell through to file
  order. On the shipped MultiNLI table this picked T5 (1911) over the earlier T5-XXL (1910) at an
  exact 92.0 tie.
- **Same-month record setters were collapsed.** Sorting `(date, -value)` processed the better paper
  first within a month, after which the other no longer beat the standing record and vanished.
  ImageNet has eleven months in the evaluation window holding two or more rows; fixing this
  recovered FixEfficientNet-L2 and LUKE, taking the corpus from 5 gold directions to 7.
- **Dropped rows were invisible.** A leaderboard row linked to a non-arXiv venue carries no date
  here and leaves the pool. If such a row held the record, the recorded seed is not the record
  holder and every `replaces` claim is wrong while looking identical. Five rows are dropped across
  the shipped tables; they are now listed in `rows_dropped` so the claim can be checked.

A fourth was latent: `max()` and `>` hardcoded higher-is-better. Every benchmark here is
higher-is-better, but a WER or perplexity spec would have seeded on the *worst* method and emitted
a chain of steadily worse numbers labelled `replaces`. `direction` is now a required spec field.

# Retrospective benchmark — construction and limits

Version `2` · content hash `1b40232d45fcc9d4195cbc203d2e436e` · frozen by run `bench-2`
Contract digest `b756e68c41a6a7a4`

## Contamination floor

**0.708** — that fraction of the gold follow-ups was named when the model was asked to recite them, with no reasoning required. Probe sources: recorded_probe_transcript; declared model under test: `claude-opus-5`.

A system score at or below this number is not evidence of research judgment. The floor is a **lower bound**: title matching misses paraphrase and misses recognition that does not surface as a title, so the true contamination is at least this. The error direction is the dangerous one: an undercounted floor makes memorization look like reasoning.

| seed | probe recall | named | gold | source |
|---|---|---|---|---|
| `CoNLL 2003 (English)::1903.07785` | 0.67 | 9 | 3 | recorded_probe_transcript |
| `ImageNet::1911.04252` | 0.75 | 10 | 4 | recorded_probe_transcript |

## Construction

- domain scope: machine learning benchmarks present in the archived Papers With Code evaluation tables
- venues: (none declared)
- seed window: {"start": null, "end": "2019-12-31"}
- evaluation window: {"start": "2020-01-01", "end": "2022-12-31"}
- seed selection rule: Per benchmark, the seed is the paper holding the record on the declared metric as of 2019-12-31, taken from the archived Papers With Code evaluation tables. Follow-ups are the record-holder chain in 2020-01-01..2022-12-31: each paper that set a new best after the seed, in date order, deduplicated by arXiv id, requiring at least 2. Within a single month the order is unknown (arXiv identifiers carry only YYYY-MM) and rows are processed ascending by value, with the ambiguity stamped on the affected follow-ups. The BENCHMARK LIST ITSELF is a convenience enumeration of five widely used benchmarks (SQuAD1.1 dev, MultiNLI, CoNLL 2003 English, ImageNet, WMT2014 English-German) chosen by the operator, not sampled from the full table; that is the one non-mechanical step and it biases toward heavily-worked benchmarks whose successors are famous, which RAISES contamination rather than lowering it.
- corpus: supplied_file (sha256 `20bd6243a0369fcd303fe224fbad068e`)
- retrieval coverage at build time: **UNKNOWN_COVERAGE**

Seeds and follow-ups came from an externally supplied corpus. Scholarly APIs are unreachable from this environment, so nothing here was harvested live and nothing was invented; the corpus hash above is what the benchmark rests on.

## Adjudication

7 follow-ups accepted, 0 citations excluded. A paper that cites the seed is not a follow-up.

| followup | verdict | why |
|---|---|---|
| `2006.01563` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2010.01057` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2010.05006` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2003.08237` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2003.10580` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2203.05482` | follow_up | material relation 'replaces' with a complete direction descriptor |
| `2205.01917` | follow_up | material relation 'replaces' with a complete direction descriptor |

## Scoring

- metric: `recall@10`
- matching: rubric-adjudicated semantic match between direction descriptors
- matching is explicitly **not**: string overlap, embedding threshold, or title match
- known ceiling: unknown and below 1.0: not every good direction was published, and not everything published was good
- interpretation: comparative signal between system versions only; never an absolute measure of research quality

## Held-out subset

0 of 7 gold directions postdate the declared model cutoff. post-cutoff subset is reported separately and never used to tune.

## Blind rubric

**Not run.** recall@k may be used as a regression signal between versions. It may not be reported as a measure of research quality without a blind rubric in which experts score generated directions against human-authored ones. The retrospective metric catches regressions cheaply; the rubric is what establishes the output is worth anything. Neither substitutes for the other.

## Freeze

This benchmark is frozen at `1b40232d45fcc9d4195cbc203d2e436e`. Post-hoc modification is rejected by `retrospective-benchmark-builder`; a revision must be a new version and requires re-running every system against it.

## Warnings at build time

- contamination floor is 0.71: the model named 71% of the gold follow-ups when asked directly. Any system recall@10 at or below this measures recall, not reasoning.
- no blind human rubric result was supplied. recall@k may be reported as a regression signal between versions, but not as evidence that the output is worth anything; the two do not substitute for each other.
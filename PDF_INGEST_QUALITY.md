# PDF ingest quality — measured on 18 real paper PDFs

**What this is.** Until now the only ingest fixture was an arXiv *abstract page*
(207 words), which `paper-ingest` correctly refuses. Nobody had measured what
`PaperIngest` / `PaperModelBuilder` do to a real full-text paper. This document
is that measurement. It is an assessment, not a fix: no source file was changed.

**Corpus.** 18 real paper PDFs, 375 pages, 190 622 extracted words, from 14
different GitHub repositories (arXiv, OpenAlex, Crossref and Semantic Scholar are
all proxy-blocked from this container; `git clone` is not, and many research
repos check their own paper into the tree). Layout families covered: ICML/PMLR
two-column (6), NeurIPS/ICLR-ish single-column preprint (7), IEEEtran
two-column (2), tech report with a table of contents (2), an HTML-print-to-PDF
essay (1). Three of the 18 are committed under `fixtures/papers/` — see
`fixtures/papers/SOURCES.md` for provenance and licence status.

**Headline.** The pipeline extracts text well and anchors offsets exactly. Almost
everything built *on top of* that text is wrong on the majority of real papers,
and — this is the problem — it is wrong **silently**. `layout_warnings.md` says
`- none: text extraction produced anchored, ordered content` for every one of the
17 PDFs that did not crash, including the ones where it detected 2 of 11
sections, 0 of 9 figures, and spliced a journal footer into the middle of a
quoted claim.

---

## 1. Per-PDF numbers

`sec` = sections detected by `HEAD_RE` / top-level sections actually in the paper
(hand-checked for `pb_pinn`, `unimoe_audio`, `livetalk`, `data_agents_survey`;
counted from line-anchored numbered headings elsewhere).
`fig`/`tab` = indexed by `_captions` / actually present.
`clm` = claims found; `apx` = of those, how many lie past the References heading;
`q` = flagged `quantitative`.
`anch` = `locator_map` anchors (compare to `pg`).

| PDF (repo) | MB | pg | words | empty pg | sec | fig | tab | clm | apx | q | anch | title verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `pb_pinn` (zjunlp/xKG) | 1.6 | 33 | 13 707 | 0 | **2 / 11** | 9/10 | 2/3 | 23 | 8 | 0 | 33 | correct |
| `pb_bam` (zjunlp/xKG) | 4.0 | 40 | 19 281 | 0 | **2 / 8** | 6/6† | 0/0 | **60 (capped)** | 35 | 0 | 40 | correct |
| `pb_cfg` (zjunlp/xKG) | 2.0 | 38 | 16 914 | 0 | **2 / 14** | 22/23 | 17/18 | 31 | 11 | 1 | 38 | correct |
| `pb_robust_clip` (zjunlp/xKG) | 5.0 | 20 | 13 686 | 0 | **2 / 7** | 5/5 | 10/14 | 24 | 10 | 0 | 20 | correct |
| `pb_tta` (zjunlp/xKG) | 1.3 | 18 | 13 204 | 0 | **2 / 7** | 4/4 | 14/17 | 18 | 5 | 0 | 18 | correct |
| `pb_stochastic_interpolants` (zjunlp/xKG) | 9.7 | 17 | 7 813 | 0 | **2 / 6** | 6/6 | 2/3 | 12 | 0 | 0 | 17 | correct |
| `lopa` (zhijie-group/LoPA) | 0.6 | 12 | 6 643 | 0 | **2 / 7** | 5/5 | 8/7‡ | 2 | 1 | 0 | 12 | correct |
| `unimoe_audio` (HITsz-TMG/Uni-MoE) | 2.9 | 11 | 9 429 | 0 | **1 / 8** | **0/5** | **0/4** | 15 | 0 | 0 | 11 | **wrong** (LaTeX header) |
| `data_agents_survey` (HKUSTDial) | 5.6 | 31 | 28 419 | 0 | **1 / 8** | **0/8** | **0/4** | 4 | 0 | 0 | 31 | **wrong** (LaTeX header) |
| `dirl` (OpenMOSS/DiRL) | 1.2 | 12 | 5 850 | 0 | 7 / 8 | 7/8 | 1/1 | 9 | 0 | 0 | 12 | truncated + logo |
| `drive_rlvr` (Tencent-Hunyuan) | 5.3 | 15 | 6 483 | 0 | 8 / 7 | 8/8 | 5/5 | 10 | 2 | 3 | 15 | truncated at line wrap |
| `livetalk` (GAIR-NLP) | 17.4 | 16 | 9 175 | 0 | 11 / 7 (4 dupes) | 4/4 | 4/4 | 12 | 0 | 0 | 16 | correct |
| `longcat_avatar15` (meituan-longcat) | 18.5 | 24 | 9 898 | 0 | 5 / 9 | 25/25 | 2/2 | 13 | 0 | 0 | 24 | correct |
| `moss_speech` (OpenMOSS) | 7.4 | 21 | 8 797 | 0 | 5 / 9 | 4/4 | 7/7 | 9 | 0 | 0 | 21 | truncated + logo |
| `reinforce_ada` (RLHFlow) | 1.3 | 16 | 7 160 | 0 | 5 / 7 | 4/6 | 2/2 | 3 | 0 | 0 | 16 | truncated at line wrap |
| `bitter_lesson` (OpenDCAI/DataFlow) | 0.1 | 2 | 1 305 | 0 | 0 / 0 | 0/0 | 0/0 | 0 | 0 | 0 | 6 | **wrong** (print header) |
| `interplay` (Interplay-LM-Reasoning) | 18.0 | 15 | **0** | **15** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | not detected — **correctly warned** |
| `qwenlong` (Tongyi-Zhiwen/Qwen-Doc) | 4.0 | 34 | 12 858 | 0 | — | — | — | — | — | — | — | **CRASH, exit 20** |

† `pb_bam` numbers its figures `5.1 … 5.6`; all six index entries collapse to id
`f5` and their "caption" text begins with the real sub-number.
‡ `lopa` gets 8 table entries for 7 tables: one is an in-text cross-reference
that happened to start a line.

Aggregates over the 17 PDFs that produced text:

| measure | value |
|---|---|
| claims extracted | 258 |
| claims located past the References heading (appendix / bibliography) | **72 (28 %)** |
| claims flagged `quantitative` | **4 (1.6 %)** — while 148 (57 %) contain a digit |
| claims truncated mid-sentence at an abbreviation (`Fig.`, `Tab.`, `et al.`, `eq.`) | **19 (7.4 %)** |
| `CLAIM_CUE` hits lost because the cue straddles a line break | 14 of 294 (4.8 %; up to **14.3 %** on `pb_tta`) |
| PDFs where `locator_map` anchors == page count exactly | **17 of 17** |
| PDFs where `layout_warnings.md` says "none: … anchored, ordered content" | **17 of 17** |
| titles exactly right | 9 / 18 |
| titles truncated at a line wrap or prefixed with logo text | 4 / 18 |
| titles completely wrong / undetected | 5 / 18 |
| PDFs whose embedded metadata carries the correct title (never read) | 8 / 18 |

---

## 2. Where it works

1. **Text extraction itself is fine.** pypdf follows the content stream, and on
   these LaTeX PDFs that means column order is *correct* — I checked
   `pb_pinn`, `pb_cfg` and `pb_robust_clip` by hand and body text runs
   left-column-then-right-column as written. The "two-column interleaving"
   failure I went looking for is **not** the failure that is here.
2. **The scanned/vector-only detector works, and it saved a run.**
   `interplay.pdf` (15 pages, all vector text) extracted 0 words and produced
   both warnings — the 15/15 empty-pages warning and the <300-word warning. The
   downstream skills still ran and produced an empty paper model, but the run is
   at least labelled.
3. **The <300-word abstract gate does not misfire.** Every real paper cleared it
   (min 1 305 words), so it is not producing false alarms.
4. **Claim character offsets are exact.** `text.startswith(claim, offset)` holds
   for all 258 claims. `text.find()` on a duplicated sentence would be wrong, but
   in practice no claim sentence repeated (6 duplicate *paragraph* anchors exist
   in `pb_bam`, but those affect `locator_map`, not claims).
5. **The thin-section-map warning fires where it matters.** All eight papers with
   ≤2 detected sections got `only N sections detected …`. That is the single most
   valuable warning in the pipeline today.
6. **Single-column preprints with `N Title` headings degrade gracefully.**
   `drive_rlvr`, `dirl`, `moss_speech`, `livetalk`, `longcat_avatar15` all got a
   usable section map, correct figure/table indexes, and mostly-real claims.

---

## 3. Where it silently produces garbage

### 3.1 A period after the section number makes every heading invisible

`HEAD_RE` is `^\s*(?:(\d+(?:\.\d+)*)\s+)?(abstract|introduction|…)\s*$`. After the
number it demands whitespace. ICML/PMLR and ICLR styles write `1. Introduction`.
The `.` is not whitespace, so the numbered alternative fails; and `1. Introduction`
is not literally `introduction`, so the unnumbered alternative fails too.

Result on the six PaperBench ICML papers plus `lopa`: **exactly two sections are
ever detected — `Abstract` and `References`.** `pb_pinn` has
`1. Introduction … 9. Conclusion` sitting in the text, plus appendices. The
section map contains neither.

This is one character of regex. It is also the root cause of §3.2.

### 3.2 Every claim is then filed under "Abstract" or "References"

`_claims.sect_of(off)` assigns a claim to the last detected heading before it.
With only `Abstract` (offset 127) and `References` (offset 38 959) detected, the
attribution is mechanical and completely wrong:

| paper | claims in "Abstract" | claims in "References" |
|---|---:|---:|
| `pb_pinn` | 15 | 8 |
| `pb_bam` | 25 | 35 |
| `pb_cfg` | 20 | 11 |
| `pb_robust_clip` | 14 | 10 |
| `pb_tta` | 13 | 5 |
| `pb_stochastic_interpolants` | 12 | 0 |

`pb_stochastic_interpolants` is the purest case: **12 of 12 claims are labelled as
living in the Abstract.** One of them actually does. On `unimoe_audio` and
`data_agents_survey` every claim gets `section: "s?"` instead.

Corpus-wide, **189 of 258 claims (73 %) carry a section label that is wrong or
unknown.** A downstream skill that reasons "this claim is in the Abstract, so it
is a headline contribution" or "this claim is in the References, so ignore it"
gets the opposite of the truth in both directions.

### 3.3 28 % of "claims" are appendix proof bookkeeping

`_claims` has no notion of where the paper ends. It walks the whole extracted
string, including the bibliography and every appendix, and it caps at 60 with a
`break` — so on a theory paper the appendix *crowds out* the real claims.
`pb_bam` hits the cap: 60 claims, **35 of them past the References heading**, and
15 further cue hits are dropped by the cap. Verbatim, from
`pb_bam` `paper_model.claims`:

> **C-036** — `(98) and (101), we find that bDqt(q;p) =tr(ΓΣ) + tr(CΣ−1) +1 (1 +λt)2  µt−z−Σg  2 Σ−1+constant , (104) KL(qt;q) =1 2 tr(Σ−1Σt)−log|Σt| |Σ|+λ2 t (1 +λt)2  µt−z−Σg  2 Σ−1−D .`

> **C-050** — `In particular, as we show in Lemma D.13 of section D.8, it is the case that |νK−1| ≤1 1+λtνJ|νJ−1|.`

> **C-043** — `In addition to the normalized covariance matrices {Jt}∞ t=0, we introduce two sequences of auxiliary matrices, {Ht}∞ t=1and{Kt}∞ t=1satisfying 0≺Ht+1⪯Jt+1⪯Kt+1 (152) for all t≥0; this is what we call the sandwiching inequality.`

> **C-053** — `(210) with respect to ν, we find that λξ2+ 2λ ν+ε2 1+λ ξf′(ν) +f′(ν)−(1+λ) = 0 , (212) 32  Batch and match: black-box variational inference with a score-based divergence where ξ=f(ν).`

C-053 also has the running footer (page number `32` + running title) welded into
the middle of it.

### 3.4 Claim precision: 60 % on a hand-judged sample

I drew a seeded random sample of 40 of the 258 claims and judged each one:

- **24 (60 %)** are genuine paper-level claims, cleanly quoted.
- **14 (35 %)** are false positives — the cue matched, but the sentence is
  methodology narration, a pointer, or derivation bookkeeping.
- **2 (5 %)** are genuine claims whose *quoted text* is corrupted (truncated at
  an abbreviation, or with a heading spliced in).

False positives, verbatim:

> `pb_pinn` **C-016** — `Additional Details on Problem Setup Here we present the differential equations that we study in our experiments.`
> *(not a claim, and the appendix heading has been welded onto the front of the sentence)*

> `pb_pinn` **C-017** — `We find the learning rate η⋆for each network width and optimization strategy that attains the lowest loss (L2RE) across all random seeds.`
> *("find" as in a hyperparameter sweep, not as in a finding)*

> `pb_cfg` **C-031** — `In the current setup (we show a humorous example), we apply CFG to an virtual assistant.`

> `pb_cfg` **C-026** — `We explore this idea further in Table 12, where we show the datasets that CFG shows similar behavior to Instruction- tuning.`

> `drive_rlvr` **C-010** — `Given the extensive length of the RL model's generated response in this case, we present only the analysis conducted by GPT-5, which identified numerous repetition patterns throughout the reasoning process.`

> `qwenlong` **C-007** — `4 Long-Context Post-Training We introduce our overall post-training paradigm based on reinforcement learning for long-context reasoning in Section 4.1.`

> `moss_speech` **C-007** — `6  MOSS-Speech: Towards True Speech-to-Speech Models Without Text Guidance 4 Evaluation 4.1 Tokenizer In this section, we present the experimental evaluation of our encoder and decoder components.`
> *(page number + running title + two section headings, then a procedural sentence)*

Corrupted-but-genuine, verbatim:

> `unimoe_audio` **C-005** — `Second, we present a hybrid expert design to establish clear functional specialization, comprising: (1) conditional routed experts for domain-specific knowledge; (2) constantly active shared experts to handle domain-agnostic  JOURNAL OF L ATEX CLASS FILES, VOL.`
> *(the sentence is cut off by the next page's running footer, and the splitter then ends the "sentence" at `VOL.`)*

> `livetalk` **C-001** — `We observe Self Forcing can result in extensive visual artifacts, e.g., flickering effects (see row 1 of Fig.`
> *(`(?<=[.!?])\s+` splits on `Fig.` — 19 claims corpus-wide end this way)*

> `pb_pinn` **C-012** — `Adam+L-BFGS Optimizes the Loss Better Than Other Methods We demonstrate that the combined optimization method Adam+L-BFGS consistently provides a smaller loss and L2RE than using Adam or L-BFGS alone.`
> *(real claim; section heading prepended)*

### 3.5 `quantitative` is effectively always False — 4 of 258

`NUM_RE = \b\d+(?:\.\d+)?\s*(?:%|BLEU|F1|AUC|mAP|accuracy|points?|x\b)`. It only
fires on a number glued to that closed list. In practice:

- `pb_pinn` claims 21 of 23 sentences with a digit, and **0** are quantitative —
  its headline number is rendered `1000 ×` (U+00D7, not the ASCII `x` the regex
  wants) and its metric is `L2RE`, which is not in the list.
- Only `drive_rlvr` (3) and `pb_cfg` (1) ever fire, both on `%`.

`_atoms` uses this flag as its only signal: `kind = "empirical" if quantitative
else "conceptual"`, and non-quantitative claims stop being admitted past 12 atoms.
So **contribution atoms on 15 of 17 papers are 100 % `conceptual`** and are simply
"the first 12 cue-matching sentences in reading order".

### 3.6 Figure/table indexing is blind to two of the three common caption styles

`_captions` compiles `^\s*(figure|table)\s+(\d+)\s*[:.]`, line-anchored, arabic
numerals only.

- **IEEE style is invisible.** `unimoe_audio` has `Fig. 1:` … `Fig. 5:` and
  `TABLE I:` … `TABLE IV:`. Indexed: **0 figures, 0 tables**, no warning.
  `data_agents_survey` the same: 0 of 8 `Fig. N:` figures and 0 of 4 `TABLE I:`–`TABLE IV:` tables.
- **Sub-numbered figures collapse.** `pb_bam` uses `Figure 5.1:`; the regex
  captures `5`, consumes the `.` as the separator, and hands back a caption that
  starts `1: Gaussian targets…`. Six entries, all with `id: "f5"`.
- **In-text cross-references become captions** whenever they start a line:
  `pb_bam` f5 = `2 and Figure E.4. Here we use a decaying learning`;
  `pb_cfg` f11 = `Figure 4: Evaluators (611 votes, 71 voters) noted that`.
- **Mid-line captions are lost**: `pb_robust_clip` indexes 10 of 14 tables,
  `pb_tta` 14 of 17.

### 3.7 `locator_map` calls pages "paragraphs"

`_extract` joins pages with `"\n\n"`; `_locators` splits on `\n\s*\n`. A LaTeX PDF
emits no blank line inside a page, so **anchor count == page count on 17 of 17
PDFs** (33/33, 40/40, 31/31, 11/11 …). Median anchor is 2 100–6 500 characters
(350–1 000 words); the largest is 10 029 characters.

The artifact nevertheless declares `"granularity": "paragraph"`, and
`paper_model.locators.granularity` repeats it. The `preview` field — the first 80
characters, which a human would read as "the start of the cited passage" — is in
most cases the page's running header:

```
p1 preview: 'JOURNAL OF L ATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 2'
p1 preview: '1. Introduction\nSII-GAIR\n1 Introduction\nDiffusion transforme'
```

### 3.8 `metrics` / `methods` / `datasets` are noise, and they are used as search queries

```
pb_pinn.metrics  = ['1X', 'Accuracy', 'accuracy', 'map', 'points', 'x']
pb_pinn.methods  = ['architectures', 'architecture', 'algorithm', 'Algorithms',
                    'Loss', '1100LossWave', 'Loss10', '1101LossConvection',
                    '1LossReaction', 'losses', '2LossConvection', '1LossWave']
pb_pinn.datasets = ['Benchmark']

drive_rlvr.metrics = ['1.32%','1.37%','11.52%','12%','12.35%','12.5%','13%','13.0%',
                      '15.17%','15.6%','16.06%','16.1%','17.3%','18.79%', …]

unimoe_audio.datasets = ['dataset','datasets','corpus','benchmarks','benchmark']
bitter_lesson.methods = ['colossal']          # 'colossal' contains 'loss'
```

Three separate defects:
- `metrics` keeps `m.split()[-1]` of a `NUM_RE` match, i.e. the *value*, not the
  name. On `drive_rlvr` "metrics" is a list of percentages. The regex's `mAP`
  alternative is case-insensitive, so the English word **"map"** is a metric.
- `methods` does unanchored substring matching (`\w*loss\w*`), so Figure 1's axis
  label `1100LossWave` is a method, and `colossal` is a method.
- `datasets` returns morphological variants of the cue words themselves. Not one
  real dataset name was recovered from any of the 18 papers.

### 3.9 Titles

`_title` returns the first line of 15–200 characters that does not start with
`arxiv`/`abstract`/`http`. That is the running header on IEEE templates, the
browser print header on an HTML-to-PDF, and a line-wrapped fragment whenever the
title spans two lines:

```
unimoe_audio        'JOURNAL OF L ATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 1'
data_agents_survey  'JOURNAL OF L ATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 1'
bitter_lesson       '2/3/22, 9:31 PM The Bitter Lesson'
qwenlong            'December 14, 2025'
drive_rlvr          'DRIVE: Data Curation Best Practices for Reinforcement Learning wIth'   # ← "Verifiable Rewards" lost
reinforce_ada       'Reinforce-Ada: An Adaptive Sampling Framework for'                     # ← rest lost
dirl                'OpenMOSSDiRL: An Efficient Post-Training Framework for Diffusion Lan-' # ← logo alt-text + hyphen
```

**8 of 18 PDFs carry the correct title in `PdfReader.metadata['/Title']`, which
the pipeline never reads** — including `drive_rlvr`, where metadata has the full
`…Reinforcement Learning with Verifiable Rewards`.

### 3.10 A real PDF hard-crashes the pipeline

`qwenlong_l1_5.pdf` page 7 contains math-italic glyphs (`U+1D400` block) that
pypdf emits as **unpaired UTF-16 surrogates** (`\ud835`, 9 of them). `PaperIngest`
hands the string to `ArtifactStore.write`, which does `payload.encode()`:

```
UnicodeEncodeError: 'utf-8' codec can't encode character '\ud835'
                    in position 23393: surrogates not allowed
```

The runner reports `kind: "internal"` with a traceback and exit code 20 — the
generic "the skill crashed" path. No artifact is written; `paper-model-builder`
then fails with a contract violation blaming a missing producer. **1 of 18 real
PDFs (5.6 %) is un-ingestable, and the failure is indistinguishable from a bug in
the runtime.**

### 3.11 The false all-clear

For all 17 non-crashing PDFs, `source/layout_warnings.md` reads exactly:

```
- none: text extraction produced anchored, ordered content
```

The PDF branch of `_extract` can only ever emit one warning (the scanned-page
one). It has no check for column layout, float/footnote interleaving,
hyphenation, running headers, or caption style — so on a paper where the section
map is 2/11 and the figure index is 0/5, the artifact that exists to describe
layout risk affirmatively states there is none. The `paper-model-builder`
warnings that *do* describe the damage live only in the `SkillResult` and the
provenance log; nothing writes them into an artifact, so a skill reading
`section_map.json` sees `detector: "regex/heading-v1"`, two sections, and no
caveat at all.

---

## 4. What a downstream skill would wrongly believe

Traced against `evidence.py`, which is the first real consumer.

1. **`LiteratureSearch._queries`** builds its query set from
   `model.title`, then `f"{title} {m}" for m in methods[:3]`, then
   `datasets[:3]` as standalone queries. On `pb_pinn` that is:
   `"Challenges in Training PINNs: A Loss Landscape Perspective architectures"`,
   `… algorithm`, and the bare query `"Benchmark"`. On `unimoe_audio` the primary
   query is `"JOURNAL OF L ATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 1"`.
   The provider search is a no-op offline today, so this is currently invisible —
   it will become a silent recall failure the moment a provider is wired up, and
   the resulting empty result set feeds `coverage_report`, which feeds the
   novelty verdict.
2. **`LiteratureSearch.benchmark_matrix`** emits one row per
   `model.metrics[:20]` with the header `benchmark,reported_by,value,source`. On
   `drive_rlvr` the `benchmark` column is literally `1.32%`, `1.37%`, `11.52%`, …
   — a benchmark matrix whose benchmarks are percentages. On `pb_pinn` one row is
   the benchmark `"map"`.
3. **`ClaimEvidenceGraph`** turns every extracted claim into a `claim_registry`
   entry with `origin: "source_paper"` and the locator verbatim. For `pb_bam`
   that registry contains 35 entries whose "claim text" is appendix algebra and
   whose stated location is the bibliography. The `claim_type` is
   `"empirical" if quantitative else "conceptual"` — so 254 of 258 real-paper
   claims are typed `conceptual`, including `"…improvements ranging from 13 % to
   58 % across various benchmarks"` where the numbers happen not to match.
4. **`claim-citation-auditor` / manuscript quoting.** Any skill that quotes a
   claim verbatim will emit `… to handle domain-agnostic JOURNAL OF L ATEX CLASS
   FILES, VOL.` or a sentence that stops at `(see row 1 of Fig.`.
5. **Anything that trusts a locator.** "Anchor p7, granularity paragraph" is a
   whole page. A reviewer asked to check a claim at `p7` is pointed at ~800 words
   and a preview line that is the running header.
6. **`PaperModelBuilder.limitations`** was empty on all 18 papers, including the
   four that have an explicit limitations discussion (`pb_robust_clip`'s
   `Limitations.` paragraph, `qwenlong`'s `7 Limitations and Future Works`). A
   consumer reading `paper_model.limitations == []` would conclude the authors
   declared none.

---

## 5. Prioritised fix list

Ordered by (harm × cheapness). Nothing below has been implemented.

| # | Fix | Why first | Rough cost |
|---|---|---|---|
| **1** | **Sanitise lone surrogates in `_extract` before writing** (`text.encode("utf-8","replace").decode()`), and raise `GateBlocked` with a remediation if the text is still undecodable. | 1 in 18 real PDFs is currently un-ingestable and reports as an internal crash. Anything else is moot on that paper. | 2 lines + a warning |
| **2** | **Accept `1.` in `HEAD_RE`** (`(?:(\d+(?:\.\d+)*)[.)]?\s+)?`), accept roman numerals, and normalise small-cap kerning (`I NTRODUCTION` → `INTRODUCTION`) before matching. | One regex change takes 7 papers from 2 detected sections to ~9, and fixes the 73 % wrong claim-section attribution in §3.2 for free. | ~1 hour |
| **3** | **Stop mining claims past the References heading**, and drop the silent `break` at 60 in favour of a warning that says how many cue hits were discarded. | Removes 28 % of claims that are structurally guaranteed not to be contributions; stops the appendix crowding out the conclusion. | ~1 hour |
| **4** | **Make `layout_warnings.md` tell the truth.** It must never say "none" unless something was actually checked. Emit measured signals: pages-per-anchor, `%` of lines ending in a soft hyphen, whether any `Fig.`/`TABLE [IVX]` string exists while the caption index is empty, whether a running header repeats on ≥3 pages. | This is what turns every other defect from silent to visible. It is also the cheapest way to stop a downstream skill trusting a bad model. | ~half a day |
| **5** | **Read `PdfReader.metadata['/Title']` first**, fall back to the heuristic, and join a title that wraps across lines. | 8/18 papers get an exactly correct title for free; 4 more stop being truncated. The title is `literature-search`'s primary query. | ~1 hour |
| **6** | **Rename the locator granularity to `page`** (or split anchors on a real paragraph heuristic: indentation / sentence-final line + short line). Even the rename alone stops the artifact lying. | Every citation the system produces is currently mislabelled by ~an order of magnitude in span. | rename: minutes; real paragraphs: ~1 day |
| **7** | **Widen `_captions`** to `(figure|fig\.|table|tab\.)\s+(\d+(?:\.\d+)?|[ivxlIVXL]+)\s*[:.]`, drop the line anchor in favour of "followed by ≥30 characters of caption-like text", dedupe by id, and warn when the index is empty but caption-like strings exist. | 0/9 floats on IEEE papers, with no warning, is the most confidently-wrong artifact in the set. | ~half a day |
| **8** | **Delete or rebuild `metrics`/`methods`/`datasets`.** As built they are substring noise that is fed to a search API. Minimum: require word boundaries and a capitalised head noun; better: take them from a model call over the abstract + results sections, or drop the fields and let downstream skills say "unknown". | They are actively used as literature-search queries and as `benchmark_matrix` rows. Noise here becomes a fabricated benchmark table. | ~1 day |
| **9** | **De-hyphenate at line breaks** (`([a-z])-\n([a-z])` → `\1\2`) as a normalisation pass, and make `CLAIM_CUE`/`NUM_RE` whitespace-tolerant (`\s+` instead of a literal space). | Recovers up to 14 % of cue hits on a single paper and unbreaks term matching. Must be done *after* offsets are decided, or offsets shift. | ~2 hours |
| **10** | **Sentence splitting**: guard against `Fig.`, `Tab.`, `Sec.`, `eq.`, `et al.`, `e.g.`, `i.e.`, `Eq.`, and a `[A-Z].` initial. | 7.4 % of claims are currently quoted as sentence fragments. | ~1 hour |
| **11** | **Strip running headers/footers** — a line that recurs on ≥3 pages at the same relative position is furniture, not prose. | Removes the `JOURNAL OF L ATEX CLASS FILES` class of corruption at the source, which also fixes titles, anchors' previews and claim text. | ~half a day |
| **12** | **Reconsider the `quantitative` flag.** `\d` + a unit-ish token, or simply "contains a numeral outside a citation", would be closer to the truth than the current closed list. | 4/258 today; it decides `empirical` vs `conceptual` for the whole evidence graph. | ~1 hour |

### Not a finding

I went in expecting two-column interleaving. It is not happening — pypdf's
content-stream order matches reading order on every two-column PDF here. The
column-layout damage is real but different: **floats, footnotes, plot axis labels
and running headers get spliced into the middle of body sentences**, because they
are separate text runs that happen to sit between the two halves of a paragraph
in the content stream. Fixing that needs position-aware extraction
(`pdfplumber`/`PyMuPDF` word boxes with a column split), not a re-ordering pass —
which is why it is not in the top of the list above; items 1–5 are cheaper and
buy more.

---

## 6. Reproducing

Committed fixtures and the tests that pin all of the above:

```
cd /home/claude/forge/python && PYTHONPATH=. python3 -m pytest tests/test_pdf_ingest.py -q
# 21 passed, 5 xfailed
```

The 5 `xfail(strict=True)` tests are the missing warnings: they are the
specification for fixes 1, 3, 4, 7 and 9, and they will fail loudly (as
`XPASS`) the moment those fixes land.

The 15 PDFs not committed can be re-fetched without any scholarly API:

```
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/<owner>/<repo> d
git -C d ls-tree -r HEAD --name-only | grep -i '\.pdf$'
git -C d checkout HEAD -- <path/to/paper.pdf>
```

Repos used: `zjunlp/xKG` (6 PaperBench papers), `RLHFlow/Reinforce-Ada`,
`OpenMOSS/MOSS-Speech`, `OpenMOSS/DiRL`, `Tencent-Hunyuan/DRIVE-RLVR`,
`zhijie-group/LoPA`, `Tongyi-Zhiwen/Qwen-Doc`, `HKUSTDial/awesome-data-agents`,
`GAIR-NLP/LiveTalk`, `HITsz-TMG/Uni-MoE`, `Interplay-LM-Reasoning/Interplay-LM-Reasoning`,
`OpenDCAI/DataFlow`, `meituan-longcat/LongCat-Video`.

# Paper fixtures — provenance

Every PDF here is a **real research paper**, obtained by cloning a public GitHub
repository that checks the PDF into its own tree. No arXiv / OpenAlex / Crossref
transport was used (all four are proxy-blocked from this container); `git clone`
from github.com is the only working path to real full-text PDFs here.

Each fixture was chosen because it pins a *distinct* ingestion behaviour, not
because it is a nice paper. See `docs/PDF_INGEST_QUALITY.md` for the measurements.

| fixture | bytes | pages | source repo | repo commit | path in repo | repo license | why it is here |
|---|---:|---:|---|---|---|---|---|
| `icml2024_pinn_loss_landscape.pdf` | 1 580 711 | 33 | https://github.com/zjunlp/xKG | `0841500da02637d6e344e48892d3b14b05d062cc` | `experiments/paperbench/project/paperbench/data/papers/pinn/paper.pdf` | MIT (repo) | ICML two-column layout: heading detector finds 2 of 11+ sections; 8/23 claims are mined out of the appendix; float/footnote text is spliced mid-sentence |
| `ieee_unimoe_audio.pdf` | 2 923 588 | 11 | https://github.com/HITsz-TMG/Uni-MoE | `6f18a7aedfdc3ade10cd1a992e28b29d1650db38` | `UniMoE-Audio/docs/UniMoE_Audio-Paper.pdf` | none declared in repo | IEEE `IEEEtran` layout: title is detected as the LaTeX class running header; 0 of 5 figures and 0 of 4 tables indexed; every claim gets section `s?` |
| `qwenlong_l1_5.pdf` | 4 041 776 | 34 | https://github.com/Tongyi-Zhiwen/Qwen-Doc | `4e5aee32280febb7b0844237ba087f837e3c8905` | `QwenLong-L1.5/paper/QwenLong_1_5.pdf` | none declared in repo | pypdf emits unpaired UTF-16 surrogates (`U+D835`, math-italic glyphs on p.7); `paper-ingest` dies with an unhandled `UnicodeEncodeError` |

SHA-256:

```
9c4d6741961f9fd97d92c9a20fbcb34a853a08776cfffc19f05da31520c5611b  icml2024_pinn_loss_landscape.pdf
17859c43eb764b771eecb387e8a0368b44ffba06c67ad3775f33e14084d5f7c8  ieee_unimoe_audio.pdf
7325d8915638f214c4052cde59b3c6e3b30481f30f27fe9bf023b8a6c8764d69  qwenlong_l1_5.pdf
```

## Licensing — read before redistributing

- **`icml2024_pinn_loss_landscape.pdf`** is *Challenges in Training PINNs: A Loss
  Landscape Perspective* (Rathore, Lei, Frangella, Lu, Udell; ICML 2024). The
  repository it was taken from (`zjunlp/xKG`) is MIT-licensed and vendors it as
  part of the PaperBench task data. **The MIT licence covers the repository, not
  the paper.** Copyright in the paper itself is held by its authors / PMLR. It is
  used here as a test input only.
- **`ieee_unimoe_audio.pdf`** (*UniMoE-Audio*) and **`qwenlong_l1_5.pdf`**
  (*QwenLong-L1.5*) come from repositories that ship **no LICENSE file at all**.
  The authors published the PDF in a public repo, but no redistribution grant is
  declared. Treated here as fair-use test input.

None of these three PDFs carries an explicit open licence. If ResearchForge is
ever published, replace them with arXiv/PMLR items whose licence is stated
(e.g. CC-BY) or fetch them at test time instead of vendoring them.

## Other real PDFs measured but not vendored

The assessment covers 18 real paper PDFs from 14 different GitHub repositories.
The 15 not committed here (size, or redundant failure mode) are listed in
`docs/PDF_INGEST_QUALITY.md`; each can be re-fetched with:

```
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/<owner>/<repo> d
git -C d checkout HEAD -- <path/to/paper.pdf>
```

## Non-PDF fixture

`arxiv_1706.03762_abs.html` — the pre-existing arXiv abstract page (207 words).
Kept: it is the only fixture that exercises the HTML branch of `_extract`.

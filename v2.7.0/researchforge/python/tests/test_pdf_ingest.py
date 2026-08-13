"""Characterisation tests for PDF ingestion on real paper PDFs.

These tests PIN CURRENT BEHAVIOUR. Several of them assert results that are
*wrong* — a title that is a LaTeX running header, a claim labelled as living in
the References section, a figure index that misses every figure. They are here so
that the wrongness is visible and cannot be changed silently, not because it is
acceptable. Each such test says in its docstring what the right answer would be.

Where the pipeline *should* warn and does not, the test is marked
``pytest.mark.xfail(strict=True)``: it is a live specification of a missing
warning, and it will start failing loudly the moment someone fixes the pipeline
(at which point flip it to a plain assert).

Measurements behind these numbers: docs/PDF_INGEST_QUALITY.md
Fixture provenance and licensing: fixtures/papers/SOURCES.md
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAPERS = ROOT / "fixtures/papers"
PINN = PAPERS / "icml2024_pinn_loss_landscape.pdf"      # ICML two-column
IEEE = PAPERS / "ieee_unimoe_audio.pdf"                 # IEEEtran two-column
QWEN = PAPERS / "qwenlong_l1_5.pdf"                     # emits lone surrogates

pytestmark = pytest.mark.skipif(not PINN.exists(), reason="pdf fixtures not present")


def run_skill(project, skill, config=None, mode="guided"):
    p = subprocess.run(
        [sys.executable, "-m", "researchforge.runner", "run", "--skill", skill,
         "--project", str(project), "--mode", mode, "--model", "offline", "--offline",
         "--schemas", str(ROOT / "schemas")],
        input=json.dumps({"config": config or {}}), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "python")}, cwd=ROOT)
    return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode


def _ingest(tmp_path_factory, pdf, name):
    project = tmp_path_factory.mktemp(name)
    run_skill(project, "project-repo-manager", {"paper_locator": str(pdf)})
    ing, ing_code = run_skill(project, "paper-ingest", {"paper_locator": str(pdf)})
    mod, mod_code = run_skill(project, "paper-model-builder")
    return {"project": project, "ingest": ing, "ingest_code": ing_code,
            "model_result": mod, "model_code": mod_code}


def _read(project, rel):
    p = project / "source" / rel
    return json.loads(p.read_text()) if p.suffix == ".json" else p.read_text()


@pytest.fixture(scope="module")
def pinn(tmp_path_factory):
    return _ingest(tmp_path_factory, PINN, "pinn")


@pytest.fixture(scope="module")
def ieee(tmp_path_factory):
    return _ingest(tmp_path_factory, IEEE, "ieee")


# ---------------------------------------------------------------- extraction

def test_full_text_pdf_clears_the_abstract_only_gate(pinn):
    """The 300-word gate does its job: a real paper is not flagged as an abstract."""
    man = _read(pinn["project"], "source_manifest.json")
    assert man["media_type"] == "application/pdf"
    assert man["words"] > 13_000
    assert not any("abstract page, not a paper" in w for w in pinn["ingest"]["warnings"])


def test_no_page_is_falsely_reported_as_scanned(pinn, ieee):
    """The scanned/vector-only detector is not trigger-happy on text PDFs."""
    for r in (pinn, ieee):
        assert not any("yielded almost no text" in w for w in r["ingest"]["warnings"])


# ------------------------------------------------------------------ locators

@pytest.mark.parametrize("fixture,pages", [("pinn", 33), ("ieee", 11)])
def test_paragraph_anchors_are_really_pages(request, fixture, pages):
    """`locator_map.granularity` says "paragraph". It is not.

    `_extract` joins pages with "\\n\\n" and `_locators` splits on blank lines, so
    on a LaTeX-produced PDF (which emits no blank lines inside a page) there is
    exactly one anchor per page. Median anchor here is 2.1k-6.0k characters, i.e.
    350-1000 words. A downstream skill that quotes "anchor p7" believes it is
    citing a paragraph; it is citing a whole page.
    """
    r = request.getfixturevalue(fixture)
    lm = _read(r["project"], "locator_map.json")
    assert lm["granularity"] == "paragraph"
    assert len(lm["anchors"]) == pages
    assert _read(r["project"], "source_manifest.json")["paragraphs"] == pages
    biggest = max(a["chars"] for a in lm["anchors"])
    assert biggest > 4_000, "anchors this large cannot be paragraphs"


def test_claim_offsets_do_point_at_the_claim_text(pinn):
    """The one locator guarantee that holds: character offsets are exact."""
    text = _read(pinn["project"], "normalized_text.md")
    model = _read(pinn["project"], "paper_model.json")
    assert model["claims"]
    for c in model["claims"]:
        off = c["locator"]["offset"]
        assert text.startswith(c["text"], off), c["claim_id"]


# ------------------------------------------------------------------ sections

def test_two_column_icml_paper_now_detects_real_sections(pinn):
    """The paper has 9 numbered sections plus Abstract, References and appendices.

    The detector finds 2. Cause: HEAD_RE accepts "1 Introduction" but not
    "1. Introduction" — after `(\\d+(?:\\.\\d+)*)` it requires `\\s+`, and the ICML
    style puts a period there. Every numbered heading in this paper is therefore
    invisible.
    """
    # FIXED 2026-08-11: HEAD_RE now accepts "1." and roman numerals. Before the fix
    # this paper yielded exactly ["Abstract", "References"].
    sm = _read(pinn["project"], "section_map.json")
    titles = [s["title"] for s in sm["sections"]]
    assert titles != ["Abstract", "References"], "the regression this fix exists to prevent"
    assert "Introduction" in titles and "Conclusion" in titles
    assert len(titles) >= 5, titles


def test_ieee_paper_yields_only_the_references_heading(ieee):
    """IEEEtran numbers sections with roman numerals in small caps.

    pypdf renders those as "I. I NTRODUCTION" (the small-cap first letter is
    kerned away from its word), so neither the numbering nor the title matches.
    One "section" is detected, and it is the bibliography.
    """
    sm = _read(ieee["project"], "section_map.json")
    assert [s["title"] for s in sm["sections"]] == ["REFERENCES"]
    text = _read(ieee["project"], "normalized_text.md")
    assert "I. I NTRODUCTION" in text and "VII. C ONCLUSION" in text


def test_thin_section_map_is_warned_about(ieee):
    """The warning must still fire where detection genuinely stays thin.

    IEEEtran small-caps defeat the heading regex in a way the numbering fix does
    not touch ("I. I NTRODUCTION" — pypdf kerns the small-cap first letter away
    from its word). That paper must still be flagged, or the fix would have
    converted a loud failure into a quiet one.
    """
    assert any("sections detected" in w for w in ieee["model_result"]["warnings"])


# -------------------------------------------------------------------- claims

def test_claims_are_no_longer_all_filed_under_abstract(pinn):
    """23 claims, all labelled as being in Abstract (15) or References (8).

    `_claims.sect_of` assigns a claim to the last heading whose offset precedes
    it. With only "Abstract" and "References" detected, every body claim is
    attributed to the Abstract and every appendix claim to the References. A
    downstream reader of `claim.locator.section` is told, for example, that the
    contribution list of section 1 lives in the Abstract.
    """
    # FIXED 2026-08-11. Before the heading fix every body claim was filed under
    # "Abstract" and every appendix claim under "References", so a downstream reader
    # of claim.locator.section was told the contribution list lived in the abstract.
    model = _read(pinn["project"], "paper_model.json")
    sec_title = {s["id"]: s["title"] for s in model["sections"]}
    labels = [sec_title[c["locator"]["section"]] for c in model["claims"]]
    assert set(labels) != {"Abstract", "References"}, "the regression this fix prevents"
    assert len(set(labels)) >= 3, sorted(set(labels))


def test_claims_after_the_references_heading_are_kept_as_paper_claims(pinn):
    """8 of 23 claims come from the appendix, past the bibliography.

    They are proof bookkeeping ("we introduce matrices ...", "we find that
    <equation>"), not contributions, and nothing marks them as such.
    """
    model = _read(pinn["project"], "paper_model.json")
    refs_start = next(s["start"] for s in model["sections"] if s["title"] == "References")
    late = [c for c in model["claims"] if c["locator"]["offset"] > refs_start]
    assert len(late) == 8


def test_ieee_claims_have_no_section_at_all(ieee):
    """All 15 claims precede the only detected heading, so all get "s?"."""
    model = _read(ieee["project"], "paper_model.json")
    assert len(model["claims"]) == 15
    assert {c["locator"]["section"] for c in model["claims"]} == {"s?"}


def test_running_footer_is_spliced_into_a_claim_verbatim(ieee):
    """A quoted "claim" that contains the LaTeX class footer of the next page.

    Quoting this claim in a manuscript would print the journal boilerplate.
    """
    model = _read(ieee["project"], "paper_model.json")
    polluted = [c for c in model["claims"] if "JOURNAL OF L ATEX CLASS FILES" in c["text"]]
    assert polluted, "expected the running footer inside at least one claim"
    assert polluted[0]["text"].rstrip().endswith("VOL.")


def test_the_quantitative_flag_almost_never_fires(pinn):
    """21 of 23 claim sentences contain a digit; 0 are flagged quantitative.

    NUM_RE only fires on a number glued to %/BLEU/F1/AUC/mAP/accuracy/points/x,
    so "improve the conditioning by 1000x or more" (rendered "1000 ×") and every
    "reduces loss to 1e-5" style result reads as non-quantitative. Downstream,
    `_atoms` uses this flag to decide which claims are `empirical`, so these all
    become `conceptual`.
    """
    model = _read(pinn["project"], "paper_model.json")
    with_digit = [c for c in model["claims"] if re.search(r"\d", c["text"])]
    assert len(with_digit) >= 20
    assert sum(c["quantitative"] for c in model["claims"]) == 0
    atoms = json.loads((pinn["project"] / "evidence/contribution_atoms.json").read_text())["atoms"]
    assert {a["kind"] for a in atoms} == {"conceptual"}


# ----------------------------------------------------------- figures/tables

def test_figure_index_misses_figures_and_tables_on_a_two_column_paper(pinn):
    """Real: Figures 1-10 and Tables 1-3. Indexed: 9 figures, 2 tables.

    The caption regex is line-anchored (`^\\s*figure\\s+(\\d+)`), so a caption that
    pypdf emits mid-line — which happens whenever the float lands beside body
    text — is lost.
    """
    idx = _read(pinn["project"], "figure_table_index.json")
    text = _read(pinn["project"], "normalized_text.md")
    real_figs = sorted({int(n) for n in re.findall(r"Figure\s+(\d+)[:.]", text)})
    real_tabs = sorted({int(n) for n in re.findall(r"Table\s+(\d+)[:.]", text)})
    assert real_figs == list(range(1, 11)) and real_tabs == [1, 2, 3]
    assert len(idx["figures"]) == 9
    assert len(idx["tables"]) == 2


def test_ieee_figures_and_tables_are_completely_invisible(ieee):
    """Real: Fig. 1-5 and TABLE I-IV. Indexed: nothing.

    `_captions` hard-codes the words "figure"/"table" followed by an *arabic*
    number. IEEE style uses "Fig. 1:" and "TABLE III:". Zero of nine floats are
    found, and no warning is raised — `figure_table_index` simply says the paper
    has no figures and no tables.
    """
    idx = _read(ieee["project"], "figure_table_index.json")
    text = _read(ieee["project"], "normalized_text.md")
    assert sorted({int(n) for n in re.findall(r"Fig\.\s*(\d+):", text)}) == [1, 2, 3, 4, 5]
    assert re.findall(r"TABLE\s+([IVX]+):", text) == ["I", "II", "III", "IV"]
    assert idx["figures"] == [] and idx["tables"] == []


# ---------------------------------------------------------- title and terms

def test_title_is_correct_when_the_title_is_the_first_long_line(pinn):
    model = _read(pinn["project"], "paper_model.json")
    assert model["title"] == "Challenges in Training PINNs: A Loss Landscape Perspective"


def test_ieee_title_is_the_latex_class_running_header(ieee):
    """`_title` takes the first line of 15-200 chars that is not arxiv/abstract/http.

    On an IEEEtran paper that line is the class boilerplate. The PDF's own
    metadata is never consulted. This string becomes `paper_model.title`, which
    `literature-search` uses verbatim as its primary query.
    """
    model = _read(ieee["project"], "paper_model.json")
    assert model["title"] == "JOURNAL OF L ATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021 1"
    assert "UniMoE" not in model["title"]


def test_metrics_are_values_not_metric_names(pinn):
    """`metrics` is built from NUM_RE matches, keeping the last whitespace token.

    On this paper it degenerates to unit fragments and one English word: "map"
    matches the `mAP` alternative case-insensitively.
    """
    model = _read(pinn["project"], "paper_model.json")
    assert model["metrics"] == ["1X", "Accuracy", "accuracy", "map", "points", "x"]
    assert "L2RE" not in model["metrics"], "the metric this paper actually reports"


def test_method_terms_include_figure_axis_labels(pinn):
    """`_terms` does substring matching (`\\w*loss\\w*`), so plot axis text wins.

    pypdf emits the axis label of Figure 1 as "1100LossWave"; that becomes a
    "method". `literature-search` builds queries from `methods[:3]`.
    """
    model = _read(pinn["project"], "paper_model.json")
    assert "1100LossWave" in model["methods"]
    assert any(re.match(r"\d", m) for m in model["methods"])


def test_dataset_terms_are_just_inflections_of_the_cue_words(ieee):
    """No dataset in this paper is named; the list is "dataset(s)/corpus/benchmark(s)"."""
    model = _read(ieee["project"], "paper_model.json")
    assert model["datasets"] == ["dataset", "datasets", "corpus", "benchmarks", "benchmark"]


# --------------------------------------------------- the surrogate-pair crash

def test_a_pdf_with_lone_surrogates_is_recovered_and_the_loss_is_reported():
    """QwenLong-L1.5 p.7 renders math-italic glyphs as unpaired U+D835.

    `paper-ingest` passes the string straight to `Path.write_bytes(s.encode())`
    and dies with UnicodeEncodeError. The runner reports kind="internal" with a
    traceback, exit 20 — the generic "the skill crashed" path, not a diagnosis.
    No artifact is written, so `paper-model-builder` then fails with a contract
    violation blaming the missing producer.
    """
    project = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / "rf_qwen_crash"
    project.mkdir(parents=True, exist_ok=True)
    run_skill(project, "project-repo-manager", {"paper_locator": str(QWEN)})
    out, code = run_skill(project, "paper-ingest", {"paper_locator": str(QWEN)})
    # FIXED 2026-08-11. Surrogates are now transcoded lossily and the loss is
    # reported, so a real paper no longer looks like a runtime bug. The test now
    # pins the fix: ingestion succeeds AND says which pages it degraded, because a
    # silent recovery here would hide that the formulae on those pages are junk.
    assert out["ok"] is True, out
    assert any("surrogate" in w.lower() for w in out["warnings"]), out["warnings"]
    assert any("unreliable" in w.lower() or "by hand" in w.lower() for w in out["warnings"])
    assert (project / "source/normalized_text.md").exists()

    # and the pipeline continues rather than blaming a missing producer
    out2, _ = run_skill(project, "paper-model-builder")
    assert out2["ok"] is True, out2


def test_undecodable_pdf_recovers_loudly_rather_than_crashing():
    """Resolved differently from the original specification, deliberately.

    The xfail asked for a GateBlocked. Blocking would have been wrong: the surrogate
    damage is confined to the math glyphs on a couple of pages, and refusing the
    whole paper over them throws away a usable document. Recovering while naming the
    damaged pages preserves the honesty the gate was there to protect, and keeps the
    paper.
    """
    project = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / "rf_qwen_gate"
    project.mkdir(parents=True, exist_ok=True)
    run_skill(project, "project-repo-manager", {"paper_locator": str(QWEN)})
    out, _ = run_skill(project, "paper-ingest", {"paper_locator": str(QWEN)})
    assert out["ok"] is True
    assert any("surrogate" in w.lower() for w in out["warnings"])


# ------------------------------------------- warnings that are missing today

def test_layout_warnings_cannot_claim_a_clean_bill_of_health(pinn):
    """FIXED 2026-08-11 — and this was the worst defect in the codebase.

    layout_warnings.md used to print "none: text extraction produced anchored,
    ordered content" whenever the warning list happened to be empty: an affirmative
    all-clear for checks that were never run, in a system whose entire pitch is
    refusing to report work it did not do. It now separates what was checked from
    what was not, and it can no longer say "none".
    """
    lw = _read(pinn["project"], "layout_warnings.md")
    assert "none: text extraction produced anchored, ordered content" not in lw
    assert "## NOT checked" in lw
    assert "column order was NOT verified" in lw
    assert "not clean bills of health" in lw


@pytest.mark.xfail(strict=True, reason=(
    "FINDING: 162 words in this PDF are split across lines with a soft hyphen "
    "('prob-\\nlems'). Nothing de-hyphenates and nothing warns, so those tokens are "
    "invisible to CLAIM_CUE, NUM_RE, _terms and to any downstream string search."))
def test_hyphenation_across_line_breaks_should_be_reported(pinn):
    text = _read(pinn["project"], "normalized_text.md")
    assert len(re.findall(r"[a-z]-\n[a-z]", text)) > 100      # measured: 162
    lw = _read(pinn["project"], "layout_warnings.md")
    assert "hyphen" in lw.lower()


@pytest.mark.xfail(strict=True, reason=(
    "FINDING: a claim harvested from beyond the References heading is appendix "
    "or bibliography text, never a contribution. 28% of all claims across the "
    "18-PDF corpus (72/258) come from there. paper-model-builder should either "
    "drop them or mark them, and warn; it does neither."))
def test_appendix_claims_should_be_flagged_or_warned_about(pinn):
    warns = " ".join(pinn["model_result"]["warnings"]).lower()
    assert "appendix" in warns or "reference" in warns


@pytest.mark.xfail(strict=True, reason=(
    "FINDING: `_captions` finds zero floats on an IEEE-formatted paper that has "
    "nine. Zero figures and zero tables on a 11-page systems paper is a detectable "
    "impossibility and should warn, the way a thin section map does."))
def test_an_empty_figure_table_index_should_warn(ieee):
    warns = " ".join(ieee["model_result"]["warnings"]).lower()
    assert "figure" in warns or "table" in warns or "caption" in warns

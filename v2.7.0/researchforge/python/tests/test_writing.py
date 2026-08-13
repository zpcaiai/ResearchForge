"""What the writing plane must refuse.

These tests are written against the refusals, not the happy path. The happy path
of a paper generator is easy and worthless; the value of this stage is entirely in
what it declines to write down.
"""
import json
import shutil
from pathlib import Path

import pytest

from researchforge import skills as _skills  # noqa: F401  registers implementations
from researchforge.artifacts import ArtifactStore
from researchforge.errors import GateBlocked
from researchforge.providers import OfflineStubProvider, QuotaLedger
from researchforge.provenance import ProvenanceLog
from researchforge.skill import Context, get
from researchforge.skills.writing import _atomic_claims

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"

DISCLOSURE = ("The comparison baseline was not reproduced locally (level RL1). All comparisons "
              "are against numbers as reported by the original authors, under a different "
              "environment. Observed shortfalls: HARDWARE_UNAVAILABLE.")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
def make_ctx(tmp_path, **config):
    prov = ProvenanceLog(tmp_path)
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test", provenance=prov)
    return Context(project=tmp_path, run_id="test", mode="auto", store=store, prov=prov,
                   quota=QuotaLedger(tmp_path / "quota.jsonl"), model=OfflineStubProvider(),
                   scholarly=[], config=config, offline=True)


def comparison_mode(mode="CM_NONE", level="RL0", *, disclosure=True):
    forbidden = {"CM_NONE": ["outperforms", "improves over", "state-of-the-art"],
                 "CM_REPORTED": ["we reproduce", "our measured baseline",
                                 "under identical conditions"]}.get(mode, [])
    return {
        "mode": mode, "derived_from_level": level,
        "admissible_idea_modes": ["explain_diagnose", "benchmark_evaluate"],
        "forbidden_claim_patterns": forbidden,
        "disclosure_required": {"required": disclosure,
                                "text_template": DISCLOSURE if disclosure else "",
                                "must_appear_in": ["experimental setup", "limitations"]
                                if disclosure else []},
        "substitute_baseline": None, "approved_by": None, "autonomous_decision_id": None,
    }


LEDGER = [{"run_id": "run-1", "experiment_id": "E-1", "status": "ok",
           "metrics": {"accuracy": 0.8213, "latency_ms": 12.5},
           "artifacts": [], "provenance": {"code_sha": "abc123", "seed": 0}}]

REFERENCES = [
    {"ref_id": "R-001", "raw": "A real cited work", "doi": "10.1234/real-and-relevant",
     "arxiv": None, "status": "IDENTIFIED", "resolved_via": "pattern"},
    {"ref_id": "R-002", "raw": "A real but irrelevant work", "doi": "10.1234/real-but-irrelevant",
     "arxiv": None, "status": "IDENTIFIED", "resolved_via": "pattern"},
]

GRAPH = [
    # C-1 is properly evidenced: a citation edge and a run behind it.
    {"claim_id": "C-1", "claim_text": "The method attains the reported accuracy.",
     "claim_type": "empirical", "status": "SUPPORTED", "conflicts": [],
     "support_edges": [{"ref_id": "R-001", "relation": "supports"},
                       {"result_id": "E-1", "relation": "supports"}]},
    # C-2 has a run behind it and no citation edge at all.
    {"claim_id": "C-2", "claim_text": "The diagnostic probe behaves as described.",
     "claim_type": "empirical", "status": "SUPPORTED", "conflicts": [],
     "support_edges": [{"result_id": "E-1", "relation": "supports"}]},
]

FINDINGS = [{"finding_id": "F-001", "statement": "The probe isolates the failure mode.",
             "result_ids": ["E-1"]}]


def seed(ctx, *, mode="CM_NONE", level="RL0", graph=None, findings=None, ledger=None,
         disclosure=True):
    s = ctx.store
    s.write("claim-evidence-graph", "evidence_graph", GRAPH if graph is None else graph)
    s.write("finding-memory", "findings", FINDINGS if findings is None else findings)
    s.write("finding-memory", "negative_findings", [])
    s.write("finding-memory", "boundary_conditions", "- fails outside the probed regime\n")
    s.write("integrity-auditor", "meta_analysis", {"id": "MA-1", "evidence_locked": True})
    s.write("integrity-auditor", "stats_audit", {"issues": []})
    s.write("reproduction-fallback-planner", "comparison_mode",
            comparison_mode(mode, level, disclosure=disclosure))
    s.write("user-feedback-gate", "selected_direction",
            {"selected_idea_ids": ["I-001"], "thesis": "A diagnostic account of the failure mode"})
    s.write("citation-resolver", "bibliography", "@misc{R-001,\n  note = {A real cited work},\n}\n")
    s.write("citation-resolver", "resolved_references", REFERENCES)
    s.write("experiment-runner", "experiment_ledger", LEDGER if ledger is None else ledger)
    s.write("result-reproducer", "source_repro_report",
            {"target_paper_id": "p-1", "target_kind": "source_paper", "level": level,
             "assessed_at_run_id": "test", "claim_comparisons": [], "failure_codes": [],
             "environment_digest": "not-captured", "timebox_seconds": 60.0,
             "timebox_exhausted": False, "human_reviewed": False})
    return ctx


def put_draft(ctx, body, *, synthetic=False, sections=("Results",)):
    """Install a manuscript the auditor must grade, without running the builder."""
    ctx.store.write("manuscript-builder", "manuscript_draft", body)
    ctx.store.write("manuscript-builder", "draft_manifest", {
        "sections": list(sections), "paragraphs": [], "claim_index": {},
        "disclosure": {"required": False, "text": "", "must_appear_in": [], "inserted_in": [],
                       "missing_sections": []},
        "every_paragraph_bound_to_a_claim": True, "_synthetic": synthetic})
    ctx.store.write("manuscript-builder", "manuscript_spine",
                    {"thesis": "t", "claims": [{"claim_id": "C-1", "statement": "s",
                                                "section": "Results", "kind": "empirical",
                                                "evidence": {"ref_ids": ["R-001"],
                                                             "result_ids": ["E-1"]}}]})


def audit(ctx):
    get("claim-citation-auditor")(ctx)
    gate = json.loads((ctx.project / "review/integrity_gate.json").read_text())
    claims = [json.loads(l) for l in
              (ctx.project / "review/claim_audit.jsonl").read_text().splitlines() if l.strip()]
    return gate, claims


def verdict_of(claims, needle):
    return next(c for c in claims if needle in c["text"])


def kinds(gate):
    return {b["kind"] for b in gate["blockers"]}


def tex(*body, sections=("Results",)):
    lines = ["\\documentclass{article}", "\\begin{document}"]
    for s in sections:
        lines.append(f"\\section{{{s}}}")
        for claim_id, text in body:
            lines += [f"% claim: {claim_id}", text, ""]
    lines.append("\\end{document}")
    return "\n".join(lines)


# ==========================================================================
# manuscript-builder: it refuses to draft before evidence lock
# ==========================================================================
def test_drafting_before_evidence_lock_raises_and_names_what_is_unlocked(tmp_path):
    ctx = make_ctx(tmp_path)
    unsupported = [{"claim_id": "C-1", "claim_text": "x", "claim_type": "empirical",
                    "status": "UNSUPPORTED", "support_edges": [], "conflicts": []}]
    seed(ctx, graph=unsupported, findings=[])
    with pytest.raises(GateBlocked) as e:
        get("manuscript-builder")(ctx)
    assert e.value.gate == "evidence_lock"
    assert "0 support edges" in e.value.reason
    assert "no experiment result" in e.value.reason
    assert not (tmp_path / "paper/main.tex").exists(), "no prose may exist before evidence lock"


def test_evidence_lock_requires_a_recorded_run_not_only_citations(tmp_path):
    ctx = make_ctx(tmp_path)
    cited_only = [{"claim_id": "C-1", "claim_text": "x", "claim_type": "conceptual",
                   "status": "SUPPORTED", "conflicts": [],
                   "support_edges": [{"ref_id": "R-001", "relation": "supports"}]}]
    seed(ctx, graph=cited_only, findings=[{"finding_id": "F-1", "statement": "no run behind it"}])
    with pytest.raises(GateBlocked, match="no experiment result"):
        get("manuscript-builder")(ctx)


def test_every_drafted_paragraph_names_a_spine_claim(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)
    result = get("manuscript-builder")(ctx)

    spine = json.loads((tmp_path / "paper/manuscript_spine.json").read_text())
    manifest = json.loads((tmp_path / "paper/draft_manifest.json").read_text())
    draft = (tmp_path / "paper/main.tex").read_text()

    assert spine["built_before_prose"] is True and spine["claims"]
    known = {c["claim_id"] for c in spine["claims"]}
    sentences = _atomic_claims(draft)
    assert sentences, "the draft has no prose at all"
    assert all(s["claim_id"] in known for s in sentences), \
        "a paragraph in the draft is not bound to any claim in the spine"
    assert manifest["every_paragraph_bound_to_a_claim"] is True
    assert result.synthetic is True and any("OFFLINE" in w for w in result.warnings)
    assert manifest["_synthetic"] is True


def test_builder_inserts_the_disclosure_its_comparison_mode_requires(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_REPORTED", level="RL1")
    get("manuscript-builder")(ctx)
    manifest = json.loads((tmp_path / "paper/draft_manifest.json").read_text())
    draft = (tmp_path / "paper/main.tex").read_text()
    assert manifest["disclosure"]["missing_sections"] == []
    assert len(manifest["disclosure"]["inserted_in"]) == 2
    assert draft.count(DISCLOSURE) == 2


def test_the_pdf_is_typeset_or_it_is_absent_never_a_placeholder(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)
    result = get("manuscript-builder")(ctx)
    pdf = tmp_path / "paper/main.pdf"
    if any(shutil.which(e) for e in ("tectonic", "pdflatex")):
        assert pdf.exists() and pdf.read_bytes().startswith(b"%PDF"), \
            "an engine is installed, so the PDF must be the real typeset manuscript"
    else:
        assert not pdf.exists()
        assert any("no typesetting engine" in w for w in result.warnings)


def test_a_finding_the_evidence_graph_does_not_carry_is_not_drafted(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)   # F-001 exists as a finding and has no claim in the graph
    result = get("manuscript-builder")(ctx)
    spine = json.loads((tmp_path / "paper/manuscript_spine.json").read_text())
    assert spine["findings_not_in_evidence_graph"] == ["F-001"]
    assert "F-001" not in {c["claim_id"] for c in spine["claims"]}
    assert "F-001" not in (tmp_path / "paper/main.tex").read_text()
    assert any("cannot be audited" in w for w in result.warnings)


def test_a_builder_draft_audits_without_fabrication_or_unbound_prose(tmp_path):
    """End to end: the two skills agree about what the draft says."""
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_REPORTED", level="RL1")
    get("manuscript-builder")(ctx)
    gate, claims = audit(ctx)

    assert claims, "the auditor found nothing to grade in a draft that has prose"
    assert all(c["claim_id"] for c in claims), "a sentence in the draft is bound to no claim"
    assert not any(c["verdict"] == "FABRICATED" for c in claims)
    assert not {k for k in kinds(gate) if k.startswith("missing_disclosure")}
    # ...and it still blocks, because the prose came from the offline stub.
    assert "synthetic_draft" in kinds(gate) and gate["verdict"] == "BLOCK"


# ==========================================================================
# claim-citation-auditor: existence is not support
# ==========================================================================
def test_a_real_but_irrelevant_citation_is_not_supported(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)
    put_draft(ctx, tex(("C-2", "The probe isolates the failure mode \\cite{R-002}.")))
    gate, claims = audit(ctx)

    rec = verdict_of(claims, "isolates")
    assert rec["verdict"] == "NOT_SUPPORTED"
    assert rec["resolved_citations"][0]["ref_id"] == "R-002", "the citation must resolve"
    assert not rec["supporting_edges"] or all(
        e.get("ref") != "R-002" for e in rec["supporting_edges"])
    assert "not sufficient" in rec["reason"]
    assert "claim_not_supported" in kinds(gate)
    assert gate["verdict"] == "BLOCK" and gate["submission_permitted"] is False

    audit_md = (tmp_path / "review/citation_audit.md").read_text()
    assert "Real citations that do not support their claim" in audit_md
    assert "| `R-002` | yes | NO |" in audit_md


def test_a_citation_to_a_work_that_does_not_exist_is_fabricated(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)
    put_draft(ctx, tex(("C-1", "The method attains the reported accuracy \\cite{R-999}.")))
    gate, claims = audit(ctx)
    rec = verdict_of(claims, "attains")
    assert rec["verdict"] == "FABRICATED"
    assert rec["unresolved_citations"] == ["R-999"]
    assert "claim_fabricated" in kinds(gate)


def test_a_number_absent_from_the_ledger_is_fabricated_and_blocks(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx)
    put_draft(ctx, tex(("C-1", "Our probe reaches 0.99 accuracy \\cite{R-001}.")))
    gate, claims = audit(ctx)

    rec = verdict_of(claims, "0.99")
    assert rec["verdict"] == "FABRICATED"
    assert rec["number_checks"] == [{"value": "0.99", "matched": False, "ledger_entry": None}]
    # Assert the substance, not the wording: the message later grew to mention
    # audited derived statistics as a second admissible source. Pinning prose makes
    # a test fail for a change that strengthened the check it is guarding.
    assert "matches neither" in rec["reason"] or "matches no metric" in rec["reason"]
    assert "fabricated" in rec["reason"].lower()
    assert gate["verdict"] == "BLOCK" and gate["submission_permitted"] is False
    assert any(c["name"] == "every_number_traced_to_the_ledger" and c["status"] == "FAIL"
               for c in gate["checks"])
    blockers_md = (tmp_path / "review/submission_blockers.md").read_text()
    assert "claim_fabricated" in blockers_md and "0.99" in blockers_md


def test_a_number_the_ledger_records_survives_rounding(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False)
    put_draft(ctx, tex(("C-1", "Our probe reaches 0.82 accuracy \\cite{R-001}.")))
    gate, claims = audit(ctx)
    rec = verdict_of(claims, "0.82")
    assert rec["verdict"] == "SUPPORTED"
    assert rec["number_checks"][0]["ledger_entry"]["metric"] == "accuracy"
    assert gate["verdict"] == "PASS" and gate["submission_permitted"] is True


def test_an_empty_ledger_blocks_rather_than_vacuously_passing(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False, ledger=[])
    put_draft(ctx, tex(("C-2", "The probe isolates the failure mode \\cite{R-001}.")))
    gate, _ = audit(ctx)
    assert "empty_ledger" in kinds(gate) and gate["verdict"] == "BLOCK"


def test_a_synthetic_draft_can_never_pass_the_gate(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False)
    put_draft(ctx, tex(("C-1", "Our probe reaches 0.82 accuracy \\cite{R-001}.")), synthetic=True)
    gate, _ = audit(ctx)
    assert "synthetic_draft" in kinds(gate) and gate["verdict"] == "BLOCK"


# ==========================================================================
# claim-citation-auditor: the comparison mode is what makes RL0 bind
# ==========================================================================
def test_a_comparative_claim_blocks_under_cm_none(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_NONE", level="RL0")
    body = tex(("C-1", "Our probe outperforms the published baseline \\cite{R-001}."),
               ("C-2", DISCLOSURE),
               sections=("Experimental Setup", "Limitations"))
    put_draft(ctx, body, sections=("Experimental Setup", "Limitations"))
    gate, claims = audit(ctx)

    rec = verdict_of(claims, "outperforms")
    assert rec["verdict"] == "NOT_SUPPORTED"
    assert "CM_NONE" in rec["reason"] and "never reproduced" in rec["reason"]
    assert "comparative_claim_under_CM_NONE" in kinds(gate)
    assert gate["verdict"] == "BLOCK"
    assert any(c["name"] == "comparison_mode_respected" and c["status"] == "FAIL"
               for c in gate["checks"])


def test_a_missing_disclosure_blocks_under_cm_reported(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_REPORTED", level="RL1")
    body = tex(("C-1", "The method attains the reported accuracy \\cite{R-001}."),
               sections=("Experimental Setup", "Limitations"))
    put_draft(ctx, body, sections=("Experimental Setup", "Limitations"))
    gate, _ = audit(ctx)

    assert "missing_disclosure" in kinds(gate)
    missing = [b for b in gate["blockers"] if b["kind"] == "missing_disclosure"]
    assert {b["locator"] for b in missing} >= {"section:experimental setup", "section:limitations"}
    assert gate["verdict"] == "BLOCK"


def test_the_required_disclosure_present_verbatim_clears_that_blocker(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_REPORTED", level="RL1")
    body = tex(("C-1", "The method attains the reported accuracy \\cite{R-001}."),
               ("C-2", DISCLOSURE),
               sections=("Experimental Setup", "Limitations"))
    put_draft(ctx, body, sections=("Experimental Setup", "Limitations"))
    gate, _ = audit(ctx)
    assert not {k for k in kinds(gate) if k.startswith("missing_disclosure")}


def test_a_paraphrased_disclosure_does_not_count(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_REPORTED", level="RL1")
    paraphrase = "We did not rerun the baseline ourselves, so treat comparisons with care."
    body = tex(("C-1", "The method attains the reported accuracy \\cite{R-001}."),
               ("C-2", paraphrase),
               sections=("Experimental Setup", "Limitations"))
    put_draft(ctx, body, sections=("Experimental Setup", "Limitations"))
    gate, _ = audit(ctx)
    assert "missing_disclosure" in kinds(gate)


def test_a_partially_supporting_source_is_not_graded_as_support(tmp_path):
    ctx = make_ctx(tmp_path)
    partial = GRAPH + [{"claim_id": "C-3", "claim_text": "partial", "claim_type": "conceptual",
                        "status": "SUPPORTED", "conflicts": [],
                        "support_edges": [{"ref_id": "R-001", "relation": "partial"}]}]
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False, graph=partial)
    put_draft(ctx, tex(("C-3", "The mechanism enables transfer across tasks \\cite{R-001}.")))
    gate, claims = audit(ctx)
    rec = verdict_of(claims, "transfer")
    assert rec["verdict"] == "PARTIALLY_SUPPORTED"
    # Not a blocker, but it may not be reported as a clean pass either.
    assert gate["verdict"] == "PASS_WITH_CONDITIONS" and gate["submission_permitted"] is True


def test_generalizing_past_the_evidence_scope_is_a_scope_mismatch(tmp_path):
    ctx = make_ctx(tmp_path)
    scoped = GRAPH + [{"claim_id": "C-4", "claim_text": "scoped", "claim_type": "empirical",
                       "status": "SUPPORTED", "conflicts": [],
                       "support_edges": [{"ref_id": "R-001", "relation": "supports",
                                          "scope": {"dataset": "CIFAR-10"}}]}]
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False, graph=scoped)
    put_draft(ctx, tex(("C-4", "The probe isolates the failure mode in every setting "
                               "\\cite{R-001}.")))
    gate, claims = audit(ctx)
    rec = verdict_of(claims, "every setting")
    assert rec["verdict"] == "SCOPE_MISMATCH"
    assert "CIFAR-10" in rec["reason"]
    assert "claim_scope_mismatch" in kinds(gate) and gate["verdict"] == "BLOCK"


def test_the_audit_consults_no_model(tmp_path):
    ctx = make_ctx(tmp_path)
    seed(ctx, mode="CM_MEASURED", level="RL3", disclosure=False)
    put_draft(ctx, tex(("C-1", "Our probe reaches 0.82 accuracy \\cite{R-001}.")))
    gate, _ = audit(ctx)
    assert gate["model_consulted"] is False
    assert gate["citation_existence_is_not_support"] is True


# ==========================================================================
# review-simulator: it concedes what the gate blocked
# ==========================================================================
def _blocked_project(tmp_path):
    ctx = make_ctx(tmp_path, target_venue="NeurIPS", resource_envelope={})
    seed(ctx, mode="CM_NONE", level="RL0",
         ledger=LEDGER + [{"run_id": "run-2", "experiment_id": "E-2", "status": "failed",
                           "metrics": {}, "artifacts": [], "provenance": {}}])
    put_draft(ctx, tex(("C-1", "Our probe outperforms the published baseline \\cite{R-002}.")))
    audit(ctx)
    return ctx


def test_review_simulator_refuses_to_rebut_a_blocked_claim(tmp_path):
    ctx = _blocked_project(tmp_path)
    result = get("review-simulator")(ctx)

    report = json.loads((tmp_path / "review/review_report.json").read_text())
    triage = json.loads((tmp_path / "review/review_triage.json").read_text())
    response = (tmp_path / "review/response_to_reviewers.md").read_text()

    assert report["recommendation"] == "DO_NOT_SUBMIT"
    assert report["submission_permitted_by_integrity_gate"] is False
    blockers = [t for t in triage["items"] if t["severity"] == "BLOCKER"]
    assert blockers and all(t["decision"] == "CONCEDE_BLOCKER" for t in blockers)
    assert all(t["rebuttal_permitted"] is False for t in blockers)
    assert "We do not contest this point" in response
    assert "advocacy" in response
    assert any("conceded verbatim" in w for w in result.warnings)


def test_review_simulator_will_not_promise_an_unfundable_experiment(tmp_path):
    ctx = _blocked_project(tmp_path)
    result = get("review-simulator")(ctx)
    plan = (tmp_path / "review/experiment_plan.md").read_text()
    triage = json.loads((tmp_path / "review/review_triage.json").read_text())

    assert any(t["decision"] == "CONCEDE_UNFUNDABLE" for t in triage["items"])
    assert "Not planned — outside the envelope" in plan
    assert "cannot fund" in plan
    assert any("unfundable" in w for w in result.warnings)


def test_review_simulator_produces_the_whole_declared_contract(tmp_path):
    ctx = _blocked_project(tmp_path)
    result = get("review-simulator")(ctx)
    for rel in ("review/review_report.json", "review/review_triage.json",
                "review/revision_matrix.csv", "review/experiment_plan.md",
                "review/response_to_reviewers.md", "review/revision_backlog.md"):
        assert (tmp_path / rel).exists(), rel
    matrix = (tmp_path / "review/revision_matrix.csv").read_text()
    assert matrix.startswith("point_id,severity,category") and "CONCEDE_BLOCKER" in matrix
    assert result.synthetic is True


def test_every_write_in_the_writing_plane_is_provenanced(tmp_path):
    ctx = _blocked_project(tmp_path)
    get("review-simulator")(ctx)
    events = [json.loads(l) for l in (tmp_path / "provenance.jsonl").read_text().splitlines() if l]
    writes = [e for e in events
              if e["kind"] == "artifact_write" and e["skill"] in
              ("claim-citation-auditor", "review-simulator")]
    assert writes and all(e["digest"] and e["path"] for e in writes)

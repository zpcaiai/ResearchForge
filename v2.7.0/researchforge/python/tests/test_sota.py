"""The state-of-the-art arm, from the plan through to the manuscript gate.

A comparative experiment with two arms answers "is our method better than the
paper we started from". No reviewer asks that. The arm these tests guard is the
third one — the current strongest method — and the property under test is that it
is *binding*: declaring it must change the plan, running it must change the
ledger, and failing to run it must stop the sentence that claims the frontier.

The failure mode being designed against is specific and common. A project
reproduces a 2019 baseline, beats it, ablates its own method against itself, and
writes "state-of-the-art". Every number in that paper is real. The claim is still
false, and nothing in a two-arm pipeline can notice.
"""
import json
from pathlib import Path

import pytest

from researchforge import skills as _skills  # noqa: F401  registers implementations
from researchforge.artifacts import ArtifactStore
from researchforge.providers import OfflineStubProvider, QuotaLedger, build_model_provider
from researchforge.provenance import ProvenanceLog
from researchforge.skill import Context, get
from researchforge.skills.execution import _spec_arms
from researchforge.skills.planning import ResearchBlueprintCompiler

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"

ENVELOPE = {"gpu_hours": 8, "wallclock_hours": 24, "usd": 40, "seeds": 5,
            "datasets": [{"name": "toy", "split": "test", "resolved": True}]}


def make_ctx(tmp_path, config=None, *, offline_model=True):
    prov = ProvenanceLog(tmp_path)
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test", provenance=prov)
    return Context(project=tmp_path, run_id="test", mode="auto", store=store, prov=prov,
                   quota=QuotaLedger(tmp_path / "quota.jsonl"),
                   model=(build_model_provider("offline", offline=True) if offline_model
                          else OfflineStubProvider()),
                   scholarly=[], config=config or {}, offline=True)


# ======================================================================
# planning: the arm exists, or its absence is stated
# ======================================================================
def seed_plan(store, *, mode="CM_MEASURED", level="RL3", sota=None, established=False):
    store.write("result-reproducer", "source_repro_report", {
        "target_paper_id": "p", "target_kind": "source_paper", "level": level,
        "assessed_at_run_id": "test", "claim_comparisons": [], "failure_codes": [],
        "environment_digest": "none", "timebox_seconds": 1.0, "timebox_exhausted": False})
    assets = {"repos": [{"url": "https://github.com/x/y"}], "checkpoints": [], "datasets": [],
              "pinned_revision": "abc123"}
    if sota is not None:
        assets["sota"] = {"candidates": sota, "established": established, "established_ids": [],
                          "relevant_metrics": ["accuracy"], "note": ""}
        assets["sota_established"] = established
    store.write("result-reproducer", "baseline_assets", assets)
    store.write("reproduction-fallback-planner", "comparison_mode", {
        "mode": mode, "derived_from_level": level,
        "admissible_idea_modes": ["explain_diagnose", "benchmark_evaluate"],
        "forbidden_claim_patterns": [],
        "disclosure_required": {"required": False, "text_template": "", "must_appear_in": []}})
    store.write("user-feedback-gate", "selected_direction", {
        "selected_idea_ids": ["I-001"], "comparison_mode": mode, "rejected_but_retained": []})


def compile_plan(tmp_path, **seed_kw):
    ctx = make_ctx(tmp_path, {
        "hypothesis": "adding a gate to the router reduces expert collapse",
        "candidate_method": "top-k router with a load-balancing gate",
        "resource_envelope": dict(ENVELOPE)})
    seed_plan(ctx.store, **seed_kw)
    res = get("research-blueprint-compiler")(ctx)
    specs = json.loads((tmp_path / "experiments" / "*.yaml").read_text())
    return ctx, res, specs


def primary_of(specs):
    return next(s for s in specs if s["claim_type"] == "comparative")


def ablations_of(specs):
    return [s for s in specs if s["claim_type"] == "ablation"]


def test_a_sota_candidate_adds_a_third_condition_to_the_primary_comparison(tmp_path):
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2", "source": "declared_by_user"}])
    primary = primary_of(specs)
    assert primary["sota"]["required"] is True
    assert primary["resources"]["conditions"] == 3
    assert primary["resources"]["runs"] == 3 * len(primary["seeds"])
    assert "current strongest method" in primary["success_metric"]


def test_the_reported_arm_says_in_the_success_metric_that_it_is_reported(tmp_path):
    """The caveat travels with the criterion, not in a footnote nobody reads."""
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}], established=False)
    assert "was not measured here" in primary_of(specs)["success_metric"]


def test_absence_of_any_sota_candidate_is_reported_rather_than_passed_over(tmp_path):
    _, res, specs = compile_plan(tmp_path, sota=None)
    primary = primary_of(specs)
    assert primary["sota"]["required"] is False
    assert primary["resources"]["conditions"] == 2
    assert any("no state-of-the-art arm is planned" in w for w in res.warnings), \
        "silence here reads as 'there is no state of the art', which is a different claim"


def test_an_unmeasured_sota_arm_narrows_the_claim_and_does_not_void_the_runs(tmp_path):
    """The defect this replaced: declaring a SOTA candidate deleted every measurement.

    It was filed as an `invalid_condition`, whose check read an artifact flag no
    runtime path ever sets True — so three arms and six completed runs, including a
    measured state-of-the-art arm, came back VOID with `best_candidate` reporting
    "no run produced a measurement".
    """
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}])
    primary = primary_of(specs)
    assert not [c for c in primary["invalid_conditions"] if "SOTA" in c["code"]], \
        "a narrow comparison is not a void one"
    cond = next(c for c in primary["narrowing_conditions"]
                if c["code"] == "SOTA_ARM_NOT_MEASURED")
    assert cond["check"] == {"kind": "ledger_arm_completed", "arm": "sota", "value": 1}
    assert "measurement stands" in cond["narrows_to"]


def test_the_narrowing_condition_clears_once_the_sota_arm_actually_runs(tmp_path):
    """It has to be satisfiable by something the runtime can do."""
    from researchforge.invalid_conditions import evaluate_all
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}])
    primary = primary_of(specs)
    conds = primary["narrowing_conditions"]
    ledger = [{"experiment_id": "E-001", "status": "COMPLETED", "arm": "sota",
               "metrics": {"primary": 0.9}, "provenance": {"arm": "sota"}}]
    ev = evaluate_all({"experiment_id": "E-001", "invalid_conditions": conds}, ledger, {})
    assert ev["fired"] == [] and ev["void"] is False
    ev_empty = evaluate_all({"experiment_id": "E-001", "invalid_conditions": conds}, [], {})
    assert ev_empty["fired"] == ["SOTA_ARM_NOT_MEASURED"]


def test_the_condition_is_absent_when_there_is_no_sota_to_establish(tmp_path):
    """An unsatisfiable condition on every spec would make the mechanism noise."""
    _, _, specs = compile_plan(tmp_path, sota=None)
    assert not primary_of(specs).get("narrowing_conditions")


def test_seeds_too_few_names_the_arms_so_a_starved_arm_is_still_counted(tmp_path):
    """An arm with zero completed runs produced no group key and CLEARED."""
    from researchforge.invalid_conditions import evaluate
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}])
    cond = next(c for c in primary_of(specs)["invalid_conditions"]
                if c["code"] == "SEEDS_TOO_FEW")
    assert cond["check"]["conditions"] == ["baseline", "candidate", "sota"]
    ledger = [{"experiment_id": "E-001", "status": "COMPLETED", "arm": arm,
               "metrics": {"primary": 1.0}, "provenance": {"arm": arm}}
              for arm in ("baseline", "candidate") for _ in range(5)]
    v = evaluate(cond, experiment_id="E-001", ledger=ledger, artifacts={})
    assert v["status"] == "FIRED" and v["short"] == {"sota": 0}


def test_ablations_record_the_strength_of_what_they_are_anchored_to(tmp_path):
    _, _, specs = compile_plan(tmp_path, sota=None)
    abl = ablations_of(specs)
    assert abl, "the fixture must produce ablations for this test to mean anything"
    for s in abl:
        assert s["anchored_to"]["parent_has_sota_arm"] is False
        assert "internal contrast only" in s["anchored_to"]["strawman_risk"]


def test_a_declared_but_unmeasured_sota_arm_is_a_different_caveat(tmp_path):
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}], established=False)
    for s in ablations_of(specs):
        assert s["anchored_to"]["parent_has_sota_arm"] is True
        assert s["anchored_to"]["parent_sota_established"] is False
        assert "never run here" in s["anchored_to"]["strawman_risk"]


def test_an_established_sota_anchor_carries_no_strawman_warning(tmp_path):
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}], established=True)
    for s in ablations_of(specs):
        assert "strawman_risk" not in s["anchored_to"]
        assert s["anchored_to"]["parent_sota_established"] is True


# ======================================================================
# execution: an arm nobody invokes is not an arm
# ======================================================================
def test_spec_arms_follow_what_the_spec_declares(tmp_path):
    assert _spec_arms({"baseline": {}, "candidate": {}}) == ["baseline", "candidate"]
    assert _spec_arms({"baseline": {}, "candidate": {}, "sota": {}}) == \
        ["baseline", "candidate", "sota"]
    # never an empty arm list: an experiment with nothing to run must still produce
    # a ledger row saying so, rather than silently contributing zero rows
    assert _spec_arms({}) == ["candidate"]
    # a spec whose `sota` is a string, not an arm definition, declares no sota arm
    assert _spec_arms({"candidate": {}, "sota": "Mamba-2"}) == ["candidate"]


def test_the_planned_arms_are_the_ones_the_runner_would_invoke(tmp_path):
    _, _, specs = compile_plan(tmp_path, sota=[{"name": "Mamba-2"}])
    primary = primary_of(specs)
    assert _spec_arms(primary) == ["baseline", "candidate", "sota"]
    assert len(_spec_arms(primary)) == primary["resources"]["conditions"], \
        "the resource estimate and the arms the runner can invoke must be the same number"


# ======================================================================
# the manuscript gate
# ======================================================================
DISCLOSURE_OFF = {
    "mode": "CM_MEASURED", "derived_from_level": "RL3",
    "admissible_idea_modes": ["explain_diagnose", "benchmark_evaluate"],
    "forbidden_claim_patterns": [],
    "disclosure_required": {"required": False, "text_template": "", "must_appear_in": []},
    "substitute_baseline": None, "approved_by": None, "autonomous_decision_id": None,
}
REFERENCES = [{"ref_id": "R-001", "raw": "A real cited work", "doi": "10.1234/real",
               "arxiv": None, "status": "IDENTIFIED", "resolved_via": "pattern"}]
GRAPH = [{"claim_id": "C-1", "claim_text": "frontier claim", "claim_type": "empirical",
          "status": "SUPPORTED", "conflicts": [],
          "support_edges": [{"ref_id": "R-001", "relation": "supports"},
                            {"result_id": "E-001", "relation": "supports"}]}]


#: The minimum an ExperimentSpec must carry to validate. The auditor reads exactly
#: one field of it — `sota` — so the rest is scaffolding, and writing it through the
#: real store rather than stubbing the reader is deliberate: a check that only works
#: against hand-made dicts is a check that has never met a real spec.
SPEC_SKELETON = {
    "experiment_id": "E-001",
    "hypothesis": "the gate reduces expert collapse",
    "baseline": {"kind": "source_paper_repo"},
    "candidate": {"description": "top-k router with a load-balancing gate"},
    "datasets": [{"name": "toy"}],
    "metrics": [{"name": "accuracy", "role": "decision"}],
    "seeds": [0, 1, 2],
    "invalid_conditions": [{"code": "SEEDS_TOO_FEW", "condition": "fewer than 3 seeds"}],
}


def ledger_entry(arm, status="COMPLETED", metrics=None):
    return {"run_id": "run-1", "experiment_id": "E-001", "arm": arm, "status": status,
            "metrics": metrics if metrics is not None else {"accuracy": 0.83},
            "artifacts": [], "provenance": {"code_sha": "abc", "seed": 0, "arm": arm}}


def seed_audit(ctx, ledger, *, specs=None):
    s = ctx.store
    s.write("claim-evidence-graph", "evidence_graph", GRAPH)
    s.write("finding-memory", "findings", [{"finding_id": "F-1", "statement": "x",
                                            "result_ids": ["E-001"]}])
    s.write("integrity-auditor", "stats_audit", {"issues": []})
    s.write("reproduction-fallback-planner", "comparison_mode", DISCLOSURE_OFF)
    s.write("citation-resolver", "resolved_references", REFERENCES)
    s.write("experiment-runner", "experiment_ledger", ledger)
    s.write("result-reproducer", "source_repro_report",
            {"target_paper_id": "p-1", "target_kind": "source_paper", "level": "RL3",
             "assessed_at_run_id": "test", "claim_comparisons": [], "failure_codes": [],
             "environment_digest": "d", "timebox_seconds": 60.0, "timebox_exhausted": False,
             "human_reviewed": False})
    if specs is not None:
        s.write("research-blueprint-compiler", "experiment_specs",
                [dict(SPEC_SKELETON, **sp) for sp in specs])


def put_draft(ctx, sentence):
    body = "\n".join(["\\documentclass{article}", "\\begin{document}", "\\section{Results}",
                      "% claim: C-1", sentence, "\\end{document}"])
    ctx.store.write("manuscript-builder", "manuscript_draft", body)
    ctx.store.write("manuscript-builder", "draft_manifest", {
        "sections": ["Results"], "paragraphs": [], "claim_index": {},
        "disclosure": {"required": False, "text": "", "must_appear_in": [], "inserted_in": [],
                       "missing_sections": []},
        "every_paragraph_bound_to_a_claim": True, "_synthetic": False})
    ctx.store.write("manuscript-builder", "manuscript_spine",
                    {"thesis": "t", "claims": [{"claim_id": "C-1", "statement": "s",
                                                "section": "Results", "kind": "empirical",
                                                "evidence": {"ref_ids": ["R-001"],
                                                             "result_ids": ["E-001"]}}]})


def audit_kinds(ctx):
    get("claim-citation-auditor")(ctx)
    gate = json.loads((ctx.project / "review/integrity_gate.json").read_text())
    return gate, {b["kind"] for b in gate["blockers"]}


SOTA_BLOCKER = "sota_claim_without_measured_sota_arm"

FRONTIER_SENTENCE = ("Our method outperforms all prior approaches on the benchmark "
                     "\\cite{R-001}.")


def test_a_frontier_claim_without_any_sota_arm_blocks_submission(tmp_path):
    ctx = make_ctx(tmp_path, offline_model=False)
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate")], specs=[])
    put_draft(ctx, FRONTIER_SENTENCE)
    gate, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER in kinds
    assert gate["submission_permitted"] is False
    detail = next(b["detail"] for b in gate["blockers"] if b["kind"] == SOTA_BLOCKER)
    assert "ever declared a state-of-the-art arm" in detail


def test_a_planned_but_never_executed_sota_arm_still_blocks(tmp_path):
    ctx = make_ctx(tmp_path, offline_model=False)
    specs = [{"experiment_id": "E-001", "sota": {"required": True, "candidates": [{"name": "M"}]}}]
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate")], specs=specs)
    put_draft(ctx, FRONTIER_SENTENCE)
    _, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER in kinds


def test_a_sota_arm_that_ran_but_produced_nothing_is_not_a_comparison(tmp_path):
    ctx = make_ctx(tmp_path, offline_model=False)
    specs = [{"experiment_id": "E-001", "sota": {"required": True, "candidates": [{"name": "M"}]}}]
    seed_audit(ctx, [ledger_entry("candidate"),
                     ledger_entry("sota", status="FAILED", metrics={})], specs=specs)
    put_draft(ctx, FRONTIER_SENTENCE)
    gate, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER in kinds
    detail = next(b["detail"] for b in gate["blockers"] if b["kind"] == SOTA_BLOCKER)
    assert "did not run is not a comparison" in detail


def test_a_measured_sota_arm_clears_the_frontier_claim(tmp_path):
    ctx = make_ctx(tmp_path, offline_model=False)
    specs = [{"experiment_id": "E-001", "sota": {"required": True, "candidates": [{"name": "M"}]}}]
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate"), ledger_entry("sota")],
               specs=specs)
    put_draft(ctx, FRONTIER_SENTENCE)
    gate, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER not in kinds
    check = next(c for c in gate["checks"] if c["name"] == "sota_claims_have_a_measured_sota_arm")
    assert check["status"] == "PASS"


def test_describing_the_literature_is_not_a_claim_about_this_work(tmp_path):
    """The check has to be trustworthy in related work or it will be turned off."""
    ctx = make_ctx(tmp_path, offline_model=False)
    seed_audit(ctx, [ledger_entry("candidate")], specs=[])
    put_draft(ctx, "Recent state-of-the-art language models use rotary embeddings "
                   "\\cite{R-001}.")
    _, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER not in kinds


def test_beating_the_source_baseline_is_not_a_frontier_claim(tmp_path):
    """A narrow comparative claim stays admissible; only 'best' needs the best."""
    ctx = make_ctx(tmp_path, offline_model=False)
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate")], specs=[])
    put_draft(ctx, "Our method improves over the baseline of \\cite{R-001} on this benchmark.")
    _, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER not in kinds


# ======================================================================
# the frontier gate: what it used to miss, and what it used to block
# ======================================================================
FRONTIER_MISSES = [
    ("a named system with no pronoun",
     "ForgeNet outperforms all prior methods on WMT14."),
    ("pronoun continuation across sentences",
     "Our approach is simple. It outperforms all prior methods."),
    ("the same claim in plainer words",
     "We report the highest accuracy ever recorded on this benchmark."),
    ("the control that always worked",
     "Our method outperforms all prior methods."),
]


@pytest.mark.parametrize("label,sentence", FRONTIER_MISSES, ids=[x[0] for x in FRONTIER_MISSES])
def test_a_frontier_claim_is_caught_however_it_is_phrased(tmp_path, label, sentence):
    ctx = make_ctx(tmp_path, offline_model=False)
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate")], specs=[])
    put_draft(ctx, sentence + " \\cite{R-001}" if "cite" not in sentence else sentence)
    _, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER in kinds, f"{label}: {sentence!r} passed the gate"


FRONTIER_NON_CLAIMS = [
    ("a mention in a setup sentence",
     "We follow the setup of prior work, e.g. state-of-the-art transformers \\cite{R-001}."),
    ("a sentence about the literature",
     "Recent state-of-the-art language models use rotary embeddings \\cite{R-001}."),
    ("a narrow comparative claim",
     "Our method improves over the baseline of \\cite{R-001} on this benchmark."),
]


@pytest.mark.parametrize("label,sentence", FRONTIER_NON_CLAIMS,
                         ids=[x[0] for x in FRONTIER_NON_CLAIMS])
def test_correct_text_is_not_blocked(tmp_path, label, sentence):
    ctx = make_ctx(tmp_path, offline_model=False)
    seed_audit(ctx, [ledger_entry("baseline"), ledger_entry("candidate")], specs=[])
    put_draft(ctx, sentence)
    _, kinds = audit_kinds(ctx)
    assert SOTA_BLOCKER not in kinds, f"{label}: {sentence!r} was blocked"


def test_a_sentence_beginning_with_a_digit_is_its_own_sentence(tmp_path):
    """The splitter's uppercase-only lookahead merged results prose constantly."""
    from researchforge.skills.writing import _sentences
    got = _sentences("Our method improves over the baseline. "
                     "12 recent systems report state-of-the-art numbers.")
    assert len(got) == 2, got
    assert _sentences("We ran it on e.g. 3 datasets.") == ["We ran it on e.g. 3 datasets."]

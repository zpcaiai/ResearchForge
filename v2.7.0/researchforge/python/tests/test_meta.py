"""What the offline maintenance plane refuses, and why.

These tests are almost entirely negative, on purpose. The meta plane's whole job
is to decide whether the system got better, and every one of these refusals is a
way that question could be answered flatteringly instead of correctly: a
benchmark the model already memorised, a benchmark edited after the scores came
in, two numbers from two different systems subtracted from each other, an edit
that only helps on the data it was mined from, a skill that rewrites itself while
it is being measured.

The affirmative tests exist only to show the refusals are not vacuous.
"""
import json
import shutil
from pathlib import Path

import pytest

from researchforge.artifacts import ArtifactStore
from researchforge.errors import GateBlocked
from researchforge.generated import CONTRACT_DIGEST
from researchforge.provenance import ProvenanceLog
from researchforge.providers import (FixtureTransport, OfflineStubProvider, QuotaLedger,
                                     ScholarlyCapabilities, ScholarlyProvider)
from researchforge.skill import Context
from researchforge.skills.meta import (RetrospectiveBenchmarkBuilder, ResearchEvalHarness,
                                       SkillEvolutionManager, assert_ab_arms,
                                       assert_same_system, audit_package)

FORGE = Path(__file__).resolve().parents[2]
SCHEMAS = FORGE / "schemas"
SKILLS_DIR = FORGE / "skills"
MANIFESTS = FORGE / "manifests"


def ctx_for(tmp_path, *, scholarly=None, **config):
    prov = ProvenanceLog(tmp_path)
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test", provenance=prov)
    return Context(project=tmp_path, run_id="test", mode="auto", store=store, prov=prov,
                   quota=QuotaLedger(tmp_path / "literature" / "quota_ledger.jsonl"),
                   model=OfflineStubProvider(), scholarly=scholarly or [],
                   config=config, offline=True)


# --------------------------------------------------------------------------
# fixtures shared by the benchmark builder tests
# --------------------------------------------------------------------------
def evidence_inputs(ctx):
    """The upstream artifacts the builder consumes."""
    ctx.store.write("literature-provider-manager", "provider_registry",
                    {"scope": "ml", "providers": [], "generated_at": 0})
    ctx.store.write("literature-provider-manager", "coverage_report",
                    {"status": "UNKNOWN_COVERAGE", "measured": False, "score": None,
                     "named_blind_spots": ["no full-text search"]})


FOLLOWUP = {
    "id": "F-001", "title": "Sparse routing for long-context sequence models",
    "published": "2021-05-01", "relation": "extends",
    "problem_delta": "long contexts blow up quadratic attention cost",
    "method_delta": "replace dense attention with learned sparse routing",
    "mechanism": "top-k routing over key blocks with a learned scorer",
    "demonstrating_experiment": "perplexity at 16k context vs dense baseline at matched compute",
}
INCIDENTAL = {
    "id": "F-002", "title": "A survey of sequence modelling", "published": "2021-06-01",
    "relation": "related_work_mention",
    "problem_delta": "n/a", "method_delta": "n/a", "mechanism": "n/a",
    "demonstrating_experiment": "n/a",
}


def corpus(extra_followups=()):
    return [
        {"seed_id": "S-001", "seed_title": "Attention primitives for sequence models",
         "seed_published": "2019-03-01",
         "followups": [dict(FOLLOWUP), dict(INCIDENTAL), *[dict(f) for f in extra_followups]]},
        {"seed_id": "S-002", "seed_title": "Curriculum ordering for pretraining corpora",
         "seed_published": "2019-09-01",
         "followups": [{**FOLLOWUP, "id": "F-010",
                        "title": "Loss-based curriculum reordering at scale",
                        "published": "2021-11-01"}]},
    ]


#: A model that can recite the follow-ups. Supplied as a recorded transcript so the
#: probe is a measurement and not a coin flip.
RECITED = {"S-001": ["Sparse routing for long-context sequence models"],
           "S-002": ["Loss-based curriculum reordering at scale"]}
FORGOTTEN = {"S-001": ["something entirely unrelated"], "S-002": []}


def build_config(**over):
    cfg = dict(retro_corpus=corpus(),
               seed_window={"start": "2019-01-01", "end": "2019-12-31"},
               eval_window={"start": "2020-01-01", "end": "2022-12-31"},
               seed_selection_rule="every NeurIPS 2019 main-track paper, no citation filter",
               model_knowledge_cutoff="2021-06-01",
               domain_scope="sequence modelling", venue_list=["NeurIPS"],
               contamination_probe_responses=RECITED)
    cfg.update(over)
    return cfg


def build_benchmark(tmp_path, **over):
    ctx = ctx_for(tmp_path, **build_config(**over))
    evidence_inputs(ctx)
    result = RetrospectiveBenchmarkBuilder()(ctx)
    return ctx, result


def freeze_record(tmp_path):
    rows = [json.loads(l) for l in
            (tmp_path / "evals/retro_benchmark.jsonl").read_text().splitlines() if l.strip()]
    return next(r for r in rows if r["record_type"] == "freeze"), rows


# ==========================================================================
# retrospective-benchmark-builder
# ==========================================================================
def test_no_corpus_and_no_transport_raises_instead_of_fabricating(tmp_path):
    ctx = ctx_for(tmp_path, **build_config(retro_corpus=None))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "benchmark_corpus"
    # the refusal must name the actual condition, not "unavailable"
    assert "403" in e.value.reason and "OpenAlex" in e.value.reason
    assert "retro_corpus" in (e.value.remediation or "")
    assert not (tmp_path / "evals/retro_benchmark.jsonl").exists(), \
        "a refused benchmark must not leave a partial one behind"


def test_a_search_transport_is_not_enough_to_harvest_follow_ups(tmp_path):
    p = ScholarlyProvider("openalex", "https://api.openalex.org", ScholarlyCapabilities(),
                          _transport=FixtureTransport(tmp_path / "fixtures"))
    ctx = ctx_for(tmp_path, scholarly=[p], **build_config(retro_corpus=None))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "benchmark_corpus"
    assert "traversal" in e.value.reason


def test_missing_windows_are_a_gate_not_a_default(tmp_path):
    ctx = ctx_for(tmp_path, **build_config(seed_window=None))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "benchmark_windows"


def test_an_incidental_citation_is_not_a_follow_up(tmp_path):
    build_benchmark(tmp_path)
    _, rows = freeze_record(tmp_path)
    adj = {a["followup_id"]: a for a in rows if a["record_type"] == "adjudication"}
    assert adj["F-001"]["verdict"] == "follow_up"
    assert adj["F-002"]["verdict"] == "excluded"
    assert "incidental" in adj["F-002"]["reason"]
    pairs = [r for r in rows if r["record_type"] == "pair"]
    assert [d["followup_id"] for p in pairs for d in p["gold_directions"]] == ["F-001", "F-010"]


def test_a_follow_up_without_a_direction_descriptor_is_excluded(tmp_path):
    thin = {"id": "F-003", "title": "Something later", "published": "2021-07-01",
            "relation": "extends", "problem_delta": "", "method_delta": "",
            "mechanism": "", "demonstrating_experiment": ""}
    build_benchmark(tmp_path, retro_corpus=corpus(extra_followups=[thin]))
    _, rows = freeze_record(tmp_path)
    adj = next(a for a in rows if a["record_type"] == "adjudication" and a["followup_id"] == "F-003")
    assert adj["verdict"] == "excluded" and "descriptor incomplete" in adj["reason"]


def test_contamination_floor_is_measured_by_recitation(tmp_path):
    ctx, result = build_benchmark(tmp_path, probe_model_id="some-model@2026-05")
    freeze, _ = freeze_record(tmp_path)
    floor = freeze["contamination_floor"]
    assert floor["measured"] is True and floor["floor_recall"] == 1.0
    assert floor["is_a_lower_bound"] is True
    # a recorded transcript must not be attributed to whatever provider this run used
    assert floor["probe_sources"] == ["recorded_probe_transcript"]
    assert floor["probed_model_declared"] == "some-model@2026-05"
    assert any("measures recall, not reasoning" in w for w in result.warnings)
    report = (tmp_path / "evals/retro_benchmark_report.md").read_text()
    assert "Contamination floor" in report and "lower bound" in report


def test_a_model_that_cannot_recite_produces_a_low_but_measured_floor(tmp_path):
    build_benchmark(tmp_path, contamination_probe_responses=FORGOTTEN)
    freeze, _ = freeze_record(tmp_path)
    assert freeze["contamination_floor"]["measured"] is True
    assert freeze["contamination_floor"]["floor_recall"] == 0.0
    assert freeze["usable_for_promotion_gating"] is True


def test_the_offline_stub_cannot_be_probed_and_the_floor_stays_unmeasured(tmp_path):
    _, result = build_benchmark(tmp_path, contamination_probe_responses=None)
    freeze, _ = freeze_record(tmp_path)
    floor = freeze["contamination_floor"]
    # A silent stub must never be read as "the model knows nothing" — that would
    # manufacture a floor of zero, the most flattering possible error.
    assert floor["measured"] is False and floor["floor_recall"] is None
    assert freeze["usable_for_promotion_gating"] is False
    assert any("UNMEASURED" in w for w in result.warnings)


def test_an_unmeasured_contamination_floor_blocks_promotion_gating(tmp_path):
    ctx = ctx_for(tmp_path, **build_config(contamination_probe_responses=None,
                                           gate_skill_promotion=True))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "contamination_floor"
    assert "memoriz" in e.value.reason
    # the artifacts survive the refusal: they are the evidence of why it happened
    freeze, _ = freeze_record(tmp_path)
    assert freeze["usable_for_promotion_gating"] is False
    assert freeze["why_not_usable"]


def test_missing_blind_rubric_is_reported_as_a_limit_on_interpretation(tmp_path):
    _, result = build_benchmark(tmp_path)
    assert any("blind human rubric" in w for w in result.warnings)
    assert "Blind rubric" in (tmp_path / "evals/retro_benchmark_report.md").read_text()


def test_rebuilding_identical_content_is_not_a_modification(tmp_path):
    build_benchmark(tmp_path)
    first, _ = freeze_record(tmp_path)
    build_benchmark(tmp_path)
    second, _ = freeze_record(tmp_path)
    assert first["content_hash"] == second["content_hash"]


def test_post_hoc_modification_of_a_frozen_benchmark_is_rejected(tmp_path):
    build_benchmark(tmp_path)
    before, _ = freeze_record(tmp_path)
    added = {**FOLLOWUP, "id": "F-777", "title": "A follow-up added after the scores came in",
             "published": "2022-01-01"}
    ctx = ctx_for(tmp_path, **build_config(retro_corpus=corpus(extra_followups=[added])))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "benchmark_freeze"
    assert "voids every score" in e.value.reason
    after, _ = freeze_record(tmp_path)
    assert after["content_hash"] == before["content_hash"], \
        "the frozen benchmark on disk must be untouched by the rejected rebuild"


def test_superseding_a_frozen_benchmark_requires_an_explicit_approved_new_version(tmp_path):
    build_benchmark(tmp_path)
    added = {**FOLLOWUP, "id": "F-777", "title": "A later follow-up", "published": "2022-01-01"}
    revised = corpus(extra_followups=[added])

    # a bare version bump is not enough: the override must be explicit and attributed
    ctx = ctx_for(tmp_path, **build_config(retro_corpus=revised, benchmark_version="2"))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked):
        RetrospectiveBenchmarkBuilder()(ctx)

    ctx2, _ = build_benchmark(tmp_path, retro_corpus=revised, benchmark_version="2",
                              supersede_frozen_benchmark={"new_version": "2",
                                                          "reason": "F-777 was missed",
                                                          "approved_by": "a.reviewer"})
    freeze, _ = freeze_record(tmp_path)
    assert freeze["benchmark_version"] == "2"


def test_an_empty_benchmark_is_refused_rather_than_scored_zero_over_zero(tmp_path):
    ctx = ctx_for(tmp_path, **build_config(
        retro_corpus=[{"seed_id": "S-001", "seed_title": "x", "followups": [dict(INCIDENTAL)]}]))
    evidence_inputs(ctx)
    with pytest.raises(GateBlocked) as e:
        RetrospectiveBenchmarkBuilder()(ctx)
    assert e.value.gate == "benchmark_empty"


def test_the_held_out_subset_is_split_at_the_declared_cutoff(tmp_path):
    build_benchmark(tmp_path)
    freeze, rows = freeze_record(tmp_path)
    splits = {d["followup_id"]: d["split"]
              for r in rows if r["record_type"] == "pair" for d in r["gold_directions"]}
    assert splits == {"F-001": "inside_cutoff", "F-010": "held_out_post_cutoff"}
    assert freeze["counts"]["held_out_post_cutoff"] == 1
    assert "never used to tune" in freeze["held_out_policy"]


# ==========================================================================
# research-eval-harness
# ==========================================================================
EVALUATOR = '''
"""Minimal grader-side scorer with the same contract as evaluator-builder's."""
SELF_SCORE_FIELDS = ("score", "reward", "confidence")


def score(submission, reference):
    if not isinstance(submission, dict):
        return {"valid": False, "score": None, "invalidations":
                [{"code": "SUBMISSION_NOT_OBJECT"}], "evaluator_version": "t1"}
    hit = [k for k in submission if k in SELF_SCORE_FIELDS]
    if hit:
        return {"valid": False, "score": None,
                "invalidations": [{"code": "SELF_REPORTED_SCORE", "detail": str(hit)}],
                "evaluator_version": "t1"}
    ref = (reference or {}).get("answer")
    got = submission.get("answer")
    return {"valid": True, "score": 1.0 if got == ref else 0.0, "invalidations": [],
            "notes": [], "evaluator_version": "t1"}
'''

SUITE = {
    "suite_id": "meta-suite-v1",
    "tasks": [
        {"task_id": "T1", "split": "train", "reference": {"answer": "a"}},
        {"task_id": "T2", "split": "train", "reference": {"answer": "b"}},
        {"task_id": "T3", "split": "held_out", "reference": {"answer": "c"}},
        {"task_id": "T4", "split": "held_out", "reference": {"answer": "d"}},
        {"task_id": "G1", "split": "held_out", "guardrail": "no_fabricated_citation",
         "reference": {"answer": "g"}},
    ],
}


def traces(correct=("T1", "T2", "T3", "T4", "G1"), **over):
    out = []
    for t in SUITE["tasks"]:
        tid = t["task_id"]
        answer = t["reference"]["answer"] if tid in correct else "wrong"
        out.append({"task_id": tid, "submission": {"answer": answer}, "files_read": ["code/"]})
    for tid, patch in over.items():
        rec = next(r for r in out if r["task_id"] == tid)
        rec.update(patch)
    return out


def harness_ctx(tmp_path, *, floor=RECITED, **config):
    ctx, _ = build_benchmark(tmp_path, contamination_probe_responses=floor)
    ctx.store.write("evaluator-builder", "evaluator_code", EVALUATOR)
    cfg = dict(task_suite=SUITE, skill_version="idea-ranker@1.0.0", run_traces=traces())
    cfg.update(config)
    ctx.config = cfg
    return ctx


def test_no_run_traces_refuses_rather_than_emitting_a_scorecard(tmp_path):
    ctx = harness_ctx(tmp_path, run_traces=None)
    with pytest.raises(GateBlocked) as e:
        ResearchEvalHarness()(ctx)
    assert e.value.gate == "eval_execution"
    assert "does not simulate" in e.value.reason
    assert not (tmp_path / "evals/scorecard.json").exists()


def test_a_suite_that_changed_after_freezing_is_refused(tmp_path):
    ctx = harness_ctx(tmp_path, task_suite={**SUITE, "frozen_hash": "0" * 32})
    with pytest.raises(GateBlocked) as e:
        ResearchEvalHarness()(ctx)
    assert e.value.gate == "suite_freeze"


def test_scores_are_stamped_with_the_system_that_produced_them(tmp_path):
    ctx = harness_ctx(tmp_path)
    ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    assert card["contract_digest"] == CONTRACT_DIGEST
    assert card["skill_version"] == "idea-ranker@1.0.0"
    assert card["suite_hash"] and card["evaluator_digest"]
    assert card["means"]["held_out"] == 1.0 and card["means"]["train"] == 1.0
    assert card["guardrails"] == {"no_fabricated_citation": 1.0}


def test_scores_across_differing_contract_digests_are_refused(tmp_path):
    ctx = harness_ctx(tmp_path)
    ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())

    stale = {**card, "contract_digest": "0000dead0000beef"}
    ctx2 = harness_ctx(tmp_path / "second", baseline_scorecard=stale)
    with pytest.raises(GateBlocked) as e:
        ResearchEvalHarness()(ctx2)
    assert e.value.gate == "score_comparability"
    assert "contract_digest" in e.value.reason
    assert "not the same system" in e.value.reason


def test_scores_across_differing_skill_versions_are_refused(tmp_path):
    ctx = harness_ctx(tmp_path)
    ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())

    other = {**card, "skill_version": "idea-ranker@0.9.0"}
    ctx2 = harness_ctx(tmp_path / "second", baseline_scorecard=other)
    with pytest.raises(GateBlocked) as e:
        ResearchEvalHarness()(ctx2)
    assert e.value.gate == "score_comparability"
    assert "skill_version" in e.value.reason
    assert "A/B arm pair" in (e.value.remediation or "")


def test_an_unstamped_scorecard_cannot_be_compared_at_all(tmp_path):
    with pytest.raises(GateBlocked) as e:
        assert_same_system({"skill_version": "v1"}, {"skill_version": "v1"})
    assert e.value.gate == "score_comparability"
    assert "no system attached" in e.value.reason


def test_reading_the_private_evaluator_voids_the_run(tmp_path):
    ctx = harness_ctx(tmp_path,
                      run_traces=traces(T3={"files_read": ["evaluation/evaluate.py"]}))
    result = ResearchEvalHarness()(ctx)
    runs = [json.loads(l) for l in
            (tmp_path / "evals/eval_runs.jsonl").read_text().splitlines() if l.strip()]
    t3 = next(r for r in runs if r["task_id"] == "T3")
    assert t3["status"] == "INTEGRITY_VIOLATION"
    assert t3["score"] is None, "a cheating run must have no score, not a low one"
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    assert card["integrity_violations"] == 1
    assert card["promotion_eligible"] is False
    assert "isolation" in card["why_not_promotion_eligible"]
    assert any("voided" in w for w in result.warnings)
    assert "EVALUATOR_ISOLATION_BREACH" in (tmp_path / "evals/failure_taxonomy.md").read_text()


def test_reading_the_hidden_tests_voids_the_run(tmp_path):
    ctx = harness_ctx(tmp_path,
                      run_traces=traces(T4={"tool_calls": ["cat evaluation/hidden_tests/x.json"]}))
    ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    assert card["integrity_violations"] == 1


def test_self_reported_success_is_never_a_score(tmp_path):
    ctx = harness_ctx(tmp_path, run_traces=traces(
        correct=("T1", "T2", "T4", "G1"),
        T3={"submission": {"answer": "wrong"}, "self_reported": {"success": True},
            "files_read": []}))
    ResearchEvalHarness()(ctx)
    runs = [json.loads(l) for l in
            (tmp_path / "evals/eval_runs.jsonl").read_text().splitlines() if l.strip()]
    t3 = next(r for r in runs if r["task_id"] == "T3")
    assert t3["score"] == 0.0 and t3["self_reported_ignored"] == {"success": True}
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    assert card["self_reported_success_counted"] is False


def test_a_submission_that_carries_its_own_score_is_invalid_not_zero(tmp_path):
    ctx = harness_ctx(tmp_path, run_traces=traces(
        T1={"submission": {"answer": "a", "score": 1.0}, "files_read": []}))
    ResearchEvalHarness()(ctx)
    runs = [json.loads(l) for l in
            (tmp_path / "evals/eval_runs.jsonl").read_text().splitlines() if l.strip()]
    t1 = next(r for r in runs if r["task_id"] == "T1")
    assert t1["status"] == "INVALID_SUBMISSION" and t1["score"] is None
    assert t1["failure_class"] == "INVALID:SELF_REPORTED_SCORE"


def test_a_task_with_no_trace_is_not_a_zero(tmp_path):
    ctx = harness_ctx(tmp_path, run_traces=[t for t in traces() if t["task_id"] != "T4"])
    result = ResearchEvalHarness()(ctx)
    runs = [json.loads(l) for l in
            (tmp_path / "evals/eval_runs.jsonl").read_text().splitlines() if l.strip()]
    t4 = next(r for r in runs if r["task_id"] == "T4")
    assert t4["status"] == "NOT_RUN" and t4["score"] is None
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    assert card["n_not_run"] == 1
    # the remaining held-out tasks were correct, so the mean must not be dragged down
    assert card["means"]["held_out"] == 1.0
    assert any("NOT_RUN" in w for w in result.warnings)


def test_recall_at_k_is_null_without_rubric_adjudication(tmp_path):
    ctx = harness_ctx(tmp_path,
                      system_directions=[{"direction_id": "D1", "rank": 1}])
    result = ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    retro = card["benchmark"]["retro_recall_at_k"]
    assert retro["value"] is None and "string overlap is not a match" in retro["why"]
    assert any("vocabulary" in w for w in result.warnings)


def test_recall_at_or_below_the_contamination_floor_is_marked_uninterpretable(tmp_path):
    ctx = harness_ctx(tmp_path,
                      system_directions=[{"direction_id": "D1", "rank": 1}],
                      match_adjudications=[{"gold_direction_id": "S-001::F-001",
                                            "system_direction_id": "D1", "match": True,
                                            "rubric": "same mechanism and same experiment"}])
    ResearchEvalHarness()(ctx)
    card = json.loads((tmp_path / "evals/scorecard.json").read_text())
    retro = card["benchmark"]["retro_recall_at_k"]
    assert retro["value"] == 0.5 and retro["contamination_floor"] == 1.0
    assert retro["above_floor"] is False
    assert "not evidence of research judgment" in retro["why"]


def test_an_unfrozen_benchmark_cannot_carry_scores(tmp_path):
    ctx = harness_ctx(tmp_path)
    (tmp_path / "evals/retro_benchmark.jsonl").write_text('{"record_type": "pair"}\n')
    with pytest.raises(GateBlocked) as e:
        ResearchEvalHarness()(ctx)
    assert e.value.gate == "benchmark_freeze"


# ==========================================================================
# skill-evolution-manager — package audit
# ==========================================================================
def test_the_real_package_satisfies_every_invariant():
    audit = audit_package(SKILLS_DIR, MANIFESTS / "artifact-graph.json",
                          MANIFESTS / "skill-catalog.json")
    assert audit["status"] == "PASS", [f for f in audit["findings"] if f["severity"] == "error"]
    assert all(audit["invariants"].values())
    assert audit["counts"]["skills"] == 32


def mutated_manifests(tmp_path, mutate):
    d = tmp_path / "manifests"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(MANIFESTS / "skill-catalog.json", d / "skill-catalog.json")
    graph = json.loads((MANIFESTS / "artifact-graph.json").read_text())
    mutate(graph)
    (d / "artifact-graph.json").write_text(json.dumps(graph))
    return d


def test_the_audit_catches_a_dangling_input(tmp_path):
    def m(g):
        g["consumers"]["idea-ranker"]["artifacts"].append("a_artifact_nobody_produces")
    d = mutated_manifests(tmp_path, m)
    audit = audit_package(SKILLS_DIR, d / "artifact-graph.json", d / "skill-catalog.json")
    assert audit["status"] == "FAIL"
    assert audit["invariants"]["no_dangling_inputs"] is False
    assert any(f["check"] == "no_dangling_inputs" for f in audit["findings"])


def test_the_audit_catches_a_dependency_cycle(tmp_path):
    def m(g):
        g["depends_on"]["literature-provider-manager"] = ["citation-resolver"]
    d = mutated_manifests(tmp_path, m)
    audit = audit_package(SKILLS_DIR, d / "artifact-graph.json", d / "skill-catalog.json")
    assert audit["invariants"]["acyclic"] is False
    assert any(f["check"] == "acyclic" for f in audit["findings"])


def test_the_audit_catches_a_second_producer_for_one_artifact(tmp_path):
    def m(g):
        g["internal_artifacts"].setdefault("idea-ranker", {})["coverage_report"] = {
            "path": "x.json", "schema": None, "description": "shadow copy"}
    d = mutated_manifests(tmp_path, m)
    audit = audit_package(SKILLS_DIR, d / "artifact-graph.json", d / "skill-catalog.json")
    assert audit["invariants"]["single_producer"] is False


def test_the_audit_catches_documentation_drifting_from_the_contract(tmp_path):
    def m(g):
        g["depends_on"]["idea-ranker"] = []
        g["consumers"]["idea-ranker"]["artifacts"] = []
    d = mutated_manifests(tmp_path, m)
    audit = audit_package(SKILLS_DIR, d / "artifact-graph.json", d / "skill-catalog.json")
    assert any(f["check"] == "doc_matches_contract" and f["skill"] == "idea-ranker"
               for f in audit["findings"])


def test_the_audit_flags_template_clones_as_low_specificity(tmp_path):
    skills = tmp_path / "skills"
    for name in ("idea-ranker", "idea-evaluator"):
        (skills / name).mkdir(parents=True)
        shutil.copy(SKILLS_DIR / name / "SKILL.md", skills / name / "SKILL.md")
    # give both the same procedure: the signature of a placeholder
    for name in ("idea-ranker", "idea-evaluator"):
        p = skills / name / "SKILL.md"
        text = p.read_text()
        head, _, _ = text.partition("## Procedure")
        p.write_text(head + "## Procedure\n\n1. Do the thing.\n2. Report it.\n"
                     + text[text.index("## Hard gates"):])
    audit = audit_package(skills, MANIFESTS / "artifact-graph.json",
                          MANIFESTS / "skill-catalog.json")
    clones = [f for f in audit["findings"] if f["check"] == "template_clone"]
    assert clones and "idea-ranker" in clones[0]["detail"]


def test_the_audit_fails_when_documentation_claims_code_that_is_not_there(tmp_path):
    skills = tmp_path / "skills"
    (skills / "idea-ranker").mkdir(parents=True)
    text = (SKILLS_DIR / "idea-ranker" / "SKILL.md").read_text().replace(
        "implementation_status: specification-ready", "implementation_status: implemented")
    text = text.replace("name: idea-ranker", "name: not-registered-anywhere", 1)
    (skills / "not-registered-anywhere").mkdir(parents=True)
    (skills / "not-registered-anywhere" / "SKILL.md").write_text(text)
    shutil.copy(SKILLS_DIR / "idea-ranker" / "SKILL.md", skills / "idea-ranker" / "SKILL.md")
    audit = audit_package(skills, MANIFESTS / "artifact-graph.json",
                          MANIFESTS / "skill-catalog.json")
    assert any(f["check"] == "doc_claims_runtime" for f in audit["findings"])
    assert audit["status"] == "FAIL"


# ==========================================================================
# skill-evolution-manager — bounded edit and promotion
# ==========================================================================
def scorecard(version, *, train, held_out, guardrails=None, floor_measured=True,
              digest=CONTRACT_DIGEST, suite_hash="s" * 32, evaluator_digest="e" * 32,
              integrity=0, not_run=0):
    return {
        "contract_digest": digest, "skill_version": version,
        "suite_id": "meta-suite-v1", "suite_hash": suite_hash,
        "evaluator_digest": evaluator_digest,
        "means": {"train": train, "held_out": held_out},
        "guardrails": guardrails if guardrails is not None else {"no_fabricated_citation": 1.0},
        "integrity_violations": integrity, "n_not_run": not_run,
        "n_tasks": 5, "n_scored": 5, "n_invalid": 0,
        "self_reported_success_counted": False,
        "benchmark": {"version": "1", "content_hash": "b" * 32,
                      "contamination_floor": 0.2 if floor_measured else None,
                      "contamination_floor_measured": floor_measured,
                      "usable_for_promotion_gating": floor_measured},
        "promotion_eligible": floor_measured,
    }


EDIT = {
    "target_skill": "idea-ranker",
    "rationale": "runs kept collapsing the Pareto front to a single scalar",
    "current_version": "idea-ranker@1.0.0",
    "edit_ops": [{"op": "add", "section": "Hard gates",
                  "text": "- A single scalar ranking may not replace the Pareto front."}],
}


def evo_ctx(tmp_path, baseline, **config):
    ctx = ctx_for(tmp_path, **config)
    ctx.store.write("research-eval-harness", "eval_scorecard", baseline)
    ctx.store.append_jsonl("research-eval-harness", "eval_runs",
                           [{"task_id": "T1", "score": 1.0}])
    ctx.store.write("research-eval-harness", "eval_failure_taxonomy", "# Failure taxonomy\n")
    return ctx


def test_the_audit_runs_and_is_reported_both_ways(tmp_path):
    ctx = evo_ctx(tmp_path, scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5))
    result = SkillEvolutionManager()(ctx)
    machine = json.loads((tmp_path / "evals/machine_report.json").read_text())
    assert machine["status"] == "PASS" and machine["invariants"]["acyclic"] is True
    human = (tmp_path / "evals/skill_audit_report.md").read_text()
    assert "Invariants" in human and "reachability" in human
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False and "no candidate edit" in decision["blockers"]
    assert result.detail["audit_status"] == "PASS"


def test_online_self_edit_is_refused(tmp_path):
    ctx = evo_ctx(tmp_path, scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5),
                  candidate_edit=EDIT, apply_patch=True)
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "online_self_edit"
    assert "does not apply them" in e.value.reason


def test_editing_a_skill_while_a_run_is_in_flight_is_refused(tmp_path):
    ctx = evo_ctx(tmp_path, scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5),
                  candidate_edit=EDIT, active_run={"run_id": "r-42", "state": "EXPERIMENTING"})
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "online_self_edit"
    assert "mid-measurement" in e.value.reason


def test_an_edit_touching_more_than_one_skill_is_refused(tmp_path):
    multi = {**EDIT, "edit_ops": [
        dict(EDIT["edit_ops"][0]),
        {"op": "add", "section": "Hard gates", "target_skill": "idea-evaluator", "text": "- x"}]}
    ctx = evo_ctx(tmp_path, scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5),
                  candidate_edit=multi)
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "bounded_edit"
    assert "One skill at a time" in e.value.reason


def test_promotion_without_a_held_out_evaluation_is_refused(tmp_path):
    ctx = evo_ctx(tmp_path, scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5),
                  candidate_edit=EDIT)
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "held_out_evaluation_missing"
    # the diff is still produced: it is the thing a human is asked to evaluate
    assert "NOT APPLIED" in (tmp_path / "evals/skill_patch.diff").read_text()


def test_promotion_is_refused_when_the_contamination_floor_is_unmeasured(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5, floor_measured=False)
    cand = scorecard("idea-ranker@1.1.0", train=0.9, held_out=0.9, floor_measured=False)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "contamination_floor"
    assert "improvement in recall" in e.value.reason
    assert not (tmp_path / "evals/promotion_decision.json").exists()


def test_arms_that_differ_in_more_than_the_version_are_refused(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5)
    cand = scorecard("idea-ranker@1.1.0", train=0.9, held_out=0.9, suite_hash="z" * 32)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    with pytest.raises(GateBlocked) as e:
        SkillEvolutionManager()(ctx)
    assert e.value.gate == "score_comparability"
    assert "two moving parts" in e.value.reason


def test_two_arms_at_the_same_version_are_not_an_ab_comparison():
    a = scorecard("v1", train=0.5, held_out=0.5)
    with pytest.raises(GateBlocked) as e:
        assert_ab_arms(a, dict(a))
    assert "no edit to evaluate" in e.value.reason


def test_an_edit_that_improves_train_but_regresses_held_out_is_rejected(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.50, held_out=0.60)
    cand = scorecard("idea-ranker@1.1.0", train=0.85, held_out=0.52)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    result = SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False
    assert any("held-out delta" in b for b in decision["blockers"])
    comparison = json.loads((tmp_path / "evals/skill_eval_comparison.json").read_text())
    assert comparison["deltas"]["train"] > 0 > comparison["deltas"]["held_out"]
    assert comparison["overfit_signature"] is True
    assert any("overfit" in w for w in result.warnings)


def test_a_guardrail_regression_rejects_an_otherwise_better_edit(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5,
                     guardrails={"no_fabricated_citation": 1.0})
    cand = scorecard("idea-ranker@1.1.0", train=0.9, held_out=0.9,
                     guardrails={"no_fabricated_citation": 0.8})
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False
    assert any("guardrail regression" in b for b in decision["blockers"])


def test_an_unmeasured_guardrail_is_treated_as_a_regression(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5)
    cand = scorecard("idea-ranker@1.1.0", train=0.9, held_out=0.9, guardrails={})
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False
    assert any("unmeasured" in b for b in decision["blockers"])


def test_integrity_violations_in_the_candidate_arm_block_promotion(tmp_path):
    base = scorecard("idea-ranker@1.0.0", train=0.5, held_out=0.5)
    cand = scorecard("idea-ranker@1.1.0", train=0.9, held_out=0.9, integrity=1)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False
    assert any("integrity" in b for b in decision["blockers"])


def test_a_held_out_improvement_with_no_regression_is_promoted_but_never_applied(tmp_path):
    before = (SKILLS_DIR / "idea-ranker" / "SKILL.md").read_bytes()
    base = scorecard("idea-ranker@1.0.0", train=0.50, held_out=0.60)
    cand = scorecard("idea-ranker@1.1.0", train=0.55, held_out=0.70)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand)
    result = SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is True and decision["blockers"] == []
    assert decision["target_skill"] == "idea-ranker"
    assert decision["applied"] is False and decision["rollback_pointer"]["current_version"]
    assert decision["changelog"] == EDIT["rationale"]
    patch = (tmp_path / "evals/skill_patch.diff").read_text()
    assert "Pareto front" in patch and "+++ b/idea-ranker/SKILL.md" in patch
    assert (SKILLS_DIR / "idea-ranker" / "SKILL.md").read_bytes() == before, \
        "skill-evolution-manager must never write into the live skills directory"
    assert result.detail["promoted"] is True


def test_a_failing_package_audit_blocks_promotion(tmp_path):
    def m(g):
        g["consumers"]["idea-ranker"]["artifacts"].append("a_artifact_nobody_produces")
    d = mutated_manifests(tmp_path, m)
    base = scorecard("idea-ranker@1.0.0", train=0.50, held_out=0.60)
    cand = scorecard("idea-ranker@1.1.0", train=0.55, held_out=0.70)
    ctx = evo_ctx(tmp_path, base, candidate_edit=EDIT, candidate_scorecard=cand,
                  skill_catalog_source=str(d))
    SkillEvolutionManager()(ctx)
    decision = json.loads((tmp_path / "evals/promotion_decision.json").read_text())
    assert decision["promote"] is False
    assert any("audit FAILED" in b for b in decision["blockers"])

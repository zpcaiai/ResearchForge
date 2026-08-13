"""Behavioural tests for the planning stage — mostly, tests of what it refuses.

Direct Context construction rather than the runner subprocess: the compiler's
upstream is four artifacts, and seeding them through their real producers is both
cheaper and more precise than driving the whole pipeline to get there. The guard
methods are also exercised directly, because the interesting cases are the specs
the compiler must never emit — which by construction it does not emit.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from researchforge.artifacts import ArtifactStore
from researchforge.errors import GateBlocked, SchemaViolation
from researchforge.provenance import ProvenanceLog
from researchforge.providers import QuotaLedger, build_model_provider
from researchforge.skill import Context, get
from researchforge.skills.planning import ResearchBlueprintCompiler

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"

ENVELOPE = {"gpu_hours": 8, "wallclock_hours": 24, "usd": 40, "seeds": 5,
            "datasets": [{"name": "toy", "split": "test", "resolved": True}]}


def make_ctx(tmp_path, config=None, mode="auto"):
    prov = ProvenanceLog(tmp_path)
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test", provenance=prov)
    return Context(project=tmp_path, run_id="test", mode=mode, store=store, prov=prov,
                   quota=QuotaLedger(tmp_path / "quota.jsonl"),
                   model=build_model_provider("offline", offline=True), scholarly=[],
                   config=config or {}, offline=True)


def seed(store, *, comparison_mode="CM_NONE", level="RL0", pinned="abc123", repos=1):
    store.write("result-reproducer", "source_repro_report", {
        "target_paper_id": "p", "target_kind": "source_paper", "level": level,
        "assessed_at_run_id": "test", "claim_comparisons": [], "failure_codes": ["NO_CODE"],
        "environment_digest": "none", "timebox_seconds": 1.0, "timebox_exhausted": False})
    store.write("result-reproducer", "baseline_assets", {
        "repos": [{"url": "https://github.com/x/y"}] * repos,
        "checkpoints": [], "datasets": [], "pinned_revision": pinned})
    store.write("reproduction-fallback-planner", "comparison_mode", {
        "mode": comparison_mode, "derived_from_level": level,
        "admissible_idea_modes": ["explain_diagnose", "benchmark_evaluate"],
        "forbidden_claim_patterns": ["outperforms", "state-of-the-art"],
        "disclosure_required": {
            "required": comparison_mode != "CM_MEASURED",
            "text_template": "The comparison baseline was not reproduced locally.",
            "must_appear_in": ["experimental setup", "limitations"]}})
    store.write("user-feedback-gate", "selected_direction", {
        "selected_idea_ids": ["I-001"], "comparison_mode": comparison_mode,
        "rejected_but_retained": ["I-002"]})


def blueprint_config(**over):
    cfg = {"hypothesis": "adding a gate to the router reduces expert collapse",
           "candidate_method": "top-k router with a load-balancing gate",
           "resource_envelope": dict(ENVELOPE)}
    cfg.update(over)
    return cfg


def compile_blueprint(tmp_path, *, comparison_mode="CM_NONE", config=None, **seed_kw):
    ctx = make_ctx(tmp_path, config or blueprint_config())
    seed(ctx.store, comparison_mode=comparison_mode, **seed_kw)
    return ctx, get("research-blueprint-compiler")(ctx)


def specs_of(tmp_path):
    # experiments/*.yaml is a literal path; the store splits on '|', not on globs
    return json.loads((tmp_path / "experiments" / "*.yaml").read_text())


# ======================================================================
# comparison_mode is a hard constraint
# ======================================================================
def test_cm_none_compiles_no_comparative_experiment(tmp_path):
    ctx, res = compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    specs = specs_of(tmp_path)
    assert specs, "CM_NONE must still yield a plan; halting is the old broken behaviour"
    assert all(s["claim_type"] != "comparative" for s in specs)
    assert all(s["comparative_claim"] is False for s in specs)
    # and the plan it produced instead is the admissible kind of work
    assert {"diagnostic", "evaluation"} <= {s["claim_type"] for s in specs}
    assert any("refused" in w or "refused" in w.lower() for w in res.warnings)


def test_cm_none_baselines_are_internal_not_the_source_paper(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    kinds = {s["baseline"]["kind"] for s in specs_of(tmp_path)}
    assert "locally_measured" not in kinds and "reported_by_authors" not in kinds
    assert "internal_reference_condition" in kinds


def test_cm_none_acceptance_criteria_state_the_refusal(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    md = (tmp_path / "experiments" / "acceptance_criteria.md").read_text()
    assert "No comparative performance claim is admissible" in md
    assert "it was refused" in md


def test_comparative_spec_under_cm_none_is_blocked(tmp_path):
    """The guard, not just the code path that happens to avoid it."""
    smuggled = [{"experiment_id": "E-666", "claim_type": "comparative",
                 "comparative_claim": True, "invalid_conditions": [{"code": "X"}]}]
    with pytest.raises(GateBlocked, match="comparative performance claim") as e:
        ResearchBlueprintCompiler()._assert_mode_respected(smuggled, "CM_NONE")
    assert e.value.gate == "comparison_mode"
    assert "diagnostic" in e.value.remediation


def test_cm_reported_carries_disclosure_into_every_comparative_spec(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_REPORTED")
    comparative = [s for s in specs_of(tmp_path) if s["claim_type"] == "comparative"]
    assert comparative
    for s in comparative:
        assert s["disclosure"]["required"] is True
        assert s["disclosure"]["enforced_as"] == "acceptance_criterion"
        crit = [c for c in s["acceptance_criteria"] if c["kind"] == "disclosure"]
        assert crit and crit[0]["failure_if_absent"] is True
        assert "not reproduced locally" in crit[0]["statement"]
        assert any(c["code"] == "DISCLOSURE_ABSENT" for c in s["invalid_conditions"])


def test_cm_reported_disclosure_reaches_the_acceptance_criteria_document(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_REPORTED")
    md = (tmp_path / "experiments" / "acceptance_criteria.md").read_text()
    assert "Mandatory disclosure" in md
    assert "The comparison baseline was not reproduced locally." in md
    assert "**fails**" in md
    bp = json.loads((tmp_path / "research_blueprint.yaml").read_text())
    project = [c for c in bp["acceptance_criteria"] if c["experiment_id"] == "*"][0]
    assert any(c["kind"] == "disclosure" for c in project["criteria"])


def test_cm_reported_comparative_spec_without_disclosure_is_blocked():
    naked = [{"experiment_id": "E-001", "claim_type": "comparative",
              "invalid_conditions": [{"code": "X"}]}]
    with pytest.raises(GateBlocked, match="must carry the disclosure forward"):
        ResearchBlueprintCompiler()._assert_mode_respected(naked, "CM_REPORTED")


def test_cm_measured_produces_a_comparative_plan(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_MEASURED")
    specs = specs_of(tmp_path)
    primary = [s for s in specs if s["experiment_id"] == "E-001"][0]
    assert primary["claim_type"] == "comparative" and primary["comparative_claim"] is True
    assert primary["baseline"]["kind"] == "locally_measured"
    assert any(c["code"] == "COMPARISON_MODE_DOWNGRADED" for c in primary["invalid_conditions"])


# ======================================================================
# falsifiability
# ======================================================================
def test_every_spec_declares_invalid_conditions(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    for s in specs_of(tmp_path):
        codes = {c["code"] for c in s["invalid_conditions"]}
        assert codes, f"{s['experiment_id']} cannot be invalidated"
        assert {"SEEDS_TOO_FEW", "EVALUATOR_CHANGED_MID_RUN"} <= codes
        for c in s["invalid_conditions"]:
            assert c["why"] and c["detect"], "an invalid condition nobody can detect is decoration"


def test_spec_without_invalid_conditions_is_rejected_by_the_guard():
    with pytest.raises(GateBlocked, match="no invalid_conditions") as e:
        ResearchBlueprintCompiler()._assert_falsifiable(
            [{"experiment_id": "E-001", "claim_type": "diagnostic", "invalid_conditions": []}])
    assert e.value.gate == "falsifiability"
    assert "void" in str(e.value)


def test_spec_without_invalid_conditions_is_rejected_by_the_schema(tmp_path):
    """Two independent refusals: the guard above, and the contract store itself."""
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test")
    with pytest.raises(SchemaViolation, match="ExperimentSpec"):
        store.write("research-blueprint-compiler", "experiment_specs", [{
            "experiment_id": "E-001", "hypothesis": "h", "baseline": {}, "candidate": {},
            "datasets": [], "metrics": [], "seeds": [1]}])


def test_unpinned_baseline_becomes_an_invalid_condition(tmp_path):
    compile_blueprint(tmp_path, comparison_mode="CM_NONE", pinned=None, repos=0)
    for s in specs_of(tmp_path):
        assert any(c["code"] == "BASELINE_NOT_ESTABLISHED" for c in s["invalid_conditions"])


def test_comparative_claim_on_too_few_seeds_is_blocked(tmp_path):
    cfg = blueprint_config(resource_envelope={**ENVELOPE, "seeds": 2})
    with pytest.raises(GateBlocked, match="dispersion to estimate"):
        compile_blueprint(tmp_path, comparison_mode="CM_MEASURED", config=cfg)


# ======================================================================
# ablations
# ======================================================================
def test_each_mechanism_gets_an_isolation_test_referenced_by_a_spec(tmp_path):
    cfg = blueprint_config(mechanisms=[{"claim": "load balancing gate"},
                                       {"claim": "top-k sparsity"}])
    compile_blueprint(tmp_path, comparison_mode="CM_NONE", config=cfg)
    plan = json.loads((tmp_path / "experiments" / "ablation_plan.yaml").read_text())
    assert len(plan["ablations"]) == 2
    for ab in plan["ablations"]:
        assert ab["isolation_test"]["kind"] == "remove_mechanism"
        assert {c["kind"] for c in ab["counterfactual_controls"]} == {"matched_compute",
                                                                     "randomized_mechanism"}
        assert ab["invalidates_claim_if"]
    specs = specs_of(tmp_path)
    referenced = {a for s in specs for a in s.get("ablations", [])}
    assert {ab["ablation_id"] for ab in plan["ablations"]} <= referenced
    isolation = [s for s in specs if s["claim_type"] == "ablation"]
    assert len(isolation) == 2 and all(s["null_result_is_publishable"] for s in isolation)


def test_undecomposed_method_yields_one_mechanism_and_says_so(tmp_path):
    _, res = compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    plan = json.loads((tmp_path / "experiments" / "ablation_plan.yaml").read_text())
    assert len(plan["mechanisms"]) == 1
    assert plan["mechanisms"][0]["decomposed_by"].startswith("none")
    assert any("undecomposed mechanism" in w for w in res.warnings)


# ======================================================================
# inputs that cannot be invented
# ======================================================================
@pytest.mark.parametrize("drop,needle", [
    ("hypothesis", "falsifiable"),
    ("candidate_method", "nothing to specify"),
    ("resource_envelope", "budgets"),
])
def test_missing_external_is_a_gate_not_a_guess(tmp_path, drop, needle):
    cfg = blueprint_config()
    cfg.pop(drop)
    with pytest.raises(GateBlocked, match=needle) as e:
        compile_blueprint(tmp_path, comparison_mode="CM_NONE", config=cfg)
    assert e.value.gate == "external_input" and drop in e.value.remediation


def test_budgets_are_null_when_unbudgeted_not_estimated(tmp_path):
    cfg = blueprint_config(resource_envelope={"seeds": 5})
    _, res = compile_blueprint(tmp_path, comparison_mode="CM_NONE", config=cfg)
    bp = json.loads((tmp_path / "research_blueprint.yaml").read_text())
    assert bp["budgets"]["gpu_hours"] is None and bp["budgets"]["usd"] is None
    assert any("null, not" in w for w in res.warnings)


def test_blueprint_is_written_and_schema_valid(tmp_path):
    _, res = compile_blueprint(tmp_path, comparison_mode="CM_NONE")
    bp = json.loads((tmp_path / "research_blueprint.yaml").read_text())
    for k in ("blueprint_id", "selected_idea_id", "hypothesis", "acceptance_criteria",
              "stages", "budgets"):
        assert bp[k] not in (None, "", [], {})
    assert bp["comparison_mode"] == "CM_NONE"
    assert res.next_state == "BLUEPRINT_READY"
    dag = json.loads((tmp_path / "experiments" / "dag.json").read_text())
    assert dag["nodes"] and dag["edges"]


def test_a_cyclic_stage_graph_is_blocked():
    dag = {"nodes": [{"id": "A"}, {"id": "B"}],
           "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]}
    with pytest.raises(GateBlocked, match="cycle"):
        ResearchBlueprintCompiler()._assert_acyclic(dag)


# ======================================================================
# evaluator-builder
# ======================================================================
EVAL_CONFIG = {
    "research_objective": "answer the question with the correct label and supporting spans",
    "submission_schema": {"properties": {"label": {"type": "string"},
                                         "spans": {"type": "array"}},
                          "required": ["label", "spans"]},
}


def build_evaluator(tmp_path, config=None):
    ctx = make_ctx(tmp_path, config if config is not None else dict(EVAL_CONFIG))
    return ctx, get("evaluator-builder")(ctx)


def test_hidden_tests_are_marked_unreadable_from_agent_context(tmp_path):
    build_evaluator(tmp_path)
    man = json.loads((tmp_path / "evaluation" / "hidden_tests" / "_manifest.json").read_text())
    assert man["agent_readable"] is False
    assert man["visibility"] == "grader_only"
    assert man["mount_policy"]["mount_into_agent_container"] is False
    assert man["payloads_written_here"] is False
    assert man["tests"] and all(t["payload_location"].startswith("grader-side") for t in man["tests"])


def test_no_hidden_test_payload_is_written_into_the_project(tmp_path):
    build_evaluator(tmp_path)
    files = sorted(p.name for p in (tmp_path / "evaluation" / "hidden_tests").iterdir())
    assert files == ["_manifest.json"], "an expected output inside the project tree is readable"
    man = json.loads((tmp_path / "evaluation" / "hidden_tests" / "_manifest.json").read_text())
    # the oracle is described and digested, never published
    assert all("expected_output" not in t for t in man["tests"])
    assert all(len(t["oracle_digest"]) == 64 for t in man["tests"])


def test_spec_carries_the_gitignore_note_and_binds_the_evaluator_digest(tmp_path):
    build_evaluator(tmp_path)
    spec = (tmp_path / "evaluation" / "evaluator_spec.md").read_text()
    assert "```gitignore" in spec and "evaluation/hidden_tests/**" in spec
    assert "not readable from the agent context" in spec
    man = json.loads((tmp_path / "evaluation" / "hidden_tests" / "_manifest.json").read_text())
    code = (tmp_path / "evaluation" / "evaluate.py").read_text()
    import hashlib
    assert man["evaluator_code_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert man["evaluator_code_sha256"] in spec


def run_evaluator(tmp_path, submission, reference):
    (tmp_path / "sub.json").write_text(json.dumps(submission))
    (tmp_path / "ref.json").write_text(json.dumps(reference))
    p = subprocess.run([sys.executable, str(tmp_path / "evaluation" / "evaluate.py"),
                        str(tmp_path / "sub.json"), str(tmp_path / "ref.json")],
                       capture_output=True, text=True)
    return json.loads(p.stdout), p.returncode


def test_generated_evaluator_actually_runs_and_scores(tmp_path):
    build_evaluator(tmp_path)
    out, code = run_evaluator(tmp_path, {"label": "yes", "spans": ["a", "b"]},
                              {"label": "yes", "spans": ["a", "b"]})
    assert code == 0 and out["valid"] is True and out["score"] == pytest.approx(1.0)
    out, _ = run_evaluator(tmp_path, {"label": "no", "spans": ["a"]},
                           {"label": "yes", "spans": ["a", "b"]})
    assert 0.0 < out["score"] < 1.0


def test_invalid_submission_scores_none_not_zero(tmp_path):
    build_evaluator(tmp_path)
    out, code = run_evaluator(tmp_path, {"label": "yes", "spans": [], "score": 1.0},
                              {"label": "yes", "spans": []})
    assert code == 2 and out["valid"] is False
    assert out["score"] is None, "0.0 is a real score; an invalid run must not produce one"
    assert out["invalidations"][0]["code"] == "SELF_REPORTED_SCORE"


def test_missing_required_field_is_invalid_not_wrong(tmp_path):
    build_evaluator(tmp_path)
    out, code = run_evaluator(tmp_path, {"label": "yes"}, {"label": "yes", "spans": []})
    assert code == 2 and out["score"] is None
    assert out["invalidations"][0]["code"] == "MISSING_REQUIRED_FIELDS"


def test_reference_leakage_is_rejected(tmp_path):
    build_evaluator(tmp_path)
    out, code = run_evaluator(tmp_path, {"label": "yes", "spans": [], "secret_key": 7},
                              {"label": "yes", "spans": [], "secret_key": 7})
    assert code == 2 and out["invalidations"][0]["code"] == "REFERENCE_LEAKAGE"


def test_evaluator_score_function_is_importable_by_a_grader(tmp_path):
    """`score(submission, reference)` is the contract experiment-runner calls."""
    build_evaluator(tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_eval",
                                                  tmp_path / "evaluation" / "evaluate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.score({"label": "a", "spans": []}, {"label": "a", "spans": []})["score"] == 1.0
    assert mod.score("not an object", {})["valid"] is False


def test_undeclared_gaming_patterns_are_named_as_unguarded(tmp_path):
    cfg = dict(EVAL_CONFIG, known_failure_modes=["model memorizes the answer key ordering"])
    _, res = build_evaluator(tmp_path, cfg)
    assert res.detail["unguarded_patterns"], "a prose pattern must not be claimed as guarded"
    spec = (tmp_path / "evaluation" / "evaluator_spec.md").read_text()
    assert "## Not guarded" in spec and "answer key ordering" in spec


@pytest.mark.parametrize("drop", ["research_objective", "submission_schema"])
def test_evaluator_refuses_to_invent_its_own_target(tmp_path, drop):
    cfg = dict(EVAL_CONFIG)
    cfg.pop(drop)
    with pytest.raises(GateBlocked) as e:
        build_evaluator(tmp_path, cfg)
    assert e.value.gate == "external_input"

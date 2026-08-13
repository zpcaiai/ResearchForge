"""What the analysis stage refuses, and why.

These tests are written from the refusals outward. The affirmative cases matter
much less than the negative ones: a statistics module that produces plausible
output on bad input is worse than no statistics module, because the output then
carries the authority of having been checked.
"""
import json
from pathlib import Path

import pytest

from researchforge.artifacts import ArtifactStore
from researchforge.errors import GateBlocked
from researchforge.provenance import ProvenanceLog
from researchforge.providers import OfflineStubProvider, QuotaLedger
from researchforge.skill import Context
from researchforge.skills.analysis import DataAnalyst, FindingMemory, IntegrityAuditor

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"

EVAL_V1 = "evaluate.py@1.4.0"
ENV_V1 = "sha256:env-a"


def ctx_for(tmp_path, **config):
    prov = ProvenanceLog(tmp_path)
    store = ArtifactStore(tmp_path, SCHEMAS, run_id="test", provenance=prov)
    return Context(project=tmp_path, run_id="test", mode="auto", store=store, prov=prov,
                   quota=QuotaLedger(tmp_path / "literature" / "quota_ledger.jsonl"),
                   model=OfflineStubProvider(), scholarly=[], config=config, offline=True)


def run(branch, metrics, seed, *, status="ok", evaluator=EVAL_V1, env=ENV_V1,
        experiment_id="E-001"):
    return {"run_id": f"{branch}-{experiment_id}-s{seed}", "experiment_id": experiment_id,
            "status": status, "metrics": metrics, "artifacts": [], "warnings": [],
            "provenance": {"seed": seed, "branch": branch, "evaluator_version": evaluator,
                           "environment_digest": env}}


def B(name, experiment_id="E-001"):
    """The branch id the analysis plane uses: one arm of one experiment.

    `run()` records `provenance.branch`, and the analysis groups by experiment AND
    arm — so that E-001's control and E-ABL-001's control do not become one group
    called "control". These tests name branches the same way the pipeline does.
    """
    return f"{experiment_id}:{name}"


def write_ledger(ctx, rows):
    ctx.store.write("experiment-runner", "experiment_ledger", rows)


def two_arm_ledger(effect=0.06, n=5, **kw):
    """A control and a treatment arm with a real, separated difference."""
    base = [0.700, 0.712, 0.695, 0.706, 0.703, 0.709, 0.698][:n]
    rows = []
    for i, v in enumerate(base):
        rows.append(run("control", {"accuracy": v}, i, **kw))
        rows.append(run("treatment", {"accuracy": round(v + effect, 4)}, i, **kw))
    return rows


def clean_dataset():
    """No overlap, no feature that determines y, train strictly before test."""
    return {
        "target": "y", "time_column": "t", "id_column": "id",
        "splits": {
            "train": [{"id": i, "t": i, "x": i * 0.5, "y": [0, 1, 1, 0, 1, 0][i % 6]}
                      for i in range(12)],
            "test": [{"id": 100 + i, "t": 100 + i, "x": i * 0.25,
                      "y": [1, 1, 0, 0, 1, 0][i]} for i in range(6)],
        }}


def analyse(tmp_path, rows, **config):
    ctx = ctx_for(tmp_path, **config)
    write_ledger(ctx, rows)
    DataAnalyst()(ctx)
    return ctx


# ----------------------------------------------------------------------
# an analysis of no data
# ----------------------------------------------------------------------
def test_empty_ledger_produces_no_analysis_at_all(tmp_path):
    ctx = ctx_for(tmp_path)
    with pytest.raises(GateBlocked) as e:
        DataAnalyst()(ctx)
    assert e.value.gate == "no_experiment_results"
    assert "nothing to analyse" in e.value.reason
    assert not (tmp_path / "analysis/analysis_results.json").exists()


def test_ledger_of_only_failures_is_not_summarised_as_results(tmp_path):
    ctx = ctx_for(tmp_path)
    write_ledger(ctx, [run("a", {}, 0, status="crashed"), run("a", {}, 1, status="crashed")])
    with pytest.raises(GateBlocked) as e:
        DataAnalyst()(ctx)
    assert e.value.gate == "no_usable_experiment_metrics"


def test_finding_memory_refuses_an_empty_ledger_too(tmp_path):
    ctx = ctx_for(tmp_path)
    with pytest.raises(GateBlocked):
        FindingMemory()(ctx)
    assert not (tmp_path / "findings.jsonl").exists()


def test_integrity_auditor_refuses_an_empty_ledger(tmp_path):
    ctx = ctx_for(tmp_path)
    with pytest.raises(GateBlocked):
        IntegrityAuditor()(ctx)
    assert not (tmp_path / "analysis/stats_audit.json").exists()


# ----------------------------------------------------------------------
# leakage is a blocker, not a warning
# ----------------------------------------------------------------------
def test_train_test_overlap_blocks_before_any_analysis(tmp_path):
    ds = clean_dataset()
    ds["splits"]["test"].append(dict(ds["splits"]["train"][3]))     # the same row, twice
    ctx = ctx_for(tmp_path, raw_dataset=ds)
    write_ledger(ctx, two_arm_ledger())
    with pytest.raises(GateBlocked) as e:
        DataAnalyst()(ctx)
    assert e.value.gate == "data_leakage"
    assert "train_test_overlap" in e.value.reason
    # the analysis must not exist, and the evidence for the refusal must
    assert not (tmp_path / "analysis/analysis_results.json").exists()
    log = [json.loads(l) for l in
           (tmp_path / "analysis/transform_log.jsonl").read_text().splitlines() if l]
    overlap = [e for e in log if e["op"] == "leakage_check.train_test_overlap"]
    assert overlap and overlap[0]["postcondition_ok"] is False


def test_target_leakage_is_a_blocker(tmp_path):
    ds = clean_dataset()
    for r in ds["splits"]["train"]:
        r["y_shifted"] = r["y"]          # a feature that is the outcome
    for r in ds["splits"]["test"]:
        r["y_shifted"] = r["y"]
    ctx = ctx_for(tmp_path, raw_dataset=ds)
    write_ledger(ctx, two_arm_ledger())
    with pytest.raises(GateBlocked) as e:
        DataAnalyst()(ctx)
    assert "target_leakage" in e.value.reason


def test_temporal_leakage_is_a_blocker(tmp_path):
    ds = clean_dataset()
    ds["splits"]["train"][0]["t"] = 999          # training after the test period begins
    ctx = ctx_for(tmp_path, raw_dataset=ds)
    write_ledger(ctx, two_arm_ledger())
    with pytest.raises(GateBlocked) as e:
        DataAnalyst()(ctx)
    assert "temporal_leakage" in e.value.reason


def test_absent_dataset_is_recorded_as_unchecked_not_clean(tmp_path):
    analyse(tmp_path, two_arm_ledger())
    res = json.loads((tmp_path / "analysis/analysis_results.json").read_text())
    suite = [c for c in res["leakage_checks"] if c["check"] == "leakage_suite"]
    assert suite and suite[0]["status"] == "NOT_RUN"
    assert "not 'clean'" in suite[0]["detail"]


def test_clean_data_passes_and_every_operation_is_logged(tmp_path):
    analyse(tmp_path, two_arm_ledger(), raw_dataset=clean_dataset())
    log = [json.loads(l) for l in
           (tmp_path / "analysis/transform_log.jsonl").read_text().splitlines() if l]
    ops = [e["op"] for e in log]
    assert {"load_raw", "profile", "leakage_check.train_test_overlap",
            "leakage_check.target_leakage", "leakage_check.temporal_leakage",
            "freeze_splits"} <= set(ops)
    assert all(e["postcondition_ok"] for e in log)
    assert all(e["reason"] for e in log), "an operation without a reason is not auditable"
    prepared = json.loads((tmp_path / "analysis/prepared/_manifest.json").read_text())
    assert prepared["raw_source_mutated"] is False
    assert prepared["splits"]["train"]["sha256"]


# ----------------------------------------------------------------------
# a fabricated number
# ----------------------------------------------------------------------
def _audit(tmp_path, ctx, **config):
    ctx.config.update(config)
    IntegrityAuditor()(ctx)
    return json.loads((tmp_path / "analysis/stats_audit.json").read_text())


def test_fabricated_mean_that_disagrees_with_the_ledger_is_caught(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(), claim_map={"accuracy": ["C-001"]})
    p = tmp_path / "analysis/analysis_results.json"
    doctored = json.loads(p.read_text())
    for g in doctored["groups"]:
        if g["branch"] == B("treatment"):
            g["mean"] = 0.99                       # the number nobody measured
    for r in doctored["reported"]:
        if r["branch"] == B("treatment"):
            r["value"] = 0.99
    ctx.store.write("data-analyst", "analysis_results", doctored)

    audit = _audit(tmp_path, ctx)
    codes = {f["code"] for f in audit["findings"]}
    assert "REPORTED_VALUE_MISMATCH" in codes
    bad = [f for f in audit["findings"] if f["code"] == "REPORTED_VALUE_MISMATCH"]
    assert all(f["severity"] == "BLOCKER" for f in bad)
    assert audit["evidence_lock"]["blocked"] is True
    assert "C-001" in audit["evidence_lock"]["blocked_claims"]
    assert "accuracy" in audit["evidence_lock"]["blocked_metrics"]


def test_tampered_per_seed_values_are_caught_against_the_ledger(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger())
    p = tmp_path / "analysis/analysis_results.json"
    doctored = json.loads(p.read_text())
    for g in doctored["groups"]:
        if g["branch"] == B("control"):
            g["values"] = [v - 0.2 for v in g["values"]]     # make the baseline look worse
            g["mean"] = sum(g["values"]) / len(g["values"])
    ctx.store.write("data-analyst", "analysis_results", doctored)
    audit = _audit(tmp_path, ctx)
    assert "VALUES_DIFFER_FROM_LEDGER" in {f["code"] for f in audit["findings"]}
    assert audit["evidence_lock"]["blocked"] is True


def test_a_metric_with_no_run_behind_it_is_a_blocker(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger())
    doctored = json.loads((tmp_path / "analysis/analysis_results.json").read_text())
    doctored["groups"].append({"group_id": "treatment::f1", "branch": B("treatment"), "metric": "f1",
                               "kind": "confirmatory", "values": [0.9], "mean": 0.9,
                               "n": 1, "seeds": [0], "ci95": None})
    ctx.store.write("data-analyst", "analysis_results", doctored)
    audit = _audit(tmp_path, ctx)
    assert "NO_RAW_SUPPORT" in {f["code"] for f in audit["findings"]}


def test_a_ledger_that_moved_since_the_analysis_is_a_blocker(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger())
    rows = two_arm_ledger() + [run("treatment", {"accuracy": 0.95}, 99)]
    write_ledger(ctx, rows)
    audit = _audit(tmp_path, ctx)
    assert "STALE_ANALYSIS" in {f["code"] for f in audit["findings"]}
    assert audit["ledger_unchanged_since_analysis"] is False


# ----------------------------------------------------------------------
# sample size, power, effect sizes
# ----------------------------------------------------------------------
def test_two_seeds_per_arm_refuses_to_produce_a_p_value(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=2))
    audit = _audit(tmp_path, ctx)
    t = audit["tests"][0]
    assert t["p_raw"] is None and t["refusal"]
    assert "not enough data here to say anything" in t["refusal"]
    assert "INSUFFICIENT_SEEDS" in {f["code"] for f in audit["findings"]}
    assert audit["evidence_lock"]["blocked"] is True


def test_power_is_reported_or_explicitly_refused(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=5))
    audit = _audit(tmp_path, ctx)
    for t in audit["tests"]:
        assert ("power" in t and t["power"]["power"] is not None) or t["power"]["refused"]
        assert t["mde_at_target_power"] is not None


def test_underpowered_null_is_not_reported_as_no_effect(tmp_path):
    # a tiny difference against seed noise: three seeds cannot resolve it
    rows = []
    for i, v in enumerate([0.700, 0.712, 0.690]):
        rows.append(run("control", {"accuracy": v}, i))
        rows.append(run("treatment", {"accuracy": v + 0.001}, i))
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    under = [f for f in audit["findings"] if f["code"] == "UNDERPOWERED"]
    assert under and under[0]["severity"] == "HIGH"
    assert "not 'there is no effect'" in under[0]["message"]
    meta = json.loads((tmp_path / "analysis/meta_analysis.json").read_text())
    readings = [e["reading"] for e in meta["evidence"]["null"]]
    assert any("underpowered" in r for r in readings)


def test_every_effect_carries_an_interval_not_just_a_point(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=5))
    audit = _audit(tmp_path, ctx)
    for t in audit["tests"]:
        assert t["effect_size"]["g"] is not None
        assert t["effect_size"]["ci95"] and len(t["effect_size"]["ci95"]) == 2
        assert t["difference_ci95"] and len(t["difference_ci95"]) == 2
    res = json.loads((tmp_path / "analysis/analysis_results.json").read_text())
    assert all(g["ci95"] or g["uncertainty_refused"] for g in res["groups"])


# ----------------------------------------------------------------------
# multiple comparisons
# ----------------------------------------------------------------------
def three_branches_two_metrics(n=5):
    rows = []
    for i, v in enumerate([0.70, 0.71, 0.69, 0.705, 0.702][:n]):
        rows.append(run("control", {"accuracy": v, "f1": v - 0.05}, i))
        rows.append(run("variant_a", {"accuracy": v + 0.06, "f1": v - 0.04}, i))
        rows.append(run("variant_b", {"accuracy": v + 0.002, "f1": v - 0.051}, i))
    return rows


def test_several_branches_and_metrics_trigger_a_named_correction(tmp_path):
    ctx = analyse(tmp_path, three_branches_two_metrics())
    audit = _audit(tmp_path, ctx)
    corr = audit["multiple_comparison_correction"]
    assert corr["applied"] is True
    assert corr["family_size"] == 6, corr          # 3 branch pairs x 2 metrics
    assert "Holm" in corr["method"]
    assert "Bonferroni" in corr["why"] and "Benjamini-Hochberg" in corr["why"]
    assert len(corr["branches_compared"]) == 3 and len(corr["metrics_compared"]) == 2
    for t in audit["tests"]:
        assert t["p_holm"] >= t["p_raw"] - 1e-12
        assert t["p_bh"] is not None


def test_a_single_comparison_is_not_corrected_and_says_why(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=5))
    audit = _audit(tmp_path, ctx)
    corr = audit["multiple_comparison_correction"]
    assert corr["applied"] is False and corr["family_size"] == 1
    assert "theatre" in corr["why"]


def test_a_win_that_survives_only_uncorrected_is_flagged(tmp_path):
    # many nearly-identical branches: one squeaks under alpha before correction
    rows = []
    base = [0.700, 0.702, 0.698, 0.701, 0.699]
    for i, v in enumerate(base):
        rows.append(run("control", {"accuracy": v}, i))
        rows.append(run("v1", {"accuracy": v + 0.0032}, i))
        rows.append(run("v2", {"accuracy": v + 0.0005}, i))
        rows.append(run("v3", {"accuracy": v - 0.0004}, i))
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    demoted = [t for t in audit["tests"]
               if t["significant_raw"] and not t["significant_corrected"]]
    assert demoted, "fixture must contain a raw-significant, corrected-non-significant test"
    assert "SURVIVES_ONLY_UNCORRECTED" in {f["code"] for f in audit["findings"]}
    assert audit["evidence_lock"]["blocked"] is True


# ----------------------------------------------------------------------
# comparability
# ----------------------------------------------------------------------
def test_differing_evaluator_versions_refuse_aggregation(tmp_path):
    rows = two_arm_ledger(n=4, evaluator=EVAL_V1) + \
        [run(b, {"accuracy": 0.80}, 10 + i, evaluator="evaluate.py@2.0.0")
         for i, b in enumerate(("control", "treatment"))]
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    blocked = [f for f in audit["findings"] if f["code"] == "INCOMPARABLE_STRATA"]
    assert blocked and blocked[0]["severity"] == "BLOCKER"
    assert "evaluator_version" in blocked[0]["message"]
    assert audit["evidence_lock"]["blocked"] is True
    meta = json.loads((tmp_path / "analysis/meta_analysis.json").read_text())
    assert meta["cross_stratum_aggregation"]["refused"] is True
    assert meta["pooled_effects"] == []
    assert len(meta["strata"]) == 2
    decision = (tmp_path / "analysis/decision.md").read_text()
    assert "refused" in decision


def test_differing_environment_digests_also_refuse_aggregation(tmp_path):
    rows = two_arm_ledger(n=4, env=ENV_V1) + \
        [run(b, {"accuracy": 0.80}, 10 + i, env="sha256:env-b")
         for i, b in enumerate(("control", "treatment"))]
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    f = [x for x in audit["findings"] if x["code"] == "INCOMPARABLE_STRATA"]
    assert f and "environment_digest" in f[0]["message"]


def test_undeclared_provenance_is_unverified_rather_than_verified(tmp_path):
    rows = []
    for i, v in enumerate([0.700, 0.712, 0.695, 0.706, 0.703]):
        for b, off in (("control", 0.0), ("treatment", 0.08)):
            rows.append({"run_id": f"{b}-{i}", "experiment_id": "E", "status": "ok",
                         "metrics": {"accuracy": round(v + off, 4)}, "artifacts": [],
                         "provenance": {"seed": i}, "branch": b})
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    f = [x for x in audit["findings"] if x["code"] == "COMPARABILITY_UNVERIFIED"]
    assert f and f[0]["severity"] == "MEDIUM"
    assert audit["evidence_lock"]["blocked"] is False, "unverified is not the same as refuted"


# ----------------------------------------------------------------------
# selective reporting and the keep/kill verdict
# ----------------------------------------------------------------------
def test_dropping_a_seed_from_the_report_is_caught(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=5))
    doctored = json.loads((tmp_path / "analysis/analysis_results.json").read_text())
    for g in doctored["groups"]:
        if g["branch"] == B("control"):
            g["values"] = g["values"][:3]        # quietly drop the inconvenient seeds
            g["n"] = 3
            g["mean"] = sum(g["values"]) / 3
    ctx.store.write("data-analyst", "analysis_results", doctored)
    audit = _audit(tmp_path, ctx)
    codes = {f["code"] for f in audit["findings"]}
    assert "VALUES_DIFFER_FROM_LEDGER" in codes or "SELECTIVE_SEED_REPORTING" in codes
    assert audit["evidence_lock"]["blocked"] is True


def test_one_outlier_win_among_failed_trials_is_not_a_go(tmp_path):
    rows = [run("treatment", {}, i, status="failed") for i in range(4)]
    rows += [run("treatment", {"accuracy": 0.94}, 4)]
    rows += [run("control", {"accuracy": 0.70}, i) for i in range(5)]
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    decision = (tmp_path / "analysis/decision.md").read_text()
    assert "**Recommendation: CONTINUE**" not in decision
    codes = {f["code"] for f in audit["findings"]}
    assert "HIGH_FAILURE_RATE" in codes and "INSUFFICIENT_SEEDS" in codes
    assert audit["evidence_lock"]["blocked"] is True


def test_a_clean_well_powered_result_locks(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(effect=0.08, n=6))
    result = IntegrityAuditor()(ctx)
    audit = json.loads((tmp_path / "analysis/stats_audit.json").read_text())
    assert audit["evidence_lock"]["blocked"] is False, audit["findings"]
    assert result.next_state == "EVIDENCE_LOCKED"
    assert "**Recommendation: CONTINUE**" in (tmp_path / "analysis/decision.md").read_text()


# ----------------------------------------------------------------------
# findings memory
# ----------------------------------------------------------------------
def full_stack(tmp_path, rows, **config):
    ctx = analyse(tmp_path, rows, **config)
    IntegrityAuditor()(ctx)
    FindingMemory()(ctx)
    findings = [json.loads(l) for l in
                (tmp_path / "findings.jsonl").read_text().splitlines() if l]
    negatives = [json.loads(l) for l in
                 (tmp_path / "findings/negative_findings.jsonl").read_text().splitlines() if l]
    return ctx, findings, negatives


def test_a_failed_branch_becomes_a_tagged_finding_not_a_deletion(tmp_path):
    rows = two_arm_ledger(n=5) + [run("treatment", {}, 9, status="crashed")]
    _, findings, negatives = full_stack(tmp_path, rows)
    impl = [n for n in negatives if n["kind"] == "implementation_failure"]
    assert impl, "a crashed branch must survive as a finding"
    assert impl[0]["is_implementation_failure"] is True
    assert "not a scientific null" in impl[0]["distinction"]
    assert impl[0]["pivot_suggestion"]
    # and it is in the main store, tagged — not only in the negatives file
    assert impl[0]["finding_id"] in {f["finding_id"] for f in findings}
    assert "negative" in next(f for f in findings
                             if f["finding_id"] == impl[0]["finding_id"])["tags"]


def test_an_adequately_powered_null_is_kept_as_a_scientific_negative(tmp_path):
    # A null only counts as a null when the interval excludes the effect worth
    # claiming. Here that effect is declared large (|g| >= 1.0), which twelve
    # seeds per arm can genuinely rule out; a small effect of interest would
    # need far more runs, and the module must not pretend otherwise.
    rows = []
    for i, v in enumerate([0.700, 0.712, 0.695, 0.706, 0.703, 0.709,
                           0.698, 0.704, 0.701, 0.707, 0.694, 0.711]):
        rows.append(run("control", {"accuracy": v}, i))
        rows.append(run("treatment", {"accuracy": round(v + 0.0001, 6)}, i))
    _, findings, negatives = full_stack(
        tmp_path, rows, keep_kill_criteria={"min_effect_size": 1.0})
    nulls = [n for n in negatives if n["kind"] == "scientific_null"]
    assert nulls, [n["kind"] for n in negatives]
    assert nulls[0]["is_implementation_failure"] is False
    assert "never deleted" in nulls[0]["retention"]


def test_boundary_conditions_are_findings_in_their_own_right(tmp_path):
    rows = two_arm_ledger(n=5) + [run("treatment", {}, 8, status="failed")]
    _, findings, _ = full_stack(tmp_path, rows)
    boundaries = [f for f in findings if f["kind"] == "boundary"]
    assert boundaries
    md = (tmp_path / "findings/boundary_conditions.md").read_text()
    assert boundaries[0]["finding_id"] in md
    graph = json.loads((tmp_path / "findings/memory_graph.json").read_text())
    assert graph["counts"]["boundary"] == len(boundaries)
    assert any(e["rel"] == "derived_from" for e in graph["edges"])


def test_nothing_is_discarded_before_it_is_distilled(tmp_path):
    rows = three_branches_two_metrics(n=4)
    _, findings, _ = full_stack(tmp_path, rows)
    covered = {b for f in findings for b in f["scope"]["branches"]}
    assert {B("control"), B("variant_a"), B("variant_b")} <= covered


def test_conflicting_memory_is_versioned_not_overwritten(tmp_path):
    ctx, findings, _ = full_stack(tmp_path, two_arm_ledger(effect=0.08, n=5))
    first = {f["finding_id"] for f in findings if f["status"] == "current"}
    # the same question, re-run with the opposite outcome
    write_ledger(ctx, two_arm_ledger(effect=-0.08, n=5))
    DataAnalyst()(ctx)
    IntegrityAuditor()(ctx)
    FindingMemory()(ctx)
    again = [json.loads(l) for l in
             (tmp_path / "findings.jsonl").read_text().splitlines() if l]
    superseded = [f for f in again if f["status"] == "superseded"]
    assert superseded, "an overwritten memory cannot be audited"
    assert superseded[0]["finding_id"] in first
    assert superseded[0]["superseded_by"]


def test_memory_is_marked_advisory(tmp_path):
    _, findings, _ = full_stack(tmp_path, two_arm_ledger(n=5))
    assert findings and all(f["advisory"] for f in findings)
    assert all("cannot be substituted" in f["advisory_note"] or
               "never stand in for" in f["advisory_note"] for f in findings)


def test_every_write_is_provenanced(tmp_path):
    full_stack(tmp_path, two_arm_ledger(n=5), raw_dataset=clean_dataset())
    events = [json.loads(l) for l in
              (tmp_path / "provenance.jsonl").read_text().splitlines() if l]
    writes = {e["artifact_id"] for e in events if e["kind"] == "artifact_write"}
    assert {"analysis_results", "analysis_plots", "analysis_code", "analysis_report",
            "data_profile", "data_transform_log", "prepared_data",
            "stats_audit", "meta_analysis", "meta_analysis_decision", "stats_required_fixes",
            "findings", "negative_findings", "boundary_conditions",
            "finding_memory_graph"} <= writes
    assert all(e["digest"] for e in events if e["kind"] == "artifact_write")


# ----------------------------------------------------------------------
# directional and numerical honesty
# ----------------------------------------------------------------------
def test_an_improvement_and_its_interval_point_the_same_way(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(effect=0.08, n=6))
    IntegrityAuditor()(ctx)
    meta = json.loads((tmp_path / "analysis/meta_analysis.json").read_text())
    assert meta["control_branch"] == B("control")
    for e in meta["evidence"]["positive"]:
        assert e["difference"] > 0 and e["ci95"][0] > 0, e
        assert e["g"] > 0
        assert e["branches"] == [B("treatment"), B("control")]


def test_lower_is_better_metrics_are_read_in_the_right_direction(tmp_path):
    rows = []
    for i, v in enumerate([0.30, 0.31, 0.29, 0.305, 0.302, 0.298]):
        rows.append(run("control", {"val_loss": v}, i))
        rows.append(run("treatment", {"val_loss": round(v - 0.08, 4)}, i))
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    assert audit["metric_directions"]["val_loss"]["direction"] == "lower_is_better"
    meta = json.loads((tmp_path / "analysis/meta_analysis.json").read_text())
    assert len(meta["evidence"]["positive"]) == 1, meta["evidence"]
    assert meta["evidence"]["positive"][0]["difference"] < 0     # loss went down


def test_an_undeclared_direction_is_labelled_as_assumed(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(n=5))
    audit = _audit(tmp_path, ctx)
    assert "assumed" in audit["metric_directions"]["accuracy"]["source"]


def test_power_is_a_number_or_a_refusal_never_nan(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(effect=0.5, n=6))   # an enormous effect
    audit = _audit(tmp_path, ctx)
    for t in audit["tests"]:
        p = t["power"]["power"]
        assert p is None or (0.0 <= p <= 1.0), t["power"]


def test_missing_experiment_specs_cannot_prove_post_hoc_promotion(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(effect=0.08, n=6), claim_map={"accuracy": ["C-1"]})
    audit = _audit(tmp_path, ctx)
    codes = {f["code"] for f in audit["findings"]}
    assert "PREREGISTRATION_UNVERIFIABLE" in codes and "POST_HOC_PROMOTION" not in codes
    assert audit["evidence_lock"]["blocked"] is False


def test_a_declared_metric_set_makes_post_hoc_promotion_provable(tmp_path):
    ctx = analyse(tmp_path, two_arm_ledger(effect=0.08, n=6),
                  confirmatory_metrics=["f1"], claim_map={"accuracy": ["C-1"]})
    audit = _audit(tmp_path, ctx)
    promo = [f for f in audit["findings"] if f["code"] == "POST_HOC_PROMOTION"]
    assert promo and promo[0]["severity"] == "HIGH"
    assert audit["evidence_lock"]["blocked"] is True


def test_repeated_seeds_are_not_independent_replicates(tmp_path):
    rows = [run("control", {"accuracy": 0.70 + 0.001 * i}, 0) for i in range(4)]
    rows += [run("treatment", {"accuracy": 0.78 + 0.001 * i}, 1) for i in range(4)]
    ctx = analyse(tmp_path, rows)
    audit = _audit(tmp_path, ctx)
    f = [x for x in audit["findings"] if x["code"] == "NON_INDEPENDENT_OBSERVATIONS"]
    assert f and f[0]["severity"] == "HIGH"
    assert audit["evidence_lock"]["blocked"] is True

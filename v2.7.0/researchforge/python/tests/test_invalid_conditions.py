"""Void conditions must be evaluated, not recited.

Before this, every `invalid_condition` the blueprint compiler emitted was prose,
so not one was ever checked on any run. An experiment that cannot be invalidated
cannot be confirmed either, which made every result the system produced formally
unfalsifiable — while the specs said otherwise in perfectly good English.
"""
import pytest

from researchforge.invalid_conditions import evaluate, evaluate_all


def run(exp="E-1", arm="candidate", digest="d1", metrics=None, cfg=None, status="COMPLETED"):
    return {"experiment_id": exp, "status": status, "metrics": metrics or {"acc": 0.5},
            "provenance": {"arm": arm, "evaluator_digest": digest, "config": cfg or {"lr": 1}}}


SEEDS3 = {"code": "SEEDS_TOO_FEW",
          "check": {"kind": "min_completed_runs_per_condition", "value": 3}}
DIGEST = {"code": "EVALUATOR_CHANGED_MID_RUN",
          "check": {"kind": "field_stable_across_runs", "field": "evaluator_digest"}}
NAMES = {"code": "METRIC_DEFINITION_DRIFT", "check": {"kind": "metric_names_stable"}}
CFG = {"code": "CONDITIONS_NOT_MATCHED",
       "check": {"kind": "configs_match_except", "ignore": ["seed", "arm"]}}


def test_too_few_seeds_fires():
    v = evaluate(SEEDS3, experiment_id="E-1", ledger=[run(), run()])
    assert v["status"] == "FIRED" and v["short"] == {"candidate": 2}


def test_enough_seeds_is_clear():
    assert evaluate(SEEDS3, experiment_id="E-1",
                    ledger=[run() for _ in range(3)])["status"] == "CLEAR"


def test_only_completed_runs_count_toward_the_seed_floor():
    led = [run(), run(), run(status="FAILED"), run(status="NOT_RUN")]
    assert evaluate(SEEDS3, experiment_id="E-1", ledger=led)["status"] == "FIRED"


def test_an_evaluator_swap_mid_experiment_fires():
    led = [run(digest="a"), run(digest="a"), run(digest="b")]
    v = evaluate(DIGEST, experiment_id="E-1", ledger=led)
    assert v["status"] == "FIRED" and v["distinct"] == ["a", "b"]


def test_an_unrecorded_field_is_unchecked_not_clear():
    """Absence of evidence is not stability.

    Treating "nobody wrote the digest down" as CLEAR is exactly how an evaluator
    swap goes unnoticed, and it is the more dangerous of the two possible errors.
    """
    led = [{"experiment_id": "E-1", "status": "COMPLETED", "metrics": {"acc": 1}}]
    assert evaluate(DIGEST, experiment_id="E-1", ledger=led)["status"] == "UNCHECKED"


def test_metric_set_drift_fires():
    led = [run(metrics={"acc": 1}), run(metrics={"acc": 1, "f1": 1})]
    assert evaluate(NAMES, experiment_id="E-1", ledger=led)["status"] == "FIRED"


def test_config_drift_fires_but_seed_and_arm_are_ignored():
    assert evaluate(CFG, experiment_id="E-1",
                    ledger=[run(arm="a"), run(arm="b")])["status"] == "CLEAR"
    assert evaluate(CFG, experiment_id="E-1",
                    ledger=[run(cfg={"lr": 1}), run(cfg={"lr": 2})])["status"] == "FIRED"


def test_prose_only_conditions_are_unchecked_never_satisfied():
    v = evaluate({"code": "X", "detect": "eyeball it"}, experiment_id="E-1", ledger=[run()])
    assert v["status"] == "UNCHECKED"


def test_an_unknown_check_kind_is_unchecked_rather_than_passing():
    v = evaluate({"code": "X", "check": {"kind": "invented"}}, experiment_id="E-1", ledger=[run()])
    assert v["status"] == "UNCHECKED" and "unknown check kind" in v["reason"]


def test_an_experiment_with_only_prose_conditions_is_not_falsifiable():
    spec = {"experiment_id": "E-1",
            "invalid_conditions": [{"code": "A", "detect": "look"}, {"code": "B", "detect": "look"}]}
    r = evaluate_all(spec, [run() for _ in range(5)])
    assert r["falsifiable"] is False
    assert r["void"] is False, "unfalsifiable is not the same as void"


def test_a_mixed_spec_is_falsifiable_and_reports_what_it_could_not_check():
    spec = {"experiment_id": "E-1", "invalid_conditions": [SEEDS3, {"code": "P", "detect": "look"}]}
    r = evaluate_all(spec, [run() for _ in range(3)])
    assert r["falsifiable"] is True and r["unchecked"] == ["P"] and r["void"] is False


def test_baseline_pin_presence_is_checkable():
    c = {"code": "BASELINE_NOT_ESTABLISHED",
         "check": {"kind": "artifact_field_present", "artifact": "baseline_assets",
                   "field": "pinned_revision"}}
    assert evaluate(c, experiment_id="E-1", ledger=[run()],
                    artifacts={"baseline_assets": {"pinned_revision": None}})["status"] == "FIRED"
    assert evaluate(c, experiment_id="E-1", ledger=[run()],
                    artifacts={"baseline_assets": {"pinned_revision": "abc"}})["status"] == "CLEAR"
    # not supplied is a different answer from not set
    assert evaluate(c, experiment_id="E-1", ledger=[run()], artifacts={})["status"] == "UNCHECKED"


def test_disclosure_presence_is_checkable():
    c = {"code": "DISCLOSURE_ABSENT",
         "check": {"kind": "text_present_in_artifact", "artifact": "manuscript_draft",
                   "text": "was not reproduced locally"}}
    assert evaluate(c, experiment_id="E-1", ledger=[run()],
                    artifacts={"manuscript_draft": "The baseline was not reproduced locally."}
                    )["status"] == "CLEAR"
    assert evaluate(c, experiment_id="E-1", ledger=[run()],
                    artifacts={"manuscript_draft": "We outperform the baseline."}
                    )["status"] == "FIRED"

"""The B-arm packet and its analysis, tested on the ways the blind breaks.

Every failure here yields a clean-looking table. An outcome field that leaks into
the packet, a swapped paper, an unattempted paper counted as a failure, or a
seventy-point-blind design reporting a twenty-point gap — none of them raises in
the wild, and all of them produce a number someone will quote.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs" / "study" / "b-arm"))
import analyse as an  # noqa: E402
import build_packet as bp  # noqa: E402

A_ARM = [{"date": "2025-10-07", "arxiv_id": f"25{i:02d}.0000{i}", "title": f"P{i}",
          "github_repo": f"https://github.com/x/p{i}", "github_stars": i, "sample_index": i,
          "codes": ["NO_CODE"], "level": "RL0", "level_rationale": "no code",
          "clone_ok": False, "resolve_ok": False, "entry_ok": None, "seconds": 1.0,
          "notes": {"revision": "abc"}} for i in range(1, 21)]


def fill(packet, levels, **over):
    for w, lv in zip(packet["worksheets"], levels):
        w.update({"level_reached": lv, "wall_clock_hours_used": 7.0,
                  "engineer_id": "eng-1", "saw_a_arm_material": False})
        w.update(over)
    return packet


# ----------------------------------------------------------------------
def test_no_a_arm_outcome_reaches_the_packet():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    sheets = json.dumps(packet["worksheets"])
    for field in ("level_rationale", "clone_ok", "resolve_ok", "seconds"):
        assert field not in sheets, f"{field} would tell the engineer how the agent did"
    assert "RL0" not in json.dumps([w["paper"] for w in packet["worksheets"]])
    # the names are listed in the packet on purpose: withholding that cannot be
    # audited is indistinguishable from forgetting
    assert set(packet["blinding"]["withheld_fields"]) >= {"level", "codes", "seconds"}
    assert len(key["a_arm_outcomes"]) == 8


def test_an_unclassified_a_arm_field_stops_the_build_rather_than_leaking():
    rows = [dict(r, agent_transcript="...") for r in A_ARM]
    with pytest.raises(SystemExit, match="does not classify"):
        bp.build(rows, 8, 1, 8.0)


def test_the_draw_is_seeded_and_reproducible():
    ids = lambda p: [w["paper"]["arxiv_id"] for w in p["worksheets"]]  # noqa: E731
    a, _ = bp.build(A_ARM, 8, 42, 8.0)
    b, _ = bp.build(A_ARM, 8, 42, 8.0)
    c, _ = bp.build(A_ARM, 8, 43, 8.0)
    assert ids(a) == ids(b) and ids(a) != ids(c)
    assert ids(a) != [r["arxiv_id"] for r in A_ARM[:8]], "an unshuffled draw is not a draw"


def test_a_swapped_paper_makes_the_two_rates_incomparable():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["paper"]["arxiv_id"] = "2599.99999"
    with pytest.raises(SystemExit, match="do not have a gap between them"):
        an.analyse(packet, key, A_ARM)


def test_an_unblinded_worksheet_leaves_the_ceiling_estimate():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["saw_a_arm_material"] = True
    res = an.analyse(packet, key, A_ARM)
    assert res["n_paired"] == 7
    assert res["excluded_unblinded"][0]["reason"].startswith("engineer reported")


def test_a_worksheet_over_its_timebox_is_reported_separately_not_merged():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["wall_clock_hours_used"] = 26.0
    res = an.analyse(packet, key, A_ARM)
    assert res["n_paired"] == 7 and len(res["excluded_over_timebox"]) == 1
    assert "different design" in res["excluded_over_timebox"][0]["reason"]


def test_an_unattempted_paper_is_not_run_not_a_failure():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["level_reached"] = None
    res = an.analyse(packet, key, A_ARM)
    assert res["n_paired"] == 7 and res["human"]["rate"] == 1.0
    assert len(res["not_run"]) == 1
    assert "makes the human arm look worse" in res["not_run_policy"]


def test_rl1_is_not_a_reproduction():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, ["RL1", "RL2"] * 4), key, A_ARM)
    assert res["human"]["rate"] == 0.0
    assert res["level_distribution"]["human"]["RL1"] == 4


def test_an_unrecognised_level_is_refused_rather_than_read_as_rl0():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["level_reached"] = "partial"
    with pytest.raises(SystemExit, match="not one of"):
        an.analyse(packet, key, A_ARM)


def test_a_difference_the_design_could_not_have_found_is_not_reported_as_one():
    """Three discordant pairs, all one way: exact McNemar p=0.25. Not a finding."""
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, ["RL3", "RL3", "RL3", "RL0", "RL0", "RL0", "RL0", "RL0"]),
                     key, A_ARM)
    assert res["difference"] == 0.375
    assert res["paired_test"]["discordant"] == 3 and res["paired_test"]["p_value"] == 0.25
    assert res["finding"] == "NOT_DETECTABLE"
    assert "not evidence of no difference" in res["why"]


def test_the_test_is_paired_because_the_papers_are_the_same():
    """The defect this replaced: a two-proportion test on a correlated design.

    Five discordant pairs all favouring the human is exact p=0.0625 — close, and
    the independent-sample MDD of 0.70 called the same data NOT_DETECTABLE while
    the paired instrument puts it at the edge of significance.
    """
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, ["RL3"] * 5 + ["RL0"] * 3), key, A_ARM)
    assert res["paired_test"]["p_value"] == 0.0625
    assert res["design"].startswith("paired")
    assert "inflates the sample size requirement" in res["why_paired"]


def test_concordant_pairs_carry_no_information_and_say_so():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, ["RL0"] * 8), key, A_ARM)
    assert res["paired_test"]["discordant"] == 0
    assert res["finding"] == "NO_DISCORDANT_PAIRS"
    assert res["difference"] == 0.0


def test_the_required_n_depends_on_discordance_not_on_the_arms_rates():
    """The correction: 0.20 needs 47 pairs at p_d=0.25, not the 99 the old
    two-independent-sample formula demanded."""
    assert an.required_pairs(0.2, 0.25) == 47
    assert an.required_pairs(0.2, 0.5) == 96
    assert an.required_pairs(0.4, 0.25) == 10
    # a difference cannot exceed the discordance it is carried by
    assert an.required_pairs(0.6, 0.25) is None
    assert an.required_pairs(0, 0.5) is None


def test_alpha_and_power_are_used_rather_than_accepted_and_ignored():
    base = an.required_pairs(0.2, 0.5)
    assert an.required_pairs(0.2, 0.5, alpha=0.01, power=0.95) > 2 * base
    assert round(an._z_two_sided(0.05), 6) == 1.959964
    assert round(an._z_power(0.8), 6) == 0.841621


def test_an_unanswered_blinding_question_is_not_a_no():
    """The packet emits the field as null; null used to pass the refusal."""
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    for w in packet["worksheets"]:
        w["level_reached"] = "RL4"      # everything else left at the emitted defaults
    res = an.analyse(packet, key, A_ARM)
    assert res["n_paired"] == 0 and res["finding"] == "NO_CEILING_ESTIMATE"
    assert len(res["excluded_unanswered"]) == 8
    assert "Unanswered is not 'no'" in res["excluded_unanswered"][0]["reason"]


def test_an_unanswered_hours_field_is_not_a_respected_timebox():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    packet = fill(packet, ["RL3"] * 8)
    packet["worksheets"][0]["wall_clock_hours_used"] = None
    res = an.analyse(packet, key, A_ARM)
    assert res["n_paired"] == 7
    assert "whether the timebox held is unknown" in res["excluded_unanswered"][0]["reason"]


def test_a_nested_carried_field_cannot_smuggle_the_outcome_through():
    rows = [dict(r, github_repo={"url": "u", "level": r["level"], "seconds": 14400})
            for r in A_ARM]
    with pytest.raises(SystemExit, match="nested structure"):
        bp.build(rows, 8, 1, 8.0)


def test_the_draw_does_not_favour_the_top_of_the_file():
    """The old LCG drew the last record half as often as the first ten."""
    import collections
    recs = [{"arxiv_id": str(i), "title": f"P{i}", "date": "d", "github_repo": "r",
             "github_stars": 0, "sample_index": i, "level": "RL0", "level_rationale": "",
             "codes": [], "clone_ok": True, "resolve_ok": True, "entry_ok": True,
             "seconds": 1.0, "notes": {}} for i in range(20)]
    c = collections.Counter()
    for seed in range(3000):
        for r in bp.draw(recs, 8, seed):
            c[r["arxiv_id"]] += 1
    rates = [c[str(i)] / 3000 for i in range(20)]
    assert 0.35 < min(rates) and max(rates) < 0.45, f"non-uniform draw: {rates}"


def test_a_gap_larger_than_the_design_can_resolve_is_reported():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, ["RL4"] * 8), key, A_ARM)
    assert res["difference"] == 1.0 and res["finding"] == "DIFFERENCE_OBSERVED"
    assert "rough ceiling, not a precise one" in res["why"]


def test_every_worksheet_lost_means_no_estimate_not_a_small_one():
    packet, key = bp.build(A_ARM, 8, 1, 8.0)
    res = an.analyse(fill(packet, [None] * 8), key, A_ARM)
    assert res["finding"] == "NO_CEILING_ESTIMATE"
    assert "remains unmeasured, which is where it started" in res["why"]


def test_the_minimum_detectable_difference_is_reported_at_the_observed_discordance():
    assert an.min_detectable_difference(8, 0.25) == pytest.approx(0.4249, abs=1e-3)
    assert an.min_detectable_difference(8, 0.5) == pytest.approx(0.6009, abs=1e-3)
    assert an.min_detectable_difference(0, 0.5) is None


def test_exact_mcnemar_matches_the_hand_computed_binomial():
    assert an.exact_mcnemar(0, 0)["p_value"] is None
    assert an.exact_mcnemar(5, 0)["p_value"] == 0.0625     # 2 * 1/32
    assert an.exact_mcnemar(6, 0)["p_value"] == 0.03125    # 2 * 1/64
    assert an.exact_mcnemar(3, 3)["p_value"] == 1.0
    assert an.exact_mcnemar(5, 1)["p_value"] == pytest.approx(0.21875)


def test_wilson_over_nothing_is_nothing():
    assert an.wilson(0, 0) is None
    lo, hi = an.wilson(0, 8)
    assert lo == 0.0 and 0.3 < hi < 0.4, "zero successes is not a zero-width interval"

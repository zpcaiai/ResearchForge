"""Scoring against the frozen benchmark, tested where a scorer would flatter itself.

Every interesting failure here produces a *higher* number, not an error: counting
an unadjudicated pair as a decision, letting one direction score against every
seed, scoring against a benchmark that was edited after the run, or reporting a
recall without the floor beside it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "benchmark" / "score_directions.py"
sys.path.insert(0, str(TOOL.parent))
import score_directions as sd  # noqa: E402

FREEZE = {"record_type": "freeze", "content_hash": "abc123", "benchmark_version": "1",
          "contamination_floor": {"floor_recall": 0.5, "measured": True}}


def pair(seed, golds, split="inside_cutoff"):
    return {"record_type": "pair", "seed_id": seed,
            "gold_directions": [{"gold_direction_id": f"{seed}::{g}", "split": split,
                                 "problem_delta": "p", "method_delta": "m",
                                 "mechanism": f"mech {g}", "demonstrating_experiment": "e"}
                                for g in golds]}


def write_bench(tmp_path, *pairs, freeze=None):
    p = tmp_path / "bench.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [freeze or FREEZE, *pairs]), encoding="utf-8")
    return p


def make_packet(tmp_path, bench, directions, k=10):
    d = tmp_path / "dirs.json"
    d.write_text(json.dumps(directions), encoding="utf-8")
    out = tmp_path / "packet.json"
    r = subprocess.run([sys.executable, str(TOOL), "--benchmark", str(bench), "--directions",
                        str(d), "--k", str(k), "--emit-packet", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8")), r.stdout


def adjudicate(packet, matches, rest="NO_MATCH"):
    for it in packet["items"]:
        gid = it["gold_direction_id"].rsplit("::", 1)[-1]
        it["verdict"] = "MATCH" if (it["seed_id"], it["generated_rank"], gid) in matches else rest
    return packet


# ----------------------------------------------------------------------
def test_the_packet_is_the_full_cross_product_not_a_shortlist(tmp_path):
    """A shortlisting function would be the real matcher, invisibly."""
    b = write_bench(tmp_path, pair("S1", ["g1", "g2", "g3"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b"]})
    assert len(packet["items"]) == 6
    assert all(it["verdict"] == "UNADJUDICATED" for it in packet["items"])


def test_only_the_top_k_generated_directions_are_eligible(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b", "c", "d"]}, k=2)
    assert {it["generated_rank"] for it in packet["items"]} == {1, 2}


def test_an_unadjudicated_pair_is_not_a_miss(tmp_path):
    """Counting undecided as wrong would make an unreviewed run look measured."""
    b = write_bench(tmp_path, pair("S1", ["g1", "g2"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    card = sd.score(packet, "human", "h")
    assert card["verdict"] == "INCOMPLETE"
    assert card["unadjudicated_pairs"] == 2
    assert "excluded from the score rather than counted as misses" in card["why"]


def test_a_score_at_the_floor_carries_no_capability_claim(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1", "g2"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b"]})
    card = sd.score(adjudicate(packet, {("S1", 1, "g1")}), "human", "h")
    assert card["recall_at_k"] == 0.5 and card["contamination_floor"] == 0.5
    assert card["verdict"] == "AT_OR_BELOW_FLOOR"


def test_only_the_margin_above_the_floor_is_treated_as_evidence(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1", "g2"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b"]})
    card = sd.score(adjudicate(packet, {("S1", 1, "g1"), ("S1", 2, "g2")}), "human", "h")
    assert card["verdict"] == "ABOVE_FLOOR" and card["recall_at_k"] == 1.0
    assert "0.500" in card["why"] and "not a significance test" in card["why"]


def test_a_benchmark_with_no_measured_floor_yields_no_readable_score(tmp_path):
    freeze = dict(FREEZE, contamination_floor={"floor_recall": None, "measured": False})
    b = write_bench(tmp_path, pair("S1", ["g1"]), freeze=freeze)
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    card = sd.score(adjudicate(packet, set()), "human", "h")
    assert card["verdict"] == "UNINTERPRETABLE"
    assert "unmeasured floor is not a low floor" in card["why"]


def test_the_same_gold_matched_twice_counts_once(tmp_path):
    """Otherwise emitting the same idea k times would score k/1."""
    b = write_bench(tmp_path, pair("S1", ["g1", "g2"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "a", "a"]})
    card = sd.score(adjudicate(packet, {("S1", 1, "g1"), ("S1", 2, "g1"), ("S1", 3, "g1")}),
                    "human", "h")
    assert card["recall_at_k"] == 0.5


def test_a_seed_with_no_generated_direction_scores_zero_by_absence_and_says_so(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]), pair("S2", ["g2"]))
    packet, out = make_packet(tmp_path, b, {"S1": ["a"]})
    assert packet["seeds_with_no_generated_directions"] == ["S2"]
    assert "scores 0 by absence, not by error" in out


def test_an_unknown_verdict_is_refused_rather_than_read_as_a_miss(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    packet["items"][0]["verdict"] = "PROBABLY"
    with pytest.raises(SystemExit, match="not one of"):
        sd.score(packet, "human", "h")


def test_a_model_judge_is_recorded_as_one_with_its_caveat(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    card = sd.score(adjudicate(packet, set()), "model", "claude-opus-5")
    assert card["adjudication"]["judge_kind"] == "model"
    assert "one level up" in card["adjudication"]["caveat"]


def test_the_scorecard_names_the_benchmark_it_was_measured_against(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    card = sd.score(adjudicate(packet, set()), "human", "h")
    assert card["benchmark_content_hash"] == "abc123"
    assert card["packet_hash"] and card["is_a_quality_measure"] is False


def test_an_unfrozen_benchmark_cannot_be_scored_against(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text(json.dumps(pair("S1", ["g1"])), encoding="utf-8")
    with pytest.raises(SystemExit, match="no freeze record"):
        sd.load_benchmark(p)


def test_a_portfolio_idea_with_no_seed_is_refused(tmp_path):
    p = tmp_path / "ideas.json"
    p.write_text(json.dumps({"ideas": [{"statement": "do something"}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="names no seed_id"):
        sd.load_directions(p)


def test_a_real_idea_portfolio_shape_is_accepted_without_reshaping(tmp_path):
    p = tmp_path / "ideas.json"
    p.write_text(json.dumps({"ideas": [
        {"seed_id": "S1", "problem_delta": "p", "mechanism": "m"},
        {"seed_id": "S1", "statement": "another"}]}), encoding="utf-8")
    got = sd.load_directions(p)
    assert list(got) == ["S1"] and len(got["S1"]) == 2


def test_the_held_out_subset_is_reported_separately_and_its_absence_named(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]), pair("S2", ["g2"], split="held_out_post_cutoff"))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"], "S2": ["b"]})
    card = sd.score(adjudicate(packet, {("S2", 1, "g2")}), "human", "h")
    assert card["held_out_recall_at_k"] == 1.0
    b2 = write_bench(tmp_path, pair("S1", ["g1"]))
    packet2, _ = make_packet(tmp_path, b2, {"S1": ["a"]})
    card2 = sd.score(adjudicate(packet2, set()), "human", "h")
    assert card2["held_out_recall_at_k"] is None
    assert "no uncontaminated subset" in card2["held_out_note"]


def test_the_two_representations_of_one_adjudication_agree(tmp_path):
    """The packet and the harness input must not produce different recalls.

    They exist separately because one is filled in by a person and the other is
    stamped with the system's identity. Two formats for one fact is exactly how a
    project ends up with two numbers and no way to tell which is right.
    """
    from researchforge.skills.meta import recall_at_k
    b = write_bench(tmp_path, pair("S1", ["g1", "g2", "g3"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b"]})
    packet = adjudicate(packet, {("S1", 1, "g1"), ("S1", 2, "g3")})
    card = sd.score(packet, "human", "h")
    payload = sd.to_harness_inputs(packet)
    gold = [f"S1::{g}" for g in ("g1", "g2", "g3")]
    ranks = {d["direction_id"]: d["rank"] for d in payload["system_directions"]}
    assert recall_at_k(gold, payload["match_adjudications"], 10, ranks) == card["recall_at_k"]


def test_a_match_outside_top_k_is_dropped_by_both_paths(tmp_path):
    from researchforge.skills.meta import recall_at_k
    b = write_bench(tmp_path, pair("S1", ["g1", "g2"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b", "c"]}, k=1)
    assert {it["generated_rank"] for it in packet["items"]} == {1}
    payload = sd.to_harness_inputs(adjudicate(packet, {("S1", 1, "g1")}))
    ranks = {d["direction_id"]: d["rank"] for d in payload["system_directions"]}
    assert recall_at_k(["S1::g1", "S1::g2"], payload["match_adjudications"], 1, ranks) == 0.5


# ======================================================================
# regressions: every one of these produced a HIGHER number, not an error
# ======================================================================
def test_a_seed_the_system_never_answered_counts_its_gold_as_missed(tmp_path):
    """Answering 1 of 5 seeds correctly used to report recall 1.0."""
    b = write_bench(tmp_path, *[pair(f"S{i}", [f"g{i}"]) for i in range(1, 6)])
    packet, out = make_packet(tmp_path, b, {"S1": ["a"]})
    card = sd.score(adjudicate(packet, {("S1", 1, "g1")}), "human", "h")
    assert card["recall_at_k"] == 0.2, "four unanswered seeds are four misses"
    assert card["verdict"] == "AT_OR_BELOW_FLOOR"
    assert card["seeds_with_no_generated_directions"] == ["S2", "S3", "S4", "S5"]
    assert "scores 0 by absence, not by error" in out


def test_the_headline_recall_matches_the_harness_on_unequal_gold_counts(tmp_path):
    """Macro over seeds and micro over gold gave different numbers from one file."""
    from researchforge.skills.meta import recall_at_k
    b = write_bench(tmp_path, pair("S1", ["g1"]), pair("S2", ["h1", "h2", "h3", "h4"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"], "S2": ["b"]})
    packet = adjudicate(packet, {("S1", 1, "g1")})
    card = sd.score(packet, "human", "h")
    payload = sd.to_harness_inputs(packet)
    ranks = {d["direction_id"]: d["rank"] for d in payload["system_directions"]}
    gold = ["S1::g1"] + [f"S2::h{i}" for i in range(1, 5)]
    assert card["recall_at_k"] == recall_at_k(gold, payload["match_adjudications"], 10, ranks)
    assert card["recall_at_k"] == 0.2 and card["macro_recall_over_seeds"] == 0.5


def test_a_score_exactly_at_the_floor_does_not_clear_it(tmp_path):
    """round(2/3, 6) is strictly greater than 2/3; the comparison must be unrounded."""
    freeze = dict(FREEZE, contamination_floor={"floor_recall": 2 / 3, "measured": True})
    b = write_bench(tmp_path, pair("S1", ["g1", "g2", "g3"]), freeze=freeze)
    packet, _ = make_packet(tmp_path, b, {"S1": ["a", "b"]})
    card = sd.score(adjudicate(packet, {("S1", 1, "g1"), ("S1", 2, "g2")}), "human", "h")
    assert card["recall_at_k"] == 0.666667
    assert card["verdict"] == "AT_OR_BELOW_FLOOR"


def test_no_overlap_between_benchmark_and_portfolio_is_diagnosed_not_a_crash(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"seed-1": ["a"]})
    card = sd.score(packet, "human", "h")
    assert card["verdict"] == "NOTHING_SUBMITTED"
    assert card["recall_at_k"] == 0.0, "0.0 is the right number; the verdict is the diagnosis"
    assert "id mismatch" in card["why"]


@pytest.mark.parametrize("value", ["use a better teacher", {"statement": "x"}, 3])
def test_a_seed_whose_value_is_not_a_list_is_refused(tmp_path, value):
    """list('a string') is 8 single-character directions a human then rates."""
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"S1": value}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not a list of directions"):
        sd.load_directions(p)


def test_a_duplicate_gold_id_would_shrink_the_denominator_and_is_refused(tmp_path):
    b = tmp_path / "b.jsonl"
    p = {"record_type": "pair", "seed_id": "S1", "gold_directions": [
        {"gold_direction_id": "S1::g1", "split": "inside_cutoff", "mechanism": "a"},
        {"gold_direction_id": "S1::g1", "split": "inside_cutoff", "mechanism": "b"},
        {"gold_direction_id": "S1::g2", "split": "inside_cutoff", "mechanism": "c"}]}
    b.write_text("\n".join(json.dumps(r) for r in [FREEZE, p]), encoding="utf-8")
    d = tmp_path / "d.json"
    d.write_text(json.dumps({"S1": ["a"]}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--benchmark", str(b), "--directions", str(d),
                        "--emit-packet", str(tmp_path / "p.json")], capture_output=True, text=True)
    assert r.returncode != 0 and "duplicate gold_direction_id" in r.stderr


def test_the_harness_file_is_not_written_for_a_packet_that_will_not_score(tmp_path):
    b = write_bench(tmp_path, pair("S1", ["g1"]))
    packet, _ = make_packet(tmp_path, b, {"S1": ["a"]})
    packet["items"][0]["verdict"] = "PROBABLY"
    pf = tmp_path / "filled.json"
    pf.write_text(json.dumps(packet), encoding="utf-8")
    hf = tmp_path / "harness.json"
    r = subprocess.run([sys.executable, str(TOOL), "--packet", str(pf),
                        "--emit-harness-inputs", str(hf)], capture_output=True, text=True)
    assert r.returncode != 0
    assert not hf.exists(), "a file from a run the tool refused to score outlived the refusal"

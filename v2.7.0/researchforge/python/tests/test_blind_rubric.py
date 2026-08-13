"""The blind rubric instrument, tested on the ways a blind stops being blind.

None of these produce an error in the wild. They produce a clean-looking table in
which the machine arm scores well because the raters could tell which arm it was.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "benchmark"))
import blind_rubric as br  # noqa: E402


def arms(n=10, machine_extra="", human_extra=""):
    m = [{"seed_id": "S1", "statement": f"alpha direction {i}{machine_extra}"} for i in range(n)]
    h = [{"seed_id": "S1", "statement": f"beta direction {i}{human_extra}"} for i in range(n)]
    return m, h


def fill(packet, key, machine_score, human_score, guess=None):
    arm = {k["entry_id"]: k["arm"] for k in key["key"]}
    for e in packet["entries"]:
        a = arm[e["entry_id"]]
        e["scores"] = {c["id"]: (machine_score if a == "machine" else human_score)
                       for c in br.CRITERIA}
        if guess is not None:
            e["arm_guess"] = a if guess else ("human" if a == "machine" else "machine")
    return packet


# ----------------------------------------------------------------------
def test_unbalanced_arms_are_refused():
    m, h = arms(10)
    with pytest.raises(SystemExit, match="unbalanced"):
        br.build(m, h[:8], 1, 8)


def test_a_panel_too_small_to_mean_anything_is_refused():
    m, h = arms(3)
    with pytest.raises(SystemExit, match="coin flip dressed as a rubric"):
        br.build(m, h, 1, 10)


def test_the_arm_labels_are_not_in_the_packet():
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    blob = json.dumps(packet["entries"])
    assert "machine" not in blob and '"arm"' not in blob
    assert len(key["key"]) == 20
    # the instructions do name both labels on purpose: a rater who does not know
    # the legal strings writes "AI", and grading that as a wrong guess reported a
    # fully unblinded panel as sitting at chance
    assert "'machine' or 'human'" in " ".join(packet["instructions"])


def test_the_packet_is_not_its_own_key():
    """entry_id used to be sha(arm, index, text) over data the packet carries."""
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    texts = {e["entry_id"]: e["text"] for e in packet["entries"]}
    recovered = 0
    for eid, text in texts.items():
        for arm in ("machine", "human"):
            for i in range(len(m)):
                if f"D-{br._sha([arm, i, text])[:10]}" == eid:
                    recovered += 1
    assert recovered == 0, "the arm label is brute-forceable from the packet alone"
    assert key["salt"] and key["salt"] not in json.dumps(packet)


def test_a_direction_that_normalises_to_nothing_is_refused():
    m, h = arms()
    m[0] = {"seed_id": "S1", "notes": "a field nobody reads"}
    with pytest.raises(SystemExit, match="normalised to empty text"):
        br.build(m, h, 7, 10)


def test_a_title_only_direction_is_read_the_same_way_the_scorer_reads_it():
    m, h = arms()
    m[0] = {"seed_id": "S1", "title": "distil the teacher into the student"}
    packet, _ = br.build(m, h, 7, 10)
    assert any("distil the teacher" in e["text"] for e in packet["entries"])


def test_editing_the_arm_labels_in_the_key_is_caught():
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    packet = chance_guesses(fill(packet, key, 5, 2, guess=True))
    for row in key["key"]:
        row["arm"] = "human" if row["arm"] == "machine" else "machine"
    with pytest.raises(SystemExit, match="edited after the packet was built"):
        br.analyse(packet, key)


def test_perfectly_inverted_guesses_are_a_broken_blind_not_a_held_one():
    """0.00 accuracy is exactly as much evidence as 1.00."""
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    packet = fill(packet, key, 5, 2, guess=False)   # every guess inverted
    res = br.analyse(packet, key)
    assert res["blind_check"]["arm_guess_accuracy"] == 0.0
    assert res["blind_check"]["deviation_from_chance"] == 0.5
    assert res["blind_check"]["held"] is False and res["reportable"] is False


def test_a_guess_in_the_raters_own_words_is_refused_not_scored_as_wrong():
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    packet = fill(packet, key, 4, 3, guess=True)
    for e in packet["entries"]:
        e["arm_guess"] = "AI" if e["arm_guess"] == "machine" else "person"
    res = br.analyse(packet, key)
    assert res["blind_check"]["graded_guesses"] == 0
    assert len(res["blind_check"]["unrecognised_guesses"]) == 20
    assert res["reportable"] is False
    assert "could not be graded" in res["why_not_reportable"]


def test_a_score_outside_the_rubric_range_is_refused():
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    packet = chance_guesses(fill(packet, key, 4, 3, guess=True))
    packet["entries"][0]["scores"][br.CRITERIA[0]["id"]] = 50
    res = br.analyse(packet, key)
    assert res["out_of_range_scores"][0]["value"] == 50.0
    assert res["reportable"] is False and "outside 1.0-5.0" in res["why_not_reportable"]


def test_an_unparsable_score_is_not_rated_rather_than_a_crash():
    m, h = arms()
    packet, key = br.build(m, h, 7, 10)
    packet = chance_guesses(fill(packet, key, 4, 3, guess=True))
    packet["entries"][0]["scores"][br.CRITERIA[0]["id"]] = "n/a"
    res = br.analyse(packet, key)
    assert res["unparsable_scores"][0]["value"] == "n/a"
    assert res["unrated_cells"] == 1 and res["reportable"] is False


def test_the_shuffle_does_not_correlate_arm_with_position():
    """A rater guessing purely from slot number must not beat chance."""
    hits = total = 0
    for seed in range(400):
        m, h = arms()
        packet, key = br.build(m, h, seed, 10, salt="fixed")
        arm = {k["entry_id"]: k["arm"] for k in key["key"]}
        for i, e in enumerate(packet["entries"]):
            guess = "machine" if i < 10 else "human"
            hits += guess == arm[e["entry_id"]]
            total += 1
    rate = hits / total
    assert 0.45 < rate < 0.55, f"position predicts the arm at {rate:.3f}"


def test_a_disputed_packet_is_rebuildable_from_its_key():
    m, h = arms()
    a, key = br.build(m, h, 7, 10)
    b, _ = br.build(m, h, 7, 10, salt=key["salt"])
    c, _ = br.build(m, h, 8, 10, salt=key["salt"])
    ids = lambda p: [e["entry_id"] for e in p["entries"]]  # noqa: E731
    assert ids(a) == ids(b), "the key carries the salt so the packet can be rebuilt"
    assert ids(a) != ids(c), "a different seed must give a different order"
    # and without the salt it is NOT rebuildable, which is what keeps the packet
    # from being its own key
    assert ids(a) != ids(br.build(m, h, 7, 10)[0])


def test_model_voice_and_citation_tells_are_stripped_and_reported():
    m, h = arms(machine_extra=". As an AI, I would suggest [12] this.")
    packet, key = br.build(m, h, 3, 10)
    blob = json.dumps(packet).lower()
    assert "as an ai" not in blob and "[12]" not in blob
    labels = {lab for t in key["tells_removed"] for lab in t["removed"]}
    assert {"model-voice phrasing", "citation marker"} <= labels


def test_an_edited_packet_no_longer_matches_its_key():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    packet["entries"][0]["text"] += " (edited after the key was written)"
    with pytest.raises(SystemExit, match="cannot be trusted"):
        br.analyse(packet, key)


def test_raters_who_can_tell_the_arms_apart_make_the_result_unreportable():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    res = br.analyse(fill(packet, key, 5, 2, guess=True), key)
    assert res["blind_check"]["arm_guess_accuracy"] == 1.0
    assert res["blind_check"]["held"] is False
    assert res["reportable"] is False
    assert "ratings of the arm" in res["blind_check"]["note"]


def test_a_blind_that_held_is_reportable_and_carries_the_difference():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    # raters at chance: half the guesses right
    res = br.analyse(chance_guesses(fill(packet, key, 4, 3, guess=True)), key)
    assert res["blind_check"]["held"] is True and res["reportable"] is True
    diffs = {c["criterion"]: c["difference"] for c in res["per_criterion"]}
    assert all(d == 1.0 for d in diffs.values())


def test_never_running_the_blind_check_is_not_the_same_as_it_holding():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    res = br.analyse(fill(packet, key, 4, 3, guess=None), key)
    assert res["blind_check"]["arm_guess_accuracy"] is None
    assert res["blind_check"]["held"] is None and res["reportable"] is False
    assert "not the same as it having held" in res["blind_check"]["note"]


def chance_guesses(packet):
    """Half the arm guesses right: raters at chance, so the blind held."""
    for i, e in enumerate(packet["entries"]):
        if i % 2:
            e["arm_guess"] = "human" if e["arm_guess"] == "machine" else "machine"
    return packet


def test_a_blank_score_is_not_a_one():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    packet = chance_guesses(fill(packet, key, 4, 4, guess=True))
    packet["entries"][0]["scores"][br.CRITERIA[0]["id"]] = None
    res = br.analyse(packet, key)
    assert res["unrated_cells"] == 1
    assert res["reportable"] is False and "unrated" in res["why_not_reportable"]
    first = next(c for c in res["per_criterion"] if c["criterion"] == br.CRITERIA[0]["id"])
    assert first["n"]["machine"] + first["n"]["human"] == 19


def test_the_result_declines_to_be_a_significance_test():
    m, h = arms()
    packet, key = br.build(m, h, 3, 10)
    res = br.analyse(fill(packet, key, 4, 3, guess=True), key)
    assert "significance test" in res["is_not"]

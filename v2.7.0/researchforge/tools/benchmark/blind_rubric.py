#!/usr/bin/env python3
"""Prepare and analyse the blind rubric: the half recall@k cannot supply.

WHY BOTH

recall@k against the retrospective benchmark catches regressions cheaply, and it
answers exactly one question: does this system name the directions the field took?
It cannot answer whether the directions are any good, for two reasons that do not
go away with more seeds. Not every good direction was published, so a direction the
benchmark scores as a miss may be better than the one it scores as a hit. And the
gold set is drawn from leaderboard succession, so a system that only ever proposes
"scale it up" scores well while proposing nothing worth reading.

The blind rubric is the other half: domain experts score generated directions
against human-authored ones **without knowing which is which**. If the raters can
tell, the comparison is over before it starts — so the interleaving, the
normalisation and the ordering are the instrument, not decoration.

WHAT THIS SCRIPT WILL NOT DO

It does not generate the human-authored directions, and it does not rate anything.
Both are the panel's job. What it does is make the packet hard to game: it strips
the tells, it fixes the order with a recorded permutation, it hides the key, and at
analysis time it refuses to report a comparison whose raters could identify the
arms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import secrets
import statistics
from pathlib import Path

#: Every criterion is scored 1-5 and every one is defined by what a 1 and a 5 look
#: like. A rubric that says "rate novelty" collects the rater's mood.
CRITERIA = [
    {"id": "specificity",
     "question": "Is this a direction someone could start on Monday?",
     "1": "names a goal, not a change ('improve robustness')",
     "5": "names the component to change and what to change it to"},
    {"id": "mechanism",
     "question": "Does it say why the change would work?",
     "1": "asserts an outcome with no mechanism",
     "5": "states a mechanism that predicts when it would fail as well as when it would work"},
    {"id": "falsifiability",
     "question": "Is there an experiment that could kill it?",
     "1": "no result would count against it",
     "5": "names the measurement and the outcome that would refute it"},
    {"id": "non_obviousness",
     "question": "Would a competent practitioner already have tried this?",
     "1": "the default next step anyone would take",
     "5": "a move a competent practitioner would not have reached for"},
    {"id": "worth_doing",
     "question": "If it worked, would it matter?",
     "1": "a marginal number on a saturated benchmark",
     "5": "would change what people do next"},
]
#: The only two strings a rater's arm guess may take. Anything else is refused
#: rather than scored as wrong: "AI", "model" and "system" all mean the rater
#: identified the machine arm, and grading them as misses reported a perfectly
#: unblinded panel as sitting at chance.
ARM_LABELS = frozenset({"machine", "human"})
SCORE_MIN, SCORE_MAX = 1.0, 5.0

#: Phrases that identify the arm rather than the direction. Stripping them is not
#: cosmetic: one "as an AI" or one citation-style bracket and the rater is no
#: longer blind for the rest of the session.
TELLS = [
    (re.compile(r"\b(as an ai|language model|i (?:would|will|can) (?:suggest|propose))\b", re.I),
     "model-voice phrasing"),
    (re.compile(r"\[[0-9]{1,3}\]|\(\w+ et al\.?,? \d{4}\)"), "citation marker"),
    (re.compile(r"\b(?:step|phase)\s*[1-9]\s*[:.]", re.I), "numbered-plan formatting"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
    (re.compile(r"^\s*(?:\*\*|##)", re.M), "markdown heading or bold lead"),
]


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _entries_hash(entries: list[dict]) -> str:
    """Hash of what the rater sees, and nothing else.

    The point of the hash is to catch an entry being edited between build and
    analysis — a reworded direction is a different direction, and the arm labels in
    the key would then be attached to text nobody rated.
    """
    return _sha([{k: e.get(k) for k in ("entry_id", "text")} for e in entries])


def shuffle(items: list, seed: int) -> list:
    """A deterministic shuffle whose seed is written into the key file.

    Deterministic so a disputed packet can be rebuilt and shown to have had the
    order it claims. Mersenne Twister rather than a hand-rolled LCG: the previous
    version drew `j = state % (i+1)` from `state = (1103515245*state + 12345) mod
    2**31`, whose low two bits are a counter, so the last swaps were decided by
    seed parity alone. Because `build` lays the machine block before the human
    block, that bias became an arm-position correlation — a rater guessing purely
    from slot number and reading nothing scored 0.579, above chance and under the
    0.65 threshold, and the run would have been reported as "the blind held".
    """
    rng, out = random.Random(seed), list(items)
    rng.shuffle(out)
    return out


def normalise(text: str) -> tuple[str, list[str]]:
    """Strip the arm's fingerprints and report which ones were there."""
    found = []
    for rx, label in TELLS:
        if rx.search(text):
            found.append(label)
            text = rx.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, found


def build(machine: list[dict], human: list[dict], seed: int, min_per_arm: int,
          salt: str | None = None) -> tuple[dict, dict]:
    if len(machine) < min_per_arm or len(human) < min_per_arm:
        raise SystemExit(
            f"each arm needs at least {min_per_arm} directions; got {len(machine)} machine and "
            f"{len(human)} human. Below that the comparison is a coin flip dressed as a rubric.")
    if len(machine) != len(human):
        raise SystemExit(
            f"the arms are unbalanced ({len(machine)} vs {len(human)}). A rater who notices the "
            f"imbalance can guess the majority arm and score above chance without reading.")

    # Salted, and the salt lives only in the key. The previous id was
    # `sha(arm, index, text)[:10]` over data the packet itself carries, so anyone
    # holding the packet recovered the whole key by brute force over 2 arms x N
    # indices — 800 hashes for a 20-entry packet. The packet was the key.
    # Random per build, and recorded in the key. Passing it back in rebuilds the
    # packet byte-identically, which is what a disputed packet needs — and the
    # party disputing has to hold the key to make any claim about arms anyway.
    salt = salt or secrets.token_hex(16)
    entries, key, tells, blank = [], [], [], []
    for arm, items in (("machine", machine), ("human", human)):
        for i, d in enumerate(items):
            clean, found = normalise(d if isinstance(d, str) else _direction_text(d))
            eid = f"D-{_sha([salt, arm, i, clean])[:10]}"
            # `seed_id` was rater-visible and is non-null only for dict-shaped
            # directions. Machine output is structured and human output is very
            # plausibly typed as bare strings, so the field identified the arm
            # perfectly whenever the two arms had different shapes.
            entries.append({"entry_id": eid, "text": clean})
            key.append({"entry_id": eid, "arm": arm, "source_index": i,
                        "seed_id": (d.get("seed_id") if isinstance(d, dict) else None)})
            if found:
                tells.append({"entry_id": eid, "arm": arm, "removed": found})
            if not clean:
                blank.append({"arm": arm, "source_index": i})
    if blank:
        raise SystemExit(
            f"{len(blank)} direction(s) normalised to empty text: {blank}. A blank entry still "
            f"goes to a rater and still gets a score, and its arm's mean is then computed over "
            f"whatever a rater does with a blank cell. Give every direction text, or drop it "
            f"from both arms.")
    if len({e["entry_id"] for e in entries}) != len(entries):
        raise SystemExit("two entries hashed to the same id; rebuild with a different seed")

    entries = shuffle(entries, seed)
    packet = {
        "packet_version": "1",
        "shuffle_seed": seed,
        "criteria": CRITERIA,
        "instructions": [
            "You are rating research directions. Some were written by a person and some by a "
            "system. You are not told which, and you should not try to guess.",
            "Score every criterion 1-5 for every entry. Leave a score blank only if the entry is "
            "unreadable; a blank is recorded as not-rated, never as a 1.",
            "After you have rated everything, and only then, fill in `arm_guess` for each entry "
            "with exactly one of 'machine' or 'human'. This is not scored against you — it is "
            "how we find out whether the blind held. Any other word is refused rather than "
            "counted as a wrong guess.",
        ],
        "entries": [{**e, "scores": {c["id"]: None for c in CRITERIA},
                     "arm_guess": None, "rater_id": None} for e in entries],
    }
    keyfile = {
        # Hashed over the rater-visible fields only. Including the empty score
        # cells would make the key stop matching the moment anyone filled one in,
        # which is the packet doing its job.
        "packet_hash": _entries_hash(packet["entries"]),
        # And the key rows are hashed too. Nothing bound them before, so flipping
        # every `arm` reversed the reported difference while the output still
        # displayed a hash that appeared to attest to it.
        "key_hash": _sha(key),
        "salt": salt,
        "shuffle_seed": seed,
        "key": key,
        "tells_removed": tells,
        "warning": ("Do not give this file to a rater. If it has been seen, the packet is spent "
                    "and a new one must be built from different directions."),
    }
    return packet, keyfile


def _direction_text(d: dict) -> str:
    """The rater-visible text of a direction.

    Kept in step with `score_directions.direction_text` on purpose: the two files
    disagreeing about what a direction *is* meant a portfolio of `{"title": ...}`
    entries produced ten blank rubric rows and a full scoring packet, from the
    same input.
    """
    fields = ("problem_delta", "method_delta", "mechanism", "demonstrating_experiment")
    parts = [str(d[f]) for f in fields if d.get(f)]
    if parts:
        return " ".join(parts)
    return str(d.get("statement") or d.get("summary") or d.get("title") or "")


def analyse(packet: dict, keyfile: dict) -> dict:
    if keyfile.get("packet_hash") != _entries_hash(packet["entries"]):
        raise SystemExit(
            "the key does not match this packet's entries. Either the packet was edited after "
            "the key was written or they are from different builds; either way the arm labels "
            "cannot be trusted and no comparison may be reported.")
    if "key_hash" in keyfile and keyfile["key_hash"] != _sha(keyfile["key"]):
        raise SystemExit(
            "the key's own rows do not hash to its recorded key_hash: the arm labels were "
            "edited after the packet was built. Flipping them reverses the reported difference "
            "and nothing else in the output changes.")
    ids = [k["entry_id"] for k in keyfile["key"]]
    if len(set(ids)) != len(ids):
        raise SystemExit("the key contains duplicate entry_ids; one arm label would silently "
                         "overwrite another and the affected entries would be double-counted")
    arm = {k["entry_id"]: k["arm"] for k in keyfile["key"]}
    by_arm: dict[str, dict[str, list[float]]] = {"machine": {}, "human": {}}
    not_rated, guesses = 0, []
    unparsable: list[dict] = []
    out_of_range: list[dict] = []
    unrecognised_guesses: list[dict] = []
    for e in packet["entries"]:
        a = arm.get(e["entry_id"])
        if a is None:
            raise SystemExit(f"entry {e['entry_id']} is not in the key")
        for c in CRITERIA:
            v = (e.get("scores") or {}).get(c["id"])
            if v is None or (isinstance(v, str) and not v.strip()):
                not_rated += 1
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                # "n/a" is the most natural thing a rater writes for unreadable,
                # and the instructions ask for a blank. Recording it as not-rated
                # is right; crashing on it loses the whole session's data.
                not_rated += 1
                unparsable.append({"entry_id": e["entry_id"], "criterion": c["id"], "value": v})
                continue
            if not SCORE_MIN <= fv <= SCORE_MAX:
                out_of_range.append({"entry_id": e["entry_id"], "criterion": c["id"],
                                     "value": fv})
                continue
            by_arm[a].setdefault(c["id"], []).append(fv)
        g = e.get("arm_guess")
        if g is not None and str(g).strip():
            norm = str(g).strip().lower()
            if norm not in ARM_LABELS:
                # Scoring an unrecognised label as "wrong" is what made a rater who
                # identified every arm and wrote "AI" instead of "machine" come out
                # at 0.00 accuracy and be reported as at chance.
                unrecognised_guesses.append({"entry_id": e["entry_id"], "value": g})
            else:
                guesses.append(norm == a)

    per_criterion = []
    for c in CRITERIA:
        m, h = by_arm["machine"].get(c["id"], []), by_arm["human"].get(c["id"], [])
        per_criterion.append({
            "criterion": c["id"],
            "machine_mean": round(statistics.fmean(m), 4) if m else None,
            "human_mean": round(statistics.fmean(h), 4) if h else None,
            "difference": (round(statistics.fmean(m) - statistics.fmean(h), 4)
                           if m and h else None),
            "n": {"machine": len(m), "human": len(h)},
        })
    blind_rate = round(sum(guesses) / len(guesses), 4) if guesses else None
    # Two-sided, on |rate - 0.5|. The one-sided `rate < 0.65` passed raters who
    # identified every arm correctly and then wrote the labels inverted: accuracy
    # 0.00 is exactly as much evidence of a broken blind as 1.00, and the packet
    # never told the rater which two strings were legal in the first place.
    blind_deviation = None if blind_rate is None else round(abs(blind_rate - 0.5), 4)
    blind_held = None if blind_deviation is None else blind_deviation < 0.15
    if unrecognised_guesses:
        # An unreadable guess is not a wrong guess. Enough of them and the blind
        # check is measuring vocabulary.
        blind_held = None if blind_rate is None else blind_held
    return {
        "analysis_version": "1",
        "packet_hash": keyfile["packet_hash"],
        "entries": len(packet["entries"]),
        "unrated_cells": not_rated,
        "per_criterion": per_criterion,
        "blind_check": {
            "arm_guess_accuracy": blind_rate,
            "deviation_from_chance": blind_deviation,
            "chance": 0.5,
            "threshold": "|accuracy - 0.5| < 0.15",
            "two_sided_because": ("consistently inverted labels identify the arm just as well as "
                                  "correct ones; only distance from chance is evidence"),
            "graded_guesses": len(guesses),
            "unrecognised_guesses": unrecognised_guesses,
            "legal_labels": sorted(ARM_LABELS),
            "held": blind_held,
            "note": ("raters identified the arm far from chance, so these ratings are ratings "
                     "of the arm and not of the directions" if blind_held is False else
                     f"every arm_guess was outside {sorted(ARM_LABELS)}, so the raters answered "
                     f"but nothing could be graded — that is not the same as being at chance"
                     if unrecognised_guesses and blind_rate is None else
                     "no rater filled in arm_guess, so whether the blind held is unknown — which "
                     "is not the same as it having held" if blind_rate is None else
                     "raters were at or near chance; the blind held"),
        },
        "unparsable_scores": unparsable,
        "out_of_range_scores": out_of_range,
        "score_range": [SCORE_MIN, SCORE_MAX],
        "reportable": bool(blind_held) and not_rated == 0 and not out_of_range
                      and not unrecognised_guesses,
        "why_not_reportable": (
            None if (blind_held and not_rated == 0 and not out_of_range
                     and not unrecognised_guesses) else
            "the blind did not hold" if blind_held is False else
            # ordered before "never run": the raters did answer, we simply could
            # not grade what they wrote, and those are different problems
            f"{len(unrecognised_guesses)} arm_guess value(s) were not one of "
            f"{sorted(ARM_LABELS)} and could not be graded" if unrecognised_guesses else
            "the blind check was never run" if blind_rate is None else
            f"{len(out_of_range)} score(s) fell outside {SCORE_MIN}-{SCORE_MAX}"
            if out_of_range else
            f"{not_rated} rubric cell(s) were left unrated"),
        "is_not": ("a significance test. It is a difference of means over a small panel; the "
                   "panel size and the rater agreement decide what it is worth."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a blind packet from two arms")
    b.add_argument("--machine", required=True)
    b.add_argument("--human", required=True)
    b.add_argument("--seed", type=int, default=20260812)
    b.add_argument("--min-per-arm", type=int, default=10)
    b.add_argument("--salt", help="rebuild a previous packet byte-identically (from its key)")
    b.add_argument("--packet", required=True)
    b.add_argument("--key", required=True)
    a2 = sub.add_parser("analyse", help="analyse a filled packet against its key")
    a2.add_argument("--packet", required=True)
    a2.add_argument("--key", required=True)
    a2.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        machine = json.loads(Path(a.machine).read_text(encoding="utf-8"))
        human = json.loads(Path(a.human).read_text(encoding="utf-8"))
        packet, key = build(machine, human, a.seed, a.min_per_arm, a.salt)
        Path(a.packet).write_text(json.dumps(packet, indent=1), encoding="utf-8")
        Path(a.key).write_text(json.dumps(key, indent=1), encoding="utf-8")
        print(f"wrote {a.packet}: {len(packet['entries'])} entries, "
              f"{len(CRITERIA)} criteria; key in {a.key} (do not distribute)")
        for t in key["tells_removed"]:
            print(f"  stripped {t['removed']} from {t['entry_id']} ({t['arm']})")
        return 0

    packet = json.loads(Path(a.packet).read_text(encoding="utf-8"))
    key = json.loads(Path(a.key).read_text(encoding="utf-8"))
    res = analyse(packet, key)
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    if not res["reportable"]:
        print(f"NOT REPORTABLE: {res['why_not_reportable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

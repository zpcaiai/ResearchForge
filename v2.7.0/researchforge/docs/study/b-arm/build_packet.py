#!/usr/bin/env python3
"""Build the B-arm packet: the human-ceiling comparison, ready for a person to run.

WHAT THE B ARM IS FOR

The A arm measured what a coding agent achieves on 20 papers under a 4-hour
timebox. On its own that number is uninterpretable: a 15% reproduction rate is a
damning result if a skilled engineer gets 80% on the same papers, and an
unremarkable one if they get 20%. The B arm is the denominator.

WHY BLINDING IS THE WHOLE DESIGN

If the engineer reads the A-arm log first, what gets measured is "can a human fix
an agent's dead end", which is a different and much easier question — the agent has
already paid the search cost. So this script builds a packet that contains the
paper, the repository and the claim targets, and *withholds every A-arm outcome*:
no level, no failure codes, no timings, no notes. What was withheld is listed in
the packet so the withholding itself is auditable.

WHY THE SUBSET IS DRAWN, NOT CHOSEN

Eight of twenty, by a seeded shuffle recorded in the packet. Picking eight by hand
would select for papers whose difficulty someone already had an opinion about, and
that opinion would come from the A-arm results this arm is supposed to be blind to.

WHAT IT CANNOT DELIVER

n=8. `analyse.py` computes the minimum difference this design could detect before
it reports any gap, because at n=8 a difference smaller than roughly forty points
is inside the noise, and a table of two percentages hides that completely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

#: Fields of an A-arm record that would tell the engineer how the agent did. Kept
#: as an explicit deny-list rather than an allow-list so that a field added to the
#: A-arm record later shows up as an unclassified key and stops the build, instead
#: of leaking silently into the packet.
WITHHELD = ("level", "level_rationale", "codes", "clone_ok", "resolve_ok", "entry_ok",
            "seconds", "notes")
#: Fields that identify the paper and are safe to hand over: the engineer would
#: find all of them in thirty seconds anyway.
CARRIED = ("date", "arxiv_id", "title", "github_repo", "github_stars", "sample_index")

FAILURE_CODES = ("NO_CODE", "DEPENDENCY_UNRESOLVABLE", "HARDWARE_UNAVAILABLE",
                 "DATA_UNAVAILABLE", "DATA_ACCESS_GATED", "CHECKPOINT_MISSING",
                 "UNDOCUMENTED_PREPROCESSING", "CONFIG_AMBIGUOUS", "NONDETERMINISM",
                 "METRIC_DEFINITION_MISMATCH", "NUMBERS_DIVERGE", "TIMEBOX_EXCEEDED",
                 "LICENSE_BLOCKED")
LEVELS = ("RL0", "RL1", "RL2", "RL3", "RL4")


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def draw(records: list[dict], n: int, seed: int) -> list[dict]:
    """A seeded shuffle, so the draw can be re-run and shown to be the draw.

    Mersenne Twister rather than a hand-rolled LCG. The previous version used
    `state = (1103515245*state + 12345) mod 2**31` and took `j = state % (i+1)`,
    which reads the LOW bits — and in that generator the low two bits are a
    counter: `state_n = seed + n (mod 4)`. The swaps at i+1 in {2, 4} therefore
    carried no randomness at all, and measured over 200,000 seeds the last record
    in the file was drawn half as often as the first ten. A draw whose bias is a
    function of file order is still a selection; it just selects on something
    nobody chose deliberately, which is worse than choosing.
    """
    rng, out = random.Random(seed), list(records)
    rng.shuffle(out)
    return out[:n]


def build(a_arm: list[dict], n: int, seed: int, timebox_hours: float) -> tuple[dict, dict]:
    unknown = sorted({k for r in a_arm for k in r} - set(WITHHELD) - set(CARRIED))
    if unknown:
        raise SystemExit(
            f"the A-arm records carry fields this packet builder does not classify: {unknown}. "
            f"Add each to CARRIED or WITHHELD. Passing an unclassified field through is how an "
            f"outcome reaches the engineer who is supposed to be blind to it.")
    # The classification is by key name, so it only sees one level. A carried field
    # whose value is a dict would be copied wholesale, and `notes` is already a dict
    # in these records — the day someone turns `github_repo` into a struct, the
    # whole A-arm outcome rides along inside it.
    nested = sorted({f"{k}.{sub}" for r in a_arm for k in CARRIED
                     if isinstance(r.get(k), dict) for sub in r[k]})
    if nested:
        raise SystemExit(
            f"carried fields contain nested structure: {nested}. A carried field must be a "
            f"scalar the engineer could look up themselves in thirty seconds. A dict passes the "
            f"key-name check and copies its contents into the packet unread.")
    if len(a_arm) < n:
        raise SystemExit(f"the A arm has {len(a_arm)} records; cannot draw {n}")

    drawn = draw(a_arm, n, seed)
    worksheets = []
    for r in drawn:
        worksheets.append({
            "paper": {k: r.get(k) for k in CARRIED},
            "timebox_hours": timebox_hours,
            "may_contact_authors": False,
            "step_0_seed_variance": {
                "instruction": ("Before comparing anything, repeat the main experiment with 3 "
                                "different random seeds and record the metric's standard "
                                "deviation. Tolerance is max(the paper's own reported variance, "
                                "2 x your measured 3-seed sd, 2% relative). A fixed +/-5% on a "
                                "high-variance task turns the task's own noise into a failed "
                                "reproduction."),
                "seeds_run": None, "metric": None, "sd": None, "tolerance_used": None,
                "skipped_because": None,
            },
            "level_reached": None,
            "level_options": list(LEVELS),
            "failure_codes": [],
            "failure_code_vocabulary": list(FAILURE_CODES),
            "claim_comparisons": [
                {"claim_id": None, "paper_reported_value": None, "paper_locator": None,
                 "local_measured_value": None, "tolerance": None, "verdict": None,
                 "run_id": None},
            ],
            "claim_comparison_rule": ("every comparison is recorded as a pair. A measurement "
                                      "with no paper-side value and locator beside it is not "
                                      "evidence and is dropped at analysis."),
            "wall_clock_hours_used": None,
            "timebox_exhausted": None,
            "engineer_id": None,
            "saw_a_arm_material": None,
            "saw_a_arm_material_note": ("Answer honestly and after the fact. A 'yes' does not "
                                        "waste the worksheet — it moves it out of the ceiling "
                                        "estimate and into a separate, still-useful sample. A "
                                        "'yes' recorded as a 'no' destroys the comparison and "
                                        "nothing downstream can detect it."),
        })

    packet = {
        "packet_version": "1",
        "arm": "B",
        "purpose": ("estimate the human ceiling on the same papers the agent attempted, so the "
                    "A-arm rate has a denominator"),
        "draw": {"n": n, "from": len(a_arm), "seed": seed,
                 "method": "seeded shuffle, first n; recorded so the draw can be re-run"},
        "timebox_hours": timebox_hours,
        "blinding": {
            "withheld_fields": list(WITHHELD),
            "carried_fields": list(CARRIED),
            "rule": ("the engineer must not read the A-arm logs, results file or notes before "
                     "finishing. Reading them turns this into 'can a human fix an agent's dead "
                     "end', which is an easier question and not the one being asked."),
        },
        "reporting_rule": ("n=8 is a cost compromise. It gives a rough ceiling, not a precise "
                           "one, and it must be reported that way. analyse.py computes the "
                           "minimum detectable difference before it reports any gap."),
        "worksheets": worksheets,
    }
    key = {
        "packet_hash": _sha([w["paper"] for w in packet["worksheets"]]),
        "draw_seed": seed,
        "a_arm_outcomes": [{"arxiv_id": r.get("arxiv_id"), "level": r.get("level"),
                            "codes": r.get("codes"), "seconds": r.get("seconds")}
                           for r in drawn],
        "warning": ("Do not give this file to the B-arm engineer, and do not open it in a "
                    "shared screen. It is the A-arm answer key for exactly the papers they are "
                    "about to attempt."),
    }
    return packet, key


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a-arm", required=True, help="docs/study/all_results.jsonl")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260812)
    ap.add_argument("--timebox-hours", type=float, default=8.0)
    ap.add_argument("--packet", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args(argv)

    rows = [json.loads(l) for l in
            Path(a.a_arm).read_text(encoding="utf-8").splitlines() if l.strip()]
    packet, key = build(rows, a.n, a.seed, a.timebox_hours)
    Path(a.packet).write_text(json.dumps(packet, indent=1), encoding="utf-8")
    Path(a.key).write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"wrote {a.packet}: {a.n} worksheet(s) drawn from {len(rows)} with seed {a.seed}")
    print(f"wrote {a.key}: the A-arm answer key for those papers — do not distribute")
    for w in packet["worksheets"]:
        print(f"  {w['paper']['arxiv_id']}  {str(w['paper']['title'])[:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

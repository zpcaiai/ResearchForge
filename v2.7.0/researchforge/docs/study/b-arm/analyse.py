#!/usr/bin/env python3
"""Analyse returned B-arm worksheets against the A arm, or refuse to.

WHAT THIS COMPUTES AND WHAT IT WILL NOT

It computes the reproduction rate for each arm on the same papers, a Wilson
interval for each, and an **exact McNemar test on the discordant pairs**. The
pairing is the whole point: both arms attempt the SAME papers, so their outcomes
are correlated by paper difficulty, and the papers where both arms succeeded or
both failed carry no information about which arm is better. A two-proportion test
treats the arms as independent samples and, on a positively correlated design,
inflates the sample-size requirement by up to 2.7x — turning real results into
"not detectable".

When the exact test does not reject, the finding is `NOT_DETECTABLE`, and the
minimum difference this design could have found is reported beside it. That is not
"no difference".

THREE REFUSALS

1. **The blind broke.** Any worksheet whose engineer reports having seen A-arm
   material is moved out of the ceiling estimate. If enough move, there is no
   ceiling estimate left, and the run is reported as such rather than as a smaller
   one.
2. **The papers are not the same papers.** The packet hash must match the key. An
   engineer who swapped a paper for an easier one, or a packet rebuilt with a
   different seed, produces two rates over two different samples — and the
   difference of those is not a gap.
3. **The timebox was not respected.** A worksheet that ran past its box measured a
   different design. It is reported separately, never folded in.

Every unfinished worksheet is `NOT_RUN`. None of them is a failure: an
unattempted paper counted as an RL0 makes the human arm look worse and the agent
look better, which is the direction of error this whole study exists to avoid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

#: Reproduction is RL3+: at least one main result inside the tolerance the step-0
#: variance measurement set. RL1 ("it ran without erroring") and RL2 ("right
#: direction at reduced scale") are progress, not reproduction, and the protocol
#: says so; collapsing them into a success rate is the most common way this kind
#: of study reports a number three times too high.
REPRODUCED_AT = ("RL3", "RL4")
LEVELS = ("RL0", "RL1", "RL2", "RL3", "RL4")


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def wilson(successes: int, n: int, z: float = 1.959963985) -> tuple[float, float] | None:
    """Wilson score interval. None when n is 0 — an interval over nothing is nothing."""
    if n <= 0:
        return None
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6))


def exact_mcnemar(b: int, c: int) -> dict:
    """Exact two-sided McNemar (the sign test on discordant pairs).

    The arms are the SAME papers, so the pairs are not independent samples and a
    two-proportion test is the wrong instrument. What carries the information is
    the discordant pairs: the papers where exactly one arm reproduced. Concordant
    pairs — both succeeded, or both failed — say nothing about which arm is
    better, and a test that counts them as evidence is diluting the signal with
    the difficulty of the papers.

    Exact rather than the chi-square approximation because at n=8 there are at
    most 8 discordant pairs and the asymptotic form is not usable there.
    """
    d = b + c
    if d == 0:
        return {"discordant": 0, "b_only": b, "c_only": c, "p_value": None,
                "why": ("no paper separated the arms: every one was reproduced by both or by "
                        "neither. A difference cannot be estimated from concordant pairs.")}
    # two-sided exact binomial against p=0.5
    k = min(b, c)
    tail = sum(math.comb(d, i) for i in range(0, k + 1)) / (2 ** d)
    return {"discordant": d, "b_only": b, "c_only": c,
            "p_value": round(min(1.0, 2 * tail), 6),
            "test": "exact two-sided McNemar (sign test on discordant pairs)"}


def required_pairs(difference: float, p_discordant: float,
                   alpha: float = 0.05, power: float = 0.8) -> int | None:
    """Pairs needed to detect `difference`, given a discordance rate (Connor, 1987).

    `p_discordant` is the fraction of papers on which the two arms disagree, and
    it is what the required n actually depends on. The two-independent-sample
    formula this used to use is the special case p_discordant = 0.5, and using it
    on a positively correlated design — which two arms attempting the SAME papers
    always are — inflates the requirement, by 2.7x at a 20-point difference.
    """
    if difference <= 0 or not 0 < p_discordant <= 1 or p_discordant <= difference ** 2:
        return None
    z_a = _z_two_sided(alpha)
    z_b = _z_power(power)
    num = z_a * math.sqrt(p_discordant) + z_b * math.sqrt(p_discordant - difference ** 2)
    return math.ceil((num / difference) ** 2)


def min_detectable_difference(n_pairs: int, p_discordant: float,
                              alpha: float = 0.05, power: float = 0.8) -> float | None:
    """Smallest difference detectable with n pairs at a given discordance rate.

    Numerically inverts `required_pairs`, because that relation has no closed
    form. Returns None when no difference in (0, 1] is detectable at this n.
    """
    if n_pairs <= 0 or not 0 < p_discordant <= 1:
        return None
    lo, hi = 1e-6, min(1.0, math.sqrt(p_discordant) - 1e-9)
    if hi <= lo:
        return None
    need = required_pairs(hi, p_discordant, alpha, power)
    if need is None or need > n_pairs:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        need = required_pairs(mid, p_discordant, alpha, power)
        if need is not None and need <= n_pairs:
            hi = mid
        else:
            lo = mid
    return round(hi, 6)


def _z_two_sided(alpha: float) -> float:
    return _probit(1 - alpha / 2.0)


def _z_power(power: float) -> float:
    return _probit(power)


def _probit(p: float) -> float:
    """Inverse standard normal CDF, by bisection on erf.

    Written out rather than hard-coded for alpha=0.05 and power=0.8, because the
    previous version accepted both parameters and ignored them: a caller who
    tightened alpha to 0.01 got the 0.05 answer with no warning.
    """
    if not 0 < p < 1:
        raise ValueError(f"probit needs 0 < p < 1, got {p}")
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analyse(packet: dict, key: dict, a_arm: list[dict]) -> dict:
    if _sha([w["paper"] for w in packet["worksheets"]]) != key.get("packet_hash"):
        raise SystemExit(
            "the returned packet does not hash to its key: the set of papers changed between "
            "the draw and the return. Two rates over two different samples do not have a gap "
            "between them.")

    a_by_id = {str(r.get("arxiv_id")): r for r in a_arm}
    included, excluded, over_box, not_run, unanswered = [], [], [], [], []
    for w in packet["worksheets"]:
        aid = str(w["paper"]["arxiv_id"])
        level = w.get("level_reached")
        rec = {"arxiv_id": aid, "title": w["paper"].get("title"),
               "b_level": level, "a_level": (a_by_id.get(aid) or {}).get("level"),
               "hours": w.get("wall_clock_hours_used"),
               "engineer_id": w.get("engineer_id")}
        if level is None:
            not_run.append(rec)
            continue
        if level not in LEVELS:
            raise SystemExit(f"{aid}: level_reached={level!r} is not one of {LEVELS}. "
                             f"An unrecognised level is not an RL0.")
        # An unanswered blinding question is not a "no". The packet emits both of
        # these fields as null, so treating null as falsy meant an engineer who
        # skipped the honesty question and the hours field passed both refusals
        # and produced a clean ceiling.
        if w.get("saw_a_arm_material") is None:
            rec["reason"] = ("saw_a_arm_material was left unanswered. Unanswered is not 'no': "
                             "whether this worksheet is a ceiling datum is unknown, and unknown "
                             "may not be resolved in the direction that keeps the sample larger")
            unanswered.append(rec)
            continue
        if w.get("saw_a_arm_material"):
            rec["reason"] = "engineer reported seeing A-arm material; this is not a ceiling datum"
            excluded.append(rec)
            continue
        box = float(packet.get("timebox_hours") or 0)
        if box and w.get("wall_clock_hours_used") is None:
            rec["reason"] = (f"wall_clock_hours_used was left unanswered against a {box}h box, so "
                             f"whether the timebox held is unknown")
            unanswered.append(rec)
            continue
        if box and float(w["wall_clock_hours_used"]) > box:
            rec["reason"] = (f"ran {w['wall_clock_hours_used']}h against a {box}h box; a "
                             f"different design was executed")
            over_box.append(rec)
            continue
        included.append(rec)

    paired = [r for r in included if r["a_level"] is not None]
    n = len(paired)
    b_hits = sum(1 for r in paired if r["b_level"] in REPRODUCED_AT)
    a_hits = sum(1 for r in paired if r["a_level"] in REPRODUCED_AT)
    b_rate = round(b_hits / n, 6) if n else None
    a_rate = round(a_hits / n, 6) if n else None
    diff = None if (b_rate is None or a_rate is None) else round(b_rate - a_rate, 6)

    # The paired counts. These, not the two marginal rates, are what the test uses.
    b_only = sum(1 for r in paired
                 if r["b_level"] in REPRODUCED_AT and r["a_level"] not in REPRODUCED_AT)
    a_only = sum(1 for r in paired
                 if r["a_level"] in REPRODUCED_AT and r["b_level"] not in REPRODUCED_AT)
    test = exact_mcnemar(b_only, a_only)
    p_d_obs = round((b_only + a_only) / n, 6) if n else None
    mdd = (min_detectable_difference(n, p_d_obs) if n and p_d_obs else None)

    if n == 0:
        finding, why = "NO_CEILING_ESTIMATE", (
            "no worksheet survived: every one was unrun, unanswered, unblinded or outside its "
            "timebox. The agent-versus-human gap remains unmeasured, which is where it started.")
    elif test["p_value"] is None:
        finding, why = "NO_DISCORDANT_PAIRS", (
            f"the arms agreed on all {n} paper(s). Concordant pairs carry no information about "
            f"which arm is better, so no difference can be estimated from this sample.")
    elif test["p_value"] <= 0.05:
        finding, why = "DIFFERENCE_OBSERVED", (
            f"human {b_rate:.3f} vs agent {a_rate:.3f} on the same {n} paper(s). Of the "
            f"{test['discordant']} paper(s) that separated the arms, {b_only} favoured the human "
            f"and {a_only} the agent; exact McNemar p={test['p_value']:.4f}. It is a paired "
            f"sample of {n}; treat it as a rough ceiling, not a precise one.")
    else:
        detect = (f"; this design could detect about {mdd:.2f} at the observed discordance of "
                  f"{p_d_obs:.2f}" if mdd else "")
        finding, why = "NOT_DETECTABLE", (
            f"the observed difference is {diff:+.3f} but exact McNemar gives p="
            f"{test['p_value']:.4f} on {test['discordant']} discordant pair(s){detect}. That is "
            f"not evidence of no difference; it is a sample too small to tell.")

    return {
        "analysis_version": "2",
        "packet_hash": key.get("packet_hash"),
        "draw_seed": key.get("draw_seed"),
        "design": "paired: the same papers were attempted by both arms",
        "test_used": test.get("test", "none applicable"),
        "why_paired": ("the two arms attempt the SAME papers, so their outcomes are correlated by "
                       "paper difficulty. A two-proportion test treats them as independent "
                       "samples; on a positively correlated design that inflates the sample size "
                       "requirement — by 2.7x at a 20-point difference — and can turn a real "
                       "result into NOT_DETECTABLE."),
        "reproduced_at": list(REPRODUCED_AT),
        "reproduced_at_note": ("RL1 is 'it ran'; RL2 is 'right direction at reduced scale'. "
                               "Neither is a reproduction and folding them in inflates the rate."),
        "n_paired": n,
        "human": {"rate": b_rate, "hits": b_hits, "ci95": wilson(b_hits, n) if n else None},
        "agent": {"rate": a_rate, "hits": a_hits, "ci95": wilson(a_hits, n) if n else None},
        "difference": diff,
        "paired_test": test,
        "observed_discordance": p_d_obs,
        "min_detectable_difference": mdd,
        "n_required_for": {
            f"{d:.2f}": {f"p_d={pd:.2f}": required_pairs(d, pd) for pd in (0.25, 0.5, 0.75)}
            for d in (0.5, 0.4, 0.3, 0.2)},
        "n_required_note": ("pairs, at 80% power. The requirement depends on the DISCORDANCE "
                            "rate, not on the arms' rates: how many papers separate the arms is "
                            "what carries the information. Read this before running, and pick "
                            "the column you expect."),
        "finding": finding,
        "why": why,
        "level_distribution": {
            "human": {lv: sum(1 for r in paired if r["b_level"] == lv) for lv in LEVELS},
            "agent": {lv: sum(1 for r in paired if r["a_level"] == lv) for lv in LEVELS},
        },
        "excluded_unblinded": excluded,
        "excluded_unanswered": unanswered,
        "excluded_over_timebox": over_box,
        "not_run": not_run,
        "not_run_policy": ("an unattempted paper is NOT_RUN, never an RL0. Counting it as a "
                           "failure makes the human arm look worse and the agent look better, "
                           "which is the direction of error this study exists to avoid."),
        "censoring_note": ("every exclusion above is a function of the B-arm worksheet, so the "
                           "agent rate is reported on a B-selected subset. With few exclusions "
                           "this is minor; with many it is informative censoring and the two "
                           "marginal rates stop being comparable to the full A arm."),
        "per_paper": paired,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packet", required=True, help="the returned, filled worksheets")
    ap.add_argument("--key", required=True)
    ap.add_argument("--a-arm", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    packet = json.loads(Path(a.packet).read_text(encoding="utf-8"))
    key = json.loads(Path(a.key).read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in
            Path(a.a_arm).read_text(encoding="utf-8").splitlines() if l.strip()]
    res = analyse(packet, key, rows)
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    print(f"{res['finding']}: {res['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

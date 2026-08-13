#!/usr/bin/env python3
"""Score a system's research directions against the frozen retrospective benchmark.

`retrospective-benchmark-builder` builds and freezes the benchmark. Nothing scored
against it. This is that half.

THE TWO PHASES, AND WHY THERE ARE TWO

Matching a generated direction to a gold one is a judgment, and the benchmark's own
scoring contract forbids the shortcuts: not string overlap, not an embedding
threshold, not a title match. Two directions can share every content word and mean
different things ("scale the teacher" vs "scale the student"), and two can share no
words and mean the same thing.

So scoring is split. `--emit-packet` writes every candidate pair with both texts and
no verdicts. Something else — a human, or a model acting as judge and recorded as
one — fills the verdicts in. `--score` then computes recall@k from the filled
packet. An unfilled pair is `UNADJUDICATED`, which is not a miss: a pair nobody
looked at contributes to neither numerator nor denominator, and the run is reported
as incomplete rather than as a low score.

WHAT THE SCORE IS COMPARED AGAINST

Zero is not the reference point. The benchmark's contamination floor is: the
fraction of gold directions the model names when asked to recite them, with no
reasoning involved. A recall@k at or below the floor is reported as
`AT_OR_BELOW_FLOOR` and carries no capability claim, because the same number is
produced by a system that has merely read the literature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

VERDICTS = ("MATCH", "NO_MATCH", "UNADJUDICATED")
#: A judge that is a model must be recorded as one. The benchmark exists because
#: a model's memory of the literature is indistinguishable from research judgment
#: in the output; a model adjudicating whether the output matches that same
#: literature has the same problem one level up.
JUDGE_KINDS = ("human", "model", "unspecified")


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_benchmark(path: Path) -> tuple[dict, list[dict]]:
    records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    freeze = next((r for r in records if r.get("record_type") == "freeze"), None)
    if freeze is None:
        raise SystemExit("the benchmark carries no freeze record; a score against an unfrozen "
                         "benchmark cannot later be shown to have been measured against the "
                         "same questions")
    pairs = [r for r in records if r.get("record_type") == "pair"]
    if not pairs:
        raise SystemExit("the benchmark has no seed/follow-up pairs to score against")
    return freeze, pairs


def load_directions(path: Path) -> dict[str, list[dict]]:
    """{seed_id: [direction, ...]} from a portfolio file.

    Accepts either an explicit map or an `idea_portfolio`-shaped list, so the
    innovation engine's real output can be scored without being reshaped by hand
    — reshaping by hand is where a direction quietly acquires a better wording.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "ideas" not in raw:
        out: dict[str, list] = {}
        for k, v in raw.items():
            if v is None:
                out[str(k)] = []
            elif isinstance(v, list):
                out[str(k)] = list(v)
            else:
                # `list("use a better teacher")` is twenty single-character
                # directions, and `list({"statement": ...})` is the key names. Both
                # produce a full adjudication packet of nonsense that a human then
                # spends an afternoon rating.
                raise SystemExit(
                    f"seed {k!r} maps to a {type(v).__name__}, not a list of directions. One "
                    f"direction still has to be written as a list of one; iterating a string or "
                    f"a dict produces characters or key names and nothing downstream notices.")
        return out
    ideas = raw.get("ideas") if isinstance(raw, dict) else raw
    out: dict[str, list[dict]] = {}
    for i in ideas or []:
        seed = str(i.get("seed_id") or i.get("for_seed") or "")
        if not seed:
            raise SystemExit(
                "an idea in the portfolio names no seed_id. Scoring needs to know which seed a "
                "direction was generated for; assigning it to all of them would let one lucky "
                "direction score against every seed.")
        out.setdefault(seed, []).append(i)
    return out


def direction_text(d) -> str:
    if isinstance(d, str):
        return d
    fields = ("problem_delta", "method_delta", "mechanism", "demonstrating_experiment")
    parts = [f"{f}: {d[f]}" for f in fields if d.get(f)]
    if parts:
        return " | ".join(parts)
    return str(d.get("statement") or d.get("summary") or d.get("title") or json.dumps(d))[:600]


def emit_packet(freeze: dict, pairs: list[dict], directions: dict[str, list], k: int) -> dict:
    """Every candidate pair, with both texts and no verdict.

    Deliberately the full cross product per seed rather than a shortlist. A
    shortlist would be produced by a similarity function, and the shortlisting
    function would then be the real matcher while the adjudicator believed they
    were doing the matching.
    """
    items, missing = [], []
    for p in pairs:
        seed = p["seed_id"]
        gids = [g["gold_direction_id"] for g in p["gold_directions"]]
        if len(set(gids)) != len(gids):
            dupes = sorted({g for g in gids if gids.count(g) > 1})
            raise SystemExit(
                f"seed {seed!r} carries duplicate gold_direction_id(s) {dupes}. The score's "
                f"denominator is the set of distinct gold ids, so a duplicate shrinks the "
                f"denominator while still emitting a pair to adjudicate: 1 of 3 real directions "
                f"would be reported as 1 of 2.")
        gen = directions.get(seed) or []
        if not gen:
            missing.append(seed)
        topk = gen[:k]
        for gi, g in enumerate(topk):
            for gold in p["gold_directions"]:
                items.append({
                    "pair_id": f"{seed}::gen{gi}::{gold['gold_direction_id']}",
                    "seed_id": seed,
                    "generated_rank": gi + 1,
                    "generated": direction_text(g),
                    "gold_direction_id": gold["gold_direction_id"],
                    "gold_split": gold["split"],
                    "gold": direction_text(gold),
                    "verdict": "UNADJUDICATED",
                    "adjudicator": None,
                    "note": None,
                })
    return {
        "packet_version": "1",
        "benchmark_content_hash": freeze["content_hash"],
        "benchmark_version": freeze["benchmark_version"],
        "k": k,
        "contamination_floor": (freeze.get("contamination_floor") or {}).get("floor_recall"),
        "seeds_with_no_generated_directions": sorted(missing),
        # The full denominator, carried explicitly. A seed the system answered with
        # nothing produces no items, so a score computed only from `items` would
        # drop it from the average — and answering one seed out of five and getting
        # it right would report recall 1.0.
        "gold_by_seed": {p["seed_id"]: [g["gold_direction_id"] for g in p["gold_directions"]]
                         for p in pairs},
        "rubric": [
            "MATCH means the generated direction and the gold direction are the same research "
            "direction: same problem delta AND same mechanism. Same benchmark is not enough; "
            "'improve accuracy on ImageNet' matches nothing.",
            "A generated direction that is strictly more general than the gold one is NO_MATCH. "
            "'use a better teacher' does not match 'update the teacher from the student's "
            "performance on labelled data'.",
            "Wording is irrelevant in both directions. Shared vocabulary is not a match; disjoint "
            "vocabulary is not a miss.",
            "If you cannot decide, leave UNADJUDICATED. An undecided pair is excluded from the "
            "score and reported; a guessed one is not recoverable.",
        ],
        "items": items,
    }


def score(packet: dict, judge_kind: str, judge_id: str | None) -> dict:
    k = int(packet.get("k") or 10)
    floor = packet.get("contamination_floor")
    gold_by_seed = {str(sid): list(dict.fromkeys(g)) for sid, g in
                    (packet.get("gold_by_seed") or {}).items()}
    if not gold_by_seed:
        # Packets written before gold_by_seed existed: fall back to what the items
        # show, and say so, rather than silently scoring over a partial denominator.
        for it in packet["items"]:
            gold_by_seed.setdefault(it["seed_id"], [])
            if it["gold_direction_id"] not in gold_by_seed[it["seed_id"]]:
                gold_by_seed[it["seed_id"]].append(it["gold_direction_id"])

    hits: dict[str, set] = {sid: set() for sid in gold_by_seed}
    held_hits: dict[str, set] = {sid: set() for sid in gold_by_seed}
    held_gold: dict[str, set] = {sid: set() for sid in gold_by_seed}
    seen_pairs: dict[str, int] = {sid: 0 for sid in gold_by_seed}
    unadjudicated = 0
    for it in packet["items"]:
        sid = it["seed_id"]
        hits.setdefault(sid, set()); held_hits.setdefault(sid, set())
        held_gold.setdefault(sid, set()); seen_pairs.setdefault(sid, 0)
        gold_by_seed.setdefault(sid, [])
        if it["gold_direction_id"] not in gold_by_seed[sid]:
            gold_by_seed[sid].append(it["gold_direction_id"])
        seen_pairs[sid] += 1
        if it["gold_split"] == "held_out_post_cutoff":
            held_gold[sid].add(it["gold_direction_id"])
        v = str(it.get("verdict") or "UNADJUDICATED").upper()
        if v not in VERDICTS:
            raise SystemExit(f"pair {it['pair_id']} carries verdict {v!r}, which is not one of "
                             f"{VERDICTS}. An unknown verdict is not a miss and will not be "
                             f"treated as one.")
        if v == "UNADJUDICATED":
            unadjudicated += 1
        elif v == "MATCH":
            hits[sid].add(it["gold_direction_id"])
            if it["gold_split"] == "held_out_post_cutoff":
                held_hits[sid].add(it["gold_direction_id"])

    per_seed = []
    for sid in sorted(gold_by_seed):
        gold = gold_by_seed[sid]
        denom = len(gold) or 1
        per_seed.append({
            "seed_id": sid,
            "recall_at_k": round(len(hits[sid]) / denom, 6),
            "matched": len(hits[sid]), "gold": len(gold),
            "held_out_recall": (round(len(held_hits[sid]) / len(held_gold[sid]), 6)
                                if held_gold[sid] else None),
            "pairs_adjudicated": seen_pairs[sid],
            "answered": seen_pairs[sid] > 0,
        })

    # Micro, not macro. The harness (`researchforge.skills.meta.recall_at_k`)
    # computes matched-over-all-gold across every seed; a macro average over seeds
    # gives a different number from the same adjudication, and two numbers for one
    # fact is how a project loses track of which is right.
    total_gold = sum(len(g) for g in gold_by_seed.values())
    total_hits = sum(len(h) for h in hits.values())
    recall = (total_hits / total_gold) if total_gold else None
    macro = ((sum(p["recall_at_k"] for p in per_seed) / len(per_seed)) if per_seed else None)
    held_total = sum(len(g) for g in held_gold.values())
    held_hit = sum(len(h) for h in held_hits.values())

    unanswered_seeds = sorted(p["seed_id"] for p in per_seed if not p["answered"])
    if recall is not None and per_seed and len(unanswered_seeds) == len(per_seed):
        # Recall is a correct 0.0, but 0.0 from "the system said nothing about any
        # seed" and 0.0 from "it answered every seed and got them all wrong" are
        # different events, and the first is almost always a wiring mistake.
        verdict, why = "NOTHING_SUBMITTED", (
            f"no seed in the benchmark matched any seed in the submitted directions, so recall "
            f"is 0.0 by absence across all {len(per_seed)} seed(s). This is almost always an id "
            f"mismatch between the portfolio and the benchmark rather than a system that "
            f"produced nothing; compare the seed_id values before reading the number.")
    elif recall is None:
        verdict, why = "NO_OVERLAP", (
            "no seed in the benchmark matched any seed in the submitted directions, so nothing "
            "was scored. This is almost always an id mismatch between the portfolio and the "
            "benchmark, not a system that produced nothing.")
    elif unadjudicated:
        verdict, why = "INCOMPLETE", (
            f"{unadjudicated} of {len(packet['items'])} candidate pair(s) were never adjudicated. "
            f"They are excluded from the score rather than counted as misses, so this number is "
            f"a partial measurement and not a low one.")
    elif floor is None:
        verdict, why = "UNINTERPRETABLE", (
            "the benchmark carries no measured contamination floor, so there is nothing to read "
            "this recall against. An unmeasured floor is not a low floor.")
    elif recall <= float(floor):
        # Compared unrounded. round(2/3, 6) is strictly greater than 2/3, so a
        # system exactly at the floor was being handed the capability claim.
        verdict, why = "AT_OR_BELOW_FLOOR", (
            f"recall@{k}={recall:.3f} does not exceed the contamination floor of "
            f"{float(floor):.3f}. A system that had merely read the literature would produce "
            f"this number, so it carries no claim about research judgment.")
    else:
        verdict, why = "ABOVE_FLOOR", (
            f"recall@{k}={recall:.3f} exceeds the contamination floor of {float(floor):.3f} by "
            f"{recall - float(floor):.3f}. That margin is the only part of this score that is "
            f"evidence of anything, and it is not a significance test.")

    return {
        "scorecard_version": "2",
        "benchmark_content_hash": packet["benchmark_content_hash"],
        "benchmark_version": packet["benchmark_version"],
        "packet_hash": _sha(packet["items"])[:32],
        "k": k,
        "recall_at_k": None if recall is None else round(recall, 6),
        "recall_is": ("micro: matched gold directions over all gold directions, matching "
                      "researchforge.skills.meta.recall_at_k"),
        "macro_recall_over_seeds": None if macro is None else round(macro, 6),
        "held_out_recall_at_k": (round(held_hit / held_total, 6) if held_total else None),
        "held_out_note": ("no gold direction postdates the declared model cutoff, so there is no "
                          "uncontaminated subset in this benchmark at all"
                          if not held_total else "held-out subset is reported, never used to tune"),
        "contamination_floor": floor,
        "verdict": verdict,
        "why": why,
        "unadjudicated_pairs": unadjudicated,
        "total_pairs": len(packet["items"]),
        "seeds_with_no_generated_directions": unanswered_seeds,
        "unanswered_seed_policy": ("a seed the system answered with nothing counts its full gold "
                                   "set as missed. Dropping it from the average would let a "
                                   "system answer one seed, get it right, and report 1.0."),
        "adjudication": {
            "judge_kind": judge_kind,
            "judge_id": judge_id,
            "caveat": ("a model judge has the same contamination problem one level up: it is "
                       "deciding whether an output matches literature it has also read"
                       if judge_kind == "model" else None),
        },
        "per_seed": per_seed,
        "is_a_quality_measure": False,
        "what_it_is": ("a comparative signal between system versions against one frozen "
                       "benchmark. Research quality needs the blind rubric; the two do not "
                       "substitute for each other."),
    }


def to_harness_inputs(packet: dict) -> dict:
    """Convert a filled packet into what `research-eval-harness` expects.

    Two representations exist because the packet is built for a human to fill in
    and the harness input is built to be stamped with the system's identity. They
    are kept in one file so that a change to either format breaks a test rather
    than silently producing two different recall numbers from one adjudication.
    """
    directions, seen = [], set()
    adjudications = []
    for it in packet["items"]:
        did = f"{it['seed_id']}::gen{it['generated_rank']}"
        if did not in seen:
            seen.add(did)
            directions.append({"direction_id": did, "rank": it["generated_rank"],
                               "seed_id": it["seed_id"], "text": it["generated"]})
        if str(it.get("verdict") or "").upper() == "MATCH":
            adjudications.append({"system_direction_id": did,
                                  "gold_direction_id": it["gold_direction_id"],
                                  "match": True,
                                  "adjudicator": it.get("adjudicator")})
    return {"system_directions": directions, "match_adjudications": adjudications}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--benchmark", help="evals/retro_benchmark.jsonl")
    ap.add_argument("--directions", help="portfolio of generated directions")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--emit-packet", help="write the adjudication packet here")
    ap.add_argument("--packet", help="a filled adjudication packet to score")
    ap.add_argument("--judge-kind", choices=JUDGE_KINDS, default="unspecified")
    ap.add_argument("--judge-id")
    ap.add_argument("--out")
    ap.add_argument("--emit-harness-inputs",
                    help="write {system_directions, match_adjudications} for research-eval-harness")
    a = ap.parse_args(argv)

    if a.emit_packet:
        if not (a.benchmark and a.directions):
            raise SystemExit("--emit-packet needs --benchmark and --directions")
        freeze, pairs = load_benchmark(Path(a.benchmark))
        packet = emit_packet(freeze, pairs, load_directions(Path(a.directions)), a.k)
        Path(a.emit_packet).write_text(json.dumps(packet, indent=1), encoding="utf-8")
        print(f"wrote {a.emit_packet}: {len(packet['items'])} pair(s) to adjudicate across "
              f"{len({i['seed_id'] for i in packet['items']})} seed(s); floor="
              f"{packet['contamination_floor']}")
        for s in packet["seeds_with_no_generated_directions"]:
            print(f"  no generated direction for seed {s}: it scores 0 by absence, not by error")
        return 0

    if not a.packet:
        raise SystemExit("pass --emit-packet to prepare adjudication, or --packet to score one")
    packet = json.loads(Path(a.packet).read_text(encoding="utf-8"))
    # Scored first. Writing the harness file before validation left a file on disk
    # from a run the tool then declared unscoreable, and nothing about the file
    # says so.
    card = score(packet, a.judge_kind, a.judge_id)
    if a.emit_harness_inputs:
        payload = to_harness_inputs(packet)
        Path(a.emit_harness_inputs).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"wrote {a.emit_harness_inputs}: {len(payload['system_directions'])} direction(s), "
              f"{len(payload['match_adjudications'])} adjudicated match(es)")
    out = Path(a.out) if a.out else None
    text = json.dumps(card, indent=1)
    if out:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)
    print(f"{card['verdict']}: {card['why']}", file=sys.stderr)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())

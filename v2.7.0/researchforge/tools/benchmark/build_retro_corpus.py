#!/usr/bin/env python3
"""Build a retrospective benchmark corpus from Papers With Code evaluation tables.

WHAT THIS IS FOR

`retrospective-benchmark-builder` asks a question the innovation engine cannot
otherwise be asked: given a paper, does the system propose the directions the
field actually took next? Answering it needs a gold set of "what came next" that
was not written by the person grading the system.

WHY LEADERBOARDS RATHER THAN CITATIONS

The obvious source is forward citations, and it is the wrong one here. A citation
edge says one paper mentioned another; deciding which mentions are *material* is a
judgment, and a judgment made by the same model under test is not evidence. A
leaderboard row is different: it is a published claim that a named method reached
a named number on a named benchmark. The record-holder chain after a fixed date is
therefore a set of directions the field took, recorded by someone else, verifiable
by anyone, and requiring no adjudication from us at all.

WHAT IT COSTS

The chain is a *narrow* slice of "what came next". It contains only work that
improved a headline number on an existing benchmark. It excludes, by construction:
work that diagnosed the seed rather than beating it, work that refuted it, work
that changed the problem, and every direction that was tried and did not win. A
system scoring 0 on this benchmark has not been shown to be bad at research; it
has been shown not to predict leaderboard succession. That limit is not a caveat
to be softened — it is what the number means.

The corpus this writes is the input to the skill, which does its own adjudication,
freezing and contamination probing. This script does not score anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ARXIV_RE = re.compile(r"arxiv\.org/abs/(\d{4})\.(\d{4,5})", re.I)
#: Old-style ids (cs/0112017) carry no usable month and are dropped rather than
#: guessed at. A wrong date silently moves a paper across the window boundary.
ROW_RE = re.compile(r"^(?P<model>.+?)\s*[~|]\s*(?P<url>\S*)\s*[~|]\s*(?P<rest>.+)$")


def arxiv_date(url: str) -> tuple[str, str] | None:
    """(arxiv_id, YYYY-MM-01) or None when the url is not a dated arXiv id.

    The month comes from the identifier itself, which is assigned at first
    submission. It is not the publication date of the version the leaderboard
    row refers to, and for a paper revised across a year boundary the two differ.
    Recorded as `date_basis: arxiv_id_month` so a reader can discount it.
    """
    m = ARXIV_RE.search(url or "")
    if not m:
        return None
    yy, num = m.group(1)[:2], m.group(1)[2:]
    year = 1900 + int(yy) if int(yy) >= 91 else 2000 + int(yy)
    month = int(num)
    if not 1 <= month <= 12:
        return None
    return f"{m.group(1)}.{m.group(2)}", f"{year:04d}-{month:02d}-01"


def parse_rows(path: Path) -> tuple[list[dict], list[dict]]:
    """(usable rows, dropped rows).

    Dropped rows are returned rather than discarded because they are the one thing
    that can falsify the seed rule. A row whose paper is linked to a non-arXiv
    venue carries no date here and leaves the pool; if that row held the record,
    the recorded "seed" is not the record holder and every `replaces` claim in the
    chain is wrong while looking exactly the same.
    """
    rows, dropped = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        m = ROW_RE.match(line)
        if not m:
            dropped.append({"line": line[:160], "why": "does not parse as a leaderboard row"})
            continue
        parts = [p.strip() for p in m.group("rest").split("|")]
        metric_name, value_s = (parts[0], parts[1]) if len(parts) >= 2 else (None, parts[0])
        try:
            value = float(str(value_s).replace("%", "").strip())
        except ValueError:
            dropped.append({"line": line[:160], "why": "metric value is not a number"})
            continue
        dated = arxiv_date(m.group("url"))
        if not dated:
            dropped.append({"line": line[:160], "value": value,
                            "why": "no dated arXiv identifier, so the row cannot be placed in "
                                   "time; if it held the record the seed below is not the "
                                   "record holder"})
            continue
        rows.append({"model_name": m.group("model").strip(), "arxiv_id": dated[0],
                     "date": dated[1], "metric_name": metric_name, "value": value,
                     "paper_url": m.group("url").strip()})
    return rows, dropped


def chain(rows: list[dict], seed_end: str, eval_start: str, eval_end: str,
          metric_name: str | None,
          direction: str = "maximize") -> tuple[dict | None, list[dict], list[dict]]:
    """The seed (record holder at seed_end), the record chain, and the ambiguities.

    Ties keep the EARLIER paper: a later paper that merely matches the record did
    not advance anything, and calling it a follow-up would reward duplication.
    (The previous key, `(value, date < seed_end)`, was constant across every
    pre-cutoff row and so fell through to file order — on the shipped MultiNLI
    table it picked T5 over the earlier T5-XXL at an exact 92.0 tie.)

    `direction` is required rather than assumed. Every benchmark shipped here is
    higher-is-better, so hardcoding `max` was latent — but a WER or perplexity
    spec would have seeded on the *worst* method and emitted a chain of steadily
    worse numbers labelled "replaces".
    """
    better = (lambda a, b: a > b) if direction == "maximize" else (lambda a, b: a < b)
    # A row with no metric column came from a file the server already filtered to
    # one metric; dropping it because it does not repeat the metric name would
    # discard the whole benchmark and report it as "no data".
    rows = [r for r in rows
            if metric_name is None or r["metric_name"] in (None, metric_name)]
    pre = [r for r in rows if r["date"] <= seed_end]
    if not pre:
        return None, [], []
    ranked = sorted(pre, key=lambda r: (-r["value"] if direction == "maximize" else r["value"],
                                        r["date"]))
    seed = ranked[0]
    best = seed["value"]
    out, seen, ambiguous = [], {seed["arxiv_id"]}, []
    # Ascending within a month. arXiv identifiers carry only YYYY-MM, so two rows
    # in one month have no known order; processing best-first silently deleted the
    # smaller of two same-month record setters, and on ImageNet eleven months in
    # the evaluation window hold two or more rows. Ascending keeps both and stamps
    # the ambiguity rather than resolving it invisibly.
    window = sorted([r for r in rows if eval_start <= r["date"] <= eval_end],
                    key=lambda r: (r["date"], r["value"] if direction == "maximize"
                                   else -r["value"]))
    month_counts: dict[str, int] = {}
    for r in window:
        month_counts[r["date"]] = month_counts.get(r["date"], 0) + 1
    for r in window:
        if better(r["value"], best) and r["arxiv_id"] not in seen:
            entry = dict(r)
            if month_counts[r["date"]] > 1:
                entry["order_within_month"] = ("unknown: the arXiv identifier carries only a "
                                               "month, and this month holds several rows. "
                                               "Processed ascending by value.")
                ambiguous.append({"arxiv_id": r["arxiv_id"], "date": r["date"]})
            out.append(entry)
            seen.add(r["arxiv_id"])
            best = r["value"]
        elif better(r["value"], best):
            best = r["value"]
    return seed, out, ambiguous


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True, help="JSON list of benchmark specs")
    ap.add_argument("--meta", help="JSON map arxiv_id -> {title, abstract, published}")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    specs = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    meta = json.loads(Path(a.meta).read_text(encoding="utf-8")) if a.meta else {}
    pairs, skipped, all_dropped = [], [], []
    for s in specs:
        direction = s.get("direction", "maximize")
        if direction not in ("maximize", "minimize"):
            raise SystemExit(f"{s['dataset']}: direction={direction!r} must be 'maximize' or "
                             f"'minimize'. Assuming higher-is-better would seed a WER benchmark "
                             f"on its worst method and call every later, worse number a record.")
        rows, dropped = parse_rows(Path(s["file"]))
        if dropped:
            all_dropped.append({"benchmark": s["dataset"], "rows": dropped})
        seed, followups, ambiguous = chain(rows, s["seed_window_end"], s["eval_window_start"],
                                           s["eval_window_end"], s.get("metric_name"), direction)
        if seed is None:
            skipped.append({"benchmark": s["dataset"], "reason": "no row dated on or before "
                            "the seed window end; nothing was state of the art to succeed"})
            continue
        if s.get("seed_window_start") and seed["date"] < s["seed_window_start"]:
            skipped.append({"benchmark": s["dataset"],
                            "reason": f"the record holder at {s['seed_window_end']} is dated "
                                      f"{seed['date']}, outside the seed window. Using it would "
                                      f"make the seed window a fiction."})
            continue
        if len(followups) < s.get("min_followups", 2):
            skipped.append({"benchmark": s["dataset"],
                            "reason": f"only {len(followups)} record-setting follow-up(s) in the "
                                      f"evaluation window; below the declared minimum"})
            continue
        sm = meta.get(seed["arxiv_id"], {})
        pair = {
            "seed_id": f"{s['dataset']}::{seed['arxiv_id']}",
            "seed_arxiv_id": seed["arxiv_id"],
            "seed_title": sm.get("title") or seed["model_name"],
            "seed_published": sm.get("published") or seed["date"],
            "seed_date_basis": "publication_date" if sm.get("published") else "arxiv_id_month",
            "benchmark": {"task": s.get("task"), "dataset": s["dataset"],
                          "metric": s.get("metric_name") or seed["metric_name"],
                          "direction": direction,
                          "seed_model": seed["model_name"], "seed_value": seed["value"]},
            "date_ambiguities": ambiguous,
            "followups": [],
        }
        prev = seed["value"]
        for f in followups:
            fm = meta.get(f["arxiv_id"], {})
            pair["followups"].append({
                "id": f["arxiv_id"],
                "title": fm.get("title") or f["model_name"],
                "published": fm.get("published") or f["date"],
                # `replaces` is the recorded fact: a published number on the same
                # benchmark that exceeds the previous record. No text was read to
                # decide it, which is the whole point.
                "relation": "replaces",
                "problem_delta": (f"same task and benchmark as the seed "
                                  f"({s.get('task')} on {s['dataset']}); the delta is in how the "
                                  f"{s.get('metric_name') or seed['metric_name']} is reached, not "
                                  f"in what is being measured"),
                "method_delta": f"{f['model_name']} replaces {seed['model_name']}",
                "mechanism": fm.get("mechanism") or "",
                "demonstrating_experiment": (
                    f"{s['dataset']} / {s.get('metric_name') or seed['metric_name']}: "
                    f"{prev} -> {f['value']} (previous record -> this paper), as recorded in the "
                    f"Papers With Code evaluation table"),
                "_source_row": {"model_name": f["model_name"], "value": f["value"],
                                "paper_url": f["paper_url"], "date_basis": (
                                    "publication_date" if fm.get("published") else "arxiv_id_month")},
            })
            prev = f["value"]
        pairs.append(pair)

    payload = {
        "corpus_version": "1",
        "source": {
            "name": "Papers With Code evaluation tables (archived dump)",
            "hub_dataset": "felixleungsc/paperswithcode-data-evaluation-tables",
            "upstream_archive": "hf://datasets/pwc-archive/files/jul-28-evaluation-tables.json.gz",
            "retrieved_via": "huggingface datasets-server /filter, server-side WHERE per benchmark",
        },
        "construction": {
            "seed_rule": ("per benchmark, the record holder on the declared metric as of the seed "
                          "window end. The window is closed above and open below: what has to be "
                          "closed for 'afterwards' to mean anything is the cut date, not the "
                          "seed's own age. A benchmark whose record holder predates the cut by "
                          "years is kept and its date recorded, because excluding it would drop "
                          "exactly the benchmarks where progress had stalled"),
            "followup_rule": ("the record-holder chain in the evaluation window: each paper that "
                              "set a new best after the seed, in date order, deduplicated by "
                              "arXiv id"),
            "relation_basis": "published number on the same benchmark, not text adjudication",
            "date_basis": "arXiv identifier month unless a publication date was supplied",
        },
        "benchmarks_skipped": skipped,
        "rows_dropped": all_dropped,
        "rows_dropped_note": ("a dropped row is the one thing that can falsify the seed rule: if "
                              "it held the record, the recorded seed did not. They are listed "
                              "rather than counted so the claim can be checked."),
        "pairs": pairs,
    }
    Path(a.out).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    digest = hashlib.sha256(Path(a.out).read_bytes()).hexdigest()[:16]
    n_dropped = sum(len(d["rows"]) for d in all_dropped)
    print(f"wrote {a.out}: {len(pairs)} seed(s), "
          f"{sum(len(p['followups']) for p in pairs)} gold direction(s), "
          f"{len(skipped)} benchmark(s) skipped, {n_dropped} row(s) dropped, sha256={digest}")
    for s in skipped:
        print(f"  skipped {s['benchmark']}: {s['reason']}")
    for d in all_dropped:
        print(f"  {d['benchmark']}: {len(d['rows'])} row(s) dropped — if one of them held the "
              f"record, this benchmark's seed is not the record holder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

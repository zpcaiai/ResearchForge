#!/usr/bin/env python3
"""Build the sampling frame.

DEVIATION FROM PROTOCOL, recorded here because it changes what the result means.

REPRO_STUDY_PROTOCOL.md specifies a paper-first frame: enumerate a venue's accepted
papers via OpenReview, then follow their code links. OpenReview, OpenAlex, arXiv and
Crossref are all unreachable from this environment (proxy 403). Only api.github.com
is reachable.

So this frame is repo-first: it enumerates repositories that announce themselves as
official paper implementations. The consequences must be stated with every number
derived from it:

  1. Papers with NO public repo cannot enter the frame. The protocol allocated 3 of
     20 slots to them, and they are guaranteed RL0. Their absence inflates every
     rate reported here.
  2. Repos that do not describe themselves as an official implementation are missed.
  3. Therefore every rate computed from this frame is an UPPER BOUND on the
     paper-first rate the protocol asked for.

An upper bound is still decision-relevant, and for the one rule that matters most it
is decisive: if even the upper bound on P(RL>=1) is below the threshold, the
paper-first rate is below it too.

Queries are fixed before execution and use no judgment about which repos look
promising. Ranking bias is removed by pooling many pages and sampling at random with
a frozen seed.
"""
import argparse, json, os, random, sys, time
import httpx

# Pre-registered. Not tuned after seeing results.
QUERIES = [
    'official implementation in:readme language:Python created:2024-01-01..2025-06-30',
    'official code in:readme arxiv in:readme language:Python created:2024-01-01..2025-06-30',
    '"code for the paper" in:readme language:Python created:2024-01-01..2025-06-30',
    'topic:iclr2025 language:Python',
    'topic:neurips2024 language:Python',
]
API = "https://api.github.com"


def search(client, q, pages=2):
    out = []
    for page in range(1, pages + 1):
        r = client.get(f"{API}/search/repositories",
                       params={"q": q, "per_page": 100, "page": page})
        if r.status_code == 403:
            print(f"  rate limited; sleeping 60s", file=sys.stderr); time.sleep(60)
            r = client.get(f"{API}/search/repositories",
                           params={"q": q, "per_page": 100, "page": page})
        if r.status_code != 200:
            print(f"  query failed {r.status_code}: {q[:50]}", file=sys.stderr); break
        items = r.json().get("items", [])
        out.extend(items)
        if len(items) < 100:
            break
        time.sleep(1.5)   # unauthenticated search: ~10 req/min
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="frame.json")
    ap.add_argument("--sample-out", default="sample.json")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ResearchForge-study/0.3"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    pool, seen = [], set()
    with httpx.Client(headers=headers, timeout=40.0) as c:
        for q in QUERIES:
            items = search(c, q)
            print(f"  {len(items):>4} from: {q[:62]}", file=sys.stderr)
            for it in items:
                if it["full_name"] in seen:
                    continue
                seen.add(it["full_name"])
                pool.append({
                    "full_name": it["full_name"], "url": it["clone_url"],
                    "html_url": it["html_url"], "stars": it["stargazers_count"],
                    "forks": it["forks_count"], "size_kb": it["size"],
                    "created_at": it["created_at"], "pushed_at": it["pushed_at"],
                    "language": it["language"], "archived": it["archived"],
                    "license": (it.get("license") or {}).get("spdx_id"),
                    "description": (it.get("description") or "")[:300],
                    "_query": q,
                })
            time.sleep(1.5)

    # mechanical eligibility filter, applied before sampling, no judgment
    frame = [r for r in pool if not r["archived"] and r["size_kb"] < 500_000]
    json.dump({"queries": QUERIES, "pool": len(pool), "frame": len(frame),
               "built_at": time.time(), "repos": frame},
              open(a.out, "w"), indent=1)

    rng = random.Random(a.seed)
    sample = rng.sample(frame, min(a.n, len(frame)))
    for i, r in enumerate(sample, 1):
        r["sample_index"] = i
    json.dump({"seed": a.seed, "n": len(sample), "frame_size": len(frame),
               "frozen_at": time.time(),
               "frame_kind": "repo-first (deviation: OpenReview unreachable)",
               "known_bias": "papers without a public repo cannot enter; rates are UPPER BOUNDS",
               "sample": sample}, open(a.sample_out, "w"), indent=1)
    print(f"\npool={len(pool)} frame={len(frame)} sampled={len(sample)} seed={a.seed}")
    print("SAMPLE IS FROZEN. No substitutions.")
    for r in sample:
        print(f"  {r['sample_index']:>2}. {r['full_name']:<52} {r['stars']:>6}★ "
              f"{r['size_kb']:>7}KB {r['license'] or '-'}")


if __name__ == "__main__":
    main()

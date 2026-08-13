# Scoring a system against retro-v1

Two commands and one judgment. The judgment is the point: matching a generated direction to a gold
one is not string overlap, and the pipeline is built so that no lexical shortcut can quietly become
the matcher.

## 1. Emit the adjudication packet

```
python3 tools/benchmark/score_directions.py \
    --benchmark benchmarks/retro-v1/retro_benchmark.jsonl \
    --directions <project>/ideas/idea_portfolio.json \
    --k 10 --emit-packet packet.json
```

`--directions` accepts the innovation engine's real `idea_portfolio.json` (each idea must name the
`seed_id` it was generated for) or a plain `{seed_id: [direction, ...]}` map. The packet is the
**full cross product** of the top-k generated directions against every gold direction for that
seed — not a shortlist, because a shortlisting function would be the real matcher while the
adjudicator believed they were doing the matching.

## 2. Adjudicate

Fill `verdict` on each item: `MATCH`, `NO_MATCH`, or leave `UNADJUDICATED`. The rubric ships inside
the packet. Two rules do most of the work:

- **MATCH means the same research direction** — same problem delta *and* same mechanism. Same
  benchmark is not enough.
- **Strictly more general is NO_MATCH.** "Use a better teacher" does not match "update the teacher
  from the student's performance on the labelled set".

An `UNADJUDICATED` pair is excluded from the score. It is never counted as a miss, and the run is
reported `INCOMPLETE` rather than as a low number.

## 3. Score

```
python3 tools/benchmark/score_directions.py --packet packet.json \
    --judge-kind human --judge-id <who> --out scorecard.json \
    --emit-harness-inputs harness_inputs.json
```

The verdict is one of:

| verdict | meaning |
|---|---|
| `INCOMPLETE` | pairs were left unadjudicated; this is a partial measurement, not a low one |
| `UNINTERPRETABLE` | the benchmark has no measured contamination floor — an unmeasured floor is not a low floor |
| `AT_OR_BELOW_FLOOR` | a system that had merely read the literature would produce this number |
| `ABOVE_FLOOR` | only the margin above the floor is evidence, and it is not a significance test |

**On retro-v1 the floor is 0.75.** That is the number to beat, not zero.

`--judge-kind model` is allowed and is recorded, with the caveat attached to the scorecard: a model
judge has the same contamination problem one level up — it is deciding whether an output matches
literature it has also read.

`--emit-harness-inputs` writes `{system_directions, match_adjudications}` for
`research-eval-harness`, which stamps the score with the skill version, contract digest and suite
hash so two runs can be compared. A test asserts both paths compute the same recall from one
adjudication; two formats for one fact is how a project ends up with two numbers and no way to
tell which is right.

## The other half

recall@k is a regression signal between versions. It is not evidence that the directions are worth
anything — a system that only ever proposes "scale it up" scores well on leaderboard succession.
That question needs `tools/benchmark/blind_rubric.py`: experts rate generated directions against
human-authored ones without knowing which is which, and the analysis refuses to report a comparison
whose raters could identify the arms.

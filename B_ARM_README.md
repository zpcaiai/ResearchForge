# B arm — the human ceiling, ready to run

The A arm measured what a coding agent achieves on 20 papers under a 4-hour timebox. On its own
that number is uninterpretable. A 15% reproduction rate is damning if a skilled engineer gets 80%
on the same papers and unremarkable if they get 20%. **The B arm is the denominator**, and it is
the one number in this study nobody has.

Everything here is built except the part that needs a person.

## Before you spend a week: the protocol's n=8 cannot detect any gap you expect

`analyse.py` computes the minimum detectable difference before it reports anything, and for the
design as written the answer is brutal:

| per-arm n | smallest difference detectable at 80% power |
|---:|---:|
| 8 (the protocol) | **0.70** |
| 16 | 0.50 |
| 25 | 0.40 |
| 44 | 0.30 |
| 99 | 0.20 |

A seventy-point gap is larger than anything anyone predicts. Run the B arm at n=8 and the most
likely outcome is `NOT_DETECTABLE` — which is *not* "no difference", it is "a sample too small to
tell", and it costs a week of a skilled engineer's time to learn.

This is a correction to `REPRO_STUDY_PROTOCOL.md` §3, which called n=8 "a cost compromise" giving
"a rough estimate of the ceiling". It does not give a rough estimate. It gives an interval from
roughly 0.14 to 0.69 around a rate of 0.375 — wide enough to contain both "the agent is fine" and
"the agent is useless".

**If you want a usable answer, budget n≈25.** If you can only afford 8, run it knowing you are
collecting failure-code distributions and qualitative notes, not a gap — those are still worth
having, and `analyse.py` reports them, but do not call the result a ceiling.

## Running it

```
# 1. draw the subset and build the packet
python3 docs/study/b-arm/build_packet.py --a-arm docs/study/all_results.jsonl \
    --n 8 --seed 20260812 --packet b_packet.json --key b_key.json

# 2. give b_packet.json to the engineer. Do NOT give them b_key.json.

# 3. when the worksheets come back
python3 docs/study/b-arm/analyse.py --packet b_packet_returned.json \
    --key b_key.json --a-arm docs/study/all_results.jsonl --out b_arm_result.json
```

The subset is **drawn, not chosen** — a seeded shuffle whose seed is recorded in the packet.
Picking eight by hand would select for papers whose difficulty someone already had an opinion
about, and that opinion comes from the A-arm results this arm is supposed to be blind to.

## The blind is the design

The packet carries the paper, the repository and the claim targets. It withholds every A-arm
outcome — level, failure codes, timings, notes — and lists what it withheld so the withholding is
auditable. If the engineer reads the A-arm log first, what gets measured is *"can a human fix an
agent's dead end"*, which is a much easier question because the agent has already paid the search
cost.

The builder refuses to run if the A-arm records contain a field it does not classify as either
carried or withheld. A field added later shows up as an error rather than leaking into the packet.

Each worksheet asks `saw_a_arm_material` after the fact. Answer honestly: a `yes` does not waste
the worksheet, it moves it out of the ceiling estimate and into a separate sample. A `yes` recorded
as a `no` destroys the comparison and nothing downstream can detect it.

## What the analysis refuses to do

1. **Report a gap over different papers.** The returned packet must hash to its key. A swapped
   paper, or a packet rebuilt with a different seed, gives two rates over two samples — and the
   difference of those is not a gap.
2. **Fold in a run that broke its timebox.** A worksheet that ran past 8 hours executed a different
   design. Reported separately, never merged.
3. **Count an unattempted paper as a failure.** Unfinished is `NOT_RUN`. Counting it as RL0 makes
   the human arm look worse and the agent look better — the exact direction of error this study
   exists to avoid.
4. **Call RL1 a reproduction.** Success is RL3+, meaning at least one main result inside the
   tolerance that step 0's seed-variance measurement set. RL1 is "it ran without erroring" and RL2
   is "right direction at reduced scale"; folding them in is the most common way a study of this
   kind reports a rate several times too high.

## Step 0 is not optional

Every worksheet carries it: repeat the main experiment with three different seeds and record the
standard deviation before comparing anything. Tolerance is `max(the paper's reported variance,
2 × your measured 3-seed sd, 2% relative)`. `docs/study/step0_results.json` is why — on the worked
example, **8 of 12 metrics needed a tolerance wider than the 2% floor, one of them 67× wider**. A
fixed ±5% turns a high-variance task's own noise into a failed reproduction.
